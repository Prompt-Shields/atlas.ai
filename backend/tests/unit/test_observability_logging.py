"""Unit tests for the structlog setup in app.observability.

They run the real structlog processors, so a structlog bump that changes how
exceptions reach the JSON renderer fails here instead of only in production
logs.
"""

from __future__ import annotations

import json
import logging

import pytest
import structlog
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app import database, observability
from app.config import Environment, get_settings

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


async def test_production_database_errors_log_no_bound_values(
    restore_logging, caplog, monkeypatch, tmp_path
):
    # A database error's message lists the statement's bound values, and its
    # traceback is now logged. The production engine must hide them.
    settings = _production_settings(database_url=f"sqlite+aiosqlite:///{tmp_path / 'p.db'}")
    monkeypatch.setattr(database, "get_settings", lambda: settings)
    monkeypatch.setattr(database, "_engine", None)
    observability.setup_logging(settings)
    caplog.set_level(logging.INFO)

    engine = database.get_engine()
    log = structlog.get_logger("test.observability")
    try:
        async with engine.begin() as conn:
            await conn.execute(text("CREATE TABLE people (email TEXT UNIQUE)"))
        insert = text("INSERT INTO people (email) VALUES (:email)")
        # Passed by name: a traceback quotes the failing source line, so a
        # literal there would show up whatever the engine hides.
        row = {"email": "alice@example.com"}
        async with engine.begin() as conn:
            await conn.execute(insert, row)
        try:
            async with engine.begin() as conn:
                await conn.execute(insert, row)
        except IntegrityError:
            log.exception("seat_sync_failed")
    finally:
        await engine.dispose()

    (line,) = _json_lines(caplog)
    assert "IntegrityError" in line["exception"]
    assert "parameters hidden" in line["exception"]
    assert "alice@example.com" not in json.dumps(line)
