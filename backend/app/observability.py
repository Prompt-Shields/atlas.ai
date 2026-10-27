"""OpenTelemetry + structured logging setup."""

from __future__ import annotations

import logging
import sqlite3
import sys
import traceback
from types import TracebackType

import structlog
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from sqlalchemy.exc import StatementError

from app.config import Settings

# Attributes of a database error that name what failed without quoting any value.
_SAFE_DB_ERROR_FIELDS = ("sqlstate", "constraint_name", "table_name")

# Drivers whose exception messages can quote row values. asyncpg repeats the
# server's message, whose DETAIL line or primary line can hold them ("Key
# (email)=(...) already exists", "invalid input syntax for type uuid: ...").
_DB_DRIVER_PACKAGES = frozenset({"asyncpg", "aiosqlite", "psycopg", "psycopg2"})

_ExcInfo = tuple[type[BaseException], BaseException, TracebackType | None]


def _exception_chain(exc: BaseException) -> list[BaseException]:
    """The exception and its causes, outermost first, as Python would print them."""
    chain: list[BaseException] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append(current)
        if current.__cause__ is not None:
            current = current.__cause__
        elif current.__context__ is not None and not current.__suppress_context__:
            current = current.__context__
        else:
            current = None
    return chain


def _is_database_error(exc: BaseException) -> bool:
    module = type(exc).__module__
    return (
        isinstance(exc, StatementError | sqlite3.Error)
        or module.split(".")[0] in _DB_DRIVER_PACKAGES
        # SQLAlchemy's async dialects re-raise driver errors as their own
        # DB-API classes, which repeat the driver's message.
        or module.startswith(("sqlalchemy.dialects.", "sqlalchemy.connectors."))
    )


def _withheld_errors(chain: list[BaseException]) -> set[int]:
    """The ids of the exceptions whose message must not be logged.

    That is the outermost database error and everything beneath it: a driver can
    raise a plain exception quoting the value as the cause (asyncpg's encoder
    raises "ValueError: invalid UUID '<value>'" under its DataError). Exceptions
    the application raised on top of a database error keep their message.
    """
    for position, exc in enumerate(chain):
        if _is_database_error(exc):
            return {id(older) for older in chain[position:]}
    return set()


def _withheld_line(exc: BaseException) -> str:
    cls = type(exc)
    fields = [
        f"{name}={value}"
        for name in _SAFE_DB_ERROR_FIELDS
        if isinstance(value := getattr(exc, name, None), str) and value
    ]
    detail = ", ".join(["message withheld", *fields])
    return f"{cls.__module__}.{cls.__qualname__}: [{detail}]\n"


def format_exception_without_db_values(exc_info: _ExcInfo) -> str:
    """Format a traceback, leaving out the messages of database errors.

    Database error messages can quote the values of the failing row (customer
    emails, billing fields), in the server's DETAIL line or in its primary line,
    and SQLAlchemy's hide_parameters only covers its own "[parameters: ...]"
    block. From the outermost database error down, each exception is written as
    its type, its frames and the SQLSTATE, constraint and table names it carries.
    A traceback with no database error is written exactly as Python would.
    """
    exc = exc_info[1]
    chain = _exception_chain(exc)
    withheld = _withheld_errors(chain)
    if not withheld:
        return "".join(traceback.format_exception(*exc_info)).rstrip("\n")

    parts: list[str] = []
    for position, current in enumerate(reversed(chain)):
        if position:
            # The exception printed just before this one is its cause or context.
            if current.__cause__ is chain[len(chain) - position]:
                parts.append(
                    "\nThe above exception was the direct cause of the following exception:\n\n"
                )
            else:
                parts.append(
                    "\nDuring handling of the above exception, another exception occurred:\n\n"
                )
        if current.__traceback__ is not None:
            parts.append("Traceback (most recent call last):\n")
            parts.extend(traceback.format_tb(current.__traceback__))
        if id(current) in withheld:
            parts.append(_withheld_line(current))
        else:
            parts.extend(traceback.format_exception_only(type(current), current))
    return "".join(parts).rstrip("\n")


_json_renderer = structlog.processors.JSONRenderer()


def _render_json_line(
    logger: structlog.types.WrappedLogger, name: str, event_dict: structlog.types.EventDict
) -> tuple[tuple[str], dict[str, bool]]:
    """Render the event as JSON, and pass it to the stdlib logger without exc_info.

    stdlib's Logger.exception sets exc_info on the record unless told otherwise,
    and the stdout handler would then print Python's own traceback after the
    JSON line, unfiltered and over several lines. The JSON line already holds
    the filtered traceback.
    """
    return (_json_renderer(logger, name, event_dict),), {"exc_info": False}


class _ProductionFormatter(logging.Formatter):
    """For records logged straight to stdlib logging by other libraries."""

    def formatException(self, ei) -> str:  # noqa: N802 (stdlib name)
        return format_exception_without_db_values(ei)


def setup_logging(settings: Settings) -> None:
    """Configure structlog with JSON output for production, console for dev."""
    processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    if settings.is_production:
        # JSONRenderer cannot serialise exc_info, so without this it writes a bare
        # "exc_info": true and the traceback is lost. This writes the plain-text
        # traceback: types, messages and frames, but no frame locals, and no
        # message of a database error (see format_exception_without_db_values).
        # dict_tracebacks keeps locals by default, which can hold tokens or
        # request bodies, so it is not used here.
        processors.append(
            structlog.processors.ExceptionRenderer(format_exception_without_db_values)
        )
        processors.append(_render_json_line)
    else:
        processors.append(structlog.dev.ConsoleRenderer())

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    handler = logging.StreamHandler(sys.stdout)
    if settings.is_production:
        handler.setFormatter(_ProductionFormatter("%(message)s"))
    else:
        handler.setFormatter(logging.Formatter("%(message)s"))
    logging.basicConfig(handlers=[handler], level=getattr(logging, settings.log_level))


def setup_telemetry(settings: Settings) -> None:
    """Configure OpenTelemetry tracing."""
    resource = Resource.create(
        {
            "service.name": settings.otel_service_name,
            "service.version": settings.app_version,
            "deployment.environment": settings.app_env.value,
        }
    )

    provider = TracerProvider(resource=resource)

    if settings.applicationinsights_connection_string:
        try:
            from azure.monitor.opentelemetry.exporter import AzureMonitorTraceExporter

            azure_exporter = AzureMonitorTraceExporter(
                connection_string=settings.applicationinsights_connection_string
            )
            provider.add_span_processor(BatchSpanProcessor(azure_exporter))
        except ImportError:
            pass

    if settings.app_debug:
        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
    else:
        # OTLP exporter for production (e.g., to collector sidecar)
        try:
            otlp_exporter = OTLPSpanExporter()
            provider.add_span_processor(BatchSpanProcessor(otlp_exporter))
        except Exception:
            pass

    trace.set_tracer_provider(provider)


def instrument_fastapi(app: object) -> None:
    """Instrument FastAPI with OpenTelemetry."""
    FastAPIInstrumentor.instrument_app(app)  # type: ignore[arg-type]
