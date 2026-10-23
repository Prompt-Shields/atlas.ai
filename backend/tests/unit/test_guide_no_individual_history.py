"""No individual history reaches the employer through Atlas's Guide routes
(promptly-guide #28 E4, #27 E15).

E4 asks for "no individual screen, audio or question history visible to the employer,
verified in the admin console and APIs". The console reads only `/guide/pilot-report`,
so the APIs are where it is verified. This test walks every route the app serves under
the Guide prefixes, against a tenant seeded with people who can be named: a team of
three, a category only two people reached, one device with a tool nobody else uses,
SCIM users, and a contribution whose team is a question. It then holds every response
to three rules:

  * no seeded personal identifier appears: a Firebase subject, an email, a device,
    a telemetry user or session id, a receipt, a sub-10 team or category, or the
    question;
  * every number in a Guide response is one the gate allows: a band, the minimum
    itself, ten devices or more, or a count of things left out (rows, categories,
    teams), never a count of people below ten;
  * SCIM answers the identity provider with its own directory, as sent, and nothing
    Guide noted about anyone.

A new route under these prefixes fails `test_every_guide_route_is_walked` until it is
added here, so it cannot ship without being held to the same rules.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.routing import APIRoute
from httpx import AsyncClient, Response
from sqlalchemy import select

from app.auth.firebase_token import get_firebase_verifier
from app.auth.jwt import create_access_token
from app.main import app
from app.models.guide_adoption import ADOPTION_KINDS, GuideAdoptionReceipt
from app.models.prompt_event import PromptEvent
from app.schemas.telemetry import PromptEventAction, PromptEventKind, PromptEventSource
from scripts.seed_guide_pilot import DEMO_DEVICE_PREFIX, DEVICES, seed_pilot
from tests.conftest import TEST_TENANT_ID, TestSessionLocal, auth_header
from tests.unit import firebase_keys as keys
from tests.unit.guide_db import (
    SENDING,
    GuideClock,
    guide_clock_fixture,  # noqa: F401
    guide_resolvers,  # noqa: F401
)

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

PREFIXES = ("/api/v1/guide", "/api/v1/scim/v2")
GUIDE = "/api/v1/guide"
SCIM = "/api/v1/scim/v2"
PATCH_OP = "urn:ietf:params:scim:api:messages:2.0:PatchOp"

# Who answers each route: the employer (an Atlas admin or analyst), the person's own
# Guide (their Firebase token), or the organisation's identity provider (SCIM token).
EMPLOYER = {
    ("GET", f"{GUIDE}/connection"),
    ("PUT", f"{GUIDE}/connection"),
    ("GET", f"{GUIDE}/adoption/report"),
    ("GET", f"{GUIDE}/pilot-report"),
    ("POST", f"{GUIDE}/scim-token"),
    ("DELETE", f"{GUIDE}/scim-token"),
}
PERSON = {
    ("GET", f"{GUIDE}/offer"),
    ("GET", f"{GUIDE}/approved-tools"),
    ("GET", f"{GUIDE}/groups"),
    ("GET", f"{GUIDE}/work-client"),
    ("POST", f"{GUIDE}/adoption"),
}
IDENTITY_PROVIDER = {
    ("GET", f"{SCIM}/Users"),
    ("POST", f"{SCIM}/Users"),
    ("GET", f"{SCIM}/Users/{{user_id}}"),
    ("PUT", f"{SCIM}/Users/{{user_id}}"),
    ("PATCH", f"{SCIM}/Users/{{user_id}}"),
    ("DELETE", f"{SCIM}/Users/{{user_id}}"),
    ("GET", f"{SCIM}/Groups"),
    ("POST", f"{SCIM}/Groups"),
    ("GET", f"{SCIM}/Groups/{{group_id}}"),
    ("PATCH", f"{SCIM}/Groups/{{group_id}}"),
    ("DELETE", f"{SCIM}/Groups/{{group_id}}"),
    ("GET", f"{SCIM}/ServiceProviderConfig"),
}

# The people. Three in the board (under ten), twelve in support, two of whom asked
# about one topic (under ten), and one who gave a question as her team.
BOARD = [f"uid-board-{i}" for i in range(3)]
SUPPORT = [f"uid-support-{i:02d}" for i in range(12)]
QUESTIONER = "uid-kari-question"
SUBJECTS = [*BOARD, *SUPPORT, QUESTIONER]


def _email(subject: str) -> str:
    # Not the subject with a domain: a test that finds one inside the other proves nothing.
    return f"{subject.removeprefix('uid-')}.mail@nordlys.example"


QUESTION = "Kari asks how to hide her expenses from finance"
BOARD_TEAM = "board-office"
BOARD_ONLY = {"kind": "topic", "id": "board-pack/merger-talks"}
FEW_IN_SUPPORT = {"kind": "topic", "id": "support-pack/sick-leave"}
CHATGPT = {"kind": "app", "id": "ChatGPT"}

# One person's machine, with a tool and a kind of personal data nobody else has.
LONE_DEVICE = "kari-macbook-0001"
LONE_USER = "kari.nordmann@nordlys.example"
LONE_SESSION = "sess-kari-0001"
LONE_APP = "rare-ai.example"
LONE_CATEGORY = "kari_only_kind"

# SCIM: the directory the identity provider pushes, and gets back.
SCIM_USER = "ola.directory@nordlys.example"
SCIM_GROUP = "Finance approvers"

PERSON_PARAMETERS = ("user_id", "email", "device", "subject")

# People send September while its intake is open; the employer reads on the 12th of
# October, after it closed on the 7th.
NOW = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)

# Every integer a Guide response may carry, by its key, and what it must be.
# A key not listed fails the test: a new number has to be looked at.
ALLOWED_NUMBERS = {
    "band_lower": lambda n: n % 10 == 0 and 0 <= n <= 80,
    "minimum_group_size": lambda n: n == 10,
    "minimum_devices": lambda n: n == 10,
    "devices": lambda n: n >= 10,
    "events": lambda n: n >= 10,
    # Counts of things left out (figures, rows), never of people: the report says
    # how many, so a partial picture is not read as the whole. Not of teams too
    # small: a count of those would say whether a team of one took part (E4).
    "suppressed": lambda n: n >= 0,
    "suppressed_categories": lambda n: n >= 0,
}


@pytest.fixture(autouse=True)
def _local_google_keys():
    local = keys.verifier()
    app.dependency_overrides[get_firebase_verifier] = lambda: local
    yield
    app.dependency_overrides.pop(get_firebase_verifier, None)


def _admin() -> dict[str, str]:
    return auth_header(
        create_access_token(
            user_id=uuid.uuid4(),
            email="admin@example.com",
            roles=["TENANT_ADMIN"],
            tenant_id=TEST_TENANT_ID,
            org_id=TEST_TENANT_ID,
        )
    )


def _guide(subject: str, email: str | None = None) -> dict[str, str]:
    email = email or _email(subject)
    return auth_header(keys.token(subject=subject, overrides={"email": email}))


def _last_month(now: datetime) -> dict[str, int]:
    year, month = (now.year, now.month - 1) if now.month > 1 else (now.year - 1, 12)
    return {"year": year, "month": month}


def _guide_routes() -> set[tuple[str, str]]:
    served = set()
    for route in app.routes:
        if isinstance(route, APIRoute) and route.path.startswith(PREFIXES):
            served |= {(m, route.path) for m in route.methods if m != "HEAD"}
    return served


async def test_every_guide_route_is_walked() -> None:
    assert _guide_routes() == EMPLOYER | PERSON | IDENTITY_PROVIDER


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------


async def _seed(client: AsyncClient, now: datetime, clock: GuideClock) -> dict[str, Any]:
    """The demo pilot (45 days, three months of teams, twelve devices), Guide's real
    connection over it, and people who could be named sending through the API while
    the month's intake was open. The clock is left at `now`."""
    clock.now = SENDING
    async with TestSessionLocal() as db:
        await seed_pilot(db, TEST_TENANT_ID, now, days_ago=45)
    r = await client.put(
        f"{GUIDE}/connection",
        json={"firebase_project_id": keys.PROJECT, "offered_kinds": list(ADOPTION_KINDS)},
        headers=_admin(),
    )
    assert r.status_code == 200, r.text

    period = _last_month(now)
    for subject in BOARD:
        r = await client.post(
            f"{GUIDE}/adoption",
            json={"team": BOARD_TEAM, "period": period, "reached": [BOARD_ONLY, CHATGPT]},
            headers=_guide(subject),
        )
        assert r.json() == {"counted": True}, r.text
    for i, subject in enumerate(SUPPORT):
        reached = [CHATGPT, FEW_IN_SUPPORT] if i < 2 else [CHATGPT]
        r = await client.post(
            f"{GUIDE}/adoption",
            json={"team": "support", "period": period, "reached": reached},
            headers=_guide(subject),
        )
        assert r.json() == {"counted": True}, r.text
    # A question is not a category...
    r = await client.post(
        f"{GUIDE}/adoption",
        json={"team": "support", "period": period, "reached": [{"kind": "topic", "id": QUESTION}]},
        headers=_guide(QUESTIONER),
    )
    assert r.status_code == 422
    # ...but a team is the organisation's own words, so a client can send one as a team.
    r = await client.post(
        f"{GUIDE}/adoption",
        json={"team": QUESTION, "period": period, "reached": [CHATGPT]},
        headers=_guide(QUESTIONER),
    )
    assert r.json() == {"counted": True}, r.text

    async with TestSessionLocal() as db:
        db.add(
            PromptEvent(
                tenant_id=TEST_TENANT_ID,
                source=PromptEventSource.SAFARI_EXTENSION,
                event_kind=PromptEventKind.VIOLATION,
                app_id=LONE_APP,
                action=PromptEventAction.BLOCKED,
                pii_categories={LONE_CATEGORY: 1, "email": 1},
                device_fingerprint=LONE_DEVICE,
                user_external_id=LONE_USER,
                session_id=LONE_SESSION,
                occurrences=3,
                occurred_at=now - timedelta(days=2),
            )
        )
        await db.commit()
        receipts = list((await db.execute(select(GuideAdoptionReceipt.receipt))).scalars())
    assert len(receipts) == len(BOARD) + len(SUPPORT) + 1
    clock.now = now

    return {
        "forbidden": {
            *SUBJECTS,
            *(_email(s) for s in SUBJECTS),
            *receipts,
            *(f"{DEMO_DEVICE_PREFIX}{d:02d}" for d in range(DEVICES)),
            LONE_DEVICE,
            LONE_USER,
            LONE_SESSION,
            LONE_APP,
            LONE_CATEGORY,
            QUESTION,
            BOARD_TEAM,
            "legal",  # the demo's team of six
            BOARD_ONLY["id"],
            FEW_IN_SUPPORT["id"],
            "kari@example.com",  # the test keys' default email
        },
    }


