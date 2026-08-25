"""SaaS Vendor AI — CSV export (SP-1, issue #191).

Ports the AISPM `toCsv` helper: one row per vendor, list-valued fields
joined with "; ". Returns a UTF-8 string the router serves as a file.
"""

from __future__ import annotations

import csv
import io

from app.schemas.saas_vendor import SaaSVendorResponse

# Column order is the export contract — the frontend and any downstream
# spreadsheet import rely on it, so keep it stable.
_COLUMNS = [
    "name",
    "department",
    "category",
    "ai_feature",
    "risk_score",
    "contract_status",
    "dpa",
    "training_opt_out",
    "models",
    "sub_processors",
    "certifications",
    "last_reviewed_at",
    "owner_user_id",
    "has_assessment",
]


def _cell(vendor: SaaSVendorResponse, column: str) -> str:
    value = getattr(vendor, column)
    if isinstance(value, list):
        return "; ".join(str(v) for v in value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    if column == "category" or column == "contract_status":
        # Enum → its wire value.
        return getattr(value, "value", str(value))
    return str(value)


def render_vendors_csv(vendors: list[SaaSVendorResponse]) -> str:
    """Render vendors to a CSV document (header + one row each)."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(_COLUMNS)
    for vendor in vendors:
        writer.writerow([_cell(vendor, column) for column in _COLUMNS])
    return buffer.getvalue()
