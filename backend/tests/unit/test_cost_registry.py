import pytest

from app.models.ai_cost_record import CostProvider
from app.services.cost import registry


class _FakeConnector:
    provider = CostProvider.anthropic

    def fetch_cost(self, integration, since, until):
        return []


def test_register_and_get():
    registry.register(_FakeConnector())
    got = registry.get_connector(CostProvider.anthropic)
    assert got.provider == CostProvider.anthropic
    assert CostProvider.anthropic in registry.registered_providers()


def test_get_unregistered_raises():
    # Every real CostProvider now has a connector wired via sync_service, so
    # we clear the registry to exercise the missing-connector path. The autouse
    # ``_restore_cost_connector_registry`` fixture restores it afterwards.
    registry._CONNECTORS.clear()
    with pytest.raises(KeyError):
        registry.get_connector(CostProvider.vercel)
