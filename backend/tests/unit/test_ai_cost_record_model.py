from app.models.ai_cost_record import (
    AICostRecord,
    CostKind,
    CostProvider,
    CostSource,
    CostSubjectKind,
)


def test_cost_record_table_and_enums():
    assert AICostRecord.__tablename__ == "ai_cost_records"
    assert AICostRecord.__table__.schema == "grc"
    assert {c.name for c in AICostRecord.__table__.columns} >= {
        "tenant_id",
        "integration_id",
        "provider",
        "usage_date",
        "cost_kind",
        "subject_kind",
        "subject_ref",
        "tokens_in",
        "tokens_out",
        "seats",
        "quantity",
        "cost_usd",
        "cost_source",
        "is_provisional",
        "raw_metadata",
        "ingested_at",
    }
    assert CostProvider.anthropic.value == "anthropic"
    assert CostKind.metered_usage.value == "metered_usage"
    assert CostSubjectKind.model.value == "model"
    assert CostSource.vendor_reported.value == "vendor_reported"


def test_cost_record_key_nullability():
    cols = {c.name: c for c in AICostRecord.__table__.columns}
    assert cols["cost_usd"].nullable is False
    assert cols["subject_ref"].nullable is False
    assert cols["cost_source"].nullable is False
    assert cols["is_provisional"].nullable is False
    assert cols["tokens_in"].nullable is True
    assert cols["seats"].nullable is True


def test_model_exports_and_integration_providers():
    from app import models

    assert models.AICostRecord is AICostRecord
    from app.models.integration import IntegrationProvider

    for name in ("ANTHROPIC", "OPENAI", "CURSOR", "GITHUB_COPILOT", "VERCEL"):
        assert hasattr(IntegrationProvider, name)
