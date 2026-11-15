import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator


# bcrypt silently truncates inputs over 72 bytes — historically a real
# source of auth-bypass bugs (a user with a 100-byte password could log
# in with any 100-byte password whose first 72 bytes match). Reject at
# create time rather than register a hash that would mask future drift.
_BCRYPT_MAX_BYTES = 72


class UserCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=64, description="Login name, 3-64 characters.", examples=['alice'])
    password: str = Field(..., min_length=8, max_length=128, description="Password, 8-128 characters and at most 72 UTF-8 bytes (bcrypt's limit).")
    email: str | None = Field(default=None, description="Optional contact address.")

    @field_validator("password")
    @classmethod
    def _password_within_bcrypt_limit(cls, v: str) -> str:
        if len(v.encode("utf-8")) > _BCRYPT_MAX_BYTES:
            raise ValueError(
                f"Password exceeds {_BCRYPT_MAX_BYTES}-byte UTF-8 limit "
                "(bcrypt truncates beyond this; refusing to silently weaken)"
            )
        return v

    model_config = {"json_schema_extra": {'examples': [{'username': 'alice', 'password': 'correct horse battery staple', 'email': 'alice@example.com'}]}}


class UserLogin(BaseModel):
    username: str = Field(description="Login name.")
    password: str = Field(description="Account password.")

    model_config = {"json_schema_extra": {'examples': [{'username': 'alice', 'password': 'correct horse battery staple'}]}}


class UserResponse(BaseModel):
    id: uuid.UUID = Field(description="User id.")
    username: str = Field(description="Login name.")
    email: str | None = Field(description="Contact address, if set.")
    role: str = Field(description="Role bundle: `admin` or `viewer`.")
    created_at: datetime = Field(description="When the account was created.")

    model_config = {"from_attributes": True}


class MeResponse(UserResponse):
    scopes: list[str] = Field(description="Effective scopes of the calling credential, sorted.")


class ScopeInfo(BaseModel):
    name: str = Field(description="Scope name, e.g. `files:read`.")
    description: str = Field(description="What the scope permits.")
    allowed_with_source_limit: bool = Field(
        description="Whether a token limited to sources may hold this scope. False scopes are refused (422) at creation.",
    )
    read_only: bool = Field(
        description="Whether the scope only reads (`:read` scopes, `files:content`, `credentials:list`). A read-only token preset is exactly these.",
    )


class ScopeCatalog(BaseModel):
    scopes: list[ScopeInfo] = Field(description="Every scope the API defines.")
    roles: dict[str, list[str]] = Field(description="Scopes granted by each role, keyed by role name.")


class TokenResponse(BaseModel):
    access_token: str = Field(description="Short-lived JWT access token; send it as a bearer token.")
    token_type: str = Field(default="bearer", description="Always `bearer`.")

    model_config = {"json_schema_extra": {'examples': [{'access_token': 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...', 'token_type': 'bearer'}]}}
