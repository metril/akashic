"""GET /api/entries/{id}/content   — streams the file's bytes
GET /api/entries/{id}/preview      — returns a JSON preview of text content

Local sources read directly via FileResponse (Range-aware natively).
Non-local sources spawn `akashic-scanner fetch` and stream stdout.
"""
from __future__ import annotations

import os
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

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
    """Try utf-8 first, then latin-1. Returns (decoded_text, encoding)
    or (None, None) if decoding gives a string we believe to be binary."""
    try:
        return buf.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        try:
            return buf.decode("latin-1"), "latin-1"
        except UnicodeDecodeError:
            return None, None


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

    if entry.size_bytes is not None:
        headers["Content-Length"] = str(entry.size_bytes)

    async def _gen():
        try:
            async for chunk in stream_via_scanner(source, entry.path):
                yield chunk
        except ContentFetchFailed as exc:
            # We've likely already started the response — best we can do
            # is end the stream. The wrapped error is logged.
            import logging
            logging.getLogger(__name__).warning(
                "entry-content fetch %s: %s", entry_id, exc
            )
            return

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
        try:
            async for chunk in stream_via_scanner(source, entry.path):
                if size + len(chunk) >= cap:
                    buf_chunks.append(chunk[: cap - size])
                    size = cap
                    break
                buf_chunks.append(chunk)
                size += len(chunk)
        except ContentFetchFailed as exc:
            raise HTTPException(status_code=502, detail=str(exc))
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
