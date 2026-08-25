"""Owner-inference engine — rank owner candidates for unowned use cases.

AISPM's `lib/owner-inference.ts` was a deterministic heuristic over a
registry + Entra ID / M365 licensing / HR signals. atlas has no licensing
model, so this ports the heuristic against what actually exists here:
`DirectoryUser` (Microsoft Graph sync — department, job title, manager
chain) and `User`/`UserRole` (atlas login identities + roles).

Two ranked candidate lists per unowned `UseCase`:

  - **Business owner candidates** — `DirectoryUser` rows in the same
    department, linked to an atlas `User` account. Only linked accounts
    are actionable: `UseCase.owner_user_id` FKs to `grc.users.id`, so a
    directory-only contact with no atlas login can't be assigned.
    Confidence rewards seniority signals (no manager on file, a
    manager/lead/director-ish job title).
  - **IT owner candidates** — active tenant `User`s holding `ORG_ADMIN`
    or `TENANT_ADMIN` (the roles already permitted to write use-case
    ownership via `PATCH /use-cases/{id}`), boosted when their linked
    directory profile's job title reads IT/security/engineering.

If the tenant has never synced a directory (no `DirectoryUser` rows at
all), business candidates fall back to the tenant's active `User`s at a
flat, lower confidence — mirrors `survey_audience.resolve_recipients`'s
directory-then-User fallback.

Accepting a suggestion is a plain `PATCH /use-cases/{id}` with
`{"owner_user_id": ...}` — this module only produces suggestions, it
never writes.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.directory import DirectoryUser
from app.models.use_case import UseCase
from app.models.user import Role, User, UserRole

_IT_TITLE_KEYWORDS = (
    "it",
    "information technology",
    "engineering",
    "security",
    "infrastructure",
    "technology",
    "devops",
    "platform",
)
_SENIOR_TITLE_KEYWORDS = ("manager", "lead", "director", "head", "chief", "vp")

_MAX_CANDIDATES = 3


@dataclass(frozen=True)
class OwnerCandidate:
    user_id: uuid.UUID
    display_name: str
    email: str | None
    confidence: float
    rationale: str


@dataclass(frozen=True)
class OwnerSuggestion:
    use_case_id: uuid.UUID
    use_case_title: str
    department: str
    business_owner_candidates: list[OwnerCandidate]
    it_owner_candidates: list[OwnerCandidate]


async def suggest_owners(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    limit: int = 50,
) -> list[OwnerSuggestion]:
    """Ranked business + IT owner suggestions for the tenant's unowned use cases."""
    use_cases_result = await db.execute(
        select(UseCase)
        .where(UseCase.tenant_id == tenant_id, UseCase.owner_user_id.is_(None))
        .order_by(UseCase.created_at.desc())
        .limit(limit)
    )
    use_cases = list(use_cases_result.scalars().all())
    if not use_cases:
        return []

    has_directory = await _tenant_has_directory(db, tenant_id)
    directory_by_dept = (
        await _directory_candidates_by_department(db, tenant_id) if has_directory else {}
    )
    fallback_business = [] if has_directory else await _fallback_business_candidates(db, tenant_id)
    it_candidates = await _it_owner_candidates(db, tenant_id)

    suggestions: list[OwnerSuggestion] = []
    for uc in use_cases:
        business = directory_by_dept.get(uc.department, []) if has_directory else fallback_business
        suggestions.append(
            OwnerSuggestion(
                use_case_id=uc.id,
                use_case_title=uc.title,
                department=uc.department,
                business_owner_candidates=business[:_MAX_CANDIDATES],
                it_owner_candidates=it_candidates[:_MAX_CANDIDATES],
            )
        )
    return suggestions


async def _tenant_has_directory(db: AsyncSession, tenant_id: uuid.UUID) -> bool:
    result = await db.execute(
        select(DirectoryUser.id).where(DirectoryUser.tenant_id == tenant_id).limit(1)
    )
    return result.scalar_one_or_none() is not None


