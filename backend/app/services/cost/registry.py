"""Connector registry — maps CostProvider enum values to live connector instances."""

from __future__ import annotations

from app.models.ai_cost_record import CostProvider
from app.services.cost.base import CostConnector

_CONNECTORS: dict[CostProvider, CostConnector] = {}


def register(connector: CostConnector) -> None:
    """Store *connector* under its declared provider key (idempotent per provider)."""
    _CONNECTORS[connector.provider] = connector


def get_connector(provider: CostProvider) -> CostConnector:
    """Return the registered connector for *provider*, or raise ``KeyError``."""
    return _CONNECTORS[provider]


def registered_providers() -> set[CostProvider]:
    """Return the set of providers that currently have a registered connector."""
    return set(_CONNECTORS)
