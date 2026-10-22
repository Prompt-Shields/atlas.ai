"""Guided steps: the Work client pool's monthly counter (promptly-guide #62).

promptly-guide `docs/step-metering.md` counts only steps answered by Promptly's
hosted model, at the gateway that serves them, and keeps the Work client's
meter in Atlas (its option C). This holds what that meter needs, and no more:

  * **One counter per organisation per month** (`GuidedStepCount`). The pool is
    "50 per provisioned user, shared across the company", so only the
    company's total is needed.
  * **Nothing per person, and nothing about the step.** Who asked, what was
    asked, the screen and the answer are never stored (E4). No table here has a
    user, device or session column, so no employer can see who used how many.
  * **A receipt per report** (`GuidedStepReceipt`): the gateway's own id for
    one report, kept only so that a retried report is not counted twice. It
    names the report, not the person, and nothing reads it but the dedupe.
  * **No timestamps**, like the adoption counts: a receipt and a counter
    updated in the same second could otherwise be lined up.

Tenant-scoped (`tenant_id` + RLS), like the Guide adoption tables.
"""

from __future__ import annotations

from sqlalchemy import Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import TenantScopedMixin, UUIDPrimaryKeyMixin


class _Untimed(Base, UUIDPrimaryKeyMixin, TenantScopedMixin):
    """No created_at / updated_at, on purpose (see the module docstring)."""

    __abstract__ = True


class GuidedStepCount(_Untimed):
    """Hosted guided steps served to one organisation in one month."""

    __tablename__ = "guided_step_counts"
    __table_args__ = (
        UniqueConstraint("tenant_id", "period", name="uq_guided_step_counts"),
        {"schema": "grc"},
    )

    period: Mapped[str] = mapped_column(String(7), nullable=False)  # "2026-10", UTC
    steps: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class GuidedStepReceipt(_Untimed):
    """A report already counted: the gateway's report id, and nothing else."""

    __tablename__ = "guided_step_receipts"
    __table_args__ = (
        UniqueConstraint("tenant_id", "period", "report_id", name="uq_guided_step_receipts"),
        {"schema": "grc"},
    )

    period: Mapped[str] = mapped_column(String(7), nullable=False)
    report_id: Mapped[str] = mapped_column(String(64), nullable=False)


__all__ = ["GuidedStepCount", "GuidedStepReceipt"]