async def _directory_candidates_by_department(
    db: AsyncSession, tenant_id: uuid.UUID
) -> dict[str, list[OwnerCandidate]]:
    result = await db.execute(
        select(DirectoryUser).where(
            DirectoryUser.tenant_id == tenant_id,
            DirectoryUser.is_active.is_(True),
            DirectoryUser.linked_user_id.is_not(None),
            DirectoryUser.department.is_not(None),
        )
    )
    rows = list(result.scalars().all())

    by_dept: dict[str, list[OwnerCandidate]] = {}
    for row in rows:
        confidence = 60.0
        rationale_bits = [f"Department match: {row.department}"]
        title = (row.job_title or "").lower()
        if any(keyword in title for keyword in _SENIOR_TITLE_KEYWORDS):
            confidence += 15
            rationale_bits.append(f"Senior title ({row.job_title})")
        if row.manager_external_user_id is None:
            confidence += 10
            rationale_bits.append("No manager on file (top of department)")
        confidence = min(confidence, 95.0)

        candidate = OwnerCandidate(
            user_id=row.linked_user_id,
            display_name=row.display_name,
            email=row.email,
            confidence=confidence,
            rationale="; ".join(rationale_bits),
        )
        by_dept.setdefault(row.department, []).append(candidate)

    for candidates in by_dept.values():
        candidates.sort(key=lambda c: c.confidence, reverse=True)
    return by_dept


async def _fallback_business_candidates(
    db: AsyncSession, tenant_id: uuid.UUID
) -> list[OwnerCandidate]:
    """No directory ever synced — active tenant users, flat low confidence."""
    result = await db.execute(
        select(User)
        .where(User.tenant_id == tenant_id, User.is_active.is_(True))
        .order_by(User.full_name)
        .limit(_MAX_CANDIDATES)
    )
    users = list(result.scalars().all())
    return [
        OwnerCandidate(
            user_id=u.id,
            display_name=u.full_name or u.email,
            email=u.email,
            confidence=40.0,
            rationale="No directory sync on file — active tenant user (fallback)",
        )
        for u in users
    ]


async def _it_owner_candidates(db: AsyncSession, tenant_id: uuid.UUID) -> list[OwnerCandidate]:
    result = await db.execute(
        select(User, UserRole.role)
        .join(UserRole, UserRole.user_id == User.id)
        .where(
            User.tenant_id == tenant_id,
            User.is_active.is_(True),
            UserRole.role.in_([Role.TENANT_ADMIN, Role.ORG_ADMIN]),
        )
    )
    rows = result.all()
    if not rows:
        return []

    directory_titles = await _directory_job_titles(db, tenant_id)

    best_by_user: dict[uuid.UUID, OwnerCandidate] = {}
    for user, role in rows:
        confidence = 80.0 if role == Role.TENANT_ADMIN else 65.0
        rationale_bits = [f"Role: {role.value}"]
        title = directory_titles.get(user.id)
        if title and any(keyword in title.lower() for keyword in _IT_TITLE_KEYWORDS):
            confidence = min(confidence + 10, 95.0)
            rationale_bits.append(f"IT-aligned title ({title})")

        candidate = OwnerCandidate(
            user_id=user.id,
            display_name=user.full_name or user.email,
            email=user.email,
            confidence=confidence,
            rationale="; ".join(rationale_bits),
        )
        existing = best_by_user.get(user.id)
        if existing is None or candidate.confidence > existing.confidence:
            best_by_user[user.id] = candidate

    candidates = list(best_by_user.values())
    candidates.sort(key=lambda c: c.confidence, reverse=True)
    return candidates


async def _directory_job_titles(db: AsyncSession, tenant_id: uuid.UUID) -> dict[uuid.UUID, str]:
    result = await db.execute(
        select(DirectoryUser.linked_user_id, DirectoryUser.job_title).where(
            DirectoryUser.tenant_id == tenant_id,
            DirectoryUser.linked_user_id.is_not(None),
            DirectoryUser.job_title.is_not(None),
        )
    )
    return {row[0]: row[1] for row in result.all() if row[1]}
