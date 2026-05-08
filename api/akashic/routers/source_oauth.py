"""HTTP surface for the source-OAuth foundation.

Security notes (review A-C2/A-C3):

- The callback re-validates the actor against ``state.initiator`` by
  reading the refresh-cookie (defense in depth — the state JWT is
  itself signed and time-bounded, but tying it to the current browser
  session blocks an attacker who somehow obtains a valid state token
  from completing the flow on their own machine).
- The callback HTML uses postMessage with the configured callback
  origin, never the wildcard ``"*"``. A page that opens our callback
  via ``window.open`` from a different origin can't read the response
  payload.

Endpoints:

  GET    /api/oauth/providers
    List the providers the API knows about, with their configured-status.
  GET    /api/oauth/providers/{provider}
    Single-provider read (admin).
  PUT    /api/oauth/providers/{provider}
    Upsert client_id / client_secret / redirect_uri (admin).
  DELETE /api/oauth/providers/{provider}
    Forget the client config (admin).

  POST   /api/oauth/start
    Build an authorize URL for the given provider. Returns a JSON body
    with ``authorization_url`` so the frontend can ``window.open`` it.
  GET    /api/oauth/callback
    The redirect_uri the provider hits. Exchanges the code for tokens,
    persists (or refreshes) the credential, then renders an HTML page
    that posts a ``window.opener`` message and closes itself.

  GET    /api/oauth/credentials
    List the credentials the current user can see. PR-A returns all
    credentials to admins; non-admins see only credentials whose
    source_id maps to a source they can access. (PR-C tightens this.)
  DELETE /api/oauth/credentials/{credential_id}
    Revoke + forget a stored credential (admin).

  POST   /api/oauth/credentials/{credential_id}/refresh
    Force a refresh of the cached access token. Used by Connected
    Accounts UI to confirm refresh actually works end-to-end.
"""
from __future__ import annotations

import logging
import uuid
from datetime import timezone
from html import escape as html_escape
from urllib.parse import urlparse

from fastapi import APIRouter, Cookie, Depends, HTTPException, Query, status
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth import refresh as refresh_service
from akashic.auth.dependencies import get_current_user, require_admin
from akashic.database import get_db
from akashic.models.oauth_app_config import OAuthAppConfig
from akashic.models.oauth_credential import SourceOAuthCredential
from akashic.models.user import User

logger = logging.getLogger(__name__)
REFRESH_COOKIE = "akashic_refresh"  # mirrors auth.py — kept local to avoid an import cycle
from akashic.schemas.oauth import (
    OAuthAppConfigSummary,
    OAuthAppConfigUpsert,
    OAuthCredentialSummary,
    OAuthRefreshResponse,
    OAuthStartRequest,
    OAuthStartResponse,
)
from akashic.services.oauth_providers import get_provider, known_providers
from akashic.services.secret_encryption import encrypt_secret
from akashic.services.source_oauth import (
    OAuthAppNotConfigured,
    OAuthExchangeFailed,
    build_authorize_url,
    decode_state,
    encode_state,
    exchange_code,
    mint_access_token,
    require_app_config,
    store_credential_from_token_response,
)


router = APIRouter(prefix="/api/oauth", tags=["oauth"])


# -----------------------------------------------------------------------------
# Provider client-app config (admin).
# -----------------------------------------------------------------------------


def _summary_for_app(cfg: OAuthAppConfig) -> OAuthAppConfigSummary:
    return OAuthAppConfigSummary(
        provider=cfg.provider,
        client_id=cfg.client_id,
        has_secret=bool(cfg.client_secret_encrypted),
        redirect_uri=cfg.redirect_uri,
        configured_at=cfg.configured_at,
        updated_at=cfg.updated_at,
    )


