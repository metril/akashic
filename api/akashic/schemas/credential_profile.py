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

from fastapi import HTTPException
from pydantic import BaseModel, Field

from akashic.schemas.source import _scrub_config


SUPPORTED_TYPES = {"smb", "nfs", "s3"}


def assert_smb_has_password(creds: dict, *, where: str) -> None:
    """v0.29.5 — SMB credential profiles must carry a non-empty
    password. Empty-password SMB scans are bypass surface: some
    servers respond to ``NTLMInitiator{User: "x", Password: ""}``
    with a fully AUTHENTICATED session against a null-password
    account (not guest), so the v0.29.1 IsGuest/IsAnonymous check
    never fires and a green probe lands against credentials the user
    knew were wrong. Profiles are for reusable, well-formed
    credentials — lab / anonymous-share configs go through inline
    `connection_config.allow_empty_password=true` at the source level
    instead.

    `where` is the human-readable context ("credential_profile" /
    "source") for the 422 detail.
    """
    pw = (creds or {}).get("password")
    if not isinstance(pw, str) or pw == "":
        raise HTTPException(
            status_code=422,
            detail=(
                f"{where}: SMB credential profiles must have a non-empty "
                f"password. For empty-password (lab / anonymous-share) "
                f"scans, configure credentials inline on the source "
                f"with connection_config.allow_empty_password=true."
            ),
        )

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
    credentials: dict = Field(default_factory=dict)
    description: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

    def model_post_init(self, __context) -> None:
        self.credentials = _scrub_config(self.credentials)

    @classmethod
    def from_model(cls, profile) -> "CredentialProfileResponse":
        """Build a response with decrypted-then-scrubbed credentials.

        v0.29.5 — the model column is `credentials_encrypted` now;
        ``model_validate(profile)`` reads the empty `credentials`
        field by default. Routers call this classmethod to ensure
        decryption fires before the scrub.
        """
        from akashic.services.credential_crypto import (
            InvalidToken,
            decrypt_credentials,
        )
        creds: dict = {}
        if profile.credentials_encrypted is not None:
            try:
                creds = decrypt_credentials(bytes(profile.credentials_encrypted))
            except InvalidToken:
                creds = {}
        elif profile.credentials is not None:
            # Legacy unmigrated row.
            creds = dict(profile.credentials)
        return cls(
            id=profile.id,
            name=profile.name,
            type=profile.type,
            credentials=creds,
            description=profile.description,
            created_at=profile.created_at,
            updated_at=profile.updated_at,
        )


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
