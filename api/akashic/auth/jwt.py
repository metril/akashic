from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt

from akashic.config import settings

ALGORITHM = "HS256"

# Audience claims — narrow each kind of access token to its intended
# verifier so a token minted for one purpose can't be replayed against
# another. Scanner JWTs (asymmetric, in scanner_keys.py) carry their
# own audience and are unaffected.
#
# AUDIENCE         — user access tokens for the public REST API.
# INGEST_AUDIENCE  — short-lived tokens handed to the scanner agent so
#   it can call /api/ingest/batch and the per-scan heartbeat. Scoped
#   separately from AUDIENCE because the scanner host runs untrusted
#   code (sees user file content) and must NOT be able to use its
#   token against /api/users/create or /api/sources/* endpoints
#   (review A-C1).
AUDIENCE = "akashic-api"
INGEST_AUDIENCE = "akashic-ingest"


def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=settings.access_token_expire_minutes))
    to_encode.update({"exp": expire, "aud": AUDIENCE})
    return jwt.encode(to_encode, settings.secret_key, algorithm=ALGORITHM)


def create_ingest_token(user_id: str, expires_delta: timedelta | None = None) -> str:
    """Mint a JWT scoped to the ingest audience. Only the ingest +
    scan-heartbeat endpoints accept this audience; presenting it to
    /api/users, /api/sources, or any other admin endpoint will fail
    audience validation in decode_access_token."""
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(hours=24))
    return jwt.encode(
        {"sub": user_id, "exp": expire, "aud": INGEST_AUDIENCE},
        settings.secret_key,
        algorithm=ALGORITHM,
    )


def decode_access_token(token: str) -> dict | None:
    try:
        return jwt.decode(
            token,
            settings.secret_key,
            algorithms=[ALGORITHM],
            audience=AUDIENCE,
            # python-jose only enforces audience matching when an `aud`
            # claim is present in the token. Force it to be present so a
            # legacy/malicious token without aud can't slip through.
            options={"require_aud": True},
        )
    except JWTError:
        return None


def decode_ingest_token(token: str) -> dict | None:
    """Decode-and-verify a token minted by create_ingest_token. Returns
    None if the token is missing/invalid/wrong-audience. Distinct from
    decode_access_token so a regular user JWT can never satisfy the
    ingest dependency and vice versa."""
    try:
        return jwt.decode(
            token,
            settings.secret_key,
            algorithms=[ALGORITHM],
            audience=INGEST_AUDIENCE,
            options={"require_aud": True},
        )
    except JWTError:
        return None