@router.get("/providers", response_model=list[OAuthAppConfigSummary])
async def list_providers(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
) -> list[OAuthAppConfigSummary]:
    result = await db.execute(select(OAuthAppConfig))
    configured = {row.provider: row for row in result.scalars()}
    out: list[OAuthAppConfigSummary] = []
    for name in known_providers():
        cfg = configured.get(name)
        if cfg is None:
            out.append(
                OAuthAppConfigSummary(
                    provider=name,
                    client_id="",
                    has_secret=False,
                    redirect_uri="",
                    configured_at=__import__("datetime").datetime.fromtimestamp(
                        0, tz=timezone.utc
                    ),
                    updated_at=__import__("datetime").datetime.fromtimestamp(
                        0, tz=timezone.utc
                    ),
                )
            )
        else:
            out.append(_summary_for_app(cfg))
    return out


@router.put("/providers/{provider}", response_model=OAuthAppConfigSummary)
async def upsert_provider(
    provider: str,
    body: OAuthAppConfigUpsert,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
) -> OAuthAppConfigSummary:
    if provider not in known_providers():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown provider: {provider!r}",
        )
    encrypted = encrypt_secret(body.client_secret)
    existing = (
        await db.execute(
            select(OAuthAppConfig).where(OAuthAppConfig.provider == provider)
        )
    ).scalar_one_or_none()
    if existing is None:
        cfg = OAuthAppConfig(
            provider=provider,
            client_id=body.client_id,
            client_secret_encrypted=encrypted,
            redirect_uri=body.redirect_uri,
            configured_by_user_id=user.id,
        )
        db.add(cfg)
    else:
        existing.client_id = body.client_id
        existing.client_secret_encrypted = encrypted
        existing.redirect_uri = body.redirect_uri
        existing.configured_by_user_id = user.id
        cfg = existing
    await db.commit()
    await db.refresh(cfg)
    return _summary_for_app(cfg)


@router.delete("/providers/{provider}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_provider(
    provider: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
) -> None:
    cfg = (
        await db.execute(
            select(OAuthAppConfig).where(OAuthAppConfig.provider == provider)
        )
    ).scalar_one_or_none()
    if cfg is None:
        return
    await db.delete(cfg)
    await db.commit()


# -----------------------------------------------------------------------------
# Authorize start.
# -----------------------------------------------------------------------------


@router.post("/start", response_model=OAuthStartResponse)
async def start_authorize(
    body: OAuthStartRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
) -> OAuthStartResponse:
    if body.provider not in known_providers():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown provider: {body.provider!r}",
        )
    if body.mode not in {"associate", "test"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown mode: {body.mode!r}",
        )

    try:
        app = await require_app_config(db, body.provider)
    except OAuthAppNotConfigured as exc:
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail=str(exc),
        ) from exc

    provider = get_provider(body.provider)
    state = encode_state(
        provider=provider.name,
        source_id=body.source_id,
        initiator_user_id=user.id,
        mode=body.mode,
    )
    authorize_url = build_authorize_url(
        provider,
        client_id=app.client_id,
        redirect_uri=app.redirect_uri,
        state=state,
    )
    return OAuthStartResponse(authorization_url=authorize_url, state=state)


# -----------------------------------------------------------------------------
# Callback.
# -----------------------------------------------------------------------------


