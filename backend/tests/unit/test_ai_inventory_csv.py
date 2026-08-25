"""Unit tests for app.services.ai_inventory_csv.

Pure-function tests on the CSV parser + template generator. The
router (DB-write side) is integration territory — this file owns the
validation logic.

Coverage priorities, in order:
  1. Happy path: well-formed CSV → ParsedRow entries with correct
     type coercion.
  2. Missing required columns: rejected at batch level (no per-row
     iteration even if rows exist).
  3. Per-row validation: invalid status/risk_tier/email don't poison
     adjacent valid rows.
  4. Tolerance: BOM, mixed case headers, surrounding whitespace, CRLF.
  5. Limits: rows past MAX_ROWS_PER_IMPORT are flagged as a batch error.
  6. Template generator: emits a parseable CSV whose data rows
     round-trip through the parser.
"""

from __future__ import annotations

import pytest

from app.models.use_case import (
    UseCaseRiskTier,
    UseCaseSource,
    UseCaseStatus,
)
from app.services.ai_inventory_csv import (
    MAX_ROWS_PER_IMPORT,
    parse_inventory_csv,
    render_import_template,
)

pytestmark = [pytest.mark.unit]


# ─── Happy path ──────────────────────────────────────────────────────


class TestHappyPath:
    def test_minimal_required_only(self) -> None:
        csv = "title,tool,department\nGrant memo,Copilot,Grants\n"
        result = parse_inventory_csv(csv)
        assert not result.has_errors
        assert len(result.valid_rows) == 1
        row = result.valid_rows[0]
        assert row.title == "Grant memo"
        assert row.tool == "Copilot"
        assert row.department == "Grants"
        assert row.status == UseCaseStatus.DRAFT  # default
        assert row.risk_tier == UseCaseRiskTier.LOW  # default
        assert row.data_classes == []
        assert row.owner_email is None
        assert row.source == UseCaseSource.IMPORT  # always
        assert row.row_number == 1

    def test_full_row(self) -> None:
        csv = (
            "title,tool,department,owner_email,status,risk_tier,"
            "data_classes,frequency,notes\n"
            "Press release,ChatGPT,Comms,alex@example.org,ACTIVE,MEDIUM,"
            "donor_pii;customer_pii,weekly,First draft only.\n"
        )
        result = parse_inventory_csv(csv)
        assert not result.has_errors
        row = result.valid_rows[0]
        assert row.owner_email == "alex@example.org"
        assert row.status == UseCaseStatus.ACTIVE
        assert row.risk_tier == UseCaseRiskTier.MEDIUM
        assert row.data_classes == ["donor_pii", "customer_pii"]
        assert row.frequency == "weekly"
        assert row.notes == "First draft only."

    def test_multiple_rows(self) -> None:
        csv = "title,tool,department\nA,T1,D1\nB,T2,D2\nC,T3,D3\n"
        result = parse_inventory_csv(csv)
        assert len(result.valid_rows) == 3
        assert [r.row_number for r in result.valid_rows] == [1, 2, 3]


# ─── Batch-level errors ──────────────────────────────────────────────


class TestBatchErrors:
    def test_empty_input(self) -> None:
        result = parse_inventory_csv("")
        assert result.has_errors
        assert len(result.valid_rows) == 0
        assert "empty" in result.errors[0].message.lower()

    def test_whitespace_only(self) -> None:
        result = parse_inventory_csv("   \n\n   ")
        assert result.has_errors
        assert len(result.valid_rows) == 0

    def test_missing_required_column(self) -> None:
        csv = "title,department\nA,D\n"  # no tool
        result = parse_inventory_csv(csv)
        assert result.has_errors
        msg = result.errors[0].message
        assert "tool" in msg
        # Required columns are not partially imported on header failure
        assert len(result.valid_rows) == 0

    def test_no_header(self) -> None:
        # An "empty" CSV body that's not whitespace-only triggers the
        # missing-header path; csv.DictReader on a single line treats
        # it as the header and finds no data rows.
        result = parse_inventory_csv("just one line of text")
        # That single line *was* parsed as a header — which is missing
        # required columns. So we expect the missing-columns error.
        assert result.has_errors
        assert "Missing required column" in result.errors[0].message


# ─── Per-row validation ──────────────────────────────────────────────


