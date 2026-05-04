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


def _profile_credentials(holder: Any | None) -> dict:
    """Read a model row's credential_profile.credentials, or {}."""
    if holder is None:
        return {}
    profile = getattr(holder, "credential_profile", None)
    if profile is None:
        return {}
    return dict(getattr(profile, "credentials", None) or {})


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
