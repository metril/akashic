"""Mirror links: config normalisation/validation (linking half lives below)."""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Any, Literal, cast

from sqlalchemy.ext.asyncio import AsyncSession

from akashic.models.source import Source

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
