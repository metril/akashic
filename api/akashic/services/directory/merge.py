"""Merge per-backend directory records into identities (pure, no I/O)."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from akashic.services.directory.records import DirRecord

_BACKEND_ORDER = {"ad": 0, "authentik": 1, "akashic": 2}


def _record_order(r: DirRecord) -> tuple[int, str]:
    return (_BACKEND_ORDER.get(r.backend, 9), r.ref)


def _first(values) -> str | None:
    return next((v for v in values if v), None)


@dataclass
class MergedIdentity:
    id: str
    kind: str
    records: list[DirRecord]
    match_quality: Literal["exact", "possible"] = "exact"
    possible_match_of: list[str] = field(default_factory=list)
    # (backend, ref) -> (synced_at, groups_synced_at) for cached directory rows.
    freshness: dict[tuple[str, str], tuple[datetime, datetime | None]] = field(
        default_factory=dict, repr=False)
    # An akashic user of this identity has at least one file-system binding.
    has_binding: bool = False
    # Expansion stopped at the record cap before reaching a fixed point.
    truncated: bool = False

    @property
    def display_name(self) -> str | None:
        return _first(r.display_name for r in self.records)

    @property
    def username(self) -> str | None:
        return _first(r.username for r in self.records)

    @property
    def email(self) -> str | None:
        return _first(r.email for r in self.records)

    @property
    def sid(self) -> str | None:
        return min((s for r in self.records for s in _sids(r)), default=None)

    @property
    def dn(self) -> str | None:
        return _first(r.dn for r in self.records)

    @property
    def backends(self) -> list[str]:
        return sorted({r.backend for r in self.records})

    @property
    def akashic_user_ids(self) -> list[uuid.UUID]:
        return sorted(uuid.UUID(r.ref) for r in self.records if r.backend == "akashic")

    @property
    def akashic_user_id(self) -> uuid.UUID | None:
        """Lowest akashic user id of the cluster (a cluster may hold several)."""
        return min(self.akashic_user_ids, default=None)

    @property
    def groups_synced_at(self) -> datetime | None:
        """Oldest successful group read among the identity's directory rows."""
        return min(
            (g for _, g in self.freshness.values() if g is not None), default=None)

    @property
    def has_fs_identity(self) -> bool:
        return bool(
            self.has_binding
            or any(
                r.sid or r.uid_number is not None or r.gid_number is not None
                for r in self.records
            )
        )


def _sids(r: DirRecord) -> list[str]:
    return ([r.sid] if r.sid else []) + list(r.alt_sids)


def merge_id(records: list[DirRecord]) -> str:
    sids = [s for r in records for s in _sids(r)]
    if sids:
        return f"sid:{min(sids)}"
    dns = [r.dn.lower() for r in records if r.dn]
    if dns:
        return f"dn:{min(dns)}"
    emails = [r.email.lower() for r in records if r.email]
    if emails:
        return f"email:{min(emails)}"
    first = min(records, key=lambda r: (r.backend, r.ref))
    return f"{first.backend}:{first.ref}"


def merge_identities(records: list[DirRecord]) -> list[MergedIdentity]:
    parent = list(range(len(records)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    seen: dict[tuple, int] = {}
    for i, r in enumerate(records):
        for key in (
            *(("sid", s) for s in _sids(r)),
            ("dn", (r.dn or "").lower() or None),
            ("email", (r.email or "").lower() or None),
        ):
            if key[1] is None:
                continue
            k = (r.kind, *key)
            if k in seen:
                parent[find(i)] = find(seen[k])
            else:
                seen[k] = i

    clusters: dict[int, list[DirRecord]] = {}
    for i, r in enumerate(records):
        clusters.setdefault(find(i), []).append(r)
    idents = [
        MergedIdentity(id=merge_id(recs), kind=recs[0].kind, records=sorted(recs, key=_record_order))
        for recs in clusters.values()
    ]

    # Same username in different clusters without a strong key: possible match.
    by_name: dict[tuple[str, str], set[int]] = {}
    for n, ident in enumerate(idents):
        for r in ident.records:
            if r.username:
                by_name.setdefault((ident.kind, r.username.lower()), set()).add(n)
    for n, ident in enumerate(idents):
        others = {
            m for ns in by_name.values() if n in ns for m in ns if m != n
        }
        if others:
            ident.match_quality = "possible"
            ident.possible_match_of = sorted(idents[m].id for m in others)
    return idents
