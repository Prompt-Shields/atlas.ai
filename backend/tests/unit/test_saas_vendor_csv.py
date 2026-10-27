"""Unit tests for app.services.saas_vendor_csv.

The column order is a documented export contract (the frontend and
spreadsheet imports rely on it), so the header is pinned here in full.
"""

from __future__ import annotations

import csv
import io
from datetime import date

import pytest

from app.models.saas_vendor import VendorCategory, VendorContractStatus
from app.schemas.saas_vendor import SaaSVendorResponse
from app.services.saas_vendor_csv import render_vendors_csv

pytestmark = [pytest.mark.unit]

_EXPECTED_HEADER = [
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


def _vendor(**overrides) -> SaaSVendorResponse:
    fields = {
        "id": "uc-1",
        "name": "Acme AI",
        "department": "Engineering",
        "source": "MANUAL",
        "category": VendorCategory.llm_provider,
    }
    fields.update(overrides)
    return SaaSVendorResponse(**fields)


def _rows(text: str) -> list[list[str]]:
    return list(csv.reader(io.StringIO(text)))


def test_header_only_when_no_vendors() -> None:
    assert _rows(render_vendors_csv([])) == [_EXPECTED_HEADER]


def test_column_order_is_the_contract() -> None:
    vendor = _vendor(
        ai_feature="Chat assistant",
        risk_score=72,
        contract_status=VendorContractStatus.pending_renewal,
        dpa=True,
        training_opt_out=False,
        models=["model-a", "model-b"],
        sub_processors=["Cloud One"],
        certifications=["SOC 2", "ISO 27001"],
        last_reviewed_at=date(2026, 3, 4),
        owner_user_id="user-9",
        has_assessment=True,
    )

    header, row = _rows(render_vendors_csv([vendor]))

    assert header == _EXPECTED_HEADER
    assert dict(zip(header, row, strict=True)) == {
        "name": "Acme AI",
        "department": "Engineering",
        "category": "llm_provider",
        "ai_feature": "Chat assistant",
        "risk_score": "72",
        "contract_status": "pending_renewal",
        "dpa": "true",
        "training_opt_out": "false",
        "models": "model-a; model-b",
        "sub_processors": "Cloud One",
        "certifications": "SOC 2; ISO 27001",
        "last_reviewed_at": "2026-03-04",
        "owner_user_id": "user-9",
        "has_assessment": "true",
    }


def test_empty_values_render_as_empty_cells() -> None:
    _, row = _rows(render_vendors_csv([_vendor()]))
    cells = dict(zip(_EXPECTED_HEADER, row, strict=True))

    # None, empty lists and empty strings all become "".
    assert cells["owner_user_id"] == ""
    assert cells["last_reviewed_at"] == ""
    assert cells["models"] == ""
    assert cells["sub_processors"] == ""
    assert cells["certifications"] == ""
    assert cells["ai_feature"] == ""
    # Defaults that are not empty keep their value.
    assert cells["risk_score"] == "0"
    assert cells["contract_status"] == "active"
    assert cells["dpa"] == "false"
    assert cells["has_assessment"] == "false"


def test_awkward_values_are_quoted_and_round_trip() -> None:
    name = 'Acme, "Inc."'
    department = "Line one\nline two"
    vendor = _vendor(name=name, department=department, models=["a,b", 'c "d"'])

    text = render_vendors_csv([vendor])

    # Commas, quotes and newlines force quoting; embedded quotes are doubled.
    assert '"Acme, ""Inc."""' in text
    header, row = _rows(text)
    cells = dict(zip(header, row, strict=True))
    assert cells["name"] == name
    assert cells["department"] == department
    assert cells["models"] == 'a,b; c "d"'


def test_one_row_per_vendor_in_input_order() -> None:
    vendors = [_vendor(id=f"uc-{i}", name=f"Vendor {i}") for i in range(3)]

    rows = _rows(render_vendors_csv(vendors))

    assert [row[0] for row in rows[1:]] == ["Vendor 0", "Vendor 1", "Vendor 2"]
