import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class ApiTokenCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100, description="A label to recognise the token by.")
    scopes: list[str] = Field(
        min_length=1,
        description="Scopes the token may use. Must be within your own role's scopes.",
    )
    source_ids: list[uuid.UUID] | None = Field(
        default=None,
        min_length=1,
        description="Limit the token to these sources. Omit for every source.",
    )
    expires_at: datetime | None = Field(
        default=None, description="When the token stops working. Omit for no expiry.",
    )


class ApiTokenUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100, description="New label.")
    scopes: list[str] | None = Field(
        default=None, min_length=1,
        description="A subset of the token's current scopes. Scopes can only be narrowed.",
    )


class ApiTokenResponse(BaseModel):
    id: uuid.UUID = Field(description="Token id.")
    name: str = Field(description="Label.")
    token_prefix: str = Field(description="First characters of the token, to recognise it by.")
    scopes: list[str] = Field(description="Scopes granted by the token (a ceiling: the owner's role also applies).")
    source_ids: list[uuid.UUID] | None = Field(description="Sources the token is limited to; null means every source.")
    created_at: datetime = Field(description="When the token was created.")
    expires_at: datetime | None = Field(description="When the token expires; null means never.")
    last_used_at: datetime | None = Field(description="Last use (refreshed at most once a minute).")
    last_used_ip: str | None = Field(description="Client address of the last use.")
    revoked_at: datetime | None = Field(description="When the token was revoked; null while active.")

    model_config = {"from_attributes": True}


class ApiTokenCreated(ApiTokenResponse):
    token: str = Field(description="The token itself. Shown once and never retrievable again.")


class AdminApiTokenResponse(ApiTokenResponse):
    user_id: uuid.UUID = Field(description="Owner's user id.")
    username: str = Field(description="Owner's username.")
