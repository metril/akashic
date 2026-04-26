import uuid
from datetime import datetime

from pydantic import BaseModel

from akashic.schemas.entry import EntryIn


class ScanBatchIn(BaseModel):
    source_id: uuid.UUID
    scan_id: uuid.UUID
    entries: list[EntryIn]
    is_final: bool = False


class ScanBatchResponse(BaseModel):
    files_processed: int
    scan_id: uuid.UUID


class ScanResponse(BaseModel):
    id: uuid.UUID
    source_id: uuid.UUID
    scan_type: str
    status: str
    files_found: int
    files_new: int
    files_changed: int
    files_deleted: int
    started_at: datetime | None
    completed_at: datetime | None

    model_config = {"from_attributes": True}
