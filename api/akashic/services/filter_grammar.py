"""Shared predicate language for Browse, Search, and the blast-radius UI.

The url-encoded form is `base64url(json)` to avoid escaping pain on
path-like values (slashes, colons, dots in SIDs). Browse and Search both
accept `?filters=<base64url>` and the web side mirrors this module's
shape in `web/src/lib/filterGrammar.ts`.

Why one grammar instead of per-page params: by the time the bridge work
in Phase 6 ships, every page renders the same chips and any cell in any
table can be clicked to add a predicate. Giving every consumer a single
parser/emitter keeps that cheap, and the principal/right predicate is
the seam where the OIDC permission-aware-search work lands without
having to widen the existing `permission_filter` enum.
"""
from __future__ import annotations

import base64
import json
from datetime import datetime
from typing import Annotated, Literal, Union
from uuid import UUID

from pydantic import BaseModel, Field, TypeAdapter, ValidationError


# ── Predicate variants ──────────────────────────────────────────────────────
#
# Each kind has its own model so Pydantic discriminates on `kind`. Adding a
# new kind is a non-breaking change for serialized URLs that only contain
# older kinds. Removing a kind is breaking — bump a version string in the
# encoded form if that ever happens.


class ExtensionPred(BaseModel):
    kind: Literal["extension"]
    value: str


class SourcePred(BaseModel):
    kind: Literal["source"]
    value: str  # UUID string; validated via `to_sqlalchemy` when used


class OwnerPred(BaseModel):
    kind: Literal["owner"]
    value: str


class PrincipalPred(BaseModel):
    """A token in the canonical vocabulary from acl_denorm.py:
    `sid:S-1-5-...`, `posix:uid:1001`, `posix:gid:1001`, `nfsv4:NAME`,
    `nfsv4:GROUP:NAME`, `s3:user:CANONICAL_ID`, `*`, `auth`."""

    kind: Literal["principal"]
    value: str
    right: Literal["read", "write", "delete"] = "read"


class MimePred(BaseModel):
    kind: Literal["mime"]
    value: str


class SizePred(BaseModel):
    kind: Literal["size"]
    op: Literal["gte", "lte", "eq"]
    value: int


class MtimePred(BaseModel):
    kind: Literal["mtime"]
    op: Literal["gte", "lte"]
    # ISO-8601 string. Stored as string (not datetime) so the JSON form is
    # human-readable and survives JS Date.toISOString() without conversion.
    value: str


class PathPred(BaseModel):
    """Path-prefix filter — matches the entry at `value` and everything
    beneath it. Emitted by the StorageExplorer's "Filter Search to this
    folder" right-click action so a click on a treemap rectangle becomes
    a Search-scoped query.
    """

    kind: Literal["path"]
    value: str  # path-prefix; "/" matches everything, empty is a no-op


Predicate = Annotated[
    Union[
        ExtensionPred,
        SourcePred,
        OwnerPred,
        PrincipalPred,
        MimePred,
        SizePred,
        MtimePred,
        PathPred,
    ],
    Field(discriminator="kind"),
]


_PredicateList = TypeAdapter(list[Predicate])


# ── Encoding ────────────────────────────────────────────────────────────────


def _b64url_decode(s: str) -> bytes:
    # Add back the stripped padding so standard base64 accepts the input.
    pad = (4 - len(s) % 4) % 4
    return base64.urlsafe_b64decode(s + ("=" * pad))


