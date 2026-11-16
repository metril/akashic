"""Mirror links: config normalisation/validation (linking half lives below)."""
from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from typing import Any, Literal, cast

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.services import meili_indexer

from akashic.models.source import Source

logger = logging.getLogger(__name__)

MIRROR_APP_TYPES = {"immich", "paperless"}
MIRROR_BACKING_TYPES = {"local", "smb", "nfs", "s3"}
_KEYS = {"source_id", "remote_prefix", "local_prefix"}


@dataclass(frozen=True)
class MirrorMapping:
    source_id: uuid.UUID
    remote_prefix: str
    local_prefix: str
    kind: Literal["immich", "paperless"]


class MirrorConfigError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _clean(p: Any, field: str) -> str:
    if not isinstance(p, str):
        raise MirrorConfigError(400, f"mirrors.{field} must be a string")
    return re.sub(r"/+", "/", p.strip()).rstrip("/")


def normalize_mapping(raw: dict, backing_type: str) -> dict:
    if not isinstance(raw, dict):
        raise MirrorConfigError(400, "mirrors entries must be objects")
    unknown = set(raw) - _KEYS
    if unknown:
        raise MirrorConfigError(400, f"mirrors: unknown keys {sorted(unknown)}")
    missing = _KEYS - set(raw)
    if missing:
        raise MirrorConfigError(400, f"mirrors: missing keys {sorted(missing)}")
    sid = raw["source_id"]
    if not isinstance(sid, str):
        raise MirrorConfigError(400, "mirrors.source_id must be a string")
    try:
        sid = str(uuid.UUID(sid))
    except ValueError:
        raise MirrorConfigError(400, "mirrors.source_id must be a UUID") from None
    remote = _clean(raw["remote_prefix"], "remote_prefix")
    if remote and not remote.startswith("/"):
        raise MirrorConfigError(400, "mirrors.remote_prefix must start with '/'")
    local = _clean(raw["local_prefix"], "local_prefix")
    if backing_type == "s3":
        local = local.lstrip("/")
    elif local and not local.startswith("/"):
        raise MirrorConfigError(400, "mirrors.local_prefix must start with '/'")
    return {"source_id": sid, "remote_prefix": remote, "local_prefix": local}


async def validate_mirrors(
    db: AsyncSession, *, app_id: uuid.UUID | None, app_type: str, raw: Any
) -> list[dict]:
    if raw is None or raw == []:
        return []
    if not isinstance(raw, list):
        raise MirrorConfigError(400, "mirrors must be a list")
    if app_type not in MIRROR_APP_TYPES:
        raise MirrorConfigError(400, f"mirrors is not supported for source type '{app_type}'")
    out: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            raise MirrorConfigError(400, "mirrors entries must be objects")
        try:
            backing_id = uuid.UUID(str(item.get("source_id")))
        except ValueError:
            raise MirrorConfigError(400, "mirrors.source_id must be a UUID") from None
        if app_id is not None and backing_id == app_id:
            raise MirrorConfigError(400, "a source cannot mirror itself")
        backing = await db.get(Source, backing_id)
        if backing is None:
            raise MirrorConfigError(404, f"mirrors source {backing_id} not found")
        if backing.type not in MIRROR_BACKING_TYPES:
            raise MirrorConfigError(
                400,
                "mirrors source must be a local, smb, nfs or s3 source, "
                f"not '{backing.type}'",
            )
        m = normalize_mapping(item, backing.type)
        if app_type == "paperless" and m["remote_prefix"]:
            raise MirrorConfigError(400, "paperless mappings must have an empty remote_prefix")
        if m in out:
            raise MirrorConfigError(400, "duplicate mirrors mapping")
        out.append(m)
    return out


def _prefix_pairs(mirrors: Any) -> dict[uuid.UUID, list[tuple[str, str]]]:
    out: dict[uuid.UUID, list[tuple[str, str]]] = {}
    for m in mirrors if isinstance(mirrors, list) else []:
        if not isinstance(m, dict):
            continue
        try:
            bid = uuid.UUID(str(m.get("source_id")))
        except ValueError:
            continue
        out.setdefault(bid, []).append((str(m.get("remote_prefix")), str(m.get("local_prefix"))))
    return out


def changed_backings(before: list[dict], after: list[dict]) -> list[uuid.UUID]:
    """Backing ids whose ordered (remote_prefix, local_prefix) list differs (incl. added/removed)."""
    b, a = _prefix_pairs(before), _prefix_pairs(after)
    return sorted(i for i in b.keys() | a.keys() if b.get(i) != a.get(i))


