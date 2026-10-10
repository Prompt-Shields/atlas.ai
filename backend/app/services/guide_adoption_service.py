"""Promptly Guide adoption figures: intake and the gate (promptly-guide #27, #37, #38).

The gate is a port of Guide's `AdoptionGate` (promptly-guide
`Sources/GuideAdoption/AdoptionGate.swift`, documented in its
`docs/adoption-analytics.md`), and the two must stay the same rules:

  1. A team with fewer than `MINIMUM_GROUP_SIZE` contributors that month reports
     **nothing**, not even which categories it touched.
  2. Within a team that reports, a category reaching fewer than
     `MINIMUM_GROUP_SIZE` of them is **suppressed, not rounded**. The report says
     how many were left out per team, never which.
  3. Figures are **bands** with open ends ("under 20%", "80% or more") and there
     is **no team total**, so nothing can be recovered by subtraction and no
     figure says "all of the team" or "none of them".
  4. Only kinds the tenant currently offers are shown.

Intake folds a contribution into running counts and keeps nothing per person;
see `app/models/guide_adoption.py` for why the tables look the way they do.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.guide_adoption import (
    ADOPTION_KINDS,
    GuideAdoptionContributors,
    GuideAdoptionCount,
    GuideAdoptionReceipt,
)

MINIMUM_GROUP_SIZE = 10
BAND_WIDTH = 10
_BOTTOM_TOP = 2 * BAND_WIDTH

# A contribution may be for one of the last few *finished* months: Guide sends
# after a month ends, and a figure for a month still running would let one more
# contribution move a band while someone watches.
OLDEST_MONTHS_BACK = 3

MAX_CATEGORIES = 200
MAX_TEAM_LENGTH = 100

_PERIOD_RE = re.compile(r"^(\d{4})-(\d{2})$")
# Compiled-in identifiers only, never free text (Guide's `AdoptionCategory`):
#   topic       "<pack>/<entry>", lowercase ids        e.g. "chatgpt/model"
#   app         a well-known AI tool's name            e.g. "Le Chat"
#   completion  a walkthrough ending's raw value       e.g. "finished"
#   friction    "<pack>/<entry>/" and where its walkthrough ended: "finished" or
#               the step it stopped at, 1 to 10 (promptly-guide #85)
#                                                     e.g. "expenses/new-claim/step-3"
_CATEGORY_ID_RE = {
    "topic": re.compile(r"^[a-z0-9][a-z0-9-]{0,59}/[a-z0-9][a-z0-9-]{0,59}$"),
    "app": re.compile(r"^[A-Za-z0-9][A-Za-z0-9 .\-]{0,39}$"),
    "completion": re.compile(r"^[a-z][A-Za-z]{0,29}$"),
    "friction": re.compile(
        r"^[a-z0-9][a-z0-9-]{0,59}/[a-z0-9][a-z0-9-]{0,59}/(finished|step-([1-9]|10))$"
    ),
}


class ContributionRejected(ValueError):
    """The contribution is not one Atlas will count. The message says why."""


# ---------------------------------------------------------------------------
# Periods
# ---------------------------------------------------------------------------


def parse_period(text: str) -> tuple[int, int]:
    match = _PERIOD_RE.match(text)
    if not match:
        raise ContributionRejected("period must be YYYY-MM")
    year, month = int(match.group(1)), int(match.group(2))
    if not 1 <= month <= 12 or not 2020 <= year <= 2100:
        raise ContributionRejected("period is not a month")
    return year, month


def period_text(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"


def _months(year: int, month: int) -> int:
    return year * 12 + (month - 1)


def acceptable_period(text: str, now: datetime) -> str:
    """`text` if it is one of the last `OLDEST_MONTHS_BACK` finished months."""
    year, month = parse_period(text)
    current = _months(now.year, now.month)
    asked = _months(year, month)
    if asked >= current:
        raise ContributionRejected("only a finished month can be contributed")
    if asked < current - OLDEST_MONTHS_BACK:
        raise ContributionRejected("that month is too long ago")
    return period_text(year, month)


# ---------------------------------------------------------------------------
# Intake
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Contribution:
    team: str
    period: str
    # (kind, id) pairs, without repeats.
    categories: frozenset[tuple[str, str]] = field(default_factory=frozenset)


def validate_contribution(
    team: str,
    period: str,
    categories: list[tuple[str, str]],
    offered: set[str],
    now: datetime | None = None,
) -> Contribution:
    """The contribution as it will be counted, or `ContributionRejected`.

    A category of a kind the tenant does not offer is a rejection, not a silent
    drop: Guide only sends what was offered, so anything else is a client that
    does not follow the rules, and its whole contribution is suspect.
    """
    name = team.strip()
    if not name or len(name) > MAX_TEAM_LENGTH or any(ord(c) < 32 for c in name):
        raise ContributionRejected("team must be 1-100 printable characters")
    if not offered:
        raise ContributionRejected("this organisation counts nothing")
    if len(categories) > MAX_CATEGORIES:
        raise ContributionRejected("too many categories")
    accepted: set[tuple[str, str]] = set()
    for kind, category_id in categories:
        if kind not in ADOPTION_KINDS:
            raise ContributionRejected(f"unknown kind {kind!r}")
        if kind not in offered:
            raise ContributionRejected(f"{kind!r} is not counted by this organisation")
        if not _CATEGORY_ID_RE[kind].match(category_id):
            raise ContributionRejected(f"{kind!r} category id is not an identifier")
        accepted.add((kind, category_id))
    return Contribution(
        team=name,
        period=acceptable_period(period, now or datetime.now(UTC)),
        categories=frozenset(accepted),
    )


def receipt(secret: str, tenant_id: uuid.UUID, subject: str, period: str) -> str:
    """A keyed hash naming "this person, this month" to Atlas's own dedupe check
    and to nothing else. The key is derived from the server secret, so the hash
    cannot be recomputed from an email list without it."""
    key = hashlib.sha256(b"guide-adoption-receipt:" + secret.encode()).digest()
    message = f"{tenant_id}|{subject}|{period}".encode()
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def _insert(session: AsyncSession):  # noqa: ANN202 — dialect-specific Insert factory
    return postgresql.insert if session.get_bind().dialect.name == "postgresql" else sqlite.insert


async def record_contribution(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    contribution: Contribution,
    receipt_hash: str,
) -> bool:
    """Folds `contribution` into the tenant's counts. False, and nothing changed,
    when this person already contributed for the month. The caller commits."""
    insert = _insert(session)
    first = await session.execute(
        insert(GuideAdoptionReceipt)
        .values(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            period=contribution.period,
            receipt=receipt_hash,
        )
        .on_conflict_do_nothing(index_elements=["tenant_id", "period", "receipt"])
    )
    if first.rowcount == 0:
        return False

    contributors = insert(GuideAdoptionContributors).values(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        period=contribution.period,
        team=contribution.team,
        people=1,
    )
    await session.execute(
        contributors.on_conflict_do_update(
            index_elements=["tenant_id", "period", "team"],
            set_={"people": GuideAdoptionContributors.people + 1},
        )
    )
    for kind, category_id in sorted(contribution.categories):
        count = insert(GuideAdoptionCount).values(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            period=contribution.period,
            team=contribution.team,
            category_kind=kind,
            category_id=category_id,
            people=1,
        )
        await session.execute(
            count.on_conflict_do_update(
                index_elements=["tenant_id", "period", "team", "category_kind", "category_id"],
                set_={"people": GuideAdoptionCount.people + 1},
            )
        )
    return True


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Band:
    lower: int

    @property
    def text(self) -> str:
        if self.lower == 0:
            return f"under {_BOTTOM_TOP}%"
        if self.lower == 100 - _BOTTOM_TOP:
            return f"{self.lower}% or more"
        return f"{self.lower}–{self.lower + BAND_WIDTH - 1}%"


def band(count: int, total: int) -> Band | None:
    """The band `count` of `total` falls in; None when it is not a share. Rounds
    down, so a figure never says more than happened."""
    if total <= 0 or count < 0 or count > total:
        return None
    percent = count * 100 // total
    if percent < _BOTTOM_TOP:
        return Band(0)
    if percent >= 100 - _BOTTOM_TOP:
        return Band(100 - _BOTTOM_TOP)
    return Band(percent // BAND_WIDTH * BAND_WIDTH)


@dataclass(frozen=True)
class Figure:
    team: str
    category_kind: str
    category_id: str
    band: Band


@dataclass(frozen=True)
class Report:
    period: str
    figures: list[Figure]
    teams_too_small: list[str]
    suppressed_categories: dict[str, int]
    unusable: int


@dataclass(frozen=True)
class Tally:
    team: str
    contributors: int
    people: dict[tuple[str, str], int]


def gate(tallies: list[Tally], period: str, kinds: set[str]) -> Report:
    """What may be shown. Same rules, same order, as Guide's `AdoptionGate.report`."""
    figures: list[Figure] = []
    too_small: list[str] = []
    suppressed: dict[str, int] = {}
    unusable = 0
    named: dict[str, int] = {}
    for tally in tallies:
        named[tally.team.strip()] = named.get(tally.team.strip(), 0) + 1
    for tally in tallies:
        team = tally.team.strip()
        if (
            not team
            or named[team] != 1
            or tally.contributors < 0
            or any(p < 0 or p > tally.contributors for p in tally.people.values())
        ):
            unusable += 1
            continue
        if tally.contributors < MINIMUM_GROUP_SIZE:
            too_small.append(team)
            continue
        for (kind, category_id), people in tally.people.items():
            if kind not in kinds:
                continue
            shown = band(people, tally.contributors) if people >= MINIMUM_GROUP_SIZE else None
            if shown is None:
                suppressed[team] = suppressed.get(team, 0) + 1
                continue
            figures.append(Figure(team, kind, category_id, shown))
    figures.sort(key=lambda f: (f.team, f.category_kind, f.category_id))
    return Report(
        period=period,
        figures=figures,
        teams_too_small=sorted(too_small),
        suppressed_categories=suppressed,
        unusable=unusable,
    )


async def tallies_for(session: AsyncSession, tenant_id: uuid.UUID, period: str) -> list[Tally]:
    contributors = (
        (
            await session.execute(
                select(GuideAdoptionContributors).where(
                    GuideAdoptionContributors.tenant_id == tenant_id,
                    GuideAdoptionContributors.period == period,
                )
            )
        )
        .scalars()
        .all()
    )
    counts = (
        (
            await session.execute(
                select(GuideAdoptionCount).where(
                    GuideAdoptionCount.tenant_id == tenant_id,
                    GuideAdoptionCount.period == period,
                )
            )
        )
        .scalars()
        .all()
    )
    people: dict[str, dict[tuple[str, str], int]] = {}
    for row in counts:
        people.setdefault(row.team, {})[(row.category_kind, row.category_id)] = row.people
    return [Tally(c.team, c.people, people.get(c.team, {})) for c in contributors]