class TestPerRowValidation:
    def test_invalid_status_isolated_to_row(self) -> None:
        csv = (
            "title,tool,department,status\n"
            "Good row,T,D,ACTIVE\n"
            "Bad row,T,D,NONSENSE\n"
            "Also good,T,D,REVIEW\n"
        )
        result = parse_inventory_csv(csv)
        # The two good rows succeed; the middle row is in errors
        assert len(result.valid_rows) == 2
        assert {r.row_number for r in result.valid_rows} == {1, 3}
        err = next((e for e in result.errors if e.row_number == 2), None)
        assert err is not None
        assert err.field == "status"
        assert "NONSENSE" in err.message

    def test_invalid_risk_tier(self) -> None:
        csv = "title,tool,department,risk_tier\nX,T,D,EXTREME\n"
        result = parse_inventory_csv(csv)
        assert len(result.valid_rows) == 0
        assert any(e.field == "risk_tier" for e in result.errors)

    def test_missing_required_field_per_row(self) -> None:
        csv = "title,tool,department\n,T,D\n"
        result = parse_inventory_csv(csv)
        assert len(result.valid_rows) == 0
        assert any(e.field == "title" and "required" in e.message for e in result.errors)

    def test_multiple_field_errors_per_row(self) -> None:
        """A row can fail validation on multiple columns at once."""
        csv = "title,tool,department,status,risk_tier\n,,,BOGUS,ALSOBAD\n"
        result = parse_inventory_csv(csv)
        assert len(result.valid_rows) == 0
        fields_failed = {e.field for e in result.errors}
        # title + tool + department + status + risk_tier
        assert fields_failed >= {"title", "tool", "department", "status", "risk_tier"}

    def test_invalid_email_format(self) -> None:
        csv = "title,tool,department,owner_email\nX,T,D,not-an-email\n"
        result = parse_inventory_csv(csv)
        assert len(result.valid_rows) == 0
        assert any(e.field == "owner_email" for e in result.errors)

    def test_oversized_title(self) -> None:
        long_title = "x" * 300
        csv = f"title,tool,department\n{long_title},T,D\n"
        result = parse_inventory_csv(csv)
        assert len(result.valid_rows) == 0
        err = result.errors[0]
        assert err.field == "title"
        assert "255" in err.message


# ─── Header/data tolerance ───────────────────────────────────────────


class TestTolerance:
    def test_bom_tolerated(self) -> None:
        # Excel exports start with a UTF-8 BOM
        csv = "﻿title,tool,department\nA,T,D\n"
        result = parse_inventory_csv(csv)
        assert not result.has_errors
        assert result.valid_rows[0].title == "A"

    def test_header_case_insensitive(self) -> None:
        csv = "Title,TOOL,Department\nA,T,D\n"
        result = parse_inventory_csv(csv)
        assert not result.has_errors
        assert result.valid_rows[0].title == "A"

    def test_header_whitespace(self) -> None:
        csv = " title , tool , department \nA,T,D\n"
        result = parse_inventory_csv(csv)
        assert not result.has_errors

    def test_header_dash_and_underscore_equivalent(self) -> None:
        # Some admins write owner-email instead of owner_email
        csv = "title,tool,department,owner-email\nA,T,D,alex@example.org\n"
        result = parse_inventory_csv(csv)
        assert not result.has_errors
        assert result.valid_rows[0].owner_email == "alex@example.org"

    def test_cell_whitespace_trimmed(self) -> None:
        csv = "title,tool,department\n  Padded title  ,  Copilot  ,  Grants  \n"
        result = parse_inventory_csv(csv)
        row = result.valid_rows[0]
        assert row.title == "Padded title"
        assert row.tool == "Copilot"

    def test_crlf_line_endings(self) -> None:
        csv = "title,tool,department\r\nA,T,D\r\n"
        result = parse_inventory_csv(csv)
        assert not result.has_errors
        assert result.valid_rows[0].title == "A"

    def test_data_classes_dedup_and_lowercase(self) -> None:
        csv = "title,tool,department,data_classes\nX,T,D,Customer_PII;customer_pii;FINANCIAL_DATA\n"
        result = parse_inventory_csv(csv)
        row = result.valid_rows[0]
        assert row.data_classes == ["customer_pii", "financial_data"]


# ─── Row limit ───────────────────────────────────────────────────────


class TestRowLimit:
    def test_over_limit_flagged_as_batch_error(self) -> None:
        rows = "\n".join(f"row{i},T,D" for i in range(MAX_ROWS_PER_IMPORT + 5))
        csv = f"title,tool,department\n{rows}\n"
        result = parse_inventory_csv(csv)
        # Up to MAX rows imported successfully
        assert len(result.valid_rows) == MAX_ROWS_PER_IMPORT
        # Plus a single batch-level error
        batch_errors = [e for e in result.errors if e.row_number == 0]
        assert len(batch_errors) == 1
        assert str(MAX_ROWS_PER_IMPORT) in batch_errors[0].message


# ─── Template generator ──────────────────────────────────────────────


class TestImportTemplate:
    def test_template_is_non_empty(self) -> None:
        body = render_import_template()
        assert body
        assert "title" in body
        assert "tool" in body
        assert "department" in body

    def test_template_round_trips_through_parser(self) -> None:
        """The generated template MUST parse cleanly — otherwise admins
        download it, fill it in, and get cryptic errors on upload."""
        body = render_import_template()
        result = parse_inventory_csv(body)
        assert not result.has_errors, f"Template has parse errors: {result.errors}"
        # 3 illustrative example rows
        assert len(result.valid_rows) == 3
        # Cover the IT lead's mental model: at least one row with each
        # of {owner_email, risk_tier=HIGH, no_owner}
        has_owner = [r for r in result.valid_rows if r.owner_email]
        no_owner = [r for r in result.valid_rows if not r.owner_email]
        high_risk = [r for r in result.valid_rows if r.risk_tier == UseCaseRiskTier.HIGH]
        assert has_owner and no_owner and high_risk
