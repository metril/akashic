"""Pydantic schemas for the OAuth foundation routes."""
from __future__ import annotations

import uuid
from datetime import datetime

from akashic.schemas.base import ApiModel
from typing import Literal

from pydantic import Field


class OAuthAppConfigUpsert(ApiModel):
    client_id: str = Field(..., min_length=1, description="OAuth client id from the provider's console.")
    # Optional on update — empty string / None means "keep the
    # existing encrypted secret." Required on first create; the
    # router enforces that (review W-I8).
    client_secret: str | None = Field(default=None, description="OAuth client secret. Required when creating; omit or leave empty on update to keep the stored secret. Write-only.")
    redirect_uri: str = Field(..., min_length=1, description="Redirect URI registered with the provider; must point at `GET /api/v1/oauth/callback` of this server.")

    model_config = {"json_schema_extra": {'examples': [{'client_id': '1234567890-abc.apps.googleusercontent.com', 'client_secret': 'GOCSPX-example', 'redirect_uri': 'https://akashic.example.com/api/v1/oauth/callback'}]}}


class OAuthAppConfigSummary(ApiModel):
    """The client_secret is never returned. ``has_secret`` lets the UI
    show "configured" without exposing the value or the encrypted blob."""

    provider: Literal["google", "microsoft", "dropbox"] = Field(description="Provider key: `google`, `microsoft` or `dropbox`.")
    client_id: str = Field(description="Configured client id; empty when not configured.")
    has_secret: bool = Field(description="Whether a client secret is stored.")
    redirect_uri: str = Field(description="Configured redirect URI; empty when not configured.")
    configured_at: datetime = Field(description="When first configured; the epoch when not configured.")
    updated_at: datetime = Field(description="When last changed; the epoch when not configured.")


class OAuthStartRequest(ApiModel):
    provider: str = Field(description="Provider key from `GET /oauth/providers`.", examples=['google'])
    source_id: uuid.UUID | None = Field(default=None, description="Source to attach the credential to; omit for an unattached credential.")
    # "associate" — the resulting credential is attached to source_id (or
    # left unattached for PR-A's smoke test).
    # "test" — same flow, but the credential is deleted at the end of the
    # callback after a successful userinfo round-trip. Used by the
    # Connected Accounts page to verify provider config without
    # leaking a long-lived row.
    mode: str = Field(default="associate", description="`associate` stores the credential; `test` verifies the account and discards it.")

    model_config = {"json_schema_extra": {'examples': [{'provider': 'google', 'source_id': '3fa85f64-5717-4562-b3fc-2c963f66afa6', 'mode': 'associate'}]}}


class OAuthStartResponse(ApiModel):
    authorization_url: str = Field(description="URL to open in a browser window to grant access.")
    state: str = Field(description="The signed state value embedded in the URL.")


class OAuthCredentialSummary(ApiModel):
    id: uuid.UUID = Field(description="Credential id.")
    source_id: uuid.UUID | None = Field(description="Source the credential is attached to; null if unattached.")
    provider: Literal["google", "microsoft", "dropbox"] = Field(description="Provider key.")
    account_email: str | None = Field(description="Account the credential belongs to.")
    account_label: str | None = Field(description="Account display name.")
    scope: str | None = Field(description="Scopes granted by the provider.")
    access_token_expires_at: datetime | None = Field(description="When the cached access token expires.")
    created_at: datetime = Field(description="When the credential was stored.")
    updated_at: datetime = Field(description="When it was last refreshed or changed.")
    # v0.21.0 — name of the Source this credential is attached to (1:1
    # relationship per migration 0029's partial unique index on
    # source_id). Surfaced on the SettingsOAuth row so operators see
    # which source they'd be breaking before clicking Disconnect.
    # Null when source_id is null (unattached / in-flight create flow).
    source_name: str | None = Field(default=None, description="Name of the attached source.")


class OAuthRefreshRequest(ApiModel):
    """Scanner-facing — POST a credential id, get back a fresh access
    token. The credential id comes from the lease payload."""

    credential_id: uuid.UUID


class OAuthRefreshResponse(ApiModel):
    access_token: str = Field(description="Fresh provider access token.")
    expires_at: datetime | None = Field(description="When the access token expires.")
