import uuid
from datetime import datetime

from pydantic import BaseModel


class SearchHit(BaseModel):
    """A search result from Meilisearch — uses only fields that are indexed."""
    id: uuid.UUID
    source_id: uuid.UUID
    path: str
    filename: str
    extension: str | None = None
    mime_type: str | None = None
    size_bytes: int | None = None
    fs_modified_at: int | None = None
    content_text: str | None = None
    tags: list[str] = []


class SearchResults(BaseModel):
    results: list[SearchHit]
    total: int
    query: str
