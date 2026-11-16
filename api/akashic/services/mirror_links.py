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
        raise MirrorConfigError(400, f"mirror_of.{field} must be a string")
    return re.sub(r"/+", "/", p.strip()).rstrip("/")


def normalize_mapping(raw: dict, backing_type: str) -> dict:
    if not isinstance(raw, dict):
        raise MirrorConfigError(400, "mirror_of entries must be objects")
    unknown = set(raw) - _KEYS
    if unknown:
        raise MirrorConfigError(400, f"mirror_of: unknown keys {sorted(unknown)}")
    missing = _KEYS - set(raw)
    if missing:
        raise MirrorConfigError(400, f"mirror_of: missing keys {sorted(missing)}")
    sid = raw["source_id"]
    if not isinstance(sid, str):
        raise MirrorConfigError(400, "mirror_of.source_id must be a string")
    try:
        sid = str(uuid.UUID(sid))
    except ValueError:
        raise MirrorConfigError(400, "mirror_of.source_id must be a UUID") from None
    remote = _clean(raw["remote_prefix"], "remote_prefix")
    if remote and not remote.startswith("/"):
        raise MirrorConfigError(400, "mirror_of.remote_prefix must start with '/'")
    local = _clean(raw["local_prefix"], "local_prefix")
    if backing_type == "s3":
        local = local.lstrip("/")
    elif local and not local.startswith("/"):
        raise MirrorConfigError(400, "mirror_of.local_prefix must start with '/'")
    return {"source_id": sid, "remote_prefix": remote, "local_prefix": local}


async def validate_mirror_of(
    db: AsyncSession, *, backing_id: uuid.UUID | None, backing_type: str, raw: Any
) -> list[dict]:
    if raw is None or raw == []:
        return []
    if not isinstance(raw, list):
        raise MirrorConfigError(400, "mirror_of must be a list")
    if backing_type not in MIRROR_BACKING_TYPES:
        raise MirrorConfigError(
            400, f"mirror_of is not supported for source type '{backing_type}'"
        )
    out: list[dict] = []
    for item in raw:
        m = normalize_mapping(item, backing_type)
        target_id = uuid.UUID(m["source_id"])
        if backing_id is not None and target_id == backing_id:
            raise MirrorConfigError(400, "a source cannot mirror itself")
        target = await db.get(Source, target_id)
        if target is None:
            raise MirrorConfigError(404, f"mirror_of target {target_id} not found")
        if target.type not in MIRROR_APP_TYPES:
            raise MirrorConfigError(
                400, f"mirror_of target must be an immich or paperless source, not '{target.type}'"
            )
        if target.type == "paperless" and m["remote_prefix"]:
            raise MirrorConfigError(400, "paperless mappings must have an empty remote_prefix")
        if m in out:
            raise MirrorConfigError(400, "duplicate mirror_of mapping")
        out.append(m)
    return out


def prefix_with_sep(local_prefix: str, backing_type: str) -> str:
    if local_prefix:
        return local_prefix + "/"
    return "" if backing_type == "s3" else "/"


async def load_mappings(db: AsyncSession, backing: Source) -> list[MirrorMapping]:
    out: list[MirrorMapping] = []
    for m in (backing.connection_config or {}).get("mirror_of") or []:
        try:
            target_id = uuid.UUID(m["source_id"])
            remote, local = m["remote_prefix"], m["local_prefix"]
        except (KeyError, ValueError, TypeError) as exc:
            raise MirrorConfigError(409, f"corrupt mirror_of mapping: {exc!r}") from exc
        target = await db.get(Source, target_id)
        if target is None or target.type not in MIRROR_APP_TYPES:
            raise MirrorConfigError(409, f"mirror_of target {target_id} is missing or invalid")
        out.append(
            MirrorMapping(
                target_id, remote, local, cast(Literal["immich", "paperless"], target.type)
            )
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
    res = await db.execute(
        text(
            "SELECT id FROM sources WHERE "
            "jsonb_path_exists(connection_config, '$.mirror_of[*] ? (@.source_id == $id)', "
            "jsonb_build_object('id', CAST(:sid AS text))) "
            "OR (id = CAST(:sid AS uuid) AND jsonb_array_length(COALESCE(connection_config->'mirror_of', '[]'::jsonb)) > 0)"
        ),
        {"sid": str(source_id)},
    )
    return [r[0] for r in res.all()]


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
    for backing_id in await backing_sources_for(db, source_id):
        try:
            await relink_and_flush(db, backing_id)
        except Exception:  # noqa: BLE001 - one backing must not skip the rest
            await db.rollback()
            logger.exception("mirror relink failed for backing_id=%s", backing_id)


async def logical_view_source_ids(db: AsyncSession) -> set[uuid.UUID]:
    res = await db.execute(
        text(
            "SELECT DISTINCT CAST(m->>'source_id' AS uuid) FROM sources, "
            "jsonb_array_elements(COALESCE(connection_config->'mirror_of', '[]'::jsonb)) m"
        )
    )
    return {r[0] for r in res.all()}
