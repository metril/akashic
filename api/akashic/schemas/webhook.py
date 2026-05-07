import uuid
from datetime import datetime
from pydantic import BaseModel, field_validator

from akashic.services.url_guard import UnsafeURL, validate_outbound_url


class WebhookCreate(BaseModel):
    event_type: str
    url: str
    secret: str

    @field_validator("url")
    @classmethod
    def _ssrf_guard(cls, v: str) -> str:
        try:
            return validate_outbound_url(v)
        except UnsafeURL as exc:
            raise ValueError(str(exc)) from exc


class WebhookResponse(BaseModel):
    id: uuid.UUID
    event_type: str
    url: str
    enabled: bool
    created_at: datetime

    model_config = {"from_attributes": True}
