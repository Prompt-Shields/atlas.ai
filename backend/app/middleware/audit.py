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


def _is_audited(path: str) -> bool:
    # Each prefix also covers its bare collection path ("/api/v1/users"), but
    # not a longer name that merely starts the same ("/api/v1/usersX").
    return any(path.startswith(p) or path == p.rstrip("/") for p in AUDIT_PATHS)


class AuditMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path
        should_audit = _is_audited(path)

        start = time.perf_counter()
        # A request that raises is still audited, as a 500, before the error
        # carries on to the server error handler.
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
        finally:
            if should_audit:
                self._log(request, path, status_code, start)

        return response

    @staticmethod
    def _log(request: Request, path: str, status_code: int, start: float) -> None:
        duration_ms = (time.perf_counter() - start) * 1000
        user_id = getattr(request.state, "user_id", None)
        tenant_id = getattr(request.state, "tenant_id", None)
        correlation_id = getattr(request.state, "correlation_id", None)

        logger.info(
            "audit_event",
            method=request.method,
            path=path,
            status_code=status_code,
            duration_ms=round(duration_ms, 2),
            user_id=str(user_id) if user_id else None,
            tenant_id=str(tenant_id) if tenant_id else None,
            correlation_id=correlation_id,
            client_ip=request.client.host if request.client else None,
        )
