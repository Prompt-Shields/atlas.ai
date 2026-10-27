"""Unit tests for the structlog setup in app.observability.

They run the real structlog processors, so a structlog bump that changes how
exceptions reach the JSON renderer fails here instead of only in production
logs.
"""

from __future__ import annotations

import json
import logging

import asyncpg
import pytest
import structlog
from asyncpg import exceptions as pg
from sqlalchemy import exc as sa_exc
from sqlalchemy import text
from sqlalchemy.dialects.postgresql.asyncpg import AsyncAdapt_asyncpg_dbapi
from sqlalchemy.pool import NullPool

from app import database, observability
from app.config import Environment, get_settings
from tests.conftest import TEST_DATABASE_URL

pytestmark = [pytest.mark.unit]


@pytest.fixture
def restore_logging():
    """Put structlog and the root logger back as they were after each test."""
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level
    try:
        yield
    finally:
        structlog.reset_defaults()
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)


def _raise_with_a_secret_local() -> None:
    api_token = "sk-local-secret-should-not-be-logged"  # noqa: F841, S105
    raise ValueError("ledger row 42 is missing")


def _json_lines(caplog: pytest.LogCaptureFixture) -> list[dict]:
    return [json.loads(record.getMessage()) for record in caplog.records]


@pytest.fixture
def production_log(restore_logging, caplog):
    """A logger configured exactly as in production, and its JSON lines."""
    observability.setup_logging(_production_settings())
    caplog.set_level(logging.INFO)
    return structlog.get_logger("test.observability"), lambda: _json_lines(caplog)


def _production_settings(**update):
    return get_settings().model_copy(
        update={"app_env": Environment.PRODUCTION, "log_level": "INFO", **update}
    )


def test_json_logging_writes_the_traceback(restore_logging, caplog):
    settings = _production_settings()
    observability.setup_logging(settings)
    caplog.set_level(logging.INFO)

    log = structlog.get_logger("test.observability")
    try:
        _raise_with_a_secret_local()
    except ValueError:
        log.exception("ledger_import_failed")

    (line,) = _json_lines(caplog)
    assert line["event"] == "ledger_import_failed"
    assert "exc_info" not in line
    assert "ValueError: ledger row 42 is missing" in line["exception"]
    assert "Traceback (most recent call last)" in line["exception"]
    # Frame locals stay out of the log; only the exception message is written.
    assert "sk-local-secret" not in json.dumps(line)


# A database error's message can quote the failing row. The values below must
# never reach a production log line; they are passed by name so that no source
# line quoted in a traceback holds them either.
EMAIL = "alice@example.com"
ROW_NAME = "Alice Example"
BAD_UUID = "alice-not-a-uuid"


def _raise_as_sqlalchemy_does(
    driver_error: Exception,
    statement: str,
    params: dict,
    driver_cause: Exception | None = None,
):
    """Raise a driver error the way SQLAlchemy's asyncpg dialect re-raises it.

    asyncpg's error (raised from driver_cause, if given) becomes the dialect's
    DB-API error, whose message repeats it, which becomes a sqlalchemy.exc
    error, each raised from the one before.
    """
    dbapi = AsyncAdapt_asyncpg_dbapi(asyncpg)
    try:
        try:
            raise driver_error from driver_cause
        except (pg.PostgresError, pg.InterfaceError) as error:
            translate = dbapi._asyncpg_error_translate
            dbapi_class = next(translate[c] for c in type(error).__mro__ if c in translate)
            adapted = dbapi_class(f"{type(error)}: {error}")
            adapted.pgcode = adapted.sqlstate = getattr(error, "sqlstate", None)
            raise adapted from error
    except dbapi.Error as error:
        # hide_parameters is off here on purpose: the log must hold without it.
        raise sa_exc.DBAPIError.instance(statement, params, error, dbapi.Error) from error


def test_unique_violation_logs_no_row_values(production_log):
    log, lines = production_log
    driver_error = pg.UniqueViolationError.new(
        {
            "S": "ERROR",
            "C": "23505",
            "M": 'duplicate key value violates unique constraint "uq_users_email"',
            "D": f"Key (email)=({EMAIL}) already exists.",
            "n": "uq_users_email",
            "t": "users",
            "s": "public",
        }
    )
    assert EMAIL in str(driver_error)  # the shape asyncpg gives

    try:
        _raise_as_sqlalchemy_does(
            driver_error, "INSERT INTO users (email) VALUES ($1)", {"email": EMAIL}
        )
    except sa_exc.IntegrityError:
        log.exception("seat_sync_failed")

    (line,) = lines()
    exception = line["exception"]
    assert EMAIL not in json.dumps(line)
    assert "DETAIL" not in exception
    assert "sqlalchemy.exc.IntegrityError: [message withheld" in exception
    assert "asyncpg.exceptions.UniqueViolationError: [message withheld" in exception
    assert "sqlstate=23505" in exception
    assert "constraint_name=uq_users_email" in exception
    assert "table_name=users" in exception
    assert exception.count("Traceback (most recent call last)") == 3
    assert "The above exception was the direct cause" in exception


def test_check_violation_logs_no_row_values(production_log):
    log, lines = production_log
    driver_error = pg.CheckViolationError.new(
        {
            "S": "ERROR",
            "C": "23514",
            "M": 'new row for relation "users" violates check constraint "ck_users_name"',
            "D": f"Failing row contains ({EMAIL}, {ROW_NAME}).",
            "n": "ck_users_name",
            "t": "users",
        }
    )

    try:
        _raise_as_sqlalchemy_does(driver_error, "INSERT INTO users VALUES ($1, $2)", {})
    except sa_exc.IntegrityError:
        log.exception("seat_outbox_failed")

    dumped = json.dumps(lines()[0])
    assert EMAIL not in dumped
    assert ROW_NAME not in dumped
    assert "sqlstate=23514" in dumped


