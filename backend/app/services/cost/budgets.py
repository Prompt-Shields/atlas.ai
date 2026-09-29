"""Budget evaluation — month-to-date spend against a ceiling, and when to shout.

The arithmetic here is trivial. Everything that makes this file worth reading
is about the cases where the arithmetic would produce a confident number that
is not true.

Three guards, each mirroring one the ROI slice needed:

1. **No ledger data is not 0% used.** A tenant whose connectors have never
   synced has no spend rows, and `sum(...) = 0` is indistinguishable in SQL
   from a tenant that genuinely spent nothing. Reporting "0% of budget used"
   to the first tenant is the same class of error as reporting infinite ROI
   for a tenant with no denominator: the most reassuring reading of an absence
   of evidence. `BudgetStatus.has_ledger_data` carries the distinction, and
   `alert_level` stays `ok` without claiming safety.

2. **Provisional spend is flagged, not hidden.** Today's rows land with
   `is_provisional=true` and can be revised by the vendor. A budget that flips
   to `exceeded` on provisional data is not wrong to say so, but it must say
   which part of the number can still move.

3. **A projection is not a measurement.** Extrapolating month-end from three
   days of a month is arithmetic dressed as foresight. `projected_month_end_usd`
   is `None` before `_MIN_DAYS_FOR_PROJECTION` days have elapsed rather than
   being computed and quietly caveated, because a number on a dashboard is read
   and a caveat beside it is not.

Alerting decides *newness*, not just badness: an alert fires when the computed
level is worse than the one already sent for this period. Equal or lower never
re-sends. A budget that mails every day it is over gets filtered, and a
filtered alert is worse than none because you believe you are covered.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_cost_record import AICostRecord, CostProvider
from app.models.cost_budget import (
    ALERT_LEVEL_RANK,
    BudgetAlertLevel,
    CostBudget,
)
from app.models.user import Role, User, UserRole
from app.services.email_service import send_email

logger = structlog.get_logger(__name__)

# Below this many elapsed days in the month, a month-end projection says more
# about which day it is than about spending. Seven is a week: enough to have
# seen a full weekday/weekend cycle, which is exactly the shape AI spend has.
_MIN_DAYS_FOR_PROJECTION = 7

_CENTS = Decimal("0.01")


def _money(value: object) -> Decimal:
    """Coerce a SQL aggregate to a 2dp Decimal.

    Via `str` rather than `Decimal(float)`: the ledger stores Numeric(14, 6)
    and a float round-trip reintroduces the binary-fraction error the Numeric
    column exists to avoid.
    """
    return Decimal(str(value or 0)).quantize(_CENTS, rounding=ROUND_HALF_UP)


def month_bounds(today: date) -> tuple[date, date]:
    """First day of `today`'s month, and `today` itself.

    The window is month-start to *today*, not to month-end: a budget is judged
    on money actually spent so far, never on days that have not happened.
    """
    return today.replace(day=1), today


def days_in_month(day: date) -> int:
    if day.month == 12:
        return (day.replace(year=day.year + 1, month=1, day=1) - day.replace(day=1)).days
    return (day.replace(month=day.month + 1, day=1) - day.replace(day=1)).days


@dataclass(frozen=True)
class BudgetStatus:
    """Where one budget stands this month."""

    budget_id: object
    provider: CostProvider | None
    amount_usd: Decimal
    spend_usd: Decimal
    provisional_usd: Decimal
    percent_used: Decimal | None
    alert_level: BudgetAlertLevel
    period_start: date
    as_of: date

    # False when the tenant has no cost rows at all in the window. Distinguishes
    # "spent nothing" from "we know nothing", which SQL alone cannot.
    has_ledger_data: bool

    # None until enough of the month has elapsed for the extrapolation to mean
    # anything. See _MIN_DAYS_FOR_PROJECTION.
    projected_month_end_usd: Decimal | None

    @property
    def is_provisional(self) -> bool:
        """True when part of the spend behind this status can still be revised."""
        return self.provisional_usd > 0


def _level_for(percent: Decimal | None, warn_at: Decimal) -> BudgetAlertLevel:
    """Map a used-percentage onto a level.

    `percent is None` means there was nothing to divide — no budget amount, or
    no data. It is `ok` rather than an error, but callers must read
    `has_ledger_data` before presenting that as reassurance.
    """
    if percent is None:
        return BudgetAlertLevel.ok
    if percent >= Decimal("100"):
        return BudgetAlertLevel.exceeded
    if percent >= warn_at:
        return BudgetAlertLevel.warning
    return BudgetAlertLevel.ok


async def _spend_for(
    db: AsyncSession,
    *,
    tenant_id: object,
    provider: CostProvider | None,
    since: date,
    until: date,
) -> tuple[Decimal, Decimal, int]:
    """Total spend, provisional spend, and row count for the window.

    The row count is what separates "spent nothing" from "no data": a tenant
    with zero rows and a tenant whose rows happen to sum to zero are different
    situations, and only the count tells them apart.

    Tenant is filtered explicitly here even though RLS is active, matching the
    rest of the cost router — we never rely on RLS alone.
    """
    cost = AICostRecord.cost_usd
    query = select(
        func.coalesce(func.sum(cost), 0),
        func.coalesce(
            func.sum(cost).filter(AICostRecord.is_provisional.is_(True)),
            0,
        ),
        func.count(AICostRecord.id),
    ).where(
        AICostRecord.tenant_id == tenant_id,
        AICostRecord.usage_date >= since,
        AICostRecord.usage_date <= until,
    )
    if provider is not None:
        query = query.where(AICostRecord.provider == provider)

    total, provisional, rows = (await db.execute(query)).one()
    return _money(total), _money(provisional), int(rows)


async def evaluate_budget(
    db: AsyncSession,
    budget: CostBudget,
    *,
    today: date | None = None,
) -> BudgetStatus:
    """Compute this month's standing for one budget."""
    today = today or datetime.now(UTC).date()
    period_start, as_of = month_bounds(today)

    spend, provisional, rows = await _spend_for(
        db,
        tenant_id=budget.tenant_id,
        provider=budget.provider,
        since=period_start,
        until=as_of,
    )

    amount = _money(budget.amount_usd)

    # A zero or absent ceiling has no percentage. Same shape as the ROI
    # no-denominator guard: dividing anyway would report a spectacular
    # overspend for a budget nobody actually set.
    percent: Decimal | None = None
    if amount > 0:
        percent = (spend / amount * Decimal("100")).quantize(_CENTS, rounding=ROUND_HALF_UP)

    elapsed_days = (as_of - period_start).days + 1
    projected: Decimal | None = None
    if rows > 0 and elapsed_days >= _MIN_DAYS_FOR_PROJECTION:
        daily = spend / Decimal(elapsed_days)
        projected = (daily * Decimal(days_in_month(as_of))).quantize(_CENTS, rounding=ROUND_HALF_UP)

    return BudgetStatus(
        budget_id=budget.id,
        provider=budget.provider,
        amount_usd=amount,
        spend_usd=spend,
        provisional_usd=provisional,
        percent_used=percent,
        alert_level=_level_for(percent, _money(budget.warn_threshold_percent)),
        period_start=period_start,
        as_of=as_of,
        has_ledger_data=rows > 0,
        projected_month_end_usd=projected,
    )