# ---------------------------------------------------------------------------
# The rules
# ---------------------------------------------------------------------------


def _numbers(value: Any, key: str = "") -> list[tuple[str, int]]:
    """Every (key, integer) in a JSON body. A dict of counts takes its own key."""
    if isinstance(value, bool):
        return []
    if isinstance(value, int):
        return [(key, value)]
    if isinstance(value, list):
        return [pair for item in value for pair in _numbers(item, key)]
    if isinstance(value, dict):
        if key in ("suppressed", "suppressed_categories"):
            return [(key, n) for n in value.values()]
        return [pair for k, v in value.items() for pair in _numbers(v, k)]
    return []


def _assert_no_person(label: str, r: Response, forbidden: set[str]) -> None:
    # Whole words only, so the seed's team "legal" is not found in "illegal".
    text = r.text
    found = sorted(f for f in forbidden if re.search(rf"(?<![\w-]){re.escape(f)}(?![\w-])", text))
    assert not found, f"{label} carries {found}: {text[:400]}"


def _assert_gated_numbers(label: str, r: Response) -> None:
    if not r.headers.get("content-type", "").startswith("application/json") or not r.content:
        return
    for key, n in _numbers(r.json()):
        assert key in ALLOWED_NUMBERS, f"{label}: a number nobody has looked at: {key}={n}"
        assert ALLOWED_NUMBERS[key](n), f"{label}: {key}={n} is not a figure the gate allows"


