from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt

from akashic.config import settings

ALGORITHM = "HS256"

# Audience claim — narrow user access tokens to this API so a token
# minted for one service can't be replayed against another that
# happens to share the HMAC secret. Scanner tokens (asymmetric, in
# scanner_keys.py) carry their own audience and are unaffected.
AUDIENCE = "akashic-api"


def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=settings.access_token_expire_minutes))
    to_encode.update({"exp": expire, "aud": AUDIENCE})
    return jwt.encode(to_encode, settings.secret_key, algorithm=ALGORITHM)


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
