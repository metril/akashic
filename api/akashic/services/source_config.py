"""Merging a Source's share-only `connection_config` with its
parent Host's `connection_config` for downstream consumers
(source-tester, scan dispatcher) that expect the legacy combined dict.

Rationale: Host owns the connection-level fields (hostname, port,
credentials, key material). Source owns the share-level fields
(`path` / `share` / `export_path` / `bucket` / `prefix`). The scanner
binary still receives one dict per scan; we merge here at the API
boundary rather than refactor the scanner protocol.

v0.5.9 — credential-profile layering. Host and Source can each
reference a CredentialProfile whose `credentials` dict is layered
under the inline values. Resolution order, last write wins:

    host_profile.credentials
    < host.connection_config
    < source_profile.credentials
    < source.connection_config

Profiles are loaded via the eager relationship (`lazy="joined"` on
both Host and Source models), so callers don't need to await the
db here — the relationship is either populated on the row or None.
"""
from __future__ import annotations

from typing import Any


def credentials_from_profile(profile: Any | None) -> dict:
    """Read a CredentialProfile row's credentials dict, or {}.

    v0.29.5 — prefers the encrypted ``credentials_encrypted`` column,
    falls back to the legacy plaintext ``credentials`` JSONB column
    when the encrypted one is unset (pre-migration row, or a write
    path that hasn't been updated). Best-effort: a decryption failure
    (wrong SECRET_KEY, tampered ciphertext) returns ``{}`` and logs at
    WARNING — the merged config will then be missing the credentials
    and downstream code surfaces a clearer auth failure than "valid
    plaintext that decrypted to nonsense".

    Takes the profile row directly so callers that hold a profile by
    id (e.g. the SMB-password validator) decrypt it the same way the
    scan-time merge does — `_profile_credentials` is the wrapper for
    callers that hold the owning Host/Source row instead.
    """
    if profile is None:
        return {}
    encrypted = getattr(profile, "credentials_encrypted", None)
    if encrypted is not None:
        from akashic.services.credential_crypto import (
            InvalidToken,
            decrypt_credentials,
        )
        try:
            return dict(decrypt_credentials(bytes(encrypted)))
        except InvalidToken as exc:
            import logging
            logging.getLogger(__name__).warning(
                "credential_crypto: decrypt failed for profile=%s: %s "
                "(SECRET_KEY rotated or ciphertext tampered)",
                getattr(profile, "id", "?"), exc,
            )
            return {}
    return dict(getattr(profile, "credentials", None) or {})


def _profile_credentials(holder: Any | None) -> dict:
    """Credentials of the CredentialProfile attached to a Host/Source
    row, or {}. The `credential_profile` relationship must be loaded."""
    if holder is None:
        return {}
    return credentials_from_profile(getattr(holder, "credential_profile", None))


def merge_host_and_source(
    host: Any | None,
    source: Any,
) -> dict:
    """Return a new dict containing the layered connection config.

    Order (last write wins):
        host_profile < host_inline < source_profile < source_inline.
    Host=None (local sources) skips both host layers.

    The `credential_profile` relationship must be loaded on the row
    for it to contribute. Both models declare it as lazy="joined"
    so the standard fetch paths populate it automatically. Tests
    that pass plain mocks without the attribute simply skip the
    profile layer (graceful degradation via getattr).
    """
    merged: dict = {}
    if host is not None:
        merged.update(_profile_credentials(host))
        merged.update(dict(getattr(host, "connection_config", None) or {}))
    merged.update(_profile_credentials(source))
    merged.update(dict(getattr(source, "connection_config", None) or {}))
    return merged
