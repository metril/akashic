import uuid
from pydantic import BaseModel, Field


class TagCreate(BaseModel):
    name: str = Field(description="Tag name; unique.", examples=['invoice'])
    color: str | None = Field(default=None, description="Display colour, e.g. `#2563eb`.")

    model_config = {"json_schema_extra": {'examples': [{'name': 'invoice', 'color': '#2563eb'}]}}


class TagResponse(BaseModel):
    id: uuid.UUID = Field(description="Tag id.")
    name: str = Field(description="Tag name.")
    color: str | None = Field(description="Display colour, if set.")

    model_config = {"from_attributes": True, "json_schema_extra": {'examples': [{'id': '0d4a9a0e-2b1c-4c5e-8f6d-1a2b3c4d5e6f', 'name': 'invoice', 'color': '#2563eb'}]}}
