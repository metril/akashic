import uuid
from datetime import datetime

from pydantic import BaseModel


class SourceCreate(BaseModel):
    name: str
    type: str
    connection_config: dict
    scan_schedule: str | None = None
    exclude_patterns: list[str] | None = None


class SourceUpdate(BaseModel):
    name: str | None = None
    connection_config: dict | None = None
    scan_schedule: str | None = None
    exclude_patterns: list[str] | None = None


class SourceResponse(BaseModel):
    id: uuid.UUID
    name: str
    type: str
    connection_config: dict
    scan_schedule: str | None
    exclude_patterns: list[str] | None
    last_scan_at: datetime | None
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
