"""Base class for API models."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ApiModel(BaseModel):
    """Marks defaulted fields as required in the response schema.

    Response models always serialize their defaults, so generated clients
    should see those fields as present. Pydantic emits separate
    `-Input` / `-Output` schemas only for models used in both directions.
    """

    model_config = ConfigDict(json_schema_serialization_defaults_required=True)
