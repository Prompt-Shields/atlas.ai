"""Response schemas for `GET /use-cases/owner-suggestions` (#244).

Read-only: accepting a suggestion is a plain `PATCH /use-cases/{id}`
with `{"owner_user_id": ...}` via the existing `UseCaseUpdate` schema.
"""

from __future__ import annotations

from pydantic import BaseModel


class OwnerCandidateResponse(BaseModel):
    user_id: str
    display_name: str
    email: str | None
    confidence: float
    rationale: str


class OwnerSuggestionResponse(BaseModel):
    use_case_id: str
    use_case_title: str
    department: str
    business_owner_candidates: list[OwnerCandidateResponse]
    it_owner_candidates: list[OwnerCandidateResponse]


class OwnerSuggestionsListResponse(BaseModel):
    suggestions: list[OwnerSuggestionResponse]
    total_unowned: int