def should_alert(budget: CostBudget, status: BudgetStatus) -> bool:
    """Whether this status is worth sending mail about.

    True only when the level is genuinely *new* for this period — worse than
    whatever was last announced. The three ways this returns False are the
    point of the function:

      * alerts switched off for this budget,
      * nothing to say (`ok`, or no ledger data behind the number),
      * the same or a lesser level already went out this month.
    """
    if not budget.alerts_enabled:
        return False
    if status.alert_level is BudgetAlertLevel.ok:
        return False

    # Never raise an alarm from an absence of data. A tenant whose connector
    # broke has no rows; that is a connector problem and saying "budget
    # exceeded" about it would point at the wrong thing entirely.
    if not status.has_ledger_data:
        return False

    # A new month resets the conversation, whatever was said last month.
    if budget.last_alerted_period != status.period_start:
        return True

    if budget.last_alerted_level is None:
        return True

    return ALERT_LEVEL_RANK[status.alert_level] > ALERT_LEVEL_RANK[budget.last_alerted_level]


def record_alert(budget: CostBudget, status: BudgetStatus) -> None:
    """Stamp the budget so the same crossing does not alert twice."""
    budget.last_alerted_period = status.period_start
    budget.last_alerted_level = status.alert_level
    budget.last_alerted_at = datetime.now(UTC)


async def load_budgets(
    db: AsyncSession,
    tenant_id: object,
) -> list[CostBudget]:
    """Every budget for a tenant, tenant-wide row first.

    Ordering puts the NULL-provider (overall) budget ahead of per-provider
    ones so a UI listing them does not have to re-sort to lead with the
    headline figure.
    """
    result = await db.execute(
        select(CostBudget)
        .where(CostBudget.tenant_id == tenant_id)
        .order_by(CostBudget.provider.is_(None).desc(), CostBudget.provider)
    )
    return list(result.scalars().all())


