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
from sqlalchemy.exc import PendingRollbackError, StatementError

from app.config import Settings

# Attributes of a database error that name what failed without quoting any value.
_SAFE_DB_ERROR_FIELDS = ("sqlstate", "constraint_name", "table_name")

# Drivers whose exception messages can quote row values. asyncpg repeats the
# server's message, whose DETAIL line or primary line can hold them ("Key
# (email)=(...) already exists", "invalid input syntax for type uuid: ...").
_DB_DRIVER_PACKAGES = frozenset({"asyncpg", "aiosqlite", "psycopg", "psycopg2"})

_ExcInfo = tuple[type[BaseException], BaseException, TracebackType | None]


def _linked(exc: BaseException) -> list[BaseException]:
    """What Python prints around an exception: its cause or context, and a group's members."""
    linked: list[BaseException] = []
    if exc.__cause__ is not None:
        linked.append(exc.__cause__)
    elif exc.__context__ is not None and not exc.__suppress_context__:
        linked.append(exc.__context__)
    if isinstance(exc, BaseExceptionGroup):
        linked.extend(exc.exceptions)
    return linked


def _is_database_error(exc: BaseException) -> bool:
    module = type(exc).__module__
    return (
        # PendingRollbackError is not a StatementError, but its message embeds the
        # original failure's text ("Original exception was: ...").
        isinstance(exc, StatementError | PendingRollbackError | sqlite3.Error)
        or module.split(".")[0] in _DB_DRIVER_PACKAGES
        # SQLAlchemy's async dialects re-raise driver errors as their own
        # DB-API classes, which repeat the driver's message.
        or module.startswith(("sqlalchemy.dialects.", "sqlalchemy.connectors."))
    )


def _withheld_exceptions(exc: BaseException) -> list[BaseException]:
    """The exceptions whose message must not be logged.

    That is every database error and everything beneath it, through causes,
    contexts and the members of exception groups: a driver can raise a plain
    exception quoting the value as the cause (asyncpg's encoder raises
    "ValueError: invalid UUID '<value>'" under its DataError). Exceptions the
    application raised on top of a database error, and groups that hold one,
    keep their message.
    """
    withheld: dict[int, BaseException] = {}
    visited: set[tuple[int, bool]] = set()

    def walk(current: BaseException, below_database_error: bool) -> None:
        below = below_database_error or _is_database_error(current)
        if (id(current), below) in visited:
            return
        visited.add((id(current), below))
        if below:
            withheld[id(current)] = current
        for linked in _linked(current):
            walk(linked, below)

    walk(exc, False)
    return list(withheld.values())


def _withheld_errors(exc: BaseException) -> set[int]:
    return {id(withheld) for withheld in _withheld_exceptions(exc)}


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
    block. Each database error, and everything it was raised from, is written as
    its type, its frames and the SQLSTATE, constraint and table names it carries.
    This follows causes, contexts and the members of exception groups. A
    traceback with no database error is written exactly as Python would.
    """
    withheld = _withheld_errors(exc_info[1])
    if not withheld:
        return "".join(traceback.format_exception(*exc_info)).rstrip("\n")
    return _render(exc_info[1], withheld, set()).rstrip("\n")


def _render(exc: BaseException, withheld: set[int], printed: set[int]) -> str:
    """Python's layout: the cause or context first, then this exception, then a group's members."""
    printed.add(id(exc))
    parts: list[str] = []
    older = exc.__cause__
    separator = "\nThe above exception was the direct cause of the following exception:\n\n"
    if older is None and not exc.__suppress_context__:
        older = exc.__context__
        separator = "\nDuring handling of the above exception, another exception occurred:\n\n"
    if older is not None and id(older) not in printed:
        parts.extend([_render(older, withheld, printed), separator])

    if exc.__traceback__ is not None:
        parts.append("Traceback (most recent call last):\n")
        parts.extend(traceback.format_tb(exc.__traceback__))
    if id(exc) in withheld:
        parts.append(_withheld_line(exc))
    else:
        parts.extend(traceback.format_exception_only(type(exc), exc))

    if isinstance(exc, BaseExceptionGroup):
        for number, member in enumerate(exc.exceptions, start=1):
            if id(member) in printed:
                continue
            body = _render(member, withheld, printed).rstrip("\n")
            indented = "\n".join(f"  | {line}" if line else "  |" for line in body.split("\n"))
            parts.append(f"  +---------------- {number} ----------------\n{indented}\n")
    return "".join(parts)


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


_FACTORY_MARK = "_filters_database_error_text"


# Shorter texts are left alone: replacing them would mangle unrelated words.
_MIN_SCRUBBED_LENGTH = 6


def _scrub_message(record: logging.LogRecord, exc: BaseException) -> None:
    """Remove a withheld exception's text from the record's own message.

    Some loggers put the exception in the message as well as in exc_info: asyncio's
    "Task exception was never retrieved" prints the task, whose repr ends with
    the exception's repr. The message is replaced by its formatted text, so this
    only touches records that carry a database error.
    """
    withheld = _withheld_exceptions(exc)
    if not withheld:
        return
    texts: set[str] = set()
    for error in withheld:
        texts.update({repr(error), str(error), *(a for a in error.args if isinstance(a, str))})
    message = record.getMessage()
    for text in sorted(texts, key=len, reverse=True):
        if len(text) >= _MIN_SCRUBBED_LENGTH:
            message = message.replace(text, "[message withheld]")
    record.msg, record.args = message, None


def _filter_logged_exceptions() -> None:
    """Give every log record the filtered traceback, whichever handler prints it.

    A Formatter uses a record's exc_text when it is set, so setting it when the
    record is made covers all handlers: the stdout handler, uvicorn's own
    handlers (its "Exception in ASGI application" error), and any a library
    adds later. Calling this twice installs it once.
    """
    current = logging.getLogRecordFactory()
    if getattr(current, _FACTORY_MARK, False):
        return

    def factory(*args: object, **kwargs: object) -> logging.LogRecord:
        record = current(*args, **kwargs)
        exc_info = record.exc_info
        if isinstance(exc_info, tuple) and exc_info[1] is not None:
            try:
                record.exc_text = format_exception_without_db_values(exc_info)
                _scrub_message(record, exc_info[1])
            except Exception:
                # Never let the log call fail, and never fall back to the raw text.
                record.exc_text = f"{type(exc_info[1]).__qualname__}: [message withheld]"
        return record

    setattr(factory, _FACTORY_MARK, True)
    logging.setLogRecordFactory(factory)


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

    if settings.is_production:
        _filter_logged_exceptions()
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, settings.log_level),
    )


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
