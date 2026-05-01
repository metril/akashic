"""Ed25519 keypair issuance + JWT sign/verify for scanner agents.

The api never stores a private key. `generate_keypair()` returns the
private key once for the admin to deliver to the scanner host; only
the public key is persisted in `scanners.public_key_pem`.

JWT format is the standard JWS compact form
(`header.payload.signature`, all base64url-encoded). We build it
ourselves rather than pulling python-jose into the EdDSA path because
its EdDSA support has been historically uneven across versions —
`cryptography`'s Ed25519 primitive is the source of truth on both
sides of the wire (Go's `crypto/ed25519` on the agent matches it
byte-for-byte).
"""
from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.exceptions import InvalidSignature


@dataclass(frozen=True)
class IssuedKeypair:
    public_pem: str
    private_pem: str
    fingerprint: str


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    pad = (4 - len(s) % 4) % 4
    return base64.urlsafe_b64decode(s + ("=" * pad))


def _fingerprint(pub: Ed25519PublicKey) -> str:
    """sha256 of the DER-encoded SubjectPublicKeyInfo, hex.

    Matches the format Go's `x509.MarshalPKIXPublicKey` produces, so
    operators can compute it themselves from the .pem with `openssl
    pkey -pubin -outform DER | sha256sum` and get the same hash.
    """
    der = pub.public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return hashlib.sha256(der).hexdigest()


def generate_keypair() -> IssuedKeypair:
    priv = Ed25519PrivateKey.generate()
    pub = priv.public_key()
    private_pem = priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    public_pem = pub.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")
    return IssuedKeypair(
        public_pem=public_pem,
        private_pem=private_pem,
        fingerprint=_fingerprint(pub),
    )


def fingerprint_of_pem(public_pem: str) -> str:
    pub = serialization.load_pem_public_key(public_pem.encode("ascii"))
    if not isinstance(pub, Ed25519PublicKey):
        raise ValueError("expected an Ed25519 public key")
    return _fingerprint(pub)


def sign_jwt(private_pem: str, claims: dict, headers: dict | None = None) -> str:
    """Mint a compact JWS. Header always sets alg=EdDSA + typ=JWT."""
    priv = serialization.load_pem_private_key(
        private_pem.encode("ascii"), password=None,
    )
    if not isinstance(priv, Ed25519PrivateKey):
        raise ValueError("expected an Ed25519 private key")
    header = {"alg": "EdDSA", "typ": "JWT"}
    if headers:
        header.update(headers)
    h = _b64url(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    p = _b64url(json.dumps(claims, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{h}.{p}".encode("ascii")
    sig = priv.sign(signing_input)
    return f"{h}.{p}.{_b64url(sig)}"


def verify_jwt(public_pem: str, token: str) -> dict:
    """Verify signature + decode payload. Raises ValueError on tampering /
    malformed input. Does NOT enforce expiry — the caller checks `exp`
    against its own clock with the desired skew tolerance."""
    pub = serialization.load_pem_public_key(public_pem.encode("ascii"))
    if not isinstance(pub, Ed25519PublicKey):
        raise ValueError("expected an Ed25519 public key")
    try:
        h_b64, p_b64, sig_b64 = token.split(".")
    except ValueError as exc:
        raise ValueError("malformed JWT (expected 3 dot-separated segments)") from exc
    signing_input = f"{h_b64}.{p_b64}".encode("ascii")
    try:
        pub.verify(_b64url_decode(sig_b64), signing_input)
    except InvalidSignature as exc:
        raise ValueError("invalid signature") from exc
    try:
        return json.loads(_b64url_decode(p_b64).decode("utf-8"))
    except (ValueError, json.JSONDecodeError) as exc:
        raise ValueError("malformed JWT payload") from exc


def peek_kid(token: str) -> str | None:
    """Pull the `kid` header without verifying — used to look up the
    public key before we know which scanner this token belongs to.
    A bogus kid just leads to a "not found" 401, no security hole."""
    try:
        h_b64, _, _ = token.split(".")
        header = json.loads(_b64url_decode(h_b64).decode("utf-8"))
    except (ValueError, json.JSONDecodeError):
        return None
    kid = header.get("kid")
    return kid if isinstance(kid, str) else None
