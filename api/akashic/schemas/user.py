import uuid
from datetime import datetime

from akashic.schemas.base import ApiModel
from typing import Literal

from pydantic import Field, field_validator, model_validator


# bcrypt silently truncates inputs over 72 bytes — historically a real
# source of auth-bypass bugs (a user with a 100-byte password could log
# in with any 100-byte password whose first 72 bytes match). Reject at
# create time rather than register a hash that would mask future drift.
_BCRYPT_MAX_BYTES = 72


class UserCreate(ApiModel):
    username: str = Field(..., min_length=3, max_length=64, description="Login name, 3-64 characters.", examples=['alice'])
    password: str = Field(..., min_length=8, max_length=128, description="Password, 8-128 characters and at most 72 UTF-8 bytes (bcrypt's limit).")
    email: str | None = Field(default=None, description="Optional contact address.")
    role: Literal["viewer", "admin"] = Field(default="viewer", description="Role of the new account: `viewer` (default) or `admin`.")

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


class UserLogin(ApiModel):
    username: str = Field(description="Login name.")
    password: str = Field(description="Account password.")

    model_config = {"json_schema_extra": {'examples': [{'username': 'alice', 'password': 'correct horse battery staple'}]}}


class UserResponse(ApiModel):
    id: uuid.UUID = Field(description="User id.")
    username: str = Field(description="Login name.")
    email: str | None = Field(description="Contact address, if set.")
    role: Literal["viewer", "admin"] = Field(description="Role bundle: `admin` or `viewer`.")
    role_pinned: bool = Field(description="True when an admin set the role by hand, so IdP group mapping leaves it alone.")
    auth_provider: str = Field(description="Where the account authenticates: `local`, `oidc` or `ldap`.")
    created_at: datetime = Field(description="When the account was created.")
    last_login_at: datetime | None = Field(description="Last successful login, if any.")

    model_config = {"from_attributes": True}


class UserRoleUpdate(ApiModel):
    role: Literal["viewer", "admin"] | None = Field(default=None, description="New role. Setting it pins the user unless `role_pinned` is also given.")
    role_pinned: bool | None = Field(default=None, description="Pin the role (true) or hand it back to IdP group mapping (false).")

    @model_validator(mode="after")
    def _at_least_one(self):
        if self.role is None and self.role_pinned is None:
            raise ValueError("provide role, role_pinned or both")
        return self

    model_config = {"json_schema_extra": {'examples': [{'role': 'admin'}, {'role_pinned': False}]}}


class MeResponse(UserResponse):
    scopes: list[str] = Field(description="Effective scopes of the calling credential, sorted.")


class ScopeInfo(ApiModel):
    name: str = Field(description="Scope name, e.g. `files:read`.")
    description: str = Field(description="What the scope permits.")
    allowed_with_source_limit: bool = Field(
        description="Whether a token limited to sources may hold this scope. False scopes are refused (422) at creation.",
    )
    read_only: bool = Field(
        description="Whether the scope only reads (`:read` scopes, `files:content`, `credentials:list`). A read-only token preset is exactly these.",
    )


class ScopeCatalog(ApiModel):
    scopes: list[ScopeInfo] = Field(description="Every scope the API defines.")
    roles: dict[str, list[str]] = Field(description="Scopes granted by each role, keyed by role name.")


class TokenResponse(ApiModel):
    access_token: str = Field(description="Short-lived JWT access token; send it as a bearer token.")
    token_type: str = Field(default="bearer", description="Always `bearer`.")

    model_config = {"json_schema_extra": {'examples': [{'access_token': 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...', 'token_type': 'bearer'}]}}
