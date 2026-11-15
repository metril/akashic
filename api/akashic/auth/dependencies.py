import uuid
from dataclasses import dataclass
from typing import Any, Literal

from fastapi import Depends, HTTPException, Request, Security, status
from fastapi.openapi.models import OAuthFlows
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer, OAuth2
from fastapi.security.utils import get_authorization_scheme_param
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.api_tokens import is_api_token, principal_from_token
from akashic.auth.jwt import decode_access_token, decode_ingest_token
from akashic.auth.scopes import ACL_BYPASS_SCOPE, INGEST_SCOPE, SCOPES, scopes_for_role
from akashic.database import get_db
from akashic.errors import ApiHTTPException
from akashic.models.user import User

class _BearerScopes(OAuth2):
    """Reads ``Authorization: Bearer <token>``.

    It subclasses ``OAuth2`` (not ``HTTPBearer``) so FastAPI records each
    operation's required scopes in its OpenAPI ``security`` requirement.
    """

    def __init__(self, scheme_name: str, description: str) -> None:
        super().__init__(
            flows=OAuthFlows(), scheme_name=scheme_name, description=description, auto_error=True,
        )

    async def __call__(self, request: Request) -> str:  # type: ignore[override]
        scheme, param = get_authorization_scheme_param(request.headers.get("Authorization"))
        if scheme.lower() != "bearer" or not param:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return param


security = _BearerScopes(
    "userJWT", "Access token from `POST /api/v1/auth/login` or `/api/v1/auth/refresh`.",
)
_ingest_security = HTTPBearer(
    scheme_name="ingestJWT", bearerFormat="JWT",
    description="Ingest-scoped token leased to a scanner for one scan. Internal.",
)


@dataclass(frozen=True)
class Principal:
    """The authenticated caller and what it may do.

    ``scopes`` are the *effective* scopes: what the credential grants,
    intersected with what the user's role currently allows.
    ``allowed_source_ids`` is ``None`` for "every source".
    ``via`` is how the caller authenticated (``jwt`` for a login session).
    """

    user: User
    scopes: frozenset[str]
    allowed_source_ids: frozenset[uuid.UUID] | None = None
    via: Literal["jwt", "token", "ingest"] = "jwt"

    @classmethod
    def for_user(cls, user: User, *, via: Literal["jwt", "token", "ingest"] = "jwt") -> "Principal":
        return cls(user=user, scopes=scopes_for_role(user.role), via=via)

    def has(self, scope: str) -> bool:
        return scope in self.scopes

    def ensure(self, scope: str) -> None:
        """Raise 403 ``insufficient_scope`` unless this principal holds ``scope``."""
        if scope not in self.scopes:
            raise ApiHTTPException(
                status.HTTP_403_FORBIDDEN, f"Missing required scope: {scope}", code="insufficient_scope",
            )

    @property
    def source_limited(self) -> bool:
        return self.allowed_source_ids is not None

    def allows_source(self, source_id: uuid.UUID | None) -> bool:
        """False when this principal is limited and ``source_id`` is outside the limit
        (a NULL/orphan source is outside every limit)."""
        if self.allowed_source_ids is None:
            return True
        return source_id is not None and source_id in self.allowed_source_ids

    def ensure_unrestricted(self) -> None:
        """403 for a source-limited principal: for actions that reach beyond its sources."""
        if self.source_limited:
            raise _source_limited_error()

    @property
    def bypasses_acl(self) -> bool:
        """True when per-user file ACL filtering does not apply to this caller."""
        return ACL_BYPASS_SCOPE in self.scopes


def _source_limited_error() -> ApiHTTPException:
    return ApiHTTPException(
        status.HTTP_403_FORBIDDEN, "This token is limited to specific sources and cannot do this",
        code="source_limited",
    )


async def _load_user(user_id: str, db: AsyncSession) -> User | None:
    try:
        uid = uuid.UUID(user_id)
    except (ValueError, TypeError):
        return None
    return (await db.execute(select(User).where(User.id == uid))).scalar_one_or_none()


async def principal_from_jwt(token: str, db: AsyncSession) -> Principal | None:
    """Resolve a user access JWT to a Principal; None if invalid or the user is gone."""
    payload = decode_access_token(token)
    sub = payload.get("sub") if payload else None
    if sub is None:
        return None
    user = await _load_user(sub, db)
    return Principal.for_user(user) if user is not None else None


