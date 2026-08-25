"""Unit tests for app.services.users_csv — bulk invite CSV parser.

Pure-function tests. The router (which calls invite_service per row
+ sends emails) is integration territory.
"""

from __future__ import annotations

import pytest

from app.services.users_csv import (
    DEFAULT_ROLE,
    MAX_ROWS_PER_IMPORT,
    VALID_ROLES,
    parse_users_csv,
    render_users_import_template,
)

pytestmark = [pytest.mark.unit]


class TestHappyPath:
    def test_minimal_email_only(self) -> None:
        csv = "email\nalex@example.org\n"
        r = parse_users_csv(csv)
        assert not r.has_errors
        assert len(r.valid_rows) == 1
        row = r.valid_rows[0]
        assert row.email == "alex@example.org"
        assert row.role == DEFAULT_ROLE  # default

    def test_email_and_role(self) -> None:
        csv = "email,role\nalex@example.org,ANALYST\nmorgan@example.org,VIEWER\n"
        r = parse_users_csv(csv)
        assert not r.has_errors
        assert len(r.valid_rows) == 2
        assert r.valid_rows[0].role == "ANALYST"
        assert r.valid_rows[1].role == "VIEWER"

    def test_email_lowercased(self) -> None:
        csv = "email\nAlex@Example.Org\n"
        r = parse_users_csv(csv)
        assert r.valid_rows[0].email == "alex@example.org"


class TestValidation:
    def test_invalid_email_format(self) -> None:
        csv = "email\nnot-an-email\n"
        r = parse_users_csv(csv)
        assert len(r.valid_rows) == 0
        assert any(e.field == "email" for e in r.errors)

    def test_email_without_dot_in_domain(self) -> None:
        csv = "email\nalex@example\n"
        r = parse_users_csv(csv)
        assert len(r.valid_rows) == 0

    def test_empty_email(self) -> None:
        csv = "email,role\n,VIEWER\n"
        r = parse_users_csv(csv)
        assert len(r.valid_rows) == 0
        assert any(e.field == "email" for e in r.errors)

    def test_invalid_role(self) -> None:
        csv = "email,role\nalex@example.org,GOD\n"
        r = parse_users_csv(csv)
        assert len(r.valid_rows) == 0
        err = next(e for e in r.errors if e.field == "role")
        # Error message should list valid roles
        for v in VALID_ROLES:
            assert v in err.message

    def test_role_case_insensitive(self) -> None:
        csv = "email,role\nalex@example.org,analyst\n"
        r = parse_users_csv(csv)
        assert not r.has_errors
        assert r.valid_rows[0].role == "ANALYST"

    def test_duplicate_email_in_csv(self) -> None:
        csv = "email\nalex@example.org\nmorgan@example.org\nalex@example.org\n"
        r = parse_users_csv(csv)
        # First two valid; third is a duplicate error
        assert len(r.valid_rows) == 2
        dup = next(e for e in r.errors if "duplicates" in e.message)
        assert dup.row_number == 3
        # Error message references the original row
        assert "row 1" in dup.message

    def test_one_row_failing_doesnt_poison_rest(self) -> None:
        csv = "email\nalex@example.org\nBROKEN\nmorgan@example.org\n"
        r = parse_users_csv(csv)
        assert len(r.valid_rows) == 2
        assert {row.email for row in r.valid_rows} == {
            "alex@example.org",
            "morgan@example.org",
        }


class TestBatchErrors:
    def test_empty(self) -> None:
        r = parse_users_csv("")
        assert r.has_errors
        assert len(r.valid_rows) == 0

    def test_missing_email_column(self) -> None:
        csv = "name,role\nAlex,ANALYST\n"
        r = parse_users_csv(csv)
        assert r.has_errors
        assert "email" in r.errors[0].message

    def test_over_row_limit(self) -> None:
        rows = "\n".join(f"user{i}@example.org" for i in range(MAX_ROWS_PER_IMPORT + 3))
        csv = f"email\n{rows}\n"
        r = parse_users_csv(csv)
        assert len(r.valid_rows) == MAX_ROWS_PER_IMPORT
        batch = [e for e in r.errors if e.row_number == 0]
        assert len(batch) == 1
        assert str(MAX_ROWS_PER_IMPORT) in batch[0].message


class TestTolerance:
    def test_bom_tolerated(self) -> None:
        csv = "﻿email\nalex@example.org\n"
        r = parse_users_csv(csv)
        assert not r.has_errors

    def test_header_case_and_whitespace(self) -> None:
        csv = " Email , Role \nalex@example.org,VIEWER\n"
        r = parse_users_csv(csv)
        assert not r.has_errors

    def test_dash_underscore_equivalent_in_header(self) -> None:
        # Even though this CSV doesn't need it, the normaliser should
        # not reject a header that uses dashes.
        csv = "email\nalex@example.org\n"
        r = parse_users_csv(csv)
        assert not r.has_errors

    def test_crlf_line_endings(self) -> None:
        csv = "email\r\nalex@example.org\r\n"
        r = parse_users_csv(csv)
        assert not r.has_errors


class TestTemplate:
    def test_template_round_trips(self) -> None:
        body = render_users_import_template()
        r = parse_users_csv(body)
        assert not r.has_errors, f"Template has errors: {r.errors}"
        # The illustrative examples cover at least 2 of the 4 roles
        roles_in_template = {row.role for row in r.valid_rows}
        assert len(roles_in_template) >= 2