def _b64url_encode(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode("ascii").rstrip("=")


def parse(encoded: str) -> list[Predicate]:
    """Decode and validate a `?filters=<...>` URL parameter.

    Raises ValueError on malformed input — the api should turn that into
    a 400. The web side has its own deserializer that returns [] silently
    so a stale/edited URL doesn't error the page.
    """
    if not encoded:
        return []
    try:
        raw = _b64url_decode(encoded).decode("utf-8")
        return _PredicateList.validate_json(raw)
    except (ValueError, ValidationError) as exc:
        raise ValueError(f"invalid filter grammar: {exc}") from exc


def serialize(preds: list[Predicate]) -> str:
    """Inverse of parse(). Round-trips through JSON + base64url."""
    if not preds:
        return ""
    payload = [p.model_dump() for p in preds]
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return _b64url_encode(raw)


# ── Sinks ───────────────────────────────────────────────────────────────────


def _meili_escape(value: str) -> str:
    """Quote a value for inclusion in a Meilisearch filter literal."""
    return value.replace('\\', '\\\\').replace('"', '\\"')


def to_meili(preds: list[Predicate]) -> str:
    """Render the predicates as a Meilisearch filter expression (AND'd)."""
    parts: list[str] = []
    for p in preds:
        if isinstance(p, ExtensionPred):
            parts.append(f'extension = "{_meili_escape(p.value)}"')
        elif isinstance(p, SourcePred):
            parts.append(f'source_id = "{_meili_escape(p.value)}"')
        elif isinstance(p, OwnerPred):
            parts.append(f'owner_name = "{_meili_escape(p.value)}"')
        elif isinstance(p, PrincipalPred):
            field = {
                "read": "viewable_by_read",
                "write": "viewable_by_write",
                "delete": "viewable_by_delete",
            }[p.right]
            parts.append(f'{field} = "{_meili_escape(p.value)}"')
        elif isinstance(p, MimePred):
            parts.append(f'mime_type = "{_meili_escape(p.value)}"')
        elif isinstance(p, SizePred):
            cmp_ = {"gte": ">=", "lte": "<=", "eq": "="}[p.op]
            parts.append(f'size_bytes {cmp_} {p.value}')
        elif isinstance(p, MtimePred):
            cmp_ = {"gte": ">=", "lte": "<="}[p.op]
            ts = int(datetime.fromisoformat(p.value).timestamp())
            parts.append(f'fs_modified_at {cmp_} {ts}')
        elif isinstance(p, PathPred):
            # Meilisearch's filter language has no path-prefix operator,
            # and `path` isn't a filterable attribute today. Skip — the
            # search router falls through to the SQL path when grammar
            # predicates can't be expressed in Meili.
            #
            # Adding a `path_prefix` filterable attribute on the Meili
            # index would let us emit `path_prefix = ".../X/"` here; out
            # of scope for this phase.
            continue
    return " AND ".join(parts)


def has_meili_inexpressible_predicate(preds: list[Predicate]) -> bool:
    """True when the predicate list contains kinds that to_meili() can't
    render. The Search router uses this to pick the SQL path when a
    grammar predicate (currently: `path`) can't ride through Meili."""
    return any(isinstance(p, PathPred) for p in preds)


def to_sqlalchemy(preds: list[Predicate]) -> list:
    """Render the SQL-expressible predicates as a list of SQLAlchemy
    clauses to AND together via `select(...).where(*clauses)`.

    Predicates whose backing column doesn't exist yet (the principal/right
    pair, until Phase 4 lands the `viewable_by_*` columns) are silently
    skipped. The caller decides whether the missing predicate should
    push the whole query toward Meilisearch instead — that policy lives
    in the Search router, not the grammar.
    """
    from akashic.models.entry import Entry

    clauses: list = []
    for p in preds:
        if isinstance(p, ExtensionPred):
            clauses.append(Entry.extension == p.value)
        elif isinstance(p, SourcePred):
            clauses.append(Entry.source_id == UUID(p.value))
        elif isinstance(p, OwnerPred):
            clauses.append(Entry.owner_name == p.value)
        elif isinstance(p, PrincipalPred):
            # Phase 4 fills in the viewable_by_* columns + a `viewable_clause`
            # helper. Until then this predicate is the search router's
            # signal to take the Meilisearch path.
            continue
        elif isinstance(p, MimePred):
            clauses.append(Entry.mime_type == p.value)
        elif isinstance(p, SizePred):
            col = Entry.size_bytes
            if p.op == "gte":
                clauses.append(col >= p.value)
            elif p.op == "lte":
                clauses.append(col <= p.value)
            else:  # eq
                clauses.append(col == p.value)
        elif isinstance(p, MtimePred):
            col = Entry.fs_modified_at
            dt = datetime.fromisoformat(p.value)
            if p.op == "gte":
                clauses.append(col >= dt)
            else:  # lte
                clauses.append(col <= dt)
        elif isinstance(p, PathPred):
            v = p.value or ""
            if not v:
                continue  # empty value is a no-op
            if v == "/":
                continue  # root-prefix matches everything → no-op
            v = v.rstrip("/") or "/"
            # Match the entry itself OR any descendant.
            clauses.append((Entry.path == v) | (Entry.path.startswith(v + "/")))
    return clauses


def has_principal_predicate(preds: list[Predicate]) -> bool:
    """Search router uses this to decide between SQL fallback and Meili.

    Until Phase 4 lands, any principal predicate forces the Meilisearch
    path because the SQL columns aren't there yet."""
    return any(isinstance(p, PrincipalPred) for p in preds)
