"""Consistent error models and exception handlers."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: dict[str, Any] | None = None
    correlation_id: str | None = None


class ErrorResponse(BaseModel):
    error: ErrorDetail


class AppException(Exception):
    """Base application exception with structured error info."""

    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = status.HTTP_400_BAD_REQUEST,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details
        super().__init__(message)


class NotFoundError(AppException):
    def __init__(self, resource: str, identifier: str | None = None) -> None:
        msg = f"{resource} not found"
        if identifier:
            msg += f": {identifier}"
        super().__init__(
            code="NOT_FOUND",
            message=msg,
            status_code=status.HTTP_404_NOT_FOUND,
        )


class ForbiddenError(AppException):
    def __init__(self, message: str = "Access denied") -> None:
        super().__init__(
            code="FORBIDDEN",
            message=message,
            status_code=status.HTTP_403_FORBIDDEN,
        )


class UnauthorizedError(AppException):
    def __init__(self, message: str = "Authentication required") -> None:
        super().__init__(
            code="UNAUTHORIZED",
            message=message,
            status_code=status.HTTP_401_UNAUTHORIZED,
        )


class ConflictError(AppException):
    def __init__(self, message: str) -> None:
        super().__init__(
            code="CONFLICT",
            message=message,
            status_code=status.HTTP_409_CONFLICT,
        )


class RateLimitError(AppException):
    def __init__(self) -> None:
        super().__init__(
            code="RATE_LIMITED",
            message="Too many requests",
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        )


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppException)
    async def app_exception_handler(request: Request, exc: AppException) -> JSONResponse:
        correlation_id = getattr(request.state, "correlation_id", None)
        return JSONResponse(
            status_code=exc.status_code,
            content=ErrorResponse(
                error=ErrorDetail(
                    code=exc.code,
                    message=exc.message,
                    details=exc.details,
                    correlation_id=correlation_id,
                )
            ).model_dump(),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        from app.config import get_settings

        correlation_id = getattr(request.state, "correlation_id", None)
        settings = get_settings()

        SENSITIVE_FIELDS = {
            "password",
            "current_password",
            "new_password",
            "token",
            "refresh_token",
            "api_key",
            "secret",
        }

        safe_errors = []
        for err in exc.errors():
            field_path = " -> ".join(str(loc) for loc in err.get("loc", []))
            safe_err = {
                "field": field_path,
                "message": err.get("msg", "Invalid value"),
                "type": err.get("type", "value_error"),
            }
            if not settings.is_production:
                field_name = str(err.get("loc", [""])[-1]).lower() if err.get("loc") else ""
                if field_name not in SENSITIVE_FIELDS:
                    safe_err["input"] = err.get("input")
            safe_errors.append(safe_err)

        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=ErrorResponse(
                error=ErrorDetail(
                    code="VALIDATION_ERROR",
                    message="Request validation failed",
                    details={"errors": safe_errors},
                    correlation_id=correlation_id,
                )
            ).model_dump(),
        )

    @app.exception_handler(Exception)
    async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        import traceback

        import structlog

        from app.config import get_settings

        logger = structlog.get_logger()
        correlation_id = getattr(request.state, "correlation_id", None)
        settings = get_settings()

        log_kwargs: dict[str, Any] = {
            "exc_type": type(exc).__name__,
            "correlation_id": correlation_id,
            "path": request.url.path,
            "method": request.method,
        }
        if not settings.is_production:
            log_kwargs["exc_message"] = str(exc)
            log_kwargs["traceback"] = traceback.format_exc()
        logger.error("unhandled_exception", **log_kwargs)

        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=ErrorResponse(
                error=ErrorDetail(
                    code="INTERNAL_ERROR",
                    message="An unexpected error occurred",
                    correlation_id=correlation_id,
                )
            ).model_dump(),
        )
