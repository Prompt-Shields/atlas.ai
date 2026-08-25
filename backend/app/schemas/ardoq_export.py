"""Ardoq AI-Lens export schemas."""

from __future__ import annotations

from pydantic import BaseModel


class ArdoqManifestResponse(BaseModel):
    """Per-entity row counts for the export bundle, without the CSV bodies."""

    generated_at: str
    totals: dict[str, int]
