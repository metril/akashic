import uuid
from datetime import datetime

from pydantic import BaseModel


class SearchHit(BaseModel):
    """A search result from Meilisearch — uses only fields that are indexed.

    `source_id` is nullable because entries can become orphaned when their
    source is deleted with ``purge_entries=False`` (default): the entry row
    keeps existing in postgres with ``source_id=NULL``, and the Meili doc
    is updated to match. Such orphans were causing 500s in the search
    handler before v0.5.10 because the schema rejected null. The api
    filters them out of the response by default — see
    routers/search.py — so they stay invisible until reattached, but the
    schema stays lenient so a single straggler can't crash the whole
    response.
    """
    id: uuid.UUID
    source_id: uuid.UUID | None = None
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