async def get_principal(
    request: Request,
    token: str = Security(security),
    db: AsyncSession = Depends(get_db),
) -> Principal:
    """Authenticate the request (login JWT or ``akp_`` API token).

    Scope enforcement is `require_scope`'s job."""
    if is_api_token(token):
        principal = await principal_from_token(token, db, request)
    else:
        principal = await principal_from_jwt(token, db)
    if principal is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return principal


def require_scope(scope: str, *, source_scoped: bool = False) -> Any:
    """Dependency factory: the caller must hold ``scope``.

    ``source_scoped=True`` declares that the route honours a source-limited
    principal (see ``auth.source_scope``). Without it, a source-limited token
    is refused (403 ``source_limited``): limits fail closed.

    Use as ``principal: Principal = require_scope("files:read")``. The scope
    is published as an OAuth2 scope on the operation. A scope missing from
    the catalog is a programming error and fails at import time.
    """
    if scope not in SCOPES:
        raise KeyError(f"unknown scope {scope!r}")

    async def _require(principal: Principal = Depends(get_principal)) -> Principal:
        principal.ensure(scope)
        if principal.source_limited and not source_scoped:
            raise _source_limited_error()
        return principal

    _require.required_scope = scope  # type: ignore[attr-defined]
    _require.source_scoped = source_scoped  # type: ignore[attr-defined]
    # Security(security, scopes=...) on a parent makes `scopes` reach the
    # OAuth2 scheme under get_principal; only the scheme reads them.
    return Security(_require, scopes=[scope])


async def get_ingest_user(
    credentials: HTTPAuthorizationCredentials = Depends(_ingest_security),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Resolve the user from a token minted by create_ingest_token.

    Distinct from get_principal so an ingest-scoped token (handed to the
    scanner agent) cannot be presented to user endpoints, and a regular
    user token cannot satisfy the ingest dependency.

    The user identity is still loaded — ingest needs an authenticated
    actor for ACL/audit purposes — but the audience boundary keeps
    each token confined to its issued purpose (review A-C1)."""
    payload = decode_ingest_token(credentials.credentials)
    if payload is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    user_id = payload.get("sub")
    if user_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    result = await db.execute(select(User).where(User.id == uuid.UUID(user_id)))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    # The token was minted while the holder could write sources; a later
    # downgrade must end the capability immediately, not at token expiry.
    Principal.for_user(user, via="ingest").ensure(INGEST_SCOPE)
    return user


# Optional-credentials bearer for the scanner_id extractor below.
# We deliberately don't raise on missing creds — the real auth gate
# is `get_ingest_user`; this dep is purely "if a valid ingest JWT
# carried a scanner_id claim, surface it". Tests that override
# `get_ingest_user` (and therefore never present a real Authorization
# header) must not 401 just because this companion dep also runs.
_optional_bearer = HTTPBearer(auto_error=False, scheme_name="ingestJWT", bearerFormat="JWT")


async def get_ingest_scanner_id(
    credentials: HTTPAuthorizationCredentials | None = Depends(_optional_bearer),
) -> uuid.UUID | None:
    """Extract the scanner_id claim (if any) from an ingest JWT.

    v0.28.2 — minted into the token at lease time by `_mint_ingest_jwt`
    so scan-progress POSTs can attribute heartbeat / log / stderr rows
    to the scanner that actually produced them, without trusting a
    client-supplied header.

    Returns None for legacy tokens that predate the claim (rows still
    persist, just with scanner_id NULL — the column is nullable) and
    for requests where no bearer is presented (auth is enforced by
    `get_ingest_user` separately; this dep only annotates).
    """
    if credentials is None:
        return None
    payload = decode_ingest_token(credentials.credentials)
    if payload is None:
        return None
    sid = payload.get("scanner_id")
    if sid is None:
        return None
    try:
        return uuid.UUID(sid)
    except (TypeError, ValueError):
        return None


async def check_source_access(
    source_id: uuid.UUID,
    principal: Principal,
    db: AsyncSession,
) -> None:
    """Per-source access hook. Authorisation is the route's declared scope
    alone; this adds no scope requirement of its own. A source-limited token
    gets 404 for any source outside its list. (File-level visibility is
    narrowed by filesystem-ACL filtering, not by per-source grants.)"""
    if not principal.allows_source(source_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found")
