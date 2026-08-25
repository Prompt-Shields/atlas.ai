"""Blob record model with pgvector embedding support."""

from __future__ import annotations

import uuid

from pgvector.sqlalchemy import Vector
from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GRCBase, OrgScopedMixin, TestDataMixin


class BlobRecord(GRCBase, OrgScopedMixin, TestDataMixin):
    """Stores ingested blob text with vector embeddings for AI processing.

    Table: grc.blob_records
    This is the primary vector table used by InputAdapters and the Semantic Kernel agents.
    """

    __tablename__ = "blob_records"

    source_type: Mapped[str] = mapped_column(
        String(50), nullable=False, index=True, comment="Adapter source: manual, purview, defender"
    )
    source_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True, comment="External source identifier"
    )
    title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True, comment="SHA-256 hash for deduplication"
    )
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(1536), nullable=True, comment="OpenAI text-embedding-3-small vector"
    )
    metadata_json: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="Additional metadata as JSON string"
    )
    batch_id: Mapped[str | None] = mapped_column(
        String(100), nullable=True, index=True, comment="Batch identifier for grouped processing"
    )
    processing_status: Mapped[str] = mapped_column(
        String(20),
        default="pending",
        nullable=False,
        index=True,
        comment="pending, processing, processed, failed",
    )
    adapter_name: Mapped[str] = mapped_column(
        String(100), nullable=False, comment="Name of the InputAdapter that created this record"
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("grc.users.id", ondelete="SET NULL"),
        nullable=True,
    )
    word_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