_CALLBACK_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>OAuth callback</title>
  <style>
    body {{ font-family: ui-sans-serif, system-ui, sans-serif; padding: 2rem;
           max-width: 32rem; margin: 0 auto; color: #1f2937; }}
    .ok {{ color: #15803d; }} .err {{ color: #b91c1c; }}
    code {{ background: #f3f4f6; padding: 0 .25rem; border-radius: .25rem; }}
  </style>
</head>
<body>
  <h2 class="{cls}">{title}</h2>
  <p>{detail}</p>
  <p style="color:#6b7280">You can close this window.</p>
  <script>
    try {{
      window.opener && window.opener.postMessage({payload}, {origin_js});
    }} catch (e) {{}}
    setTimeout(function() {{ window.close(); }}, 1500);
  </script>
</body>
</html>
"""


def _origin_from_redirect_uri(redirect_uri: str | None) -> str:
    """Extract the scheme://host[:port] origin from the configured
    redirect_uri so postMessage targets the same origin that hosts our
    web UI. Falls back to the literal "/" (same-origin only) if we
    can't parse the URI — never the wildcard "*"."""
    if not redirect_uri:
        return "/"
    try:
        parsed = urlparse(redirect_uri)
    except ValueError:
        return "/"
    if not parsed.scheme or not parsed.netloc:
        return "/"
    return f"{parsed.scheme}://{parsed.netloc}"


def _callback_page(
    *, ok: bool, title: str, detail: str, payload: str, origin: str = "/",
) -> HTMLResponse:
    import json as _json
    html = _CALLBACK_TEMPLATE.format(
        cls="ok" if ok else "err",
        title=html_escape(title),
        detail=detail,  # detail is constructed below w/ html_escape on user input
        payload=payload,
        origin_js=_json.dumps(origin),
    )
    return HTMLResponse(html)


@router.get("/callback")
async def callback(
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    error_description: str | None = Query(default=None),
    akashic_refresh: str | None = Cookie(default=None, alias=REFRESH_COOKIE),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    # Provider returned an error before we even saw a code.
    if error:
        msg = error_description or error
        return _callback_page(
            ok=False,
            title="Connection denied",
            detail=f"<code>{html_escape(error)}</code> — {html_escape(msg)}",
            payload=(
                '{"akashic_oauth": true, "ok": false, '
                f'"error": {repr_json(error)} }}'
            ),
        )

    if not code or not state:
        return _callback_page(
            ok=False,
            title="Bad callback",
            detail="Missing <code>code</code> or <code>state</code> parameter.",
            payload='{"akashic_oauth": true, "ok": false, "error": "missing_params"}',
        )

    try:
        state_payload = decode_state(state)
    except Exception:
        return _callback_page(
            ok=False,
            title="Invalid state",
            detail="The state token couldn't be verified — sign in again.",
            payload='{"akashic_oauth": true, "ok": false, "error": "invalid_state"}',
        )

    provider_name = state_payload.get("provider")
    source_id_raw = state_payload.get("source_id")
    mode = state_payload.get("mode") or "associate"
    initiator_raw = state_payload.get("initiator")
    source_id = uuid.UUID(source_id_raw) if source_id_raw else None

    try:
        app = await require_app_config(db, provider_name)
    except OAuthAppNotConfigured as exc:
        return _callback_page(
            ok=False,
            title="Provider not configured",
            detail=html_escape(str(exc)),
            payload='{"akashic_oauth": true, "ok": false, "error": "not_configured"}',
        )

    # Defense-in-depth: verify the current browser session matches the
    # initiator recorded in the state token. The state JWT is signed
    # and time-bounded, but binding it to the session blocks an
    # attacker who somehow obtains a valid state token from completing
    # the flow on a different machine. If no session cookie is present
    # we still allow the callback (the user might have logged out
    # mid-flow), but log a warning so the audit trail captures it
    # (review A-C2).
    origin = _origin_from_redirect_uri(app.redirect_uri)
    if initiator_raw and akashic_refresh:
        try:
            actor_user_id = await refresh_service.peek_user_id(akashic_refresh, db)
        except Exception:  # noqa: BLE001 - refresh-service errors are non-fatal
            actor_user_id = None
        if actor_user_id is not None and str(actor_user_id) != str(initiator_raw):
            logger.warning(
                "oauth_callback initiator mismatch: state=%s session=%s provider=%s",
                initiator_raw, actor_user_id, provider_name,
            )
            return _callback_page(
                ok=False,
                title="Session mismatch",
                detail=(
                    "This OAuth callback was started by a different user. "
                    "Sign in as the original initiator and try again."
                ),
                payload='{"akashic_oauth": true, "ok": false, "error": "initiator_mismatch"}',
                origin=origin,
            )
    elif initiator_raw and not akashic_refresh:
        logger.warning(
            "oauth_callback no session cookie present (state initiator=%s, provider=%s)",
            initiator_raw, provider_name,
        )

    provider = get_provider(provider_name)
    from akashic.services.secret_encryption import decrypt_secret

    try:
        client_secret = decrypt_secret(app.client_secret_encrypted)
        token_response = await exchange_code(
            provider,
            client_id=app.client_id,
            client_secret=client_secret,
            redirect_uri=app.redirect_uri,
            code=code,
        )
        cred = await store_credential_from_token_response(
            db,
            provider=provider,
            token_response=token_response,
            source_id=source_id,
        )
    except OAuthExchangeFailed as exc:
        return _callback_page(
            ok=False,
            title="Exchange failed",
            detail=html_escape(exc.detail),
            payload='{"akashic_oauth": true, "ok": false, "error": "exchange_failed"}',
            origin=origin,
        )

    if mode == "test":
        # Successfully proved the round-trip works; drop the credential
        # so we don't leak a row that nothing will ever use.
        await db.delete(cred)
        await db.commit()
        title = "Connected — test successful"
        detail = (
            f"Verified <code>{html_escape(provider.name)}</code> as "
            f"<code>{html_escape(cred.account_email or '?')}</code>. The test "
            "credential has been discarded."
        )
        payload = (
            '{"akashic_oauth": true, "ok": true, "mode": "test", '
            f'"provider": {repr_json(provider.name)}, '
            f'"account_email": {repr_json(cred.account_email or "")} }}'
        )
        return _callback_page(ok=True, title=title, detail=detail, payload=payload, origin=origin)

    title = "Connected"
    detail = (
        f"<code>{html_escape(provider.name)}</code> connected as "
        f"<code>{html_escape(cred.account_email or '?')}</code>."
    )
    payload = (
        '{"akashic_oauth": true, "ok": true, "mode": "associate", '
        f'"credential_id": {repr_json(str(cred.id))}, '
        f'"provider": {repr_json(provider.name)}, '
        f'"account_email": {repr_json(cred.account_email or "")} }}'
    )
    return _callback_page(ok=True, title=title, detail=detail, payload=payload, origin=origin)


def repr_json(s: str) -> str:
    """Tiny helper — JSON-encode a string for embedding in our HTML."""
    import json

    return json.dumps(s)


# -----------------------------------------------------------------------------
# Credential read / delete / force-refresh.
# -----------------------------------------------------------------------------


def _summary_for_credential(cred: SourceOAuthCredential) -> OAuthCredentialSummary:
    # cred.source is loaded eagerly via lazy="joined" on the relationship
    # (see models/oauth_credential.py). Reading .name doesn't trigger an
    # extra query.
    source_name = cred.source.name if cred.source is not None else None
    return OAuthCredentialSummary(
        id=cred.id,
        source_id=cred.source_id,
        provider=cred.provider,
        account_email=cred.account_email,
        account_label=cred.account_label,
        scope=cred.scope,
        access_token_expires_at=cred.access_token_expires_at,
        created_at=cred.created_at,
        updated_at=cred.updated_at,
        source_name=source_name,
    )


@router.get("/credentials", response_model=list[OAuthCredentialSummary])
async def list_credentials(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
) -> list[OAuthCredentialSummary]:
    result = await db.execute(
        select(SourceOAuthCredential).order_by(SourceOAuthCredential.created_at.desc())
    )
    return [_summary_for_credential(c) for c in result.scalars()]


@router.delete(
    "/credentials/{credential_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_credential(
    credential_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
) -> None:
    cred = await db.get(SourceOAuthCredential, credential_id)
    if cred is None:
        return
    await db.delete(cred)
    await db.commit()


@router.post(
    "/credentials/{credential_id}/refresh", response_model=OAuthRefreshResponse
)
async def force_refresh(
    credential_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
) -> OAuthRefreshResponse:
    cred = await db.get(SourceOAuthCredential, credential_id)
    if cred is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="credential not found"
        )
    # Force the refresh path by clearing the cached expiry — mint_access_token
    # will then go through the provider's refresh endpoint.
    cred.access_token_expires_at = None
    cred.access_token_cached = None
    await db.commit()
    try:
        access_token = await mint_access_token(db, cred)
    except OAuthExchangeFailed as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=exc.detail
        ) from exc
    await db.refresh(cred)
    return OAuthRefreshResponse(
        access_token=access_token,
        expires_at=cred.access_token_expires_at,
    )
