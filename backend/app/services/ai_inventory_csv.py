"""AI inventory CSV parser + validator.

Pure-function module. Takes a CSV text blob, returns a structured
result with valid rows + per-row errors. The router does the DB
write; this module never touches the DB.

CSV schema (admins generate this from the /import-template endpoint):

  title*           required, ≤255 chars
  tool*            required, ≤100 chars  (e.g. "Microsoft Copilot")
  department*      required, ≤255 chars
  owner_email      optional — resolved to owner_user_id in the router;
                   unknown emails are accepted but logged as an
                   import warning (the use case is still created
                   without an owner).
  status           optional — DRAFT | REVIEW | ACTIVE | RETIRED
                   (default: DRAFT)
  risk_tier        optional — LOW | MEDIUM | HIGH  (default: LOW)
  data_classes     optional — semicolon-separated taxonomy values
                   ("customer_pii;financial_data"). Empty cell = no
                   classes.
  frequency        optional — free text, ≤100 chars
  notes            optional — free text

Encoding: UTF-8 with BOM tolerance (Excel exports leave a BOM).
Newlines: \\n, \\r\\n, \\r all accepted (csv module handles this).
Quoting: standard CSV (RFC 4180) — quoted cells, doubled quotes.
Header row: required. Column order is flexible (we key by header name).

Validation philosophy:
  - Per-row failures don't poison the batch. Return both valid_rows
    and errors; the caller decides whether to import the valid subset
    or abort.
  - Empty cells in optional columns become None / [] — not validation
    errors.
  - The first 1000 rows are processed; beyond that we return a single
    batch-level error. Imports larger than that should be split.

The IMPORT source value on every produced UseCaseCreate distinguishes
these records from user-submitted forms in the audit trail.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field

from app.models.use_case import (
    UseCaseRiskTier,
    UseCaseSource,
    UseCaseStatus,
)

# Maximum number of rows we'll process in one upload. Sized so the
# parse + DB-insert loop completes inside the FastAPI 30s default
# request timeout even on a cold tenant.
MAX_ROWS_PER_IMPORT = 1000

# Canonical column names — order is irrelevant in input.
REQUIRED_COLUMNS = ("title", "tool", "department")
OPTIONAL_COLUMNS = (
    "owner_email",
    "status",
    "risk_tier",
    "data_classes",
    "frequency",
    "notes",
)
ALL_COLUMNS = REQUIRED_COLUMNS + OPTIONAL_COLUMNS


# ─── Result types ────────────────────────────────────────────────────


@dataclass(frozen=True)
class ParsedRow:
    """One validated CSV row, ready to become a UseCase.

    `row_number` is 1-indexed and counts the *data* rows (header is
    row 0 in our reporting — so the first data row is row 1). This
    matches what spreadsheet apps show users.
    """

    row_number: int
    title: str
    tool: str
    department: str
    owner_email: str | None
    status: UseCaseStatus
    risk_tier: UseCaseRiskTier
    data_classes: list[str]
    frequency: str | None
    notes: str | None
    # IMPORT — every row created via this path is admin-asserted.
    source: UseCaseSource = UseCaseSource.IMPORT


@dataclass(frozen=True)
class ParseError:
    """One row-level or batch-level failure."""

    row_number: int  # 0 = batch-level (e.g. missing header)
    field: str | None  # which column triggered the error (None = row-level)
    message: str


@dataclass
class ParseResult:
    valid_rows: list[ParsedRow] = field(default_factory=list)
    errors: list[ParseError] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return bool(self.errors)


# ─── Public entry point ──────────────────────────────────────────────


def parse_inventory_csv(text: str) -> ParseResult:
    """Parse a CSV blob into validated rows + per-row errors.

    Pure function. No DB lookups, no I/O. The caller (router)
    resolves `owner_email` → `owner_user_id` and writes UseCase rows.

    Args:
      text: the CSV file contents as a decoded string.

    Returns:
      ParseResult with `valid_rows` and `errors`. A row is in `errors`
      iff at least one validation failed for that row; otherwise it's
      in `valid_rows`. The two are disjoint.
    """
    result = ParseResult()

    if not text or not text.strip():
        result.errors.append(ParseError(0, None, "CSV is empty."))
        return result

    # Strip UTF-8 BOM that Excel adds to CSV exports.
    if text.startswith("﻿"):
        text = text[1:]

    reader = csv.DictReader(io.StringIO(text))

    if reader.fieldnames is None:
        result.errors.append(ParseError(0, None, "CSV is missing a header row."))
        return result

    # Lower + strip header names so users can write "Title" or " title "
    # and we still match.
    normalised_headers = {
        _norm_header(name): (idx, name)
        for idx, name in enumerate(reader.fieldnames or [])
        if name is not None
    }
    missing = [col for col in REQUIRED_COLUMNS if _norm_header(col) not in normalised_headers]
    if missing:
        result.errors.append(
            ParseError(
                0,
                None,
                f"Missing required column(s): {', '.join(missing)}. "
                f"Required: {', '.join(REQUIRED_COLUMNS)}. "
                f"Optional: {', '.join(OPTIONAL_COLUMNS)}.",
            )
        )
        return result

    # Iterate. We deliberately don't `enumerate(reader, start=1)`
    # before bumping past the header; csv.DictReader already skips
    # the header row, so the first row we see *is* data row #1.
    for idx, raw in enumerate(reader, start=1):
        if idx > MAX_ROWS_PER_IMPORT:
            result.errors.append(
                ParseError(
                    0,
                    None,
                    f"CSV has more than {MAX_ROWS_PER_IMPORT} rows. Split the file and re-upload.",
                )
            )
            break

        parsed, row_errors = _parse_row(idx, raw, normalised_headers)
        if row_errors:
            result.errors.extend(row_errors)
        elif parsed is not None:
            result.valid_rows.append(parsed)

    return result


# ─── Per-row parsing ─────────────────────────────────────────────────


def _parse_row(
    row_number: int,
    raw: dict[str, str | None],
    headers: dict[str, tuple[int, str]],
) -> tuple[ParsedRow | None, list[ParseError]]:
    """Validate one row. Returns (parsed_row_or_None, errors)."""
    errors: list[ParseError] = []

    def cell(name: str) -> str:
        """Pull a cell by canonical column name, tolerating header drift."""
        info = headers.get(_norm_header(name))
        if info is None:
            return ""
        _idx, raw_name = info
        value = raw.get(raw_name)
        return (value or "").strip()

    # ── Required: title / tool / department ─────────────────────
    title = cell("title")
    tool = cell("tool")
    department = cell("department")

    if not title:
        errors.append(ParseError(row_number, "title", "title is required."))
    elif len(title) > 255:
        errors.append(ParseError(row_number, "title", "title exceeds 255 characters."))
    if not tool:
        errors.append(ParseError(row_number, "tool", "tool is required."))
    elif len(tool) > 100:
        errors.append(ParseError(row_number, "tool", "tool exceeds 100 characters."))
    if not department:
        errors.append(ParseError(row_number, "department", "department is required."))
    elif len(department) > 255:
        errors.append(ParseError(row_number, "department", "department exceeds 255 characters."))

    # ── Optional: owner_email ───────────────────────────────────
    owner_email_raw = cell("owner_email") or None
    owner_email: str | None = None
    if owner_email_raw:
        if "@" not in owner_email_raw or " " in owner_email_raw:
            errors.append(
                ParseError(
                    row_number,
                    "owner_email",
                    f"owner_email {owner_email_raw!r} is not a valid email.",
                )
            )
        else:
            owner_email = owner_email_raw.lower()

    # ── Optional: status ────────────────────────────────────────
    status_raw = cell("status")
    status = UseCaseStatus.DRAFT
    if status_raw:
        try:
            status = UseCaseStatus(status_raw.upper())
        except ValueError:
            errors.append(
                ParseError(
                    row_number,
                    "status",
                    f"status {status_raw!r} is not one of "
                    f"{', '.join(s.value for s in UseCaseStatus)}.",
                )
            )

    # ── Optional: risk_tier ─────────────────────────────────────
    risk_raw = cell("risk_tier")
    risk_tier = UseCaseRiskTier.LOW
    if risk_raw:
        try:
            risk_tier = UseCaseRiskTier(risk_raw.upper())
        except ValueError:
            errors.append(
                ParseError(
                    row_number,
                    "risk_tier",
                    f"risk_tier {risk_raw!r} is not one of "
                    f"{', '.join(t.value for t in UseCaseRiskTier)}.",
                )
            )

    # ── Optional: data_classes (semicolon-separated) ────────────
    dc_raw = cell("data_classes")
    data_classes: list[str] = []
    if dc_raw:
        seen: set[str] = set()
        for piece in dc_raw.split(";"):
            cleaned = piece.strip().lower()
            if cleaned and cleaned not in seen:
                seen.add(cleaned)
                data_classes.append(cleaned)

    # ── Optional: frequency / notes ─────────────────────────────
    frequency = cell("frequency") or None
    if frequency and len(frequency) > 100:
        errors.append(ParseError(row_number, "frequency", "frequency exceeds 100 characters."))
    notes = cell("notes") or None

    if errors:
        return None, errors

    return (
        ParsedRow(
            row_number=row_number,
            title=title,
            tool=tool,
            department=department,
            owner_email=owner_email,
            status=status,
            risk_tier=risk_tier,
            data_classes=data_classes,
            frequency=frequency,
            notes=notes,
        ),
        [],
    )


# ─── Helpers ─────────────────────────────────────────────────────────


def _norm_header(name: str) -> str:
    return (name or "").strip().lower().replace("-", "_").replace(" ", "_")


# ─── CSV template generator (used by /import-template) ───────────────


def render_import_template() -> str:
    """Return a CSV file body the admin can download as a starting point.

    Includes:
      - the canonical header row
      - three illustrative example rows showing the expected shape
        for common atlas scenarios (donor-PII grant memos, internal
        press releases, financial-data 990-PF assist)

    Generated rather than served from a file so the template stays
    in sync with REQUIRED_COLUMNS + OPTIONAL_COLUMNS.
    """
    buf = io.StringIO()
    writer = csv.writer(buf, quoting=csv.QUOTE_MINIMAL)
    writer.writerow(ALL_COLUMNS)
    writer.writerow(
        [
            "Grant memo first-draft",
            "Microsoft Copilot",
            "Grants",
            "alex@example.org",
            "ACTIVE",
            "MEDIUM",
            "donor_pii;customer_pii",
            "weekly",
            "Used for first-draft grant memos before human edit.",
        ]
    )
    writer.writerow(
        [
            "Press release first-draft",
            "ChatGPT",
            "Communications",
            "",
            "REVIEW",
            "LOW",
            "",
            "monthly",
            "No owner yet — assign before promoting to ACTIVE.",
        ]
    )
    writer.writerow(
        [
            "990-PF narrative assist",
            "Claude",
            "Finance",
            "morgan@example.org",
            "ACTIVE",
            "HIGH",
            "financial_data;proprietary_code",
            "annually",
            "Used during 990-PF cycle. Highest data-sensitivity case.",
        ]
    )
    return buf.getvalue()