def prefix_with_sep(local_prefix: str, backing_type: str) -> str:
    if local_prefix:
        return local_prefix + "/"
    return "" if backing_type == "s3" else "/"


_MIRRORS = (
    "CASE WHEN jsonb_typeof(s.connection_config->'mirrors') = 'array' "
    "THEN s.connection_config->'mirrors' ELSE '[]'::jsonb END"
)


async def load_mappings(db: AsyncSession, backing: Source) -> list[MirrorMapping]:
    """Mappings naming this backing source, from every app source (name, then list order)."""
    res = await db.execute(
        text(
            "SELECT s.id, s.type, m FROM sources s "
            f"CROSS JOIN LATERAL jsonb_array_elements({_MIRRORS}) WITH ORDINALITY AS t(m, ord) "
            "WHERE s.type IN ('immich', 'paperless') AND m->>'source_id' = :b "
            "ORDER BY s.name, s.id, t.ord"
        ),
        {"b": str(backing.id)},
    )
    out: list[MirrorMapping] = []
    for app_id, app_type, m in res.all():
        remote, local = m.get("remote_prefix"), m.get("local_prefix")
        if not isinstance(remote, str) or not isinstance(local, str):
            raise MirrorConfigError(409, f"corrupt mirrors mapping on source {app_id}: {m!r}")
        out.append(
            MirrorMapping(app_id, remote, local, cast(Literal["immich", "paperless"], app_type))
        )
    return out


# --- Link pass ---------------------------------------------------------------

ROLE_PRIORITY = {
    "original": 0, "archive": 1, "version": 2,
    "sidecar": 3, "thumbnail": 4, "encoded_video": 5,
}


@dataclass
class LinkPassResult:
    changed_ids: list[uuid.UUID]
    linked: int


_AE = "ae.source_id = :a AND ae.kind = 'file' AND NOT ae.is_deleted"
_BE = "be.source_id = :b AND be.kind = 'file' AND NOT be.is_deleted"
_INS = "INSERT INTO _mirror_desired (backing_id, target_id, role, prio) "
_IMMICH_PATH = (
    "CAST(:pfx AS text) || substr(ae.domain_metadata->>'original_path', "
    "length(CAST(:rp AS text)) + 2)"
)
_IMMICH_OK = (
    "starts_with(ae.domain_metadata->>'original_path', CAST(:rp AS text) || '/')"
)
_UUID_RE = (
    "'^([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
    "(-preview|-thumbnail|-fullsize)?\\.[A-Za-z0-9]+$'"
)
_P = "CAST(:pfx AS text)"


def _rule(role: str, frm: str, where: str) -> str:
    return (
        f"{_INS}SELECT be.id, ae.id, '{role}', CAST(:prio AS int) FROM entries ae {frm} "
        f"WHERE {_AE} AND {_BE} AND {where}"
    )


# Each rule: (sql, base params it uses, {param name: role whose priority it carries}).
_BASE_ALL = ("a", "b", "pfx", "rp")
_BASE_PAPERLESS = ("a", "b", "pfx")

_IMMICH_RULES = [
    (
        _rule("original", "JOIN entries be ON be.path = " + _IMMICH_PATH, _IMMICH_OK),
        _BASE_ALL, {"prio": "original"},
    ),
    (
        _rule("sidecar", "JOIN entries be ON be.path = " + _IMMICH_PATH + " || '.xmp'", _IMMICH_OK),
        _BASE_ALL, {"prio": "sidecar"},
    ),
    # Immich also accepts the extension-less form (IMG_1.xmp); the
    # IMG_1.jpg.xmp form wins when both exist.
    (
        _rule(
            "sidecar",
            "JOIN entries be ON be.path = regexp_replace(" + _IMMICH_PATH
            + ", '\\.[^./]*$', '') || '.xmp'",
            _IMMICH_OK + " AND NOT EXISTS (SELECT 1 FROM entries bx WHERE bx.source_id = :b "
            "AND bx.kind = 'file' AND NOT bx.is_deleted AND bx.path = " + _IMMICH_PATH + " || '.xmp')",
        ),
        _BASE_ALL, {"prio": "sidecar"},
    ),
    # Derivatives are matched by the asset uuid embedded in the file name, so
    # this rule scans the backing source's thumbs/ and encoded-video/ rows
    # (no index on the path prefix); it is the costliest rule of the pass.
    (
        f"{_INS}SELECT be.id, ae.id, "
        f"CASE WHEN starts_with(be.path, {_P} || 'encoded-video/') "
        "THEN 'encoded_video' ELSE 'thumbnail' END, "
        f"CASE WHEN starts_with(be.path, {_P} || 'encoded-video/') "
        "THEN CAST(:evprio AS int) ELSE CAST(:thprio AS int) END "
        "FROM entries ae JOIN entries be ON ae.domain_metadata->>'immich_id' = "
        f"substring(be.name from {_UUID_RE}) "
        f"WHERE {_AE} AND {_BE} AND "
        f"(starts_with(be.path, {_P} || 'thumbs/') "
        f"OR starts_with(be.path, {_P} || 'encoded-video/'))",
        ("a", "b", "pfx"), {"evprio": "encoded_video", "thprio": "thumbnail"},
    ),
]

