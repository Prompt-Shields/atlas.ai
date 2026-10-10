"""The 30-day pilot report (promptly-guide #39): which AI tools are used, where people
get stuck, and what risky behaviour appeared, from aggregate data only.

Three sections, each from data that is already aggregate, and each through a gate:

  * **AI tools in use** and **where people get stuck** come from Promptly Guide's
    adoption figures (`guide_adoption_service`): teams of ten or more, open-ended
    bands, no totals, and only people who opted in (#38). Tools are the `app`
    kind; getting stuck is how walkthroughs ended (`completion`) and which of
    Guide's topics people needed (`topic`).
  * **Risky behaviour** comes from Atlas's own prompt telemetry from the Prompt
    Shields clients (`grc.prompt_events`), as tenant-level counts. Guide adds
    nothing here: its prompt tips are not counted at all (promptly-guide #34). A
    row is shown only when it spans at least `MINIMUM_DEVICES` devices, so no row
    describes one person's machine.

Ready 30 days after Guide was connected (`GuideConnection.created_at`). Guide's
figures are monthly, so the report covers the finished months since then, at most
`MAX_MONTHS` of them; the risk section covers the same days.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.guide_adoption import GuideConnection
from app.models.prompt_event import PromptEvent
from app.schemas.telemetry import PromptEventKind
from app.services import guide_adoption_service as adoption

PILOT_DAYS = 30
MAX_MONTHS = 3
# The same floor as Guide's teams: a risk row from fewer devices than this is
# about too few machines to show.
MINIMUM_DEVICES = adoption.MINIMUM_GROUP_SIZE

OPT_IN_NOTE = (
    "Guide's figures count only people who chose to be counted, so each range is a "
    "share of them, not of the whole team."
)
RISK_NOTE = (
    "Risky behaviour comes from Prompt Shields' own prompt telemetry, as counts for "
    "the whole organisation. Promptly Guide's prompt tips are not counted. A row is "
    f"shown only where at least {MINIMUM_DEVICES} devices contributed to it."
)


class PilotNotReady(Exception):
    def __init__(self, ready_on: datetime) -> None:
        self.ready_on = ready_on
        super().__init__(f"The pilot report is ready on {ready_on.date().isoformat()}")


@dataclass(frozen=True)
class RiskRow:
    key: str
    events: int
    devices: int


@dataclass(frozen=True)
class RiskSection:
    since: datetime
    until: datetime
    by_category: list[RiskRow]
    by_app: list[RiskRow]
    by_action: list[RiskRow]
    # Rows left out for spanning too few devices, per breakdown. Never which.
    suppressed: dict[str, int]


@dataclass(frozen=True)
class MonthSection:
    period: str
    tools: list[adoption.Figure]
    finished: list[adoption.Figure]
    not_finished: list[adoption.Figure]
    topics: list[adoption.Figure]
    # Where a topic's walkthrough ended (promptly-guide #85): the friction in the
    # organisation's own apps, by team.
    friction: list[adoption.Figure]
    teams_too_small: list[str]
    suppressed_categories: dict[str, int]


@dataclass(frozen=True)
class PilotReport:
    connected_at: datetime
    generated_at: datetime
    offered_kinds: list[str]
    months: list[MonthSection]
    risk: RiskSection
    notes: list[str] = field(default_factory=list)


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def report_months(connected_at: datetime, now: datetime) -> list[str]:
    """The finished months the pilot overlaps, newest last, at most `MAX_MONTHS`."""
    start = adoption._months(connected_at.year, connected_at.month)
    current = adoption._months(now.year, now.month)
    months = [adoption.period_text(m // 12, m % 12 + 1) for m in range(start, current)]
    return months[-MAX_MONTHS:]


async def _risk_rows(db: AsyncSession, filters: list, column) -> tuple[list[RiskRow], int]:  # noqa: ANN001 — a SQLAlchemy column expression
    stmt = (
        select(
            column.label("key"),
            func.sum(PromptEvent.occurrences).label("events"),
            func.count(func.distinct(PromptEvent.device_fingerprint)).label("devices"),
        )
        .where(*filters)
        .group_by(column)
    )
    rows: list[RiskRow] = []
    suppressed = 0
    for row in await db.execute(stmt):
        key = row.key.value if hasattr(row.key, "value") else str(row.key)
        if int(row.devices) < MINIMUM_DEVICES:
            suppressed += 1
            continue
        rows.append(RiskRow(key=key, events=int(row.events), devices=int(row.devices)))
    rows.sort(key=lambda r: (-r.events, r.key))
    return rows, suppressed


async def _risk_by_category(db: AsyncSession, filters: list) -> tuple[list[RiskRow], int]:
    # Python fan-out over the JSON column, like the telemetry breakdown, for
    # sqlite/Postgres portability.
    events: dict[str, int] = {}
    devices: dict[str, set[str]] = {}
    stmt = select(
        PromptEvent.pii_categories, PromptEvent.occurrences, PromptEvent.device_fingerprint
    ).where(*filters)
    for row in await db.execute(stmt):
        for category, count in (row.pii_categories or {}).items():
            events[category] = events.get(category, 0) + int(count) * int(row.occurrences or 0)
            if row.device_fingerprint:
                devices.setdefault(category, set()).add(row.device_fingerprint)
    rows: list[RiskRow] = []
    suppressed = 0
    for category, total in events.items():
        seen = len(devices.get(category, set()))
        if seen < MINIMUM_DEVICES:
            suppressed += 1
            continue
        rows.append(RiskRow(key=category, events=total, devices=seen))
    rows.sort(key=lambda r: (-r.events, r.key))
    return rows, suppressed


async def risk_section(
    db: AsyncSession, tenant_id: uuid.UUID, since: datetime, until: datetime
) -> RiskSection:
    filters = [
        PromptEvent.tenant_id == tenant_id,
        PromptEvent.occurred_at >= since,
        PromptEvent.occurred_at <= until,
        PromptEvent.event_kind == PromptEventKind.VIOLATION,
    ]
    by_category, cat_suppressed = await _risk_by_category(db, filters)
    by_app, app_suppressed = await _risk_rows(
        db, filters, func.coalesce(PromptEvent.app_id, "(none)")
    )
    by_action, action_suppressed = await _risk_rows(
        db, [*filters, PromptEvent.action.is_not(None)], PromptEvent.action
    )
    return RiskSection(
        since=since,
        until=until,
        by_category=by_category,
        by_app=by_app,
        by_action=by_action,
        suppressed={
            "category": cat_suppressed,
            "app": app_suppressed,
            "action": action_suppressed,
        },
    )


def month_section(tallies: list[adoption.Tally], period: str, offered: set[str]) -> MonthSection:
    report = adoption.gate(tallies, period, offered)
    tools = [f for f in report.figures if f.category_kind == "app"]
    endings = [f for f in report.figures if f.category_kind == "completion"]
    return MonthSection(
        period=period,
        tools=tools,
        finished=[f for f in endings if f.category_id == "finished"],
        not_finished=[f for f in endings if f.category_id != "finished"],
        topics=[f for f in report.figures if f.category_kind == "topic"],
        friction=[f for f in report.figures if f.category_kind == "friction"],
        teams_too_small=report.teams_too_small,
        suppressed_categories=report.suppressed_categories,
    )


async def build(
    db: AsyncSession, tenant_id: uuid.UUID, now: datetime | None = None
) -> PilotReport | None:
    """The report, or None when Guide is not connected. `PilotNotReady` before the
    pilot has run `PILOT_DAYS` days."""
    now = _utc(now or datetime.now(UTC))
    connection = (
        await db.execute(select(GuideConnection).where(GuideConnection.tenant_id == tenant_id))
    ).scalar_one_or_none()
    if connection is None:
        return None
    connected_at = _utc(connection.created_at)
    ready_on = connected_at + timedelta(days=PILOT_DAYS)
    if now < ready_on:
        raise PilotNotReady(ready_on)
    offered = {k for k in (connection.offered_kinds or []) if k in adoption.ADOPTION_KINDS}
    months = [
        month_section(await adoption.tallies_for(db, tenant_id, period), period, offered)
        for period in report_months(connected_at, now)
    ]
    risk = await risk_section(db, tenant_id, connected_at, now)
    return PilotReport(
        connected_at=connected_at,
        generated_at=now,
        offered_kinds=sorted(offered),
        months=months,
        risk=risk,
        notes=[OPT_IN_NOTE, RISK_NOTE],
    )


# ── Markdown, for sharing ──────────────────────────────────────────────────────


def _figures(lines: list[str], title: str, figures: list[adoption.Figure], label) -> None:  # noqa: ANN001
    if not figures:
        return
    lines += ["", f"**{title}**", "", "| Team | | Share of the team |", "| --- | --- | --- |"]
    lines += [f"| {f.team} | {label(f)} | {f.band.text} |" for f in figures]


_ENDINGS = {
    "stopped": "stopped by the person",
    "timedOut": "left unanswered",
    "cannotContinue": "Guide could not see a way on",
    "tooManySteps": "ran out of steps",
    "stuck": "a step did not work",
    "planFailed": "Guide could not plan the next step",
}


def friction_label(category_id: str) -> str:
    """ "expenses/new-claim/step-3" -> "expenses/new-claim: stopped at step 3"."""
    topic, _, outcome = category_id.rpartition("/")
    if outcome == "finished":
        return f"{topic}: finished"
    return f"{topic}: stopped at step {outcome.removeprefix('step-')}"


def markdown(report: PilotReport, organisation: str) -> str:
    lines = [
        f"# Promptly Guide pilot report: {organisation}",
        "",
        f"Guide connected {report.connected_at.date().isoformat()}; report made "
        f"{report.generated_at.date().isoformat()}.",
    ]
    lines += ["", *[f"> {note}" for note in report.notes]]
    lines += ["", "## AI tools in use, and where people get stuck"]
    if not report.months:
        lines += ["", "No finished month yet."]
    for month in report.months:
        lines += ["", f"### {month.period}"]
        _figures(lines, "AI tools in use", month.tools, lambda f: f.category_id)
        _figures(lines, "Walkthroughs finished", month.finished, lambda f: "finished")
        _figures(
            lines,
            "Where walkthroughs stopped",
            month.not_finished,
            lambda f: _ENDINGS.get(f.category_id, f.category_id),
        )
        _figures(lines, "What people asked Guide about", month.topics, lambda f: f.category_id)
        _figures(
            lines,
            "Where people get stuck in a task",
            month.friction,
            lambda f: friction_label(f.category_id),
        )
        if not (
            month.tools or month.finished or month.not_finished or month.topics or month.friction
        ):
            lines += ["", "Not enough people for any figure this month."]
        if month.teams_too_small:
            lines += [
                "",
                f"{len(month.teams_too_small)} team(s) had fewer than "
                f"{adoption.MINIMUM_GROUP_SIZE} people counted and are not shown.",
            ]
        left = sum(month.suppressed_categories.values())
        if left:
            lines += ["", f"{left} figure(s) were left out for being about too few people."]
    risk = report.risk
    lines += [
        "",
        "## Risky behaviour",
        "",
        f"{risk.since.date().isoformat()} to {risk.until.date().isoformat()}, "
        "the whole organisation.",
    ]
    for title, rows in (
        ("Personal data found in prompts, by kind", risk.by_category),
        ("By AI tool", risk.by_app),
        ("What was done about it", risk.by_action),
    ):
        lines += ["", f"**{title}**", ""]
        if not rows:
            lines += ["Nothing to show."]
            continue
        lines += ["| | Events | Devices |", "| --- | --- | --- |"]
        lines += [f"| {r.key} | {r.events} | {r.devices} |" for r in rows]
    left = sum(risk.suppressed.values())
    if left:
        lines += [
            "",
            f"{left} row(s) were left out for spanning fewer than {MINIMUM_DEVICES} devices.",
        ]
    return "\n".join(lines) + "\n"
