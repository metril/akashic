"""Schemas for POST /api/ingest/content (v0.30.0).

The scanner extracts file text (plain decode / Apache Tika) and ships
it back on this channel, keyed by (source_id, path). The API resolves
each path to its entry and merges `content_text` into the Meilisearch
document so full-text search can hit file contents.
"""
import uuid

from pydantic import BaseModel, Field


class ContentItemIn(BaseModel):
    path: str
    content_text: str = ""


class ContentBatchIn(BaseModel):
    source_id: uuid.UUID
    scan_id: uuid.UUID
    items: list[ContentItemIn] = Field(..., max_length=500)


class ContentBatchResponse(BaseModel):
    items_indexed: int
    scan_id: uuid.UUID
