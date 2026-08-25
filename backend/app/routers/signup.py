"""Signup router — POST /api/v1/signup."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.rate_limit import check_rate_limit
from app.database import get_db_session
from app.schemas.signup import SignupRequest, SignupResponse
from app.services import signup_service

router = APIRouter(prefix="/signup", tags=["Signup"])


@router.post("", response_model=SignupResponse, status_code=201)
async def signup(
    body: SignupRequest,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> SignupResponse:
    client_ip = request.client.host if request.client else "unknown"
    await check_rate_limit(f"signup:{client_ip}", max_attempts=5, window_seconds=300)
    result = await signup_service.signup(
        db,
        email=body.email,
        password=body.password,
        tenant_name=body.tenant_name,
        tenant_slug=body.tenant_slug,
        full_name=body.full_name,
    )
    return SignupResponse(**result)
