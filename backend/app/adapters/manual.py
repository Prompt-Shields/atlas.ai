"""Manual InputAdapter — ingests blob text via REST POST endpoint."""

from __future__ import annotations

from typing import Any

from app.adapters.base import BlobData, InputAdapter, register_adapter


class ManualAdapter(InputAdapter):
    """Accepts blob text directly from the REST API.

    Usage:
        POST /api/v1/adapters/manual/ingest
        Body: { "title": "...", "content": "...", "source_id": "...", "metadata": {...} }

    The fetch_blobs method simply wraps the provided data into BlobData objects.
    """

    async def fetch_blobs(self, **kwargs: Any) -> list[BlobData]:
        content = kwargs.get("content", "")
        title = kwargs.get("title")
        source_id = kwargs.get("source_id")
        metadata = kwargs.get("metadata")

        if not content:
            return []

        return [
            BlobData(
                content=content,
                title=title,
                source_id=source_id,
                metadata=metadata,
            )
        ]


# Register
register_adapter("manual", ManualAdapter)
