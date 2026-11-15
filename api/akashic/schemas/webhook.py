import uuid
from datetime import datetime
from pydantic import BaseModel, Field, field_validator

from akashic.services.url_guard import UnsafeURL, validate_outbound_url_literal


class WebhookCreate(BaseModel):
    event_type: str = Field(description="Event to subscribe to, e.g. `scan.completed` or `scan.failed`.")
    url: str = Field(description="HTTPS or HTTP endpoint that receives the POST. Must resolve to a public address.")
    secret: str = Field(description="Shared secret used to sign deliveries (HMAC-SHA256 in `X-Akashic-Signature`). Write-only: never returned.")

    @field_validator("url")
    @classmethod
    def _ssrf_guard(cls, v: str) -> str:
        try:
            return validate_outbound_url_literal(v)
        except UnsafeURL as exc:
            raise ValueError(str(exc)) from exc

    model_config = {"json_schema_extra": {'examples': [{'event_type': 'scan.completed', 'url': 'https://hooks.example.com/akashic', 'secret': 'whsec_4f9d2c'}]}}


class WebhookResponse(BaseModel):
    id: uuid.UUID = Field(description="Webhook id.")
    event_type: str = Field(description="Event the webhook subscribes to.")
    url: str = Field(description="Delivery endpoint.")
    enabled: bool = Field(description="Whether deliveries are sent.")
    created_at: datetime = Field(description="When the webhook was created.")

    model_config = {"from_attributes": True}
