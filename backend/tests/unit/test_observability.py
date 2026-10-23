"""Unit tests for app.observability against the real OpenTelemetry packages.

Nothing here is faked: these tests exist so a Dependabot bump of the
OpenTelemetry SDK, the instrumentors or the Azure Monitor exporter fails here
when it changes an API we call, instead of only at startup in production.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.util._once import Once

from app import observability
from app.config import get_settings

pytestmark = [pytest.mark.unit]

# A well-formed connection string; nothing is sent because no span is exported.
FAKE_AI_CONNECTION = (
    "InstrumentationKey=00000000-0000-0000-0000-000000000000;"
    "IngestionEndpoint=https://localhost.invalid/"
)


@pytest.fixture
def captured_provider(monkeypatch):
    """Capture the provider setup_telemetry installs instead of setting it globally."""
    captured: dict[str, TracerProvider] = {}
    monkeypatch.setattr(
        observability.trace,
        "set_tracer_provider",
        lambda provider: captured.__setitem__("provider", provider),
    )
    monkeypatch.setenv("APPLICATIONINSIGHTS_STATSBEAT_DISABLED_ALL", "true")
    return captured


def _exporters(provider: TracerProvider) -> list[object]:
    processors = provider._active_span_processor._span_processors
    return [p.span_exporter for p in processors]


def _settings(**update):
    return get_settings().model_copy(update=update)


def test_setup_telemetry_sets_resource_attributes(captured_provider):
    settings = _settings(app_debug=True, applicationinsights_connection_string="")
    observability.setup_telemetry(settings)

    provider = captured_provider["provider"]
    attributes = provider.resource.attributes
    assert attributes["service.name"] == settings.otel_service_name
    assert attributes["service.version"] == settings.app_version
    assert attributes["deployment.environment"] == settings.app_env.value


def test_setup_telemetry_debug_uses_console_exporter(captured_provider):
    observability.setup_telemetry(
        _settings(app_debug=True, applicationinsights_connection_string="")
    )

    exporters = _exporters(captured_provider["provider"])
    assert [type(e) for e in exporters] == [ConsoleSpanExporter]


def test_setup_telemetry_non_debug_uses_otlp_exporter(captured_provider):
    observability.setup_telemetry(
        _settings(app_debug=False, applicationinsights_connection_string="")
    )

    exporters = _exporters(captured_provider["provider"])
    assert [type(e) for e in exporters] == [OTLPSpanExporter]


def test_setup_telemetry_attaches_azure_monitor_exporter(captured_provider):
    # setup_telemetry swallows ImportError; a version clash between the Azure
    # exporter and the OpenTelemetry SDK would silently drop Application
    # Insights. Importing it here fails loudly instead.
    from azure.monitor.opentelemetry.exporter import AzureMonitorTraceExporter

    observability.setup_telemetry(
        _settings(app_debug=True, applicationinsights_connection_string=FAKE_AI_CONNECTION)
    )

    exporters = _exporters(captured_provider["provider"])
    assert [type(e) for e in exporters] == [AzureMonitorTraceExporter, ConsoleSpanExporter]


@pytest.fixture
def in_memory_tracing():
    """Install a global provider with an in-memory exporter, restored afterwards."""
    saved_provider = trace._TRACER_PROVIDER
    saved_once = trace._TRACER_PROVIDER_SET_ONCE
    trace._TRACER_PROVIDER_SET_ONCE = Once()
    trace._TRACER_PROVIDER = None
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    try:
        yield exporter
    finally:
        trace._TRACER_PROVIDER = saved_provider
        trace._TRACER_PROVIDER_SET_ONCE = saved_once


def test_instrument_fastapi_records_server_span(in_memory_tracing):
    app = FastAPI()

    @app.get("/ping/{item}")
    async def ping(item: str) -> dict:
        return {"item": item}

    observability.instrument_fastapi(app)
    try:
        with TestClient(app) as client:
            response = client.get("/ping/abc")
        assert response.status_code == 200

        server_spans = [
            s for s in in_memory_tracing.get_finished_spans() if s.kind == trace.SpanKind.SERVER
        ]
        assert len(server_spans) == 1
        span = server_spans[0]
        assert span.name == "GET /ping/{item}"
        assert span.attributes["http.route"] == "/ping/{item}"
    finally:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.uninstrument_app(app)
