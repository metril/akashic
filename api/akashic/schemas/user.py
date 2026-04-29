import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator


# bcrypt silently truncates inputs over 72 bytes — historically a real
# source of auth-bypass bugs (a user with a 100-byte password could log
# in with any 100-byte password whose first 72 bytes match). Reject at
# create time rather than register a hash that would mask future drift.
_BCRYPT_MAX_BYTES = 72


class UserCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=64)
    password: str = Field(..., min_length=8, max_length=128)
    email: str | None = None

    @field_validator("password")
    @classmethod
    def _password_within_bcrypt_limit(cls, v: str) -> str:
        if len(v.encode("utf-8")) > _BCRYPT_MAX_BYTES:
            raise ValueError(
                f"Password exceeds {_BCRYPT_MAX_BYTES}-byte UTF-8 limit "
                "(bcrypt truncates beyond this; refusing to silently weaken)"
            )
        return v


class UserLogin(BaseModel):
    username: str
    password: str


class UserResponse(BaseModel):
    id: uuid.UUID
    username: str
    email: str | None
    role: str
    created_at: datetime

    model_config = {"from_attributes": True}


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
