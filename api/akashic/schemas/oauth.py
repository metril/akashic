"""Pydantic schemas for the OAuth foundation routes."""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class OAuthAppConfigUpsert(BaseModel):
    client_id: str = Field(..., min_length=1)
    # Optional on update — empty string / None means "keep the
    # existing encrypted secret." Required on first create; the
    # router enforces that (review W-I8).
    client_secret: str | None = None
    redirect_uri: str = Field(..., min_length=1)


class OAuthAppConfigSummary(BaseModel):
    """The client_secret is never returned. ``has_secret`` lets the UI
    show "configured" without exposing the value or the encrypted blob."""

    provider: str
    client_id: str
    has_secret: bool
    redirect_uri: str
    configured_at: datetime
    updated_at: datetime


class OAuthStartRequest(BaseModel):
    provider: str
    source_id: uuid.UUID | None = None
    # "associate" — the resulting credential is attached to source_id (or
    # left unattached for PR-A's smoke test).
    # "test" — same flow, but the credential is deleted at the end of the
    # callback after a successful userinfo round-trip. Used by the
    # Connected Accounts page to verify provider config without
    # leaking a long-lived row.
    mode: str = "associate"


class OAuthStartResponse(BaseModel):
    authorization_url: str
    state: str  # echoed back for tests; the JWT is also encoded into the URL


class OAuthCredentialSummary(BaseModel):
    id: uuid.UUID
    source_id: uuid.UUID | None
    provider: str
    account_email: str | None
    account_label: str | None
    scope: str | None
    access_token_expires_at: datetime | None
    created_at: datetime
    updated_at: datetime
    # v0.21.0 — name of the Source this credential is attached to (1:1
    # relationship per migration 0029's partial unique index on
    # source_id). Surfaced on the SettingsOAuth row so operators see
    # which source they'd be breaking before clicking Disconnect.
    # Null when source_id is null (unattached / in-flight create flow).
    source_name: str | None = None


class OAuthRefreshRequest(BaseModel):
    """Scanner-facing — POST a credential id, get back a fresh access
    token. The credential id comes from the lease payload."""

    credential_id: uuid.UUID


class OAuthRefreshResponse(BaseModel):
    access_token: str
    expires_at: datetime | None