# Paperless pads ids to 7 digits but never truncates longer ones (lpad would).
_PID = (
    "CASE WHEN length(ae.domain_metadata->>'paperless_id') < 7 "
    "THEN lpad(ae.domain_metadata->>'paperless_id', 7, '0') "
    "ELSE ae.domain_metadata->>'paperless_id' END"
)

_PAPERLESS_RULES = [
    (
        _rule("original",
              f"JOIN entries be ON be.path = {_P} || 'originals/' || (ae.domain_metadata->>'media_filename')",
              "TRUE"),
        _BASE_PAPERLESS, {"prio": "original"},
    ),
    (
        _rule("archive",
              f"JOIN entries be ON be.path = {_P} || 'archive/' || (ae.domain_metadata->>'archive_media_filename')",
              "TRUE"),
        _BASE_PAPERLESS, {"prio": "archive"},
    ),
    (
        _rule("thumbnail",
              "JOIN entries be ON be.path IN ("
              f"{_P} || 'thumbnails/' || {_PID} || '.webp', "
              f"{_P} || 'thumbnails/' || {_PID} || '.png')",
              "TRUE"),
        _BASE_PAPERLESS, {"prio": "thumbnail"},
    ),
    (
        _rule("version",
              "CROSS JOIN LATERAL jsonb_array_elements("
              "COALESCE(ae.domain_metadata->'versions', '[]'::jsonb)) v "
              "JOIN entries be ON be.path IN ("
              f"{_P} || 'originals/' || (v->>'media_filename'), "
              f"{_P} || 'archive/' || (v->>'archive_media_filename'))",
              "TRUE"),
        _BASE_PAPERLESS, {"prio": "version"},
    ),
]

_APPLY = """
WITH pick AS (
  SELECT DISTINCT ON (backing_id) backing_id, target_id, role
  FROM _mirror_desired ORDER BY backing_id, prio, target_id
), cur AS (
  SELECT e.id, p.target_id, p.role, t.viewable_by_read AS viewable, t.source_id AS tsrc
  FROM entries e
  LEFT JOIN pick p ON p.backing_id = e.id
  LEFT JOIN entries t ON t.id = p.target_id
  WHERE e.source_id = :b
    AND (p.backing_id IS NOT NULL OR e.link_role IS NOT NULL OR e.linked_entry_id IS NOT NULL)
)
UPDATE entries e SET linked_entry_id = cur.target_id, link_role = cur.role,
       link_target_viewable = cur.viewable, link_target_source_id = cur.tsrc
FROM cur
WHERE e.id = cur.id AND (e.linked_entry_id IS DISTINCT FROM cur.target_id
   OR e.link_role IS DISTINCT FROM cur.role
   OR e.link_target_viewable IS DISTINCT FROM cur.viewable
   OR e.link_target_source_id IS DISTINCT FROM cur.tsrc)
RETURNING e.id
"""


