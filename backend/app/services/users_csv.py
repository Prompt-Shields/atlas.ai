"""User CSV parser + validator for bulk-invite onboarding.

Pure-function module — symmetric to `ai_inventory_csv.py` (PR #59).
The router resolves each row to an invite via the existing
`invite_service.create_invite` flow; this module never touches DB.

CSV schema:

  email*    required, valid email
  role      optional — TENANT_ADMIN | ORG_ADMIN | ANALYST | VIEWER
            (default: VIEWER — least-privilege when admin omits)

`full_name` is intentionally NOT a column — the invitee sets it
themselves when they confirm the invite (alongside their password).
That gates the data quality on the user, not on the OrgAdmin's
spreadsheet hygiene.

Tolerance + limits match the inventory importer (UTF-8 BOM, mixed
case headers, 1000-row cap, 2MB upload cap enforced by the router).
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field

MAX_ROWS_PER_IMPORT = 1000

REQUIRED_COLUMNS = ("email",)
OPTIONAL_COLUMNS = ("role",)
ALL_COLUMNS = REQUIRED_COLUMNS + OPTIONAL_COLUMNS

VALID_ROLES = ("TENANT_ADMIN", "ORG_ADMIN", "ANALYST", "VIEWER")
DEFAULT_ROLE = "VIEWER"


# ─── Result types ────────────────────────────────────────────────────


@dataclass(frozen=True)
class ParsedUserRow:
    row_number: int
    email: str
    role: str


@dataclass(frozen=True)
class ParseError:
    row_number: int  # 0 = batch-level
    field: str | None
    message: str


@dataclass
class ParseResult:
    valid_rows: list[ParsedUserRow] = field(default_factory=list)
    errors: list[ParseError] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return bool(self.errors)


# ─── Public entry point ──────────────────────────────────────────────


def parse_users_csv(text: str) -> ParseResult:
    """Parse a CSV blob into validated user-invite rows.

    Pure function. No I/O. The caller (router) creates invites via
    invite_service.create_invite and sends emails.
    """
    result = ParseResult()

    if not text or not text.strip():
        result.errors.append(ParseError(0, None, "CSV is empty."))
        return result

    # Strip UTF-8 BOM.
    if text.startswith("﻿"):
        text = text[1:]

    reader = csv.DictReader(io.StringIO(text))

    if reader.fieldnames is None:
        result.errors.append(ParseError(0, None, "CSV is missing a header row."))
        return result

    headers = {_norm(name): name for name in (reader.fieldnames or []) if name is not None}
    missing = [c for c in REQUIRED_COLUMNS if _norm(c) not in headers]
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

    # Track duplicate emails within the same upload. Backend invite
    # service catches DB-level duplicates, but it's friendlier to
    # surface "row 7 duplicates row 3" before hitting the DB.
    seen_emails: dict[str, int] = {}

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

        parsed, row_errors = _parse_row(idx, raw, headers, seen_emails)
        if row_errors:
            result.errors.extend(row_errors)
        elif parsed is not None:
            result.valid_rows.append(parsed)
            seen_emails[parsed.email] = idx

    return result


def _parse_row(
    row_number: int,
    raw: dict[str, str | None],
    headers: dict[str, str],
    seen_emails: dict[str, int],
) -> tuple[ParsedUserRow | None, list[ParseError]]:
    errors: list[ParseError] = []

    def cell(name: str) -> str:
        raw_name = headers.get(_norm(name))
        if raw_name is None:
            return ""
        return (raw.get(raw_name) or "").strip()

    # ── email (required) ────────────────────────────────────────
    email_raw = cell("email")
    email = email_raw.lower()
    if not email:
        errors.append(ParseError(row_number, "email", "email is required."))
    elif "@" not in email or " " in email or "." not in email.split("@", 1)[-1]:
        errors.append(
            ParseError(
                row_number,
                "email",
                f"email {email_raw!r} is not a valid email.",
            )
        )
    else:
        if email in seen_emails:
            errors.append(
                ParseError(
                    row_number,
                    "email",
                    f"email {email!r} duplicates row {seen_emails[email]}.",
                )
            )

    # ── role (optional) ─────────────────────────────────────────
    role_raw = cell("role")
    role = role_raw.upper() if role_raw else DEFAULT_ROLE
    if role and role not in VALID_ROLES:
        errors.append(
            ParseError(
                row_number,
                "role",
                f"role {role_raw!r} is not one of {', '.join(VALID_ROLES)}.",
            )
        )

    if errors:
        return None, errors
    return ParsedUserRow(row_number=row_number, email=email, role=role), []


def _norm(name: str) -> str:
    return (name or "").strip().lower().replace("-", "_").replace(" ", "_")


# ─── Template generator ──────────────────────────────────────────────


def render_users_import_template() -> str:
    """Return a downloadable CSV template — header + 3 example rows."""
    buf = io.StringIO()
    writer = csv.writer(buf, quoting=csv.QUOTE_MINIMAL)
    writer.writerow(ALL_COLUMNS)
    writer.writerow(["alex@example.org", "ANALYST"])
    writer.writerow(["morgan@example.org", "VIEWER"])
    writer.writerow(["sam@example.org", "ORG_ADMIN"])
    return buf.getvalue()
