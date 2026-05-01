"""GET /api/entries/{id}/content   — streams the file's bytes
GET /api/entries/{id}/preview      — returns a JSON preview of text content

Local sources read directly via FileResponse (Range-aware natively).
Non-local sources spawn `akashic-scanner fetch` and stream stdout.
"""
from __future__ import annotations

import logging
import os
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

from akashic.auth.dependencies import check_source_access, get_current_user
from akashic.database import get_db
from akashic.models.entry import Entry
from akashic.models.source import Source
from akashic.models.user import User
from akashic.services.entry_content import (
    ContentFetchFailed,
    PathTraversal,
    PREVIEW_MAX_BYTES,
    open_local,
    read_local_preview,
    stream_via_scanner,
    validate_remote_path,
)

router = APIRouter(prefix="/api/entries", tags=["entries"])


class PreviewResponse(BaseModel):
    encoding: Optional[str] = None
    text: Optional[str] = None
    truncated: bool = False
    byte_size_total: int = 0
    binary: bool = False


# ── helpers ────────────────────────────────────────────────────────────────


async def _load_entry_and_source(
    entry_id: uuid.UUID, db: AsyncSession, user: User
) -> tuple[Entry, Source]:
    entry = await db.get(Entry, entry_id)
    if entry is None or entry.kind != "file" or entry.is_deleted:
        raise HTTPException(status_code=404, detail="entry not found or not a file")
    await check_source_access(entry.source_id, user, db, "read")
    # Phase-5 per-user ACL trim — content/preview must respect the same
    # filter as Browse and the entry detail. 404 (not 403) so an
    # unviewable entry's existence isn't leaked through the status code.
    from akashic.services.access_query import user_can_view
    if not await user_can_view(entry, user, db):
        raise HTTPException(status_code=404, detail="entry not found or not a file")
    source = await db.get(Source, entry.source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="source not found")
    return entry, source


def _content_disposition(filename: str, as_attachment: bool) -> str:
    disp = "attachment" if as_attachment else "inline"
    safe = filename.replace('"', "_")
    return f'{disp}; filename="{safe}"'


def _looks_binary(buf: bytes) -> bool:
    """Heuristic: any NUL byte in the first 4KB → binary."""
    return b"\x00" in buf[:4096]


def _decode_text(buf: bytes) -> tuple[str | None, str | None]:
    """Try utf-8 first, then latin-1. latin-1 is bijective over bytes so
    it never raises — it's the universal fallback. The 'binary' flag in
    PreviewResponse comes from the NUL-byte heuristic upstream, not from
    decode failures here. Caveat: a true UTF-16 file with no NUL bytes
    in the first 4KB would decode as garbled latin-1; users would see
    mojibake rather than 'binary'. Acceptable for v1."""
    try:
        return buf.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return buf.decode("latin-1"), "latin-1"


# ── /content ───────────────────────────────────────────────────────────────


@router.get("/{entry_id}/content")
async def get_content(
    entry_id: uuid.UUID,
    as_attachment: bool = Query(default=False, alias="attachment"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    entry, source = await _load_entry_and_source(entry_id, db, user)
    media_type = entry.mime_type or "application/octet-stream"
    headers = {"Content-Disposition": _content_disposition(entry.name, as_attachment)}

    if source.type == "local":
        cfg = source.connection_config or {}
        root = cfg.get("path") or "/"
        try:
            canonical, total = open_local(root, entry.path)
        except PathTraversal as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="file not on disk")
        except PermissionError:
            raise HTTPException(status_code=403, detail="not readable by api")
        headers["Content-Length"] = str(total)
        return FileResponse(canonical, media_type=media_type, headers=headers)

    # Non-local: stream via scanner subprocess.
    try:
        validate_remote_path(entry.path)
    except PathTraversal as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # Deliberately omit Content-Length: entry.size_bytes was captured at
    # scan time and may be stale. Sending a wrong header risks HTTP/1.1
    # framing bugs (clients hang waiting for missing bytes, or stop
    # reading early). Chunked transfer-encoding is correctly framed
    # without it.

    async def _gen():
        try:
            async for chunk in stream_via_scanner(source, entry.path):
                yield chunk
        except ContentFetchFailed as exc:
            # Once the StreamingResponse has emitted any bytes the HTTP
            # status code is locked in (200), so we can't retroactively
            # signal a 502. Logging at WARN so failures are operator-
            # visible. The truncation is observable via the ContentFetchFailed
            # raise — re-raise so FastAPI/Starlette terminates the
            # response abnormally rather than cleanly returning, which
            # at least tells well-behaved clients the body was incomplete.
            logger.warning("entry-content fetch %s: %s", entry_id, exc)
            raise

    return StreamingResponse(_gen(), media_type=media_type, headers=headers)


# ── /preview ───────────────────────────────────────────────────────────────


@router.get("/{entry_id}/preview", response_model=PreviewResponse)
async def get_preview(
    entry_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    entry, source = await _load_entry_and_source(entry_id, db, user)

    # Read up to PREVIEW_MAX_BYTES + 1 so we know if truncation happened.
    cap = PREVIEW_MAX_BYTES
    total = entry.size_bytes or 0

    if source.type == "local":
        cfg = source.connection_config or {}
        root = cfg.get("path") or "/"
        try:
            canonical, real_size = open_local(root, entry.path)
        except PathTraversal as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="file not on disk")
        except PermissionError:
            raise HTTPException(status_code=403, detail="not readable by api")
        total = real_size
        buf = read_local_preview(canonical, max_bytes=cap)
    else:
        try:
            validate_remote_path(entry.path)
        except PathTraversal as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        buf_chunks: list[bytes] = []
        size = 0
        # Hold a reference to the generator so we can aclose() it
        # synchronously after early-break — guarantees the subprocess is
        # killed and the concurrency-semaphore slot released without
        # waiting for GC to finalize the suspended generator.
        gen = stream_via_scanner(source, entry.path)
        try:
            async for chunk in gen:
                if size + len(chunk) >= cap:
                    buf_chunks.append(chunk[: cap - size])
                    size = cap
                    break
                buf_chunks.append(chunk)
                size += len(chunk)
        except ContentFetchFailed as exc:
            raise HTTPException(status_code=502, detail=str(exc))
        finally:
            await gen.aclose()
        buf = b"".join(buf_chunks)

    truncated = total > len(buf)

    if _looks_binary(buf):
        return PreviewResponse(
            encoding=None, text=None, truncated=truncated,
            byte_size_total=total, binary=True,
        )

    text, encoding = _decode_text(buf)
    if text is None:
        return PreviewResponse(
            encoding=None, text=None, truncated=truncated,
            byte_size_total=total, binary=True,
        )
    return PreviewResponse(
        encoding=encoding, text=text, truncated=truncated,
        byte_size_total=total, binary=False,
    )
