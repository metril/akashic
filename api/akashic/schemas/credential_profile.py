"""Pydantic schemas for /api/credential-profiles.

Reuses the same secret-scrubbing rules from schemas/source.py
(_scrub_config / _SECRET_KEYS) so passwords and keys are masked as
``"***"`` on every read. The "sentinel update" pattern lets clients
PATCH a profile without re-sending the masked secret: any value that
arrives as ``"***"`` (or ``"********"``) is treated as "leave the
existing value untouched."
"""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from akashic.schemas.source import _scrub_config


SUPPORTED_TYPES = {"smb", "nfs", "s3"}

# Values the client may send to mean "don't replace this field". The
# api scrubs to "***"; an older client might still send "********".
_KEEP_SENTINELS = {"***", "********"}


def merge_sentinel_credentials(existing: dict, incoming: dict) -> dict:
    """Apply a partial credentials update.

    Any incoming key whose value is one of the keep-sentinels is
    dropped from the merge so the existing stored value persists.
    Other keys are written/overwritten as-is. Keys not present in
    `incoming` keep their existing value (partial update).
    """
    out = dict(existing or {})
    for k, v in (incoming or {}).items():
        if isinstance(v, str) and v in _KEEP_SENTINELS:
            continue
        out[k] = v
    return out


class CredentialProfileCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    type: str  # one of SUPPORTED_TYPES — validated at the endpoint
    credentials: dict = Field(default_factory=dict)
    description: str | None = None


class CredentialProfileUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    credentials: dict | None = None


class CredentialProfileResponse(BaseModel):
    id: uuid.UUID
    name: str
    type: str
    credentials: dict
    description: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

    def model_post_init(self, __context) -> None:
        self.credentials = _scrub_config(self.credentials)


class CredentialProfileSummary(BaseModel):
    """Lean shape for the picker dropdown — name + type only.

    No credentials surface here so a list endpoint can be served
    without a per-row scrub pass.
    """

    id: uuid.UUID
    name: str
    type: str
    description: str | None = None

    model_config = {"from_attributes": True}
