"""CostRecord TypedDict and CostConnector Protocol — pure types, no DB coupling."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Protocol, TypedDict, runtime_checkable

from app.models.ai_cost_record import CostKind, CostProvider, CostSource, CostSubjectKind

if TYPE_CHECKING:
    from app.models.integration import Integration


class CostRecord(TypedDict):
    """Normalised cost row returned by a connector (one per daily-grain slot)."""

    usage_date: date
    cost_kind: CostKind
    subject_kind: CostSubjectKind | None
    subject_ref: str
    tokens_in: int | None
    tokens_out: int | None
    seats: int | None
    quantity: Decimal | None
    cost_usd: Decimal
    cost_source: CostSource
    is_provisional: bool
    raw_metadata: dict[str, Any]


@runtime_checkable
class CostConnector(Protocol):
    """Protocol that every vendor adapter must satisfy.

    Adapters are *pure*: they fetch and normalise vendor data into
    ``CostRecord`` dicts.  Persistence is the orchestrator's job.
    """

    provider: CostProvider

    async def fetch_cost(
        self,
        integration: Integration,
        since: date,
        until: date,
    ) -> list[CostRecord]:
        """Return normalised cost rows for the given date window (inclusive).

        Async because adapters do async HTTP and are awaited by the
        orchestrator from within the running event loop.
        """
        ...