# ─── Alert dispatch ──────────────────────────────────────────────────
#
# Delivery is email only in this slice. Slack exists in the codebase and is a
# natural second channel, but it is per-tenant OAuth state that a budget alert
# would have to reason about (which workspace, which channel, what if the
# token lapsed), and a half-wired second channel that silently drops messages
# is exactly the failure this feature exists to prevent.

_ALERT_RECIPIENT_ROLES = (Role.ORG_ADMIN, Role.TENANT_ADMIN)


async def _alert_recipients(db: AsyncSession, tenant_id: object) -> list[str]:
    """Active admin emails for the tenant.

    Admins rather than everyone: a spend ceiling is a thing an admin sets and
    can act on, and mailing every viewer about it is how an organisation
    learns to filter mail from this product.
    """
    result = await db.execute(
        select(User.email)
        .join(UserRole, UserRole.user_id == User.id)
        .where(
            User.tenant_id == tenant_id,
            User.is_active.is_(True),
            UserRole.role.in_(_ALERT_RECIPIENT_ROLES),
        )
        .distinct()
    )
    return [e for e in result.scalars().all() if e]


def _scope_label(provider: CostProvider | None) -> str:
    return "AI spend" if provider is None else f"{provider.value} spend"


def render_alert(status: BudgetStatus) -> tuple[str, str]:
    """Subject and HTML body for one budget crossing.

    The provisional caveat is in the body whenever any of the spend can still
    move. It is the difference between "you are over" and "you are over on
    numbers the vendor has not finalised", and an admin about to go and switch
    something off deserves to know which they are reading.
    """
    scope = _scope_label(status.provider)
    verb = "exceeded" if status.alert_level is BudgetAlertLevel.exceeded else "is approaching"
    pct = f"{status.percent_used}%" if status.percent_used is not None else "an unknown share of"

    subject = f"{scope} {verb} its monthly budget"

    caveat = ""
    if status.is_provisional:
        caveat = (
            f"<p><strong>${status.provisional_usd} of this is provisional</strong> — "
            "the vendor has not finalised those days and the figure may still change.</p>"
        )

    projection = ""
    if status.projected_month_end_usd is not None:
        projection = (
            f"<p>At the current rate this month ends near "
            f"<strong>${status.projected_month_end_usd}</strong>.</p>"
        )

    body = (
        f"<p>{scope} for {status.period_start:%B %Y} has reached "
        f"<strong>${status.spend_usd}</strong> of a <strong>${status.amount_usd}</strong> "
        f"budget ({pct}), as of {status.as_of:%-d %B}.</p>"
        f"{caveat}{projection}"
        "<p>This is sent once per threshold crossing, not daily.</p>"
    )
    return subject, body


async def dispatch_budget_alerts(
    db: AsyncSession,
    tenant_id: object,
    *,
    today: date | None = None,
) -> int:
    """Evaluate a tenant's budgets and mail the crossings. Returns count sent.

    Caller is responsible for having set the tenant GUC. Send failures are
    logged and swallowed per budget: one unreachable mailbox must not stop the
    other budgets alerting, for the same reason one failing connector never
    aborts the cost sweep.

    The row is stamped only after a successful send. Stamping first would mean
    a bounced mail permanently silences that crossing — the tenant would be
    over budget, believe they would be told, and never be.
    """
    budgets = await load_budgets(db, tenant_id)
    if not budgets:
        return 0

    recipients: list[str] | None = None
    sent = 0

    for budget in budgets:
        status = await evaluate_budget(db, budget, today=today)
        if not should_alert(budget, status):
            continue

        if recipients is None:
            recipients = await _alert_recipients(db, tenant_id)
        if not recipients:
            logger.warning(
                "budget_alert_no_recipients",
                tenant_id=str(tenant_id),
                budget_id=str(budget.id),
            )
            continue

        subject, body = render_alert(status)
        delivered = False
        for address in recipients:
            try:
                await send_email(address, subject, body)
                delivered = True
            except Exception as exc:  # noqa: BLE001 — one bad address must not stop the rest.
                logger.warning(
                    "budget_alert_send_failed",
                    tenant_id=str(tenant_id),
                    budget_id=str(budget.id),
                    error=str(exc),
                )

        if delivered:
            record_alert(budget, status)
            sent += 1

    if sent:
        await db.commit()
    return sent
