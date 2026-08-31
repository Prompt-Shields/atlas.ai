"""Usage-quality scorecard pipeline for the Adoption page (issue #249, M6).

atlas.ai has no telemetry pipeline yet that classifies AI-assisted work by
outcome, so this module ports AISPM's usage-quality model as a *seeded*
transcript -> scorecard pipeline: a fixed set of representative sessions
(`_SEED_SESSIONS`) stands in for what a real ingestion pipeline would
produce, and `build_scorecard()` runs the same aggregation a live pipeline
would run over real transcripts. Swapping the seed for a real query is the
only change needed once telemetry exists — the aggregation logic does not
change.

Four quality dimensions, each scored 0-100:
  - outcome     share of sessions that reached a concrete, kept deliverable
  - leverage    how much task complexity users hand to the assistant
  - trajectory  week-over-week change in the outcome rate (adoption maturing)
  - diffusion   breadth of departments sustaining a healthy outcome rate

Headline: ROI (value of hours saved against a blended fully-loaded cost) and
an upskilling score derived from how much complexity handled per user grew
across the window.

The ROI multiplier here is a *scorecard* figure: it divides by an assumed
per-seat tooling cost (`_WEEKLY_SEAT_COST`) because this module is
tenant-agnostic and has no database access. `GET /api/v1/cost/roi` is the
canonical answer — it divides by the tenant's real ledger spend and applies
that tenant's own blended rate. Where the two disagree, the endpoint is right.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models.roi_assumptions import DEFAULT_BLENDED_HOURLY_RATE_USD

# ──────────────────────────────────────────────────────────────────────
# Seed data — stand-in for a real transcript store, one row per session.
# `week` is 1-indexed and increasing (1 = oldest, `_WINDOW_WEEKS` = latest).
# `complexity` is a 1-5 estimate of how much of the task was delegated.
# ──────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class _Session:
    department: str
    user: str
    week: int
    complexity: int
    reached_outcome: bool
    minutes_saved: int


_WINDOW_WEEKS = 4

_SEED_SESSIONS: tuple[_Session, ...] = (
    # Engineering — high, steadily improving delegation
    _Session("Engineering", "eng-1", 1, 2, True, 20),
    _Session("Engineering", "eng-1", 2, 3, True, 35),
    _Session("Engineering", "eng-1", 3, 4, True, 45),
    _Session("Engineering", "eng-1", 4, 4, True, 50),
    _Session("Engineering", "eng-2", 1, 3, True, 25),
    _Session("Engineering", "eng-2", 2, 3, False, 5),
    _Session("Engineering", "eng-2", 3, 4, True, 40),
    _Session("Engineering", "eng-2", 4, 5, True, 60),
    # Sales — moderate, choppy outcomes
    _Session("Sales", "sales-1", 1, 2, True, 15),
    _Session("Sales", "sales-1", 2, 2, False, 5),
    _Session("Sales", "sales-1", 3, 3, True, 20),
    _Session("Sales", "sales-1", 4, 3, True, 25),
    _Session("Sales", "sales-2", 1, 1, False, 5),
    _Session("Sales", "sales-2", 2, 2, True, 10),
    _Session("Sales", "sales-2", 3, 2, True, 15),
    _Session("Sales", "sales-2", 4, 3, True, 20),
    # Legal — low volume, low delegation (sensitive review work)
    _Session("Legal", "legal-1", 1, 1, False, 5),
    _Session("Legal", "legal-1", 2, 2, True, 10),
    _Session("Legal", "legal-1", 3, 2, True, 10),
    _Session("Legal", "legal-1", 4, 2, True, 15),
    # Marketing — high volume, high and rising delegation
    _Session("Marketing", "mkt-1", 1, 3, True, 30),
    _Session("Marketing", "mkt-1", 2, 4, True, 40),
    _Session("Marketing", "mkt-1", 3, 4, True, 45),
    _Session("Marketing", "mkt-1", 4, 5, True, 55),
    _Session("Marketing", "mkt-2", 1, 2, True, 15),
    _Session("Marketing", "mkt-2", 2, 3, True, 25),
    _Session("Marketing", "mkt-2", 3, 4, True, 35),
    _Session("Marketing", "mkt-2", 4, 4, True, 40),
)

# Blended fully-loaded hourly cost used to translate time saved into ROI.
# Imported rather than restated: this module and the AI Spend page each used to
# carry their own rate ($75 here, $95 there), so one organisation read two
# different ROI figures depending on which page it opened. The canonical,
# per-tenant model lives in `RoiAssumptions`; this is only its default, and
# this scorecard is tenant-agnostic so it cannot consult the override.
_BLENDED_HOURLY_COST = float(DEFAULT_BLENDED_HOURLY_RATE_USD)
# Assumed weekly per-seat AI tooling spend the time saved is measured against.
_WEEKLY_SEAT_COST = 15.0
# A department needs at least this share of sessions reaching an outcome to
# count as "diffused" — i.e. genuinely, not just nominally, adopting.
_DIFFUSION_OUTCOME_THRESHOLD = 0.6


@dataclass(frozen=True, slots=True)
class QualityDimension:
    key: str
    label: str
    score: int
    trend: float
    narrative: str


@dataclass(frozen=True, slots=True)
class UsageQualityHeadline:
    roi_multiplier: float
    hours_saved_per_week: float
    upskilling_score: int
    window_days: int


@dataclass(frozen=True, slots=True)
class UsageQualityScorecard:
    headline: UsageQualityHeadline
    dimensions: tuple[QualityDimension, ...]


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


def _outcome_rate(sessions: tuple[_Session, ...]) -> float:
    if not sessions:
        return 0.0
    return sum(1 for s in sessions if s.reached_outcome) / len(sessions)


def _outcome_dimension(sessions: tuple[_Session, ...]) -> QualityDimension:
    rate = _outcome_rate(sessions)
    first_week_rate = _outcome_rate(tuple(s for s in sessions if s.week == 1))
    last_week_rate = _outcome_rate(tuple(s for s in sessions if s.week == _WINDOW_WEEKS))
    return QualityDimension(
        key="outcome",
        label="Outcome",
        score=round(_clamp(rate * 100)),
        trend=round((last_week_rate - first_week_rate) * 100, 1),
        narrative=(
            f"{round(rate * 100)}% of sessions ended in a kept, usable deliverable "
            "rather than an abandoned draft."
        ),
    )


def _leverage_dimension(sessions: tuple[_Session, ...]) -> QualityDimension:
    kept = [s for s in sessions if s.reached_outcome]
    if not kept:
        avg_complexity = 0.0
    else:
        avg_complexity = sum(s.complexity for s in kept) / len(kept)
    # complexity is 1-5; map to a 0-100 score.
    score = (avg_complexity - 1) / 4 * 100
    first_avg = _avg_complexity(sessions, week=1)
    last_avg = _avg_complexity(sessions, week=_WINDOW_WEEKS)
    return QualityDimension(
        key="leverage",
        label="Leverage",
        score=round(_clamp(score)),
        trend=round(last_avg - first_avg, 1),
        narrative=(
            f"Kept work averages {avg_complexity:.1f}/5 delegated complexity — "
            "users are handing over more than one-line questions."
        ),
    )


def _avg_complexity(sessions: tuple[_Session, ...], *, week: int) -> float:
    week_sessions = [s for s in sessions if s.week == week]
    if not week_sessions:
        return 0.0
    return sum(s.complexity for s in week_sessions) / len(week_sessions)


def _trajectory_dimension(sessions: tuple[_Session, ...]) -> QualityDimension:
    first_week_rate = _outcome_rate(tuple(s for s in sessions if s.week == 1))
    last_week_rate = _outcome_rate(tuple(s for s in sessions if s.week == _WINDOW_WEEKS))
    delta = last_week_rate - first_week_rate
    # A +50pp swing over the window maps to a perfect score; center at 50.
    score = 50 + (delta * 100)
    return QualityDimension(
        key="trajectory",
        label="Trajectory",
        score=round(_clamp(score)),
        trend=round(delta * 100, 1),
        narrative=(
            f"Outcome rate moved from {round(first_week_rate * 100)}% in week 1 to "
            f"{round(last_week_rate * 100)}% in week {_WINDOW_WEEKS}."
        ),
    )


def _diffusion_dimension(sessions: tuple[_Session, ...]) -> QualityDimension:
    departments = sorted({s.department for s in sessions})
    if not departments:
        return QualityDimension(
            key="diffusion", label="Diffusion", score=0, trend=0.0, narrative="No sessions yet."
        )
    healthy = [
        dept
        for dept in departments
        if _outcome_rate(tuple(s for s in sessions if s.department == dept))
        >= _DIFFUSION_OUTCOME_THRESHOLD
    ]
    score = len(healthy) / len(departments) * 100
    first_half = _diffusion_share(sessions, departments, max_week=_WINDOW_WEEKS // 2)
    full = _diffusion_share(sessions, departments, max_week=_WINDOW_WEEKS)
    return QualityDimension(
        key="diffusion",
        label="Diffusion",
        score=round(_clamp(score)),
        trend=round((full - first_half) * 100, 1),
        narrative=(
            f"{len(healthy)} of {len(departments)} departments sustain a healthy "
            "outcome rate, not just trial usage."
        ),
    )


def _diffusion_share(
    sessions: tuple[_Session, ...], departments: list[str], *, max_week: int
) -> float:
    windowed = tuple(s for s in sessions if s.week <= max_week)
    healthy = [
        dept
        for dept in departments
        if _outcome_rate(tuple(s for s in windowed if s.department == dept))
        >= _DIFFUSION_OUTCOME_THRESHOLD
    ]
    return len(healthy) / len(departments) if departments else 0.0


def _headline(sessions: tuple[_Session, ...]) -> UsageQualityHeadline:
    total_minutes_saved = sum(s.minutes_saved for s in sessions)
    hours_saved = total_minutes_saved / 60
    hours_saved_per_week = hours_saved / _WINDOW_WEEKS

    users = sorted({s.user for s in sessions})
    weekly_cost = len(users) * _WEEKLY_SEAT_COST * _WINDOW_WEEKS
    weekly_value = hours_saved * _BLENDED_HOURLY_COST
    roi_multiplier = round(weekly_value / weekly_cost, 1) if weekly_cost else 0.0

    # Upskilling: average per-user growth in delegated complexity from their
    # first tracked week to their last, normalised against the 1-5 scale.
    growths: list[float] = []
    for user in users:
        user_sessions = sorted((s for s in sessions if s.user == user), key=lambda s: s.week)
        if len(user_sessions) < 2:
            continue
        growths.append(user_sessions[-1].complexity - user_sessions[0].complexity)
    avg_growth = sum(growths) / len(growths) if growths else 0.0
    upskilling_score = round(_clamp(50 + avg_growth / 4 * 50))

    return UsageQualityHeadline(
        roi_multiplier=roi_multiplier,
        hours_saved_per_week=round(hours_saved_per_week, 1),
        upskilling_score=upskilling_score,
        window_days=_WINDOW_WEEKS * 7,
    )


def build_scorecard() -> UsageQualityScorecard:
    """Run the transcript -> scorecard aggregation over the seeded session set."""
    sessions = _SEED_SESSIONS
    dimensions = (
        _outcome_dimension(sessions),
        _leverage_dimension(sessions),
        _trajectory_dimension(sessions),
        _diffusion_dimension(sessions),
    )
    return UsageQualityScorecard(headline=_headline(sessions), dimensions=dimensions)