_RISK_ROW = re.compile(r"^\| (?P<key>[^|]+) \| (?P<events>\d+) \| (?P<devices>\d+) \|$", re.M)


def _assert_gated_markdown(label: str, r: Response) -> None:
    rows = _RISK_ROW.findall(r.text)
    assert rows, f"{label}: the risk tables are expected to have rows"
    for key, events, devices in rows:
        assert int(devices) >= 10, f"{label}: risk row {key!r} spans {devices} devices"
        assert int(events) >= 10, f"{label}: risk row {key!r} has {events} events"


# ---------------------------------------------------------------------------
# The walk
# ---------------------------------------------------------------------------


async def test_no_guide_route_hands_the_employer_anything_about_one_person(
    client: AsyncClient,
    guide_clock: GuideClock,
) -> None:
    now = NOW
    seeded = await _seed(client, now, guide_clock)
    forbidden: set[str] = seeded["forbidden"]
    walked: set[tuple[str, str]] = set()
    period = _last_month(now)
    period_text = f"{period['year']:04d}-{period['month']:02d}"

    async def employer(method: str, template: str, r: Response, *, markdown: bool = False) -> None:
        assert (method, template) in EMPLOYER
        walked.add((method, template))
        label = f"{method} {template}"
        assert r.status_code < 500, f"{label}: {r.status_code} {r.text}"
        _assert_no_person(label, r, forbidden | {SCIM_USER})
        _assert_gated_numbers(label, r)
        if markdown:
            _assert_gated_markdown(label, r)

    async def person(method: str, template: str, r: Response, own: set[str]) -> None:
        # The person's own Guide may hear about them; nobody else's.
        assert (method, template) in PERSON
        walked.add((method, template))
        label = f"{method} {template}"
        assert r.status_code < 500, f"{label}: {r.status_code} {r.text}"
        _assert_no_person(label, r, forbidden - own)
        _assert_gated_numbers(label, r)

    # -- The employer: the admin console's one page, and the APIs behind it ----------
    h = _admin()
    await employer("GET", f"{GUIDE}/connection", await client.get(f"{GUIDE}/connection", headers=h))
    await employer(
        "PUT",
        f"{GUIDE}/connection",
        await client.put(
            f"{GUIDE}/connection",
            json={"firebase_project_id": keys.PROJECT, "offered_kinds": list(ADOPTION_KINDS)},
            headers=h,
        ),
    )
    report = await client.get(f"{GUIDE}/adoption/report", params={"period": period_text}, headers=h)
    assert report.status_code == 200, report.text
    body = report.json()
    # The seed did reach the gate: support shows; the small teams (legal, board and
    # the question) are neither named nor counted, only the fixed note is said.
    assert any(f["team"] == "support" for f in body["figures"])
    assert "teams_too_small_count" not in body
    assert body["small_teams_note"] == "Teams with fewer than 10 counted people are not shown."
    assert body["suppressed_categories"].get("support") == 1
    await employer("GET", f"{GUIDE}/adoption/report", report)
    # The month running is not shown at all while it can still change.
    running = await client.get(f"{GUIDE}/adoption/report", params={"period": "2026-10"}, headers=h)
    assert running.status_code == 409, running.text
    assert running.json()["error"]["code"] == "MONTH_STILL_COLLECTING"
    await employer("GET", f"{GUIDE}/adoption/report", running)

    pilot = await client.get(f"{GUIDE}/pilot-report", headers=h)
    assert pilot.status_code == 200, pilot.text
    assert pilot.json()["risk"]["by_app"], "the risk section is expected to have rows"
    await employer("GET", f"{GUIDE}/pilot-report", pilot)
    await employer(
        "GET",
        f"{GUIDE}/pilot-report",
        await client.get(f"{GUIDE}/pilot-report", params={"format": "markdown"}, headers=h),
        markdown=True,
    )
    # Changing a parameter does not reach a person: it is refused, and says why.
    for name in PERSON_PARAMETERS:
        for path, params in (
            (f"{GUIDE}/adoption/report", {"period": period_text, name: SUBJECTS[0]}),
            (f"{GUIDE}/pilot-report", {name: SUBJECTS[0]}),
        ):
            r = await client.get(path, params=params, headers=h)
            assert r.status_code == 400, f"{path}?{name}= answered {r.status_code}"
            assert r.json()["error"]["code"] == "ADOPTION_IS_BY_TEAM"
            await employer("GET", path, r)

    # -- The person's own Guide -----------------------------------------------------
    kari = SUPPORT[0]
    own = {kari, _email(kari)}
    await person(
        "GET", f"{GUIDE}/offer", await client.get(f"{GUIDE}/offer", headers=_guide(kari)), own
    )
    await person(
        "GET",
        f"{GUIDE}/approved-tools",
        await client.get(f"{GUIDE}/approved-tools", headers=_guide(kari)),
        own,
    )
    # The work client's 90 days are about the organisation: nobody, not even Kari.
    await person(
        "GET",
        f"{GUIDE}/work-client",
        await client.get(f"{GUIDE}/work-client", headers=_guide(kari)),
        set(),
    )
    await person(
        "POST",
        f"{GUIDE}/adoption",
        await client.post(
            f"{GUIDE}/adoption",
            json={"team": "support", "period": period, "reached": [CHATGPT]},
            headers=_guide(kari),
        ),
        own,
    )

    # -- The identity provider --------------------------------------------------------
    token = await client.post(f"{GUIDE}/scim-token", headers=h)
    assert token.status_code == 200, token.text
    await employer("POST", f"{GUIDE}/scim-token", token)
    s = {
        "Authorization": f"Bearer {token.json()['token']}",
        "Content-Type": "application/scim+json",
    }
    directory_keys = {"schemas", "id", "userName", "active", "meta", "externalId"}
    group_keys = {"schemas", "id", "displayName", "members", "meta", "externalId"}
    # What Guide noted is never in a SCIM answer; the directory itself is the IdP's.
    noted = forbidden - {_email(s) for s in SUBJECTS}

    async def idp(method: str, template: str, r: Response) -> Response:
        assert (method, template) in IDENTITY_PROVIDER
        walked.add((method, template))
        label = f"{method} {template}"
        assert r.status_code < 500, f"{label}: {r.status_code} {r.text}"
        _assert_no_person(label, r, noted)
        if r.content and template != f"{SCIM}/ServiceProviderConfig":
            data = r.json()
            for item in data.get("Resources", [data]):
                if "userName" in item:
                    assert set(item) <= directory_keys, f"{label}: {sorted(item)}"
                elif "displayName" in item:
                    assert set(item) <= group_keys, f"{label}: {sorted(item)}"
        return r

    user = {"schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"], "userName": SCIM_USER}
    created = await idp(
        "POST", f"{SCIM}/Users", await client.post(f"{SCIM}/Users", json=user, headers=s)
    )
    assert created.status_code == 201, created.text
    user_id = created.json()["id"]
    # Kari is in the directory too, so her own Guide can ask for her groups.
    kari_dir = await client.post(
        f"{SCIM}/Users", json={**user, "userName": _email(kari)}, headers=s
    )
    kari_id = kari_dir.json()["id"]
    group = await idp(
        "POST",
        f"{SCIM}/Groups",
        await client.post(
            f"{SCIM}/Groups",
            json={"displayName": SCIM_GROUP, "members": [{"value": user_id}, {"value": kari_id}]},
            headers=s,
        ),
    )
    assert group.status_code == 201, group.text
    group_id = group.json()["id"]
    await idp("GET", f"{SCIM}/Users", await client.get(f"{SCIM}/Users", headers=s))
    await idp(
        "GET", f"{SCIM}/Users/{{user_id}}", await client.get(f"{SCIM}/Users/{user_id}", headers=s)
    )
    await idp(
        "PUT",
        f"{SCIM}/Users/{{user_id}}",
        await client.put(f"{SCIM}/Users/{user_id}", json={**user, "active": True}, headers=s),
    )
    patch = {
        "schemas": [PATCH_OP],
        "Operations": [{"op": "Replace", "path": "active", "value": "True"}],
    }
    await idp(
        "PATCH",
        f"{SCIM}/Users/{{user_id}}",
        await client.patch(f"{SCIM}/Users/{user_id}", json=patch, headers=s),
    )
    await idp("GET", f"{SCIM}/Groups", await client.get(f"{SCIM}/Groups", headers=s))
    await idp(
        "GET",
        f"{SCIM}/Groups/{{group_id}}",
        await client.get(f"{SCIM}/Groups/{group_id}", headers=s),
    )
    rename = {
        "schemas": [PATCH_OP],
        "Operations": [{"op": "Replace", "path": "displayName", "value": SCIM_GROUP}],
    }
    await idp(
        "PATCH",
        f"{SCIM}/Groups/{{group_id}}",
        await client.patch(f"{SCIM}/Groups/{group_id}", json=rename, headers=s),
    )
    await idp(
        "GET",
        f"{SCIM}/ServiceProviderConfig",
        await client.get(f"{SCIM}/ServiceProviderConfig", headers=s),
    )

    # Kari's own Guide hears her groups; the employer's reports still name nobody.
    groups = await client.get(f"{GUIDE}/groups", headers=_guide(kari))
    assert groups.json() == {"groups": [SCIM_GROUP]}
    await person("GET", f"{GUIDE}/groups", groups, own)
    await employer(
        "GET", f"{GUIDE}/pilot-report", await client.get(f"{GUIDE}/pilot-report", headers=h)
    )

    await idp(
        "DELETE",
        f"{SCIM}/Groups/{{group_id}}",
        await client.delete(f"{SCIM}/Groups/{group_id}", headers=s),
    )
    await idp(
        "DELETE",
        f"{SCIM}/Users/{{user_id}}",
        await client.delete(f"{SCIM}/Users/{user_id}", headers=s),
    )
    await employer(
        "DELETE", f"{GUIDE}/scim-token", await client.delete(f"{GUIDE}/scim-token", headers=h)
    )

    assert walked == _guide_routes(), f"not walked: {sorted(_guide_routes() - walked)}"
