"""InputAdapter abstract base class.

Responsibilities:
1. Fetch blob text from a datasource
2. Store it in Cosmos DB for PostgreSQL with pgvector (grc.blob_records table)
3. Track adapter metadata and batch information

All adapters share this interface. To implement a new adapter:
1. Subclass InputAdapter
2. Implement fetch_blobs() to return blob data from your source
3. Register the adapter in the adapter registry
"""

from __future__ import annotations

import hashlib
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.blob import BlobRecord

logger = structlog.get_logger()


@dataclass
class BlobData:
    """Intermediate representation of blob data from a source."""

    content: str
    title: str | None = None
    source_id: str | None = None
    metadata: dict[str, Any] | None = None


class InputAdapter(ABC):
    """Abstract base class for all input adapters.

    Each adapter fetches text blobs from a specific datasource and
    stores them in the grc.blob_records table with pgvector support.
    """

    def __init__(self, adapter_name: str) -> None:
        self.adapter_name = adapter_name

    @abstractmethod
    async def fetch_blobs(self, **kwargs: Any) -> list[BlobData]:
        """Fetch blob data from the external source.

        Returns a list of BlobData objects to be stored.
        Override this method in subclasses.
        """
        ...

    async def ingest(
        self,
        db: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        org_id: uuid.UUID,
        created_by: uuid.UUID | None = None,
        batch_id: str | None = None,
        is_test_data: bool = False,
        **fetch_kwargs: Any,
    ) -> list[BlobRecord]:
        """Fetch blobs and store them in the database.

        This is the main entry point for adapter usage.
        """
        import json

        blobs = await self.fetch_blobs(**fetch_kwargs)
        if not blobs:
            logger.info("adapter_no_blobs", adapter=self.adapter_name)
            return []

        batch_id = batch_id or str(uuid.uuid4())
        records: list[BlobRecord] = []

        for blob in blobs:
            content_hash = hashlib.sha256(blob.content.encode()).hexdigest()

            record = BlobRecord(
                tenant_id=tenant_id,
                org_id=org_id,
                source_type=self.adapter_name,
                source_id=blob.source_id,
                title=blob.title,
                content=blob.content,
                content_hash=content_hash,
                metadata_json=json.dumps(blob.metadata) if blob.metadata else None,
                batch_id=batch_id,
                processing_status="pending",
                adapter_name=self.adapter_name,
                created_by=created_by,
                word_count=len(blob.content.split()),
                is_test_data=is_test_data,
            )
            db.add(record)
            records.append(record)

        await db.flush()
        logger.info(
            "adapter_ingest_complete",
            adapter=self.adapter_name,
            record_count=len(records),
            batch_id=batch_id,
        )
        return records


# ── Adapter Registry ──────────────────────────────────────────────────

_registry: dict[str, type[InputAdapter]] = {}


def register_adapter(name: str, adapter_cls: type[InputAdapter]) -> None:
    _registry[name] = adapter_cls


def get_adapter(name: str) -> InputAdapter:
    if name not in _registry:
        raise ValueError(f"Unknown adapter: {name}. Available: {list(_registry.keys())}")
    return _registry[name](adapter_name=name)


def list_adapters() -> list[str]:
    return list(_registry.keys())
