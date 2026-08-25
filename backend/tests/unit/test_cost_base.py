from datetime import date
from decimal import Decimal

from app.models.ai_cost_record import CostKind, CostSource, CostSubjectKind
from app.services.cost.base import CostConnector, CostRecord


def test_costrecord_shape():
    rec: CostRecord = {
        "usage_date": date(2026, 6, 1),
        "cost_kind": CostKind.metered_usage,
        "subject_kind": CostSubjectKind.model,
        "subject_ref": "claude-opus-4",
        "tokens_in": 100,
        "tokens_out": 50,
        "seats": None,
        "quantity": None,
        "cost_usd": Decimal("1.23"),
        "cost_source": CostSource.vendor_reported,
        "is_provisional": False,
        "raw_metadata": {},
    }
    assert rec["cost_usd"] == Decimal("1.23")
    assert hasattr(CostConnector, "fetch_cost")
