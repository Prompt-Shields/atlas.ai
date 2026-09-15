"""AICostUsageBatch — the replay guard for pushed self-hosted usage.

Cost-ledger slice 2 inverts the direction of slice 1: instead of a connector
fetching a vendor's billed dollars for a day, the customer's own app reports
its per-call token usage and we derive cost. That difference forces a
different write mode.

A pull connector re-fetches the *whole* day and overwrites the ledger row, so
running it twice is harmless. A pushed batch carries only the calls made since
the last push, so it must **accumulate** into the day's row — and accumulation
is not idempotent. A client that times out and retries would silently double
its own reported spend, which is worse than losing the batch: the number still
looks plausible.

So every accepted batch records its client-supplied id here, unique per tenant,
and a replay short-circuits to the original result instead of adding again.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GRCBase, TenantScopedMixin


class AICostUsageBatch(GRCBase, TenantScopedMixin):
    """One accepted push of self-hosted usage."""

    __tablename__ = "ai_cost_usage_batches"
    __table_args__ = (
        # Per tenant, not global: two customers picking the same uuid must not
        # collide, and a tenant cannot probe another's batch ids.
        UniqueConstraint("tenant_id", "batch_id", name="uq_grc_ai_cost_usage_batch_tenant_batch"),
        {"schema": "grc"},
    )

    integration_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("grc.integrations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Client-supplied idempotency key.
    batch_id: Mapped[str] = mapped_column(String(200), nullable=False)

    # What the batch contained and what we did with it — enough to answer a
    # replay identically without recomputing, and to explain a discrepancy
    # ("we accepted 900 of your 1000 calls") without keeping the payload.
    call_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    accepted_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_touched: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(14, 6), nullable=False, default=Decimal("0"))
