"""Access tokens carry an `aud` claim and decoding rejects mismatched
audiences (review I8). Without this guard, a token minted for the
api could in principle be replayed against another service that
shared the HMAC secret."""
from datetime import datetime, timedelta, timezone

from jose import jwt as jose_jwt

from akashic.auth.jwt import (
    ALGORITHM,
    AUDIENCE,
    create_access_token,
    decode_access_token,
)
from akashic.config import settings


def test_token_includes_audience_claim():
    token = create_access_token({"sub": "user-id"})
    payload = decode_access_token(token)
    assert payload is not None
    assert payload["aud"] == AUDIENCE
    assert payload["sub"] == "user-id"


def test_token_with_wrong_audience_rejected():
    """A token signed with the right secret but the wrong aud is
    rejected by decode_access_token."""
    payload = {
        "sub": "user-id",
        "aud": "some-other-service",
        "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
    }
    foreign = jose_jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)
    assert decode_access_token(foreign) is None


def test_token_with_no_audience_rejected():
    """python-jose raises if `audience` was passed to decode but the
    token has no aud claim — same path returns None here."""
    payload = {
        "sub": "user-id",
        "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
    }
    audless = jose_jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)
    assert decode_access_token(audless) is None
