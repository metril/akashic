import uuid
from datetime import datetime
from pydantic import BaseModel


class WebhookCreate(BaseModel):
    event_type: str
    url: str
    secret: str


class WebhookResponse(BaseModel):
    id: uuid.UUID
    event_type: str
    url: str
    enabled: bool
    created_at: datetime

    model_config = {"from_attributes": True}
