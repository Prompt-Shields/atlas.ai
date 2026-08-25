"""Team (directory group) schemas for the admin portal (PRO-57)."""

from __future__ import annotations

from pydantic import BaseModel


class TeamResponse(BaseModel):
    id: str
    external_group_id: str
    display_name: str
    description: str | None
    group_type: str | None
    member_count: int


class TeamListResponse(BaseModel):
    teams: list[TeamResponse]
    total: int


class TeamMemberResponse(BaseModel):
    id: str
    external_user_id: str
    email: str | None
    display_name: str
    department: str | None
    job_title: str | None
    is_active: bool


class TeamMemberListResponse(BaseModel):
    members: list[TeamMemberResponse]
    total: int
