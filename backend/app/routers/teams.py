"""Admin teams router (PRO-57) — read APIs over synced Entra directory groups.

Backs the admin portal's teams view (PRO-58). Role-gated to admins and
tenant-scoped (super admins see all tenants). Manual (non-Entra) teams +
membership mutation are deferred — these are read endpoints over the directory
groups synced by the microsoft_sync worker.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import OrgAdmin
from app.database import get_db_session
from app.errors import NotFoundError
from app.models.directory import (
    DirectoryGroup,
    DirectoryGroupMembership,
    DirectoryUser,
)
from app.schemas.team import (
    TeamListResponse,
    TeamMemberListResponse,
    TeamMemberResponse,
    TeamResponse,
)

router = APIRouter(prefix="/teams", tags=["Teams"])


@router.get("", response_model=TeamListResponse)
async def list_teams(
    user: OrgAdmin,
    db: AsyncSession = Depends(get_db_session),
) -> TeamListResponse:
    """List the caller tenant's directory groups (teams)."""
    query = select(DirectoryGroup)
    if not user.is_super_admin():
        query = query.where(DirectoryGroup.tenant_id == user.tenant_id)

    groups = list((await db.execute(query.order_by(DirectoryGroup.display_name))).scalars().all())
    return TeamListResponse(
        teams=[
            TeamResponse(
                id=str(g.id),
                external_group_id=g.external_group_id,
                display_name=g.display_name,
                description=g.description,
                group_type=g.group_type,
                member_count=g.member_count,
            )
            for g in groups
        ],
        total=len(groups),
    )


@router.get("/{team_id}/members", response_model=TeamMemberListResponse)
async def list_team_members(
    team_id: str,
    user: OrgAdmin,
    db: AsyncSession = Depends(get_db_session),
) -> TeamMemberListResponse:
    """List the directory users who belong to a team."""
    gid = uuid.UUID(team_id)
    group_q = select(DirectoryGroup).where(DirectoryGroup.id == gid)
    if not user.is_super_admin():
        group_q = group_q.where(DirectoryGroup.tenant_id == user.tenant_id)
    group = (await db.execute(group_q)).scalar_one_or_none()
    if group is None:
        raise NotFoundError("Team", team_id)

    members = list(
        (
            await db.execute(
                select(DirectoryUser)
                .join(
                    DirectoryGroupMembership,
                    DirectoryGroupMembership.user_id == DirectoryUser.id,
                )
                .where(DirectoryGroupMembership.group_id == gid)
                .order_by(DirectoryUser.display_name)
            )
        )
        .scalars()
        .all()
    )
    return TeamMemberListResponse(
        members=[
            TeamMemberResponse(
                id=str(m.id),
                external_user_id=m.external_user_id,
                email=m.email,
                display_name=m.display_name,
                department=m.department,
                job_title=m.job_title,
                is_active=m.is_active,
            )
            for m in members
        ],
        total=len(members),
    )