async def relink_backing(db: AsyncSession, backing_id: uuid.UUID) -> LinkPassResult:
    """Recompute link columns for every entry of one backing source.

    Does not commit; the caller owns the transaction.
    """
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended('mirror_link:' || CAST(:b AS text), 0))"),
        {"b": str(backing_id)},
    )
    backing = await db.get(Source, backing_id, populate_existing=True)
    if backing is None:
        return LinkPassResult([], 0)
    mappings = await load_mappings(db, backing)
    await db.execute(text("DROP TABLE IF EXISTS _mirror_desired"))
    await db.execute(text(
        "CREATE TEMP TABLE _mirror_desired "
        "(backing_id uuid, target_id uuid, role text, prio int) ON COMMIT DROP"
    ))
    for i, m in enumerate(mappings):
        base = {
            "a": m.source_id, "b": backing_id,
            "pfx": prefix_with_sep(m.local_prefix, backing.type), "rp": m.remote_prefix,
        }
        rules = _IMMICH_RULES if m.kind == "immich" else _PAPERLESS_RULES
        for sql, keys, prios in rules:
            params = {k: base[k] for k in keys}
            for name, role in prios.items():
                params[name] = i * 10 + ROLE_PRIORITY[role]
            await db.execute(text(sql), params)
    res = await db.execute(text(_APPLY), {"b": backing_id})
    changed = [r[0] for r in res.all()]
    linked = await db.scalar(
        text("SELECT count(*) FROM entries WHERE source_id = :b AND linked_entry_id IS NOT NULL"),
        {"b": backing_id},
    )
    return LinkPassResult(changed, int(linked or 0))


async def backing_sources_for(db: AsyncSession, source_id: uuid.UUID) -> list[uuid.UUID]:
    """App source: its mirrored backing sources (list order). Backing source: itself if mirrored."""
    row = (
        await db.execute(
            text(
                "SELECT type, connection_config->'mirrors' FROM sources WHERE id = :sid"
            ),
            {"sid": source_id},
        )
    ).first()
    if row is None:
        return []
    if row[0] in MIRROR_APP_TYPES:
        ids: list[uuid.UUID] = []
        for m in row[1] if isinstance(row[1], list) else []:
            try:
                bid = uuid.UUID(m["source_id"])
            except (KeyError, ValueError, TypeError, AttributeError) as exc:
                raise MirrorConfigError(
                    409, f"corrupt mirrors mapping on source {source_id}: {m!r}"
                ) from exc
            if bid not in ids:
                ids.append(bid)
        if not ids:
            return []
        existing = {
            r[0]
            for r in (
                await db.execute(text("SELECT id FROM sources WHERE id = ANY(:ids)"), {"ids": ids})
            ).all()
        }
        return [i for i in ids if i in existing]
    named = await db.scalar(
        text(
            "SELECT EXISTS (SELECT 1 FROM sources s "
            f"CROSS JOIN LATERAL jsonb_array_elements({_MIRRORS}) m "
            "WHERE s.type IN ('immich', 'paperless') AND m->>'source_id' = :sid)"
        ),
        {"sid": str(source_id)},
    )
    return [source_id] if named else []


_MAX_FLUSH_ROUNDS = 1000
_MARK_DIRTY_CHUNK = 10_000


async def relink_and_flush(db: AsyncSession, backing_id: uuid.UUID) -> bool:
    """Relink one backing, commit, and sync the changed entries to the search index.

    Rolls the session back and returns False on an invalid mapping.
    """
    try:
        result = await relink_backing(db, backing_id)
        await db.commit()
    except MirrorConfigError:
        await db.rollback()
        logger.exception("mirror relink failed for backing_id=%s", backing_id)
        return False
    # Chunked so one SADD never carries an unbounded argument list (a first
    # run over a large library changes every backing row); flush() caps work
    # per call, so drain this backing's dirty set after each chunk.
    ids = result.changed_ids
    for i in range(0, len(ids), _MARK_DIRTY_CHUNK):
        await meili_indexer.mark_dirty(backing_id, ids[i:i + _MARK_DIRTY_CHUNK])
        for _ in range(_MAX_FLUSH_ROUNDS):
            if not await meili_indexer.flush(backing_id):
                break
    return True


async def relink_after_scan(db: AsyncSession, source_id: uuid.UUID) -> None:
    try:
        backing_ids = await backing_sources_for(db, source_id)
    except MirrorConfigError:
        logger.exception("mirror relink skipped, corrupt mirrors on source_id=%s", source_id)
        return
    for backing_id in backing_ids:
        try:
            await relink_and_flush(db, backing_id)
        except Exception:  # noqa: BLE001 - one backing must not skip the rest
            await db.rollback()
            logger.exception("mirror relink failed for backing_id=%s", backing_id)


async def logical_view_source_ids(db: AsyncSession) -> set[uuid.UUID]:
    res = await db.execute(
        text(
            "SELECT s.id FROM sources s WHERE s.type IN ('immich', 'paperless') "
            f"AND jsonb_array_length({_MIRRORS}) > 0"
        )
    )
    return {r[0] for r in res.all()}
