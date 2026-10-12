"""The request's session commits before the response is sent.

FastAPI runs a `yield` dependency's exit code after the response has gone out unless
the dependency is declared with `scope="function"`. `get_db_session` commits in its
exit code, so with the default a client acting at once on a 201 could miss the write:
the E2E run did, creating an organisation straight after signing up. `DB_SESSION` is
the function-scoped declaration every route and auth dependency uses.
"""

from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient

from app.database import DB_SESSION, get_db_session
from app.main import app as atlas

pytestmark = pytest.mark.unit


def _session_scopes(dependant, seen: list[str | None]) -> list[str | None]:
    for sub in dependant.dependencies:
        if sub.call is get_db_session:
            seen.append(sub.scope)
        _session_scopes(sub, seen)
    return seen


def test_every_route_commits_before_it_responds() -> None:
    """A route, or a dependency it uses, asking for the session any other way would
    commit after responding, and alongside DB_SESSION would get a second session."""
    found: dict[str, list[str | None]] = {}
    for route in atlas.routes:
        if isinstance(route, APIRoute):
            scopes = _session_scopes(route.dependant, [])
            if scopes:
                found[f"{sorted(route.methods)} {route.path}"] = scopes
    assert found, "no route uses the session: the walk is wrong"
    wrong = {path: s for path, s in found.items() if any(x != "function" for x in s)}
    assert wrong == {}


async def _order(session_dependency) -> list[str]:
    """What happens first, the commit or the response, for a route using the given
    session dependency."""
    events: list[str] = []
    app = FastAPI()

    @app.post("/write", status_code=201)
    async def write(db: object = session_dependency) -> dict[str, bool]:
        return {"ok": True}

    async def fake_session():
        yield object()
        events.append("commit")

    app.dependency_overrides[get_db_session] = fake_session

    async def recording(scope, receive, send):
        async def send_and_note(message):
            if message["type"] == "http.response.start":
                events.append("response")
            await send(message)

        await app(scope, receive, send_and_note)

    async with AsyncClient(transport=ASGITransport(app=recording), base_url="http://t") as c:
        r = await c.post("/write")
    assert r.status_code == 201
    return events


@pytest.mark.asyncio
async def test_the_session_commits_before_the_response_starts() -> None:
    assert await _order(DB_SESSION) == ["commit", "response"]


@pytest.mark.asyncio
async def test_a_bare_dependency_would_commit_after_responding() -> None:
    # The behaviour DB_SESSION exists to avoid, so this test shows the first one can fail.
    assert await _order(Depends(get_db_session)) == ["response", "commit"]
