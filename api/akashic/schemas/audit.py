"""Schemas for audit events."""
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SearchAsOverride(BaseModel):
    """The override-principal payload used by both the search endpoint and the
    audit log. `type` matches IdentityType from schemas/identity.py."""
    type: Literal["posix_uid", "sid", "nfsv4_principal", "s3_canonical"]
    identifier: str
    groups: list[str] = Field(default_factory=list)


class AuditEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID | None
    event_type: str
    occurred_at: datetime
    source_id: uuid.UUID | None
    request_ip: str
    user_agent: str
    payload: dict


class AuditEventList(BaseModel):
    items: list[AuditEventOut]
    total: int
    page: int
    page_size: int
