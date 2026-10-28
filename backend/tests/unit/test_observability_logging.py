"""Unit tests for the structlog setup in app.observability.

They run the real structlog processors, so a structlog bump that changes how
exceptions reach the JSON renderer fails here instead of only in production
logs.
"""

from __future__ import annotations

import asyncio
import gc
import json
import logging
import logging.config
import sys
import traceback

import asyncpg
import httpx
import pytest
import structlog
import uvicorn
import uvicorn.config
from asyncpg import exceptions as pg
from fastapi import FastAPI
from sqlalchemy import exc as sa_exc
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql.asyncpg import AsyncAdapt_asyncpg_dbapi
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.pool import NullPool

from app import database, observability
from app.config import Environment, get_settings
from tests.conftest import TEST_DATABASE_URL

pytestmark = [pytest.mark.unit]


_LOGGERS_TOUCHED = ("", "uvicorn", "uvicorn.error", "uvicorn.access")


@pytest.fixture
def restore_logging():
    """Put structlog, the stdlib loggers and the record factory back after each test."""
    saved = {
        name: (
            logging.getLogger(name).handlers[:],
            logging.getLogger(name).level,
            logging.getLogger(name).propagate,
        )
        for name in _LOGGERS_TOUCHED
    }
    saved_factory = logging.getLogRecordFactory()
    try:
        yield
    finally:
        structlog.reset_defaults()
        logging.setLogRecordFactory(saved_factory)
        for name, (handlers, level, propagate) in saved.items():
            logger = logging.getLogger(name)
            logger.handlers[:] = handlers
            logger.setLevel(level)
            logger.propagate = propagate


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


# ── Exception groups ──────────────────────────────────────────────────────


def _database_error() -> sa_exc.IntegrityError:
    try:
        _raise_as_sqlalchemy_does(_unique_violation(), "INSERT", {})
    except sa_exc.IntegrityError as error:
        return error
    raise AssertionError("unreachable")


def _log_exception(log, event: str, exc: BaseException) -> None:
    log.error(event, exc_info=exc)


def test_exception_group_with_a_database_error_logs_no_values(production_log):
    log, lines = production_log
    group = ExceptionGroup(
        "seat sync failed",
        [_database_error(), RuntimeError("seat 7 could not be saved")],
    )

    _log_exception(log, "seat_sync_failed", group)

    (line,) = lines()
    exception = line["exception"]
    assert EMAIL not in json.dumps(line)
    assert "sqlalchemy.exc.IntegrityError: [message withheld" in exception
    assert "asyncpg.exceptions.UniqueViolationError: [message withheld" in exception
    # The group's own message and its other members keep theirs.
    assert "ExceptionGroup: seat sync failed (2 sub-exceptions)" in exception
    assert "RuntimeError: seat 7 could not be saved" in exception


def test_nested_exception_groups_log_no_values(production_log):
    log, lines = production_log
    inner = ExceptionGroup("inner", [ValueError("plain"), _database_error()])
    outer = ExceptionGroup("outer", [inner, KeyError("k")])

    _log_exception(log, "nested", outer)

    (line,) = lines()
    assert EMAIL not in json.dumps(line)
    assert "ValueError: plain" in line["exception"]
    assert "ExceptionGroup: inner (2 sub-exceptions)" in line["exception"]


def test_a_database_error_under_a_group_members_cause_logs_no_values(production_log):
    log, lines = production_log
    try:
        raise RuntimeError("billing step failed") from _database_error()
    except RuntimeError as member:
        group = ExceptionGroup("steps failed", [member])

    _log_exception(log, "steps_failed", group)

    (line,) = lines()
    assert EMAIL not in json.dumps(line)
    assert "RuntimeError: billing step failed" in line["exception"]


def test_a_group_without_a_database_error_is_written_as_python_writes_it(production_log):
    log, lines = production_log
    try:
        raise ExceptionGroup("two", [ValueError("a"), TypeError("b")])
    except ExceptionGroup as group:
        expected = "".join(traceback.format_exception(group)).rstrip("\n")
        log.exception("plain_group")

    (line,) = lines()
    assert line["exception"] == expected


async def test_task_group_errors_log_no_values(production_log):
    log, lines = production_log

    async def failing() -> None:
        _raise_as_sqlalchemy_does(_unique_violation(), "INSERT", {})

    async def stopping() -> None:
        raise RuntimeError("billing sync stopped")

    # Caught whole, by a plain except...
    try:
        async with asyncio.TaskGroup() as group:
            group.create_task(failing())
            group.create_task(stopping())
    except ExceptionGroup:
        log.exception("whole_group")

    # ...and split by except*, which logs the part holding the database error.
    try:
        try:
            async with asyncio.TaskGroup() as group:
                group.create_task(failing())
                group.create_task(stopping())
        except* sa_exc.IntegrityError:
            log.exception("split_group")
    except* RuntimeError:
        pass

    whole, split = lines()
    for line in (whole, split):
        assert EMAIL not in json.dumps(line)
        assert "sqlalchemy.exc.IntegrityError: [message withheld" in line["exception"]
    assert "RuntimeError: billing sync stopped" in whole["exception"]


