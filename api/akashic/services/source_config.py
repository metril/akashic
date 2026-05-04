"""Merging a Source's share-only `connection_config` with its
parent Host's `connection_config` for downstream consumers
(source-tester, scan dispatcher) that expect the legacy combined dict.

Rationale: Host owns the connection-level fields (hostname, port,
credentials, key material). Source owns the share-level fields
(`path` / `share` / `export_path` / `bucket` / `prefix`). The scanner
binary still receives one dict per scan; we merge here at the API
boundary rather than refactor the scanner protocol.
"""
from __future__ import annotations

from typing import Any


def merge_host_and_source(
    host: Any | None,
    source: Any,
) -> dict:
    """Return a new dict containing host fields layered with source fields.

    Source keys win on conflict (so a per-share override of e.g.
    `port` is honored). Host=None (local sources) returns just the
    source's connection_config.
    """
    merged: dict = {}
    if host is not None:
        merged.update(dict(getattr(host, "connection_config", None) or {}))
    merged.update(dict(getattr(source, "connection_config", None) or {}))
    return merged
