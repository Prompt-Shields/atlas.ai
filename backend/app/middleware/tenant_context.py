"""Tenant context middleware - extracts tenant info from JWT and sets on request state."""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response


class TenantContextMiddleware(BaseHTTPMiddleware):
    """Extracts tenant_id and org_id from request state (set by auth dependencies)
    and makes them available for downstream logging and data access."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # These will be populated by auth dependencies if the route requires auth
        if not hasattr(request.state, "tenant_id"):
            request.state.tenant_id = None
        if not hasattr(request.state, "org_id"):
            request.state.org_id = None
        if not hasattr(request.state, "user_id"):
            request.state.user_id = None

        return await call_next(request)
