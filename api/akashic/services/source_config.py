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


# ── Scan-distribution controls (v0.35.0) ────────────────────────────────
#
# max_parallel_scanners and scan_chunk_size both follow the host → source
# inheritance rule: a NULL on the source means "inherit from the host";
# a NULL on the host means "use the built-in default". The resolvers
# below collapse that two-level lookup to a single concrete int so call
# sites (lease_unit's cap, the lease payload, scan_join) never have to
# reason about NULLs.

# Built-in fallbacks when neither the source nor its host pins a value.
# DEFAULT_MAX_PARALLEL_SCANNERS mirrors the legacy one-scanner-per-scan
# behaviour; DEFAULT_SCAN_CHUNK_SIZE mirrors scanner.go's
# defaultShallowBudget so the API and the scanner agree on the fallback.
DEFAULT_MAX_PARALLEL_SCANNERS = 1
DEFAULT_SCAN_CHUNK_SIZE = 2000


def _inherit_int(source: Any, attr: str, default: int) -> int:
    """Resolve ``source.<attr> ?? source.host.<attr> ?? default``.

    The ``host`` relationship is declared ``lazy="joined"`` on the Source
    model, so it is populated on any normally-fetched row; a plain mock
    without the attribute degrades gracefully to the source/default
    lookup via ``getattr``.
    """
    sv = getattr(source, attr, None)
    if sv is not None:
        return int(sv)
    host = getattr(source, "host", None)
    if host is not None:
        hv = getattr(host, attr, None)
        if hv is not None:
            return int(hv)
    return default


def effective_max_parallel_scanners(source: Any) -> int:
    """Cap on cooperating scanners for a scan of this source —
    ``source ?? host ?? 1``."""
    return _inherit_int(
        source, "max_parallel_scanners", DEFAULT_MAX_PARALLEL_SCANNERS,
    )


def effective_chunk_size(source: Any) -> int:
    """Work-unit entry budget for a scan of this source —
    ``source ?? host ?? 2000``."""
    return _inherit_int(source, "scan_chunk_size", DEFAULT_SCAN_CHUNK_SIZE)


# Accepted ranges for the writable scan-control fields. A cap above 16
# is almost never useful (coordination overhead dominates) and a chunk
# size below 100 would split a scan into thousands of tiny units; the
# upper chunk bound just guards against a fat-fingered value.
_MAX_PARALLEL_SCANNERS_MAX = 16
_SCAN_CHUNK_SIZE_MIN = 100
_SCAN_CHUNK_SIZE_MAX = 1_000_000


def validate_scan_controls(
    *,
    max_parallel_scanners: int | None = None,
    scan_chunk_size: int | None = None,
) -> str | None:
    """Range-check the writable scan-control fields shared by the Host
    and Source create/update paths. Returns an error string for the
    first out-of-range value, or None when everything (including any
    NULL = inherit) is acceptable."""
    if max_parallel_scanners is not None and not (
        1 <= int(max_parallel_scanners) <= _MAX_PARALLEL_SCANNERS_MAX
    ):
        return (
            f"max_parallel_scanners must be between 1 and "
            f"{_MAX_PARALLEL_SCANNERS_MAX}"
        )
    if scan_chunk_size is not None and not (
        _SCAN_CHUNK_SIZE_MIN <= int(scan_chunk_size) <= _SCAN_CHUNK_SIZE_MAX
    ):
        return (
            f"scan_chunk_size must be between {_SCAN_CHUNK_SIZE_MIN} and "
            f"{_SCAN_CHUNK_SIZE_MAX}"
        )
    return None
