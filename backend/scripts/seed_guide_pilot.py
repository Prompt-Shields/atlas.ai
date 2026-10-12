"""Seed a demo Guide pilot, so the 30-day pilot report can be shown (promptly-guide #39).

The pilot report is the Atlas.AI demo, but it refuses to exist before a pilot has run
30 days, and it shows a figure only for teams of ten or more. A demo tenant has neither.
This seeds one:

- a Guide connection made `--days-ago` days ago (45 by default), offering every kind;
- adoption counts, team by team and month by month, in the same tables Guide's
  contributions fold into: two teams large enough to show, and one too small;
- prompt telemetry violations across twelve demo devices, for the risk section.

Usage (from backend/, with the venv):

    python -m scripts.seed_guide_pilot --tenant-slug demo

It refuses a tenant with a real Guide connection, and seeds nothing twice: a demo is
marked by its connection's project id (`demo-…`) and its devices (`demo-device-…`).
The counts are made up and stored as counts, as real contributions are: there is no
person behind them, and no row about one.
"""

from __future__ import annotations

import argparse
import asyncio
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_standalone_session
from app.models.guide_adoption import (
    ADOPTION_KINDS,
    GuideAdoptionContributors,
    GuideAdoptionCount,
    GuideConnection,
)
from app.models.prompt_event import PromptEvent
from app.models.tenant import Tenant
from app.schemas.telemetry import PromptEventAction, PromptEventKind, PromptEventSource
from app.services.guide_pilot_report import report_months

DEMO_PROJECT_PREFIX = "demo-"
DEMO_DEVICE_PREFIX = "demo-device-"

# Per team: how many people contributed, and how many of them reached each category.
# A category is shown only when ten or more people reached it, as with any pilot, so a
# few here stay under that and show what a held-back figure looks like.
TEAMS: dict[str, tuple[int, dict[tuple[str, str], int]]] = {
    "sales": (
        40,
        {
            ("app", "ChatGPT"): 31,
            ("app", "Microsoft Copilot"): 18,
            ("completion", "finished"): 26,
            ("completion", "stopped"): 7,
            ("topic", "chatgpt/model"): 14,
            ("topic", "m365-copilot/draft-email"): 21,
            ("friction", "nordlys-expenses/new-claim/finished"): 20,
            ("friction", "nordlys-expenses/new-claim/step-3"): 13,
        },
    ),
    "finance": (
        22,
        {
            ("app", "Microsoft Copilot"): 19,
            ("app", "Claude"): 6,
            ("completion", "finished"): 15,
            ("topic", "m365-copilot/analyse-data"): 12,
            ("friction", "nordlys-expenses/new-claim/step-3"): 10,
        },
    ),
    # Under the minimum group size: named in the report, without figures.
    "legal": (6, {("app", "Claude"): 5, ("completion", "finished"): 4}),
}

# Violations: (app, PII categories, action), spread across the demo devices.
VIOLATIONS = [
    ("chatgpt.com", {"email": 1}, PromptEventAction.REDACTED),
    ("chatgpt.com", {"credit_card": 1}, PromptEventAction.BLOCKED),
    ("copilot.microsoft.com", {"email": 2}, PromptEventAction.FLAGGED),
    ("claude.ai", {"national_id": 1}, PromptEventAction.BLOCKED),
]
DEVICES = 12


@dataclass
class Seeded:
    connection: bool
    months: list[str]
    events: int


class DemoRefused(Exception):
    pass


async def seed_pilot(
    db: AsyncSession, tenant_id: uuid.UUID, now: datetime, days_ago: int = 45
) -> Seeded:
    connection = (
        await db.execute(select(GuideConnection).where(GuideConnection.tenant_id == tenant_id))
    ).scalar_one_or_none()
    if connection is not None and not connection.firebase_project_id.startswith(
        DEMO_PROJECT_PREFIX
    ):
        raise DemoRefused("this tenant has a real Guide connection; demo data is not added to it")
    made = connection is None
    if made:
        connection = GuideConnection(
            tenant_id=tenant_id,
            firebase_project_id=f"{DEMO_PROJECT_PREFIX}{str(tenant_id)[:8]}",
            offered_kinds=list(ADOPTION_KINDS),
        )
        connection.created_at = now - timedelta(days=days_ago)
        db.add(connection)
        await db.flush()

    connected_at = connection.created_at
    if connected_at.tzinfo is None:
        connected_at = connected_at.replace(tzinfo=UTC)
    months = report_months(connected_at, now)
    seeded_months = set(
        (
            await db.execute(
                select(GuideAdoptionContributors.period).where(
                    GuideAdoptionContributors.tenant_id == tenant_id
                )
            )
        ).scalars()
    )
    new_months = [m for m in months if m not in seeded_months]
    for month in new_months:
        for team, (people, reached) in TEAMS.items():
            db.add(
                GuideAdoptionContributors(
                    tenant_id=tenant_id, period=month, team=team, people=people
                )
            )
            for (kind, category), count in reached.items():
                db.add(
                    GuideAdoptionCount(
                        tenant_id=tenant_id,
                        period=month,
                        team=team,
                        category_kind=kind,
                        category_id=category,
                        people=count,
                    )
                )

    already = (
        await db.execute(
            select(func.count())
            .select_from(PromptEvent)
            .where(
                PromptEvent.tenant_id == tenant_id,
                PromptEvent.device_fingerprint.like(f"{DEMO_DEVICE_PREFIX}%"),
            )
        )
    ).scalar_one()
    events = 0
    if not already:
        for d in range(DEVICES):
            for i, (app, categories, action) in enumerate(VIOLATIONS):
                db.add(
                    PromptEvent(
                        tenant_id=tenant_id,
                        source=PromptEventSource.SAFARI_EXTENSION,
                        event_kind=PromptEventKind.VIOLATION,
                        app_id=app,
                        action=action,
                        pii_categories=categories,
                        device_fingerprint=f"{DEMO_DEVICE_PREFIX}{d:02d}",
                        occurrences=1 + (d + i) % 3,
                        occurred_at=connected_at + timedelta(days=1 + (d * 3 + i) % days_ago),
                    )
                )
                events += 1
    await db.commit()
    return Seeded(connection=made, months=new_months, events=events)


async def _main(tenant_slug: str, days_ago: int) -> None:
    async with get_standalone_session() as db:
        tenant = (
            await db.execute(select(Tenant).where(Tenant.slug == tenant_slug))
        ).scalar_one_or_none()
        if tenant is None:
            raise SystemExit(f"No tenant with slug {tenant_slug!r}: seed a tenant first.")
        try:
            seeded = await seed_pilot(db, tenant.id, datetime.now(UTC), days_ago)
        except DemoRefused as refused:
            raise SystemExit(f"Not seeded: {refused}.") from None
    print(
        f"Demo Guide pilot for {tenant_slug!r}: connection "
        f"{'made' if seeded.connection else 'kept'}, months {seeded.months or 'already there'}, "
        f"{seeded.events} telemetry events. Open /dashboard/pilot-report."
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tenant-slug", required=True)
    parser.add_argument("--days-ago", type=int, default=45, help="when the demo pilot began")
    args = parser.parse_args()
    asyncio.run(_main(args.tenant_slug, args.days_ago))