# ── Handlers other than ours ──────────────────────────────────────────────


def test_installing_the_filter_twice_wraps_the_record_factory_once(restore_logging):
    observability.setup_logging(_production_settings())
    first = logging.getLogRecordFactory()
    observability.setup_logging(_production_settings())

    assert logging.getLogRecordFactory() is first


def test_development_logging_leaves_the_record_factory_alone(restore_logging):
    before = logging.getLogRecordFactory()

    observability.setup_logging(
        get_settings().model_copy(update={"app_env": Environment.DEVELOPMENT})
    )

    assert logging.getLogRecordFactory() is before


def test_uvicorns_error_logger_writes_no_values(restore_logging, capfd):
    # Uvicorn logs an unhandled application error to "uvicorn.error", through its
    # own stderr handler, which the root handler never sees.
    logging.config.dictConfig(uvicorn.config.LOGGING_CONFIG)
    observability.setup_logging(_production_settings())

    try:
        raise _database_error()
    except sa_exc.IntegrityError:
        logging.getLogger("uvicorn.error").error(
            "Exception in ASGI application\n", exc_info=sys.exc_info()
        )

    err = capfd.readouterr().err
    assert "Exception in ASGI application" in err
    assert EMAIL not in err
    assert "sqlalchemy.exc.IntegrityError: [message withheld" in err


async def test_a_route_failing_under_uvicorn_logs_no_values(restore_logging, capfd):
    app = FastAPI()

    @app.get("/seats")
    async def seats() -> None:
        _raise_as_sqlalchemy_does(_unique_violation(), "INSERT", {})

    # Uvicorn sets its logging up when the Config is made; the app calls
    # setup_logging later, in its lifespan.
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=0,
        log_config=uvicorn.config.LOGGING_CONFIG,
        lifespan="off",
    )
    observability.setup_logging(_production_settings())
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    try:
        async with asyncio.timeout(20):
            while not server.started:
                await asyncio.sleep(0.05)
            port = server.servers[0].sockets[0].getsockname()[1]
            async with httpx.AsyncClient() as client:
                response = await client.get(f"http://127.0.0.1:{port}/seats")
        assert response.status_code == 500
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, timeout=20)

    err = capfd.readouterr().err
    assert "Exception in ASGI application" in err
    assert EMAIL not in err
    assert "sqlalchemy.exc.IntegrityError: [message withheld" in err


# ── Errors that repeat a database error's text ────────────────────────────


class _SeatBase(DeclarativeBase):
    pass


class _Seat(_SeatBase):
    __tablename__ = "seats"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner: Mapped[str] = mapped_column(unique=True)


async def test_a_pending_rollback_error_logs_no_values(production_log, tmp_path):
    # After a failed flush the session refuses further work until it is rolled
    # back, and says why: "Original exception was: ..." with the first error's
    # text, which quotes the statement's values.
    log, lines = production_log
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'seats.db'}")
    owner = EMAIL
    try:
        async with engine.begin() as conn:
            await conn.run_sync(_SeatBase.metadata.create_all)
        async with AsyncSession(engine) as session:
            session.add(_Seat(owner=owner))
            await session.commit()
            session.add(_Seat(owner=owner))
            try:
                await session.flush()
            except sa_exc.IntegrityError:
                pass  # handled, but the session is left without a rollback
            try:
                await session.execute(select(_Seat))
            except sa_exc.PendingRollbackError as error:
                repeated_text = str(error)
                log.exception("seat_lookup_failed")
            assert owner in repeated_text  # the error does repeat it, which is the point
    finally:
        await engine.dispose()

    (line,) = lines()
    assert owner not in json.dumps(line)
    assert "PendingRollbackError: [message withheld" in line["exception"]


async def test_asyncio_never_retrieved_task_logs_no_values(production_log, caplog):
    # asyncio prints the unretrieved task, and the task's repr ends with the
    # exception's repr, in the message as well as in exc_info.
    async def failing() -> None:
        _raise_as_sqlalchemy_does(_unique_violation(), "INSERT", {})

    task = asyncio.create_task(failing())
    await asyncio.sleep(0.01)
    assert task.done()
    del task
    gc.collect()

    records = [r for r in caplog.records if r.name == "asyncio"]
    assert records, "asyncio did not log the unretrieved task"
    for record in records:
        assert EMAIL not in record.getMessage()
        assert EMAIL not in (record.exc_text or "")
    assert any("Task exception was never retrieved" in r.getMessage() for r in records)
