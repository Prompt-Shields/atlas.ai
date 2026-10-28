"""Error text that is safe to log or store.

A database error's message can quote the values of the failing row (the server's
DETAIL line, or the value in its primary line), and the text of a caught
exception is often put in a log field or a ``last_error`` column. Use
``safe_error_text`` for that text: a database error is reduced to its type and
the names that say what failed; any other exception keeps its normal message.
"""

from __future__ import annotations

import sqlite3

from sqlalchemy.exc import PendingRollbackError, StatementError

# Attributes of a database error that name what failed without quoting any value.
_SAFE_FIELDS = ("sqlstate", "constraint_name", "table_name")

# Drivers whose exception messages can quote row values.
_DRIVER_PACKAGES = frozenset({"asyncpg", "aiosqlite", "psycopg", "psycopg2"})

# How far down an error's cause chain to look for the names above.
_MAX_DEPTH = 6


def is_database_error(exc: BaseException) -> bool:
    """True for SQLAlchemy statement errors and for the driver errors under them."""
    module = type(exc).__module__
    return (
        # PendingRollbackError is not a StatementError, but its message embeds the
        # original failure's text ("Original exception was: ...").
        isinstance(exc, StatementError | PendingRollbackError | sqlite3.Error)
        or module.split(".")[0] in _DRIVER_PACKAGES
        # SQLAlchemy's async dialects re-raise driver errors as their own DB-API
        # classes, which repeat the driver's message.
        or module.startswith(("sqlalchemy.dialects.", "sqlalchemy.connectors."))
    )


def _related(exc: BaseException) -> list[BaseException]:
    """The error, SQLAlchemy's wrapped driver error, and the causes beneath them."""
    found: list[BaseException] = []
    pending: list[BaseException | None] = [exc]
    while pending and len(found) < _MAX_DEPTH:
        current = pending.pop(0)
        if current is None or any(current is seen for seen in found):
            continue
        found.append(current)
        pending.extend([getattr(current, "orig", None), current.__cause__])
    return found


def safe_error_text(exc: BaseException, *, with_type: bool = False) -> str:
    """The text of ``exc`` for a log field or a stored ``last_error``.

    A database error is written as its type, and the SQLSTATE, constraint and
    table names it carries when it has them:
    ``IntegrityError: [message withheld, sqlstate=..., constraint_name=...]``.
    Any other exception gives ``str(exc)``, or ``"Type: message"`` with
    ``with_type``, exactly as before.
    """
    if not is_database_error(exc):
        return f"{type(exc).__name__}: {exc}" if with_type else str(exc)

    details = ["message withheld"]
    related = _related(exc)
    for name in _SAFE_FIELDS:
        for candidate in related:
            value = getattr(candidate, name, None)
            if isinstance(value, str) and value:
                details.append(f"{name}={value}")
                break
    return f"{type(exc).__name__}: [{', '.join(details)}]"
