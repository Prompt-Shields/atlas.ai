"""Database errors shaped the way SQLAlchemy's asyncpg dialect raises them.

No database is needed: the driver error, the dialect's DB-API error and the
``sqlalchemy.exc`` error are built and chained with ``raise ... from ...`` as
the dialect does. The row value sits in the server's DETAIL line, where
Postgres puts it.
"""

from __future__ import annotations

import asyncpg
from asyncpg import exceptions as pg
from sqlalchemy import exc as sa_exc
from sqlalchemy.dialects.postgresql.asyncpg import AsyncAdapt_asyncpg_dbapi

# A reserved example domain, passed by name so no traceback quotes it as source.
SECRET_VALUE = "alice@example.com"


def _wrap(driver_error: Exception, statement: str) -> sa_exc.DBAPIError:
    dbapi = AsyncAdapt_asyncpg_dbapi(asyncpg)
    try:
        try:
            raise driver_error
        except (pg.PostgresError, pg.InterfaceError) as error:
            translate = dbapi._asyncpg_error_translate
            dbapi_class = next(translate[c] for c in type(error).__mro__ if c in translate)
            adapted = dbapi_class(f"{type(error)}: {error}")
            adapted.pgcode = adapted.sqlstate = getattr(error, "sqlstate", None)
            raise adapted from error
    except dbapi.Error as error:
        try:
            raise sa_exc.DBAPIError.instance(statement, {}, error, dbapi.Error) from error
        except sa_exc.DBAPIError as wrapped:
            return wrapped
    raise AssertionError("unreachable")


def unique_violation(value: str = SECRET_VALUE) -> sa_exc.IntegrityError:
    """A unique-constraint IntegrityError whose message quotes ``value``."""
    driver_error = pg.UniqueViolationError.new(
        {
            "S": "ERROR",
            "C": "23505",
            "M": 'duplicate key value violates unique constraint "uq_users_email"',
            "D": f"Key (email)=({value}) already exists.",
            "n": "uq_users_email",
            "t": "users",
            "s": "public",
        }
    )
    error = _wrap(driver_error, "INSERT INTO users (email) VALUES ($1)")
    assert isinstance(error, sa_exc.IntegrityError)
    return error


def invalid_uuid(value: str = SECRET_VALUE) -> sa_exc.DBAPIError:
    """An error whose primary line, not only its DETAIL, quotes ``value``."""
    driver_error = pg.InvalidTextRepresentationError.new(
        {"S": "ERROR", "C": "22P02", "M": f'invalid input syntax for type uuid: "{value}"'}
    )
    return _wrap(driver_error, "SELECT * FROM users WHERE id = $1")
