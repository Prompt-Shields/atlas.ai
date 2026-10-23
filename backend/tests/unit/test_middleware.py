"""Pins the current behaviour of the three request middlewares in app/middleware/.

Each test builds a tiny Starlette app with only the middleware under test, so a
failure points at the middleware and not at routing, auth or the database.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
import structlog
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient
from structlog.testing import capture_logs

from app.middleware import audit as audit_module
from app.middleware.audit import AUDIT_PATHS, AuditMiddleware
from app.middleware.correlation import CORRELATION_HEADER, CorrelationIdMiddleware
from app.middleware.tenant_context import TenantContextMiddleware

pytestmark = [pytest.mark.unit]

_MISSING = "<missing>"


def _state_snapshot(request: Request) -> dict[str, Any]:
    state = request.state
    return {
        name: getattr(state, name, _MISSING)
        for name in ("correlation_id", "tenant_id", "org_id", "user_id")
    }


async def _echo_state(request: Request) -> JSONResponse:
    return JSONResponse(_state_snapshot(request))


def _client(*middleware: Middleware, routes: list[Route] | None = None) -> TestClient:
    app = Starlette(
        routes=routes or [Route("/{path:path}", _echo_state, methods=["GET", "POST"])],
        middleware=list(middleware),
    )
    return TestClient(app)


def _is_uuid4(value: str) -> bool:
    try:
        return uuid.UUID(value).version == 4 and str(uuid.UUID(value)) == value
    except ValueError:
        return False


# ── CorrelationIdMiddleware ──────────────────────────────────────────────


class TestCorrelationId:
    def test_valid_client_uuid_is_kept_and_echoed(self) -> None:
        client = _client(Middleware(CorrelationIdMiddleware))
        sent = str(uuid.uuid4())

        response = client.get("/x", headers={CORRELATION_HEADER: sent})

        assert response.headers[CORRELATION_HEADER] == sent
        assert response.json()["correlation_id"] == sent

    def test_uppercase_uuid_is_kept_as_sent(self) -> None:
        # The pattern is case-insensitive and the value is not normalised.
        client = _client(Middleware(CorrelationIdMiddleware))
        sent = str(uuid.uuid4()).upper()

        response = client.get("/x", headers={CORRELATION_HEADER: sent})

        assert response.headers[CORRELATION_HEADER] == sent
        assert response.json()["correlation_id"] == sent

    def test_any_uuid_version_is_kept(self) -> None:
        # Only the shape is checked, not the version nibble.
        client = _client(Middleware(CorrelationIdMiddleware))
        sent = str(uuid.uuid1())

        response = client.get("/x", headers={CORRELATION_HEADER: sent})

        assert response.headers[CORRELATION_HEADER] == sent

    def test_missing_header_gets_a_fresh_uuid4(self) -> None:
        client = _client(Middleware(CorrelationIdMiddleware))

        response = client.get("/x")

        echoed = response.headers[CORRELATION_HEADER]
        assert _is_uuid4(echoed)
        assert response.json()["correlation_id"] == echoed

    @pytest.mark.parametrize(
        "sent",
        [
            "",
            "not-a-uuid",
            "12345",
            # Hex without dashes is a valid uuid.UUID() input but not the accepted shape.
            uuid.uuid4().hex,
            # Braced and urn forms are rejected too.
            "{" + str(uuid.uuid4()) + "}",
            "urn:uuid:" + str(uuid.uuid4()),
            # Extra text either side must not pass.
            str(uuid.uuid4()) + "x",
            "x" + str(uuid.uuid4()),
            str(uuid.uuid4()) + "; drop table",
            # Right length, wrong alphabet.
            "gggggggg-gggg-gggg-gggg-gggggggggggg",
            # An oversized value is not echoed back.
            "a" * 4096,
        ],
        ids=[
            "empty",
            "word",
            "digits",
            "hex-no-dashes",
            "braced",
            "urn",
            "trailing-text",
            "leading-text",
            "injection",
            "non-hex",
            "oversized",
        ],
    )
    def test_anything_else_is_replaced(self, sent: str) -> None:
        client = _client(Middleware(CorrelationIdMiddleware))

        response = client.get("/x", headers={CORRELATION_HEADER: sent})

        echoed = response.headers[CORRELATION_HEADER]
        assert echoed != sent
        assert _is_uuid4(echoed)
        assert response.json()["correlation_id"] == echoed

    def test_each_request_gets_its_own_id(self) -> None:
        client = _client(Middleware(CorrelationIdMiddleware))

        first = client.get("/x").headers[CORRELATION_HEADER]
        second = client.get("/x").headers[CORRELATION_HEADER]

        assert first != second

    def test_header_name_is_case_insensitive_on_the_way_in(self) -> None:
        client = _client(Middleware(CorrelationIdMiddleware))
        sent = str(uuid.uuid4())

        response = client.get("/x", headers={"x-correlation-id": sent})

        assert response.headers[CORRELATION_HEADER] == sent

    def test_header_is_echoed_on_error_responses(self) -> None:
        async def boom(request: Request) -> PlainTextResponse:
            return PlainTextResponse("nope", status_code=404)

        client = _client(Middleware(CorrelationIdMiddleware), routes=[Route("/x", boom)])
        sent = str(uuid.uuid4())

        response = client.get("/x", headers={CORRELATION_HEADER: sent})

        assert response.status_code == 404
        assert response.headers[CORRELATION_HEADER] == sent

    def test_route_set_header_is_overwritten(self) -> None:
        async def sets_own(request: Request) -> PlainTextResponse:
            return PlainTextResponse("ok", headers={CORRELATION_HEADER: "from-route"})

        client = _client(Middleware(CorrelationIdMiddleware), routes=[Route("/x", sets_own)])

        response = client.get("/x")

        assert response.headers[CORRELATION_HEADER] != "from-route"
        assert _is_uuid4(response.headers[CORRELATION_HEADER])


# ── TenantContextMiddleware ──────────────────────────────────────────────


class _PresetState:
    """Pure ASGI middleware that puts values on request.state before the next layer."""

    def __init__(self, app: Any, **values: Any) -> None:
        self.app = app
        self.values = values

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            state = Request(scope).state
            for name, value in self.values.items():
                setattr(state, name, value)
        await self.app(scope, receive, send)


class TestTenantContext:
    def test_defaults_to_none(self) -> None:
        client = _client(Middleware(TenantContextMiddleware))

        body = client.get("/x").json()

        assert body["tenant_id"] is None
        assert body["org_id"] is None
        assert body["user_id"] is None

    def test_does_not_touch_correlation_id(self) -> None:
        client = _client(Middleware(TenantContextMiddleware))

        assert client.get("/x").json()["correlation_id"] == _MISSING

    def test_does_not_overwrite_values_already_set(self) -> None:
        # Starlette runs the first listed middleware outermost, so _PresetState
        # sets the values before TenantContextMiddleware looks at them.
        client = _client(
            Middleware(_PresetState, tenant_id="t-1", org_id="o-1", user_id="u-1"),
            Middleware(TenantContextMiddleware),
        )

        body = client.get("/x").json()

        assert body["tenant_id"] == "t-1"
        assert body["org_id"] == "o-1"
        assert body["user_id"] == "u-1"

    def test_fills_only_the_missing_values(self) -> None:
        client = _client(
            Middleware(_PresetState, tenant_id="t-1"),
            Middleware(TenantContextMiddleware),
        )

        body = client.get("/x").json()

        assert body["tenant_id"] == "t-1"
        assert body["org_id"] is None
        assert body["user_id"] is None

    def test_an_explicit_none_is_left_alone(self) -> None:
        client = _client(
            Middleware(_PresetState, tenant_id=None, org_id="o-1"),
            Middleware(TenantContextMiddleware),
        )

        body = client.get("/x").json()

        assert body["tenant_id"] is None
        assert body["org_id"] == "o-1"

    def test_route_can_set_values_after_it(self) -> None:
        # Auth dependencies set these inside the route; the defaults must not
        # stop that, and the values must be visible on the shared state.
        async def sets_tenant(request: Request) -> JSONResponse:
            request.state.tenant_id = "t-2"
            return JSONResponse(_state_snapshot(request))

        client = _client(Middleware(TenantContextMiddleware), routes=[Route("/x", sets_tenant)])

        assert client.get("/x").json()["tenant_id"] == "t-2"

    def test_passes_the_response_through(self) -> None:
        async def teapot(request: Request) -> PlainTextResponse:
            return PlainTextResponse("short and stout", status_code=418, headers={"X-Kept": "1"})

        client = _client(Middleware(TenantContextMiddleware), routes=[Route("/x", teapot)])

        response = client.get("/x")

        assert response.status_code == 418
        assert response.text == "short and stout"
        assert response.headers["X-Kept"] == "1"


# ── AuditMiddleware ──────────────────────────────────────────────────────


_AUDIT_FIELDS = {
    "event",
    "log_level",
    "method",
    "path",
    "status_code",
    "duration_ms",
    "user_id",
    "tenant_id",
    "correlation_id",
    "client_ip",
}


@pytest.fixture
def audit_logs(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[dict[str, Any]]]:
    """Collects the audit logger's events.

    The app configures structlog with cache_logger_on_first_use, so a module
    logger used earlier in the session may be bound to the real processors and
    bypass capture_logs. A fresh lazy proxy picks up the capturing config.
    """
    with capture_logs() as logs:
        monkeypatch.setattr(audit_module, "logger", structlog.get_logger("audit"))
        yield logs


def _audit_events(logs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [entry for entry in logs if entry.get("event") == "audit_event"]


class TestAudit:
    def test_audit_paths_are_unchanged(self) -> None:
        # A guard so a change to the list is deliberate and reviewed.
        assert AUDIT_PATHS == {
            "/api/v1/auth/",
            "/api/v1/invites/",
            "/api/v1/users/",
            "/api/v1/tenants/",
            "/api/v1/admin/",
            "/api/v1/adapters/",
        }

    @pytest.mark.parametrize("prefix", sorted(AUDIT_PATHS))
    def test_logs_under_every_audit_path(
        self, prefix: str, audit_logs: list[dict[str, Any]]
    ) -> None:
        client = _client(Middleware(AuditMiddleware))

        client.get(prefix + "anything")

        events = _audit_events(audit_logs)
        assert len(events) == 1
        assert events[0]["path"] == prefix + "anything"

    @pytest.mark.parametrize(
        "path",
        [
            "/",
            "/health",
            "/api/v1/risks/",
            "/api/v1/cost/summary",
            # Prefix match is on the raw path start, so a version change escapes it.
            "/api/v2/users/1",
            # Not a prefix of the path.
            "/x/api/v1/users/1",
            # The trailing slash in AUDIT_PATHS means the bare collection path
            # is not audited. See the PR that added this file: create and list
            # routes for users, invites and tenants live at these paths.
            "/api/v1/users",
            "/api/v1/invites",
            "/api/v1/tenants",
            "/api/v1/auth",
            # Lookalike prefixes are not matched.
            "/api/v1/usersettings/1",
            "/api/v1/authz/check",
        ],
    )
    def test_does_not_log_elsewhere(self, path: str, audit_logs: list[dict[str, Any]]) -> None:
        client = _client(Middleware(AuditMiddleware))

        response = client.get(path)

        assert response.status_code == 200
        assert _audit_events(audit_logs) == []

    def test_logs_the_expected_fields(self, audit_logs: list[dict[str, Any]]) -> None:
        client = _client(Middleware(AuditMiddleware))

        client.post("/api/v1/users/42?secret=hunter2")

        (event,) = _audit_events(audit_logs)
        assert set(event) == _AUDIT_FIELDS
        assert event["log_level"] == "info"
        assert event["method"] == "POST"
        # The path is logged without the query string.
        assert event["path"] == "/api/v1/users/42"
        assert event["status_code"] == 200
        assert isinstance(event["duration_ms"], float)
        assert event["duration_ms"] >= 0
        assert event["duration_ms"] == round(event["duration_ms"], 2)
        # TestClient's peer address.
        assert event["client_ip"] == "testclient"
        # With nothing on request.state these are None.
        assert event["user_id"] is None
        assert event["tenant_id"] is None
        assert event["correlation_id"] is None

    def test_logs_the_response_status(self, audit_logs: list[dict[str, Any]]) -> None:
        async def denied(request: Request) -> PlainTextResponse:
            return PlainTextResponse("no", status_code=403)

        client = _client(
            Middleware(AuditMiddleware), routes=[Route("/api/v1/admin/{rest:path}", denied)]
        )

        client.get("/api/v1/admin/tenants")

        (event,) = _audit_events(audit_logs)
        assert event["status_code"] == 403

    def test_reads_ids_set_by_the_route(self, audit_logs: list[dict[str, Any]]) -> None:
        # The ids are read after the response, so values an auth dependency puts
        # on request.state during the route are the ones logged, as strings.
        user_id = uuid.uuid4()
        tenant_id = uuid.uuid4()

        async def authed(request: Request) -> PlainTextResponse:
            request.state.user_id = user_id
            request.state.tenant_id = tenant_id
            return PlainTextResponse("ok")

        client = _client(
            Middleware(AuditMiddleware), routes=[Route("/api/v1/users/{rest:path}", authed)]
        )

        client.get("/api/v1/users/me")

        (event,) = _audit_events(audit_logs)
        assert event["user_id"] == str(user_id)
        assert event["tenant_id"] == str(tenant_id)

    def test_falsy_ids_are_logged_as_none(self, audit_logs: list[dict[str, Any]]) -> None:
        async def empty_ids(request: Request) -> PlainTextResponse:
            request.state.user_id = ""
            request.state.tenant_id = 0
            return PlainTextResponse("ok")

        client = _client(
            Middleware(AuditMiddleware), routes=[Route("/api/v1/users/{rest:path}", empty_ids)]
        )

        client.get("/api/v1/users/me")

        (event,) = _audit_events(audit_logs)
        assert event["user_id"] is None
        assert event["tenant_id"] is None

    def test_works_in_the_app_order(self, audit_logs: list[dict[str, Any]]) -> None:
        # app.main adds Correlation, then TenantContext, then Audit. Each
        # add_middleware call wraps what is there, so Audit ends up outermost and
        # reads the correlation id the inner middleware set on the shared state.
        client = _client(
            Middleware(AuditMiddleware),
            Middleware(TenantContextMiddleware),
            Middleware(CorrelationIdMiddleware),
        )
        sent = str(uuid.uuid4())

        response = client.get("/api/v1/tenants/1", headers={CORRELATION_HEADER: sent})

        assert response.headers[CORRELATION_HEADER] == sent
        (event,) = _audit_events(audit_logs)
        assert event["correlation_id"] == sent
        assert event["user_id"] is None
        assert event["tenant_id"] is None

    def test_unhandled_error_is_not_logged(self, audit_logs: list[dict[str, Any]]) -> None:
        # The log call comes after call_next with no try/finally, so a request
        # that raises leaves no audit event. See the PR that added this file.
        async def crash(request: Request) -> PlainTextResponse:
            raise RuntimeError("boom")

        client = TestClient(
            Starlette(
                routes=[Route("/api/v1/admin/{rest:path}", crash)],
                middleware=[Middleware(AuditMiddleware)],
            ),
            raise_server_exceptions=False,
        )

        response = client.get("/api/v1/admin/x")

        assert response.status_code == 500
        assert _audit_events(audit_logs) == []

    def test_passes_the_response_through(self, audit_logs: list[dict[str, Any]]) -> None:
        async def teapot(request: Request) -> PlainTextResponse:
            return PlainTextResponse("short and stout", status_code=418, headers={"X-Kept": "1"})

        client = _client(
            Middleware(AuditMiddleware), routes=[Route("/api/v1/auth/{rest:path}", teapot)]
        )

        response = client.get("/api/v1/auth/login")

        assert response.status_code == 418
        assert response.text == "short and stout"
        assert response.headers["X-Kept"] == "1"
