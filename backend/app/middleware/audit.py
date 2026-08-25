"""Audit logging middleware — logs auth events and sensitive endpoint access."""

from __future__ import annotations

import time

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

logger = structlog.get_logger("audit")

# Paths that trigger audit logging
AUDIT_PATHS = {
    "/api/v1/auth/",
    "/api/v1/invites/",
    "/api/v1/users/",
    "/api/v1/tenants/",
    "/api/v1/admin/",
    "/api/v1/adapters/",
}


class AuditMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path
        should_audit = any(path.startswith(p) for p in AUDIT_PATHS)

        start = time.perf_counter()
        response = await call_next(request)
        duration_ms = (time.perf_counter() - start) * 1000

        if should_audit:
            user_id = getattr(request.state, "user_id", None)
            tenant_id = getattr(request.state, "tenant_id", None)
            correlation_id = getattr(request.state, "correlation_id", None)

            logger.info(
                "audit_event",
                method=request.method,
                path=path,
                status_code=response.status_code,
                duration_ms=round(duration_ms, 2),
                user_id=str(user_id) if user_id else None,
                tenant_id=str(tenant_id) if tenant_id else None,
                correlation_id=correlation_id,
                client_ip=request.client.host if request.client else None,
            )

        return response
