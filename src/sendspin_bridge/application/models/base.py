"""Base for documents the API returns."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ResponseModel(BaseModel):
    """Every field is always present in a response, defaults included — the
    output schema says so, which keeps generated clients from treating them
    as optional."""

    model_config = ConfigDict(json_schema_serialization_defaults_required=True)
