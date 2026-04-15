import uuid
from datetime import datetime

from pydantic import BaseModel


class FileEntryIn(BaseModel):
    path: str
    filename: str
    extension: str | None = None
    size_bytes: int = 0
    mime_type: str | None = None
    content_hash: str | None = None
    permissions: str | None = None
    owner: str | None = None
    file_group: str | None = None
    fs_created_at: datetime | None = None
    fs_modified_at: datetime | None = None
    fs_accessed_at: datetime | None = None
    is_dir: bool = False


class FileResponse(BaseModel):
    id: uuid.UUID
    source_id: uuid.UUID
    path: str
    filename: str
    extension: str | None
    size_bytes: int | None
    mime_type: str | None
    content_hash: str | None
    fs_modified_at: datetime | None
    first_seen_at: datetime
    last_seen_at: datetime
    is_deleted: bool

    model_config = {"from_attributes": True}
