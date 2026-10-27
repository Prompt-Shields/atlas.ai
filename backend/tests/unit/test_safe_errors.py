"""Unit tests for app.safe_errors: error text that is safe to log or store."""

from __future__ import annotations

import sqlite3

import pytest

from app.safe_errors import is_database_error, safe_error_text
from tests.db_errors import SECRET_VALUE, invalid_uuid, unique_violation

pytestmark = [pytest.mark.unit]


def test_a_unique_violation_keeps_its_type_and_names_but_not_the_value():
    error = unique_violation()
    assert SECRET_VALUE in str(error)  # the shape the driver gives

    text = safe_error_text(error)

    assert SECRET_VALUE not in text
    assert text == (
        "IntegrityError: [message withheld, sqlstate=23505, "
        "constraint_name=uq_users_email, table_name=users]"
    )


def test_a_value_in_the_primary_line_is_withheld_too():
    text = safe_error_text(invalid_uuid())

    assert SECRET_VALUE not in text
    assert text.startswith("DBAPIError: [message withheld")
    assert "sqlstate=22P02" in text


def test_the_driver_error_alone_is_withheld():
    driver_error = unique_violation().__cause__.__cause__
    assert SECRET_VALUE in str(driver_error)

    text = safe_error_text(driver_error)

    assert SECRET_VALUE not in text
    assert text.startswith("UniqueViolationError: [message withheld, sqlstate=23505")


def test_with_type_makes_no_difference_for_a_database_error():
    error = unique_violation()

    assert safe_error_text(error, with_type=True) == safe_error_text(error)


def test_a_sqlite_error_is_withheld():
    error = sqlite3.IntegrityError(f"UNIQUE constraint failed: users.email ({SECRET_VALUE})")

    text = safe_error_text(error)

    assert SECRET_VALUE not in text
    assert text == "IntegrityError: [message withheld]"


@pytest.mark.parametrize(
    ("exc", "plain", "typed"),
    [
        (ValueError("bad input"), "bad input", "ValueError: bad input"),
        (RuntimeError(), "", "RuntimeError: "),
        (KeyError("k"), "'k'", "KeyError: 'k'"),
    ],
)
def test_any_other_error_keeps_its_normal_text(exc, plain, typed):
    assert safe_error_text(exc) == plain
    assert safe_error_text(exc, with_type=True) == typed


def test_an_application_error_raised_from_a_database_error_keeps_its_own_message():
    try:
        raise RuntimeError("seat could not be saved") from unique_violation()
    except RuntimeError as error:
        text = safe_error_text(error)

    assert text == "seat could not be saved"


def test_is_database_error():
    assert is_database_error(unique_violation())
    assert is_database_error(unique_violation().__cause__)
    assert is_database_error(sqlite3.OperationalError("locked"))
    assert not is_database_error(ValueError("x"))
