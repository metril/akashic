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
    model_config = ConfigDict(from_attributes=True, json_schema_extra={'examples': [{'id': '3b1f0c9e-5d0a-4e7c-8a41-9a6b2f5c7d10', 'user_id': '5a2d9f10-77c3-4b8e-9d21-4c0a8e1b6f33', 'event_type': 'access_lookup', 'occurred_at': '2026-04-27T22:00:00Z', 'source_id': '0d4a9a0e-2b1c-4c5e-8f6d-1a2b3c4d5e6f', 'request_ip': '203.0.113.7', 'user_agent': 'curl/8.5.0', 'payload': {'principal': 'posix:uid:1000', 'right': 'read'}}]})

    id: uuid.UUID = Field(description="Event id.")
    user_id: uuid.UUID | None = Field(description="User who performed the action; null when the event is not attributable to a user.")
    event_type: str = Field(description="Event type, e.g. `access_lookup`, `api_token_created`, `host_created`.")
    occurred_at: datetime = Field(description="When the event happened.")
    source_id: uuid.UUID | None = Field(description="Source the event relates to, if any.")
    request_ip: str = Field(description="Client address of the request; empty for system events.")
    user_agent: str = Field(description="Client user agent; empty when not available.")
    payload: dict = Field(description="Event-specific details; the shape depends on `event_type`. Free-form: the shape depends on the event type.", json_schema_extra={"x-free-form": True})