def test_invalid_uuid_logs_no_value_from_the_primary_line(production_log):
    # Postgres can quote the value in the message itself, not only in DETAIL.
    log, lines = production_log
    driver_error = pg.InvalidTextRepresentationError.new(
        {
            "S": "ERROR",
            "C": "22P02",
            "M": f'invalid input syntax for type uuid: "{BAD_UUID}"',
        }
    )

    try:
        _raise_as_sqlalchemy_does(driver_error, "SELECT * FROM users WHERE id = $1", {})
    except sa_exc.DBAPIError:
        log.exception("step_overage_failed")

    (line,) = lines()
    assert BAD_UUID not in json.dumps(line)
    assert "asyncpg.exceptions.InvalidTextRepresentationError" in line["exception"]
    assert "sqlstate=22P02" in line["exception"]


def test_a_driver_side_encoding_error_logs_no_value(production_log):
    # asyncpg checks some values before sending them, and the plain ValueError
    # it raises underneath its own DataError quotes the value.
    log, lines = production_log
    encoder_error = ValueError(f"invalid UUID '{BAD_UUID}': length must be 32..36")
    driver_error = pg.DataError(f"invalid input for query argument $1: '{BAD_UUID}'")

    try:
        _raise_as_sqlalchemy_does(driver_error, "SELECT $1::uuid", {}, encoder_error)
    except sa_exc.DBAPIError:
        log.exception("seat_sync_failed")

    (line,) = lines()
    assert BAD_UUID not in json.dumps(line)
    assert "ValueError: [message withheld]" in line["exception"]
    assert "asyncpg.exceptions.DataError: [message withheld" in line["exception"]


def test_an_application_error_raised_from_a_database_error_keeps_its_message(
    production_log,
):
    log, lines = production_log
    driver_error = pg.UniqueViolationError.new(
        {"S": "ERROR", "C": "23505", "M": "duplicate key", "D": f"Key (email)=({EMAIL})"}
    )

    try:
        try:
            _raise_as_sqlalchemy_does(driver_error, "INSERT", {})
        except sa_exc.IntegrityError as error:
            raise RuntimeError("seat sync could not save the seat") from error
    except RuntimeError:
        log.exception("seat_sync_failed")

    exception = lines()[0]["exception"]
    assert EMAIL not in exception
    assert exception.endswith("RuntimeError: seat sync could not save the seat")


async def test_database_errors_from_the_production_engine_log_no_values(production_log):
    # A real round trip on the test database: Postgres in CI, SQLite locally.
    log, lines = production_log
    settings = _production_settings(database_url=TEST_DATABASE_URL)
    engine = database.create_async_engine(
        settings.database_url, poolclass=NullPool, hide_parameters=settings.is_production
    )
    assert engine.sync_engine.hide_parameters

    insert = text("INSERT INTO log_probe (email) VALUES (:email)")
    row = {"email": EMAIL}
    try:
        async with engine.connect() as conn:
            await conn.execute(text("CREATE TEMPORARY TABLE log_probe (email TEXT UNIQUE)"))
            await conn.execute(insert, row)
            try:
                await conn.execute(insert, row)
            except sa_exc.IntegrityError:
                log.exception("seat_sync_failed")
            await conn.rollback()
    finally:
        await engine.dispose()

    (line,) = lines()
    assert EMAIL not in json.dumps(line)
    assert "IntegrityError: [message withheld" in line["exception"]


def test_production_engine_hides_parameters(monkeypatch):
    settings = _production_settings()
    monkeypatch.setattr(database, "get_settings", lambda: settings)
    monkeypatch.setattr(database, "_engine", None)
    captured = {}
    monkeypatch.setattr(
        database, "create_async_engine", lambda url, **kwargs: captured.update(kwargs)
    )

    database.get_engine()

    assert captured["hide_parameters"] is True


def _unique_violation() -> pg.UniqueViolationError:
    return pg.UniqueViolationError.new(
        {"S": "ERROR", "C": "23505", "M": "duplicate key", "D": f"Key (email)=({EMAIL})"}
    )


def test_stdout_gets_one_json_line_per_exception(restore_logging, capsys):
    # What production actually writes: the stdout handler, not caplog's copy.
    logging.getLogger().handlers.clear()
    observability.setup_logging(_production_settings())

    try:
        _raise_as_sqlalchemy_does(_unique_violation(), "INSERT", {})
    except sa_exc.IntegrityError:
        structlog.get_logger("test.observability").exception("seat_sync_failed")

    out = capsys.readouterr().out
    assert EMAIL not in out
    (line,) = out.splitlines()  # no second, unfiltered traceback after the JSON
    assert "IntegrityError: [message withheld" in json.loads(line)["exception"]


def test_stdout_filters_exceptions_logged_by_other_libraries(restore_logging, capsys):
    logging.getLogger().handlers.clear()
    observability.setup_logging(_production_settings())

    try:
        _raise_as_sqlalchemy_does(_unique_violation(), "INSERT", {})
    except sa_exc.IntegrityError:
        logging.getLogger("some.library").exception("request failed")

    out = capsys.readouterr().out
    assert EMAIL not in out
    assert out.startswith("request failed\nTraceback (most recent call last)")
    assert "sqlalchemy.exc.IntegrityError: [message withheld" in out
