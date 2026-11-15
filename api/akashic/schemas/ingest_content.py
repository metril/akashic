"""Schemas for POST /api/v1/ingest/content (v0.30.0).

The scanner extracts file text (plain decode / Apache Tika) and ships
it back on this channel, keyed by (source_id, path). The API resolves
each path to its entry and merges `content_text` into the Meilisearch
document so full-text search can hit file contents.
"""
import uuid

from pydantic import BaseModel, Field


class ContentItemIn(BaseModel):
    path: str = Field(description="Path of the file, as in the metadata batch.")
    content_text: str = Field(default="", description="Extracted text.")


class ContentBatchIn(BaseModel):
    source_id: uuid.UUID = Field(description="Source the files belong to.")
    scan_id: uuid.UUID = Field(description="Scan that produced the text.")
    items: list[ContentItemIn] = Field(..., max_length=500, description="Up to 500 files.")

    model_config = {"json_schema_extra": {'examples': [{'source_id': '3fa85f64-5717-4562-b3fc-2c963f66afa6', 'scan_id': '7c9e6679-7425-40de-944b-e07fc1f90ae7', 'items': [{'path': '/projects/report.pdf', 'content_text': 'Quarterly report ...'}]}]}}


class ContentBatchResponse(BaseModel):
    items_indexed: int = Field(description="Items matched to an indexed file and written to the search index.")
    scan_id: uuid.UUID = Field(description="Scan the batch belonged to.")
