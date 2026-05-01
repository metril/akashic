"""Ed25519 keypair issuance + JWT sign/verify primitives."""
import json

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from akashic.services.scanner_keys import (
    fingerprint_of_pem,
    generate_keypair,
    peek_kid,
    sign_jwt,
    verify_jwt,
)


def test_generate_keypair_round_trip():
    kp = generate_keypair()
    # Keys parse back as Ed25519.
    priv = serialization.load_pem_private_key(
        kp.private_pem.encode(), password=None,
    )
    assert isinstance(priv, Ed25519PrivateKey)
    pub = serialization.load_pem_public_key(kp.public_pem.encode())
    # Fingerprint matches the helper applied to the same PEM.
    assert kp.fingerprint == fingerprint_of_pem(kp.public_pem)
    # Different keys → different fingerprints.
    other = generate_keypair()
    assert kp.fingerprint != other.fingerprint


def test_sign_and_verify_round_trip():
    kp = generate_keypair()
    claims = {"iss": "scanner", "sub": "abc", "iat": 100, "exp": 200}
    token = sign_jwt(kp.private_pem, claims, headers={"kid": "abc"})
    out = verify_jwt(kp.public_pem, token)
    assert out == claims
    assert peek_kid(token) == "abc"


def test_verify_rejects_tampered_payload():
    kp = generate_keypair()
    token = sign_jwt(kp.private_pem, {"iss": "scanner", "sub": "x"}, {"kid": "x"})
    h, p, s = token.split(".")
    # Replace the payload with one claiming a different subject.
    import base64
    bad_payload_bytes = json.dumps({"iss": "scanner", "sub": "evil"}).encode()
    bad_p = base64.urlsafe_b64encode(bad_payload_bytes).rstrip(b"=").decode()
    tampered = f"{h}.{bad_p}.{s}"
    with pytest.raises(ValueError, match="invalid signature"):
        verify_jwt(kp.public_pem, tampered)


def test_verify_rejects_wrong_key():
    kp = generate_keypair()
    other = generate_keypair()
    token = sign_jwt(kp.private_pem, {"iss": "scanner"}, {"kid": "x"})
    with pytest.raises(ValueError, match="invalid signature"):
        verify_jwt(other.public_pem, token)


def test_peek_kid_returns_none_on_garbage():
    assert peek_kid("not a jwt") is None
    assert peek_kid("a.b") is None
    assert peek_kid("aGVsbG8=.aGVsbG8=.aGVsbG8=") is None  # not JSON


def test_fingerprint_is_stable_across_pem_serialisations():
    kp = generate_keypair()
    # Re-serialise the public key through a load → re-export round-trip.
    pub = serialization.load_pem_public_key(kp.public_pem.encode())
    repem = pub.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    assert fingerprint_of_pem(repem) == kp.fingerprint
