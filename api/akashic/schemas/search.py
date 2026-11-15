import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from akashic.schemas.common import Page


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
    id: uuid.UUID = Field(description="Entry id.")
    source_id: uuid.UUID | None = Field(default=None, description="Source id; null for an orphaned entry whose source was deleted.")
    path: str = Field(description="Full path within the source.")
    filename: str = Field(description="Base name.")
    extension: str | None = Field(default=None, description="Extension without the dot.")
    mime_type: str | None = Field(default=None, description="Detected MIME type.")
    size_bytes: int | None = Field(default=None, description="Size in bytes.")
    fs_modified_at: int | None = Field(default=None, description="Modification time as Unix seconds.")
    content_text: str | None = Field(default=None, description="Matched extracted text, when the index returns it.")
    tags: list[str] = Field(default=[], description="Tags on the file.")
    # v0.20.0 — opaque content_hash + count of *other* entries that share
    # it (within the user's permitted source set). Computed server-side
    # per request rather than indexed in Meili because dup_count would
    # churn whenever any other row's hash mutated. content_hash is plumbed
    # through so the Search row can deep-link to /duplicates?hash=...
    content_hash: str | None = Field(default=None, description="Content hash; null for directories and unhashed files.")
    dup_count: int = Field(default=0, description="Number of other files with the same content hash within the caller's visible sources.")

    model_config = {"json_schema_extra": {'examples': [{'id': '3b1f0c9e-5d0a-4e7c-8a41-9a6b2f5c7d10', 'source_id': '3fa85f64-5717-4562-b3fc-2c963f66afa6', 'path': '/projects/report.pdf', 'filename': 'report.pdf', 'extension': 'pdf', 'mime_type': 'application/pdf', 'size_bytes': 482113, 'fs_modified_at': 1777327200, 'tags': ['invoice'], 'content_hash': '9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08', 'dup_count': 2}]}}


class SearchPage(Page[SearchHit]):
    """One page of search results. `total` is the (estimated) number of matches."""

    query: str = Field(description="The query that produced these results.")
    # v0.6.0 — facet distribution returned by the Meilisearch path when
    # the request asks for it (currently: domain_metadata.* keys for the
    # Library Metadata facet panel on the Search page). Empty when the
    # SQL fallback path served the request, since postgres doesn't
    # produce these distributions cheaply. Shape:
    #   {"domain_metadata.correspondent": {"Bank": 12, "ACME Inc.": 4}}
    # The frontend uses key presence to decide whether to render the
    # panel at all; absent keys mean "no entries with that field in
    # this result set".
    facet_distribution: dict[str, dict[str, int]] | None = Field(
        default=None,
        description=(
            "Facet counts keyed by field (e.g. `domain_metadata.correspondent`); "
            "null on the SQL fallback path."
        ),
    )
