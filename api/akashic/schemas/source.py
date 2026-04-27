import uuid
from datetime import datetime

from pydantic import BaseModel


class SourceCreate(BaseModel):
    name: str
    type: str
    connection_config: dict
    scan_schedule: str | None = None
    exclude_patterns: list[str] | None = None


class SourceUpdate(BaseModel):
    name: str | None = None
    connection_config: dict | None = None
    scan_schedule: str | None = None
    exclude_patterns: list[str] | None = None


_SECRET_KEYS = {"password", "secret", "key", "token", "credentials", "private_key"}


def _scrub_config(config: dict) -> dict:
    """Remove sensitive values from connection_config for API responses."""
    return {
        k: "***" if any(s in k.lower() for s in _SECRET_KEYS) else v
        for k, v in config.items()
    }


class SourceResponse(BaseModel):
    id: uuid.UUID
    name: str
    type: str
    connection_config: dict
    scan_schedule: str | None
    exclude_patterns: list[str] | None
    last_scan_at: datetime | None
    status: str
    created_at: datetime
    updated_at: datetime
    security_metadata: dict | None = None

    model_config = {"from_attributes": True}

    def model_post_init(self, __context) -> None:
        self.connection_config = _scrub_config(self.connection_config)
