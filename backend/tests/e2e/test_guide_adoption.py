"""Promptly Guide's adoption figures, end to end (promptly-guide #27, E15).

Guide's half is built and tested on its side, Atlas's half on this one, and each
against `tests/contracts/atlas-guide.json`. This drives the running stack the way a
fleet of Macs would, to check the halves meet:

  * every Mac in a team sends its finished month as Guide's `AdoptionSender` does:
    ask the offer, then post the contribution, each with the person's Firebase ID
    token as the only credential;
  * the admin's report then shows the team, in bands;
  * a team under ten shows nothing, not even its name (promptly-guide #28, E4);
  * a payload Guide would never send (a person, an exact count) is refused.

The requests are built from the contract and from Guide's own code
(`Sources/GuideAdoption/AtlasAdoption.swift`, `AdoptionConsent.swift`,
`Sources/GuideApp/AdoptionSender.swift` in promptly-guide), not from Atlas's tests.

Firebase ID tokens can only come from Google, so the stack is started with
`GUIDE_TEST_FIREBASE_CERTS` naming a throwaway certificate, and the tests sign with
its key, at `E2E_FIREBASE_KEY_FILE` (CI makes both: ci.yml, E2E job).
"""

from __future__ import annotations

import json
import os
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import asyncpg
import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwt

from tests.e2e.stack import BASE_URL, TenantUser, auth, tenant_user

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]

CONTRACT = json.loads((Path(__file__).parents[1] / "contracts" / "atlas-guide.json").read_text())
# Guide is given the organisation's Atlas address and appends the contract's paths
# to it (`AtlasAdoption.offerPath`, `contributionPath`).
ATLAS = BASE_URL.removesuffix("/").removesuffix("/api/v1")
MINIMUM_GROUP_SIZE = 10


def _interaction(name: str) -> dict:
    return next(i for i in CONTRACT["interactions"] if i["name"] == name)


def _last_finished_month(now: datetime) -> dict[str, int]:
    """`AdoptionPeriod.lastFinished(before:)`, in UTC as Guide reckons it. Atlas takes
    only finished months, so the contract's own month is replaced with this one."""
    if now.month == 1:
        return {"year": now.year - 1, "month": 12}
    return {"year": now.year, "month": now.month - 1}


# ---------------------------------------------------------------------------
# The Firebase project's stand-in
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Issuer:
    kid: str
    key: str

    def token(self, project: str, subject: str) -> str:
        """An ID token shaped as Firebase issues one (see tests/unit/firebase_keys.py)."""
        now = int(time.time())
        claims = {
            "iss": f"https://securetoken.google.com/{project}",
            "aud": project,
            "sub": subject,
            "iat": now - 10,
            "exp": now + 3600,
            "auth_time": now - 10,
            "firebase": {"sign_in_provider": "saml.entra"},
        }
        return jwt.encode(claims, self.key, algorithm="RS256", headers={"kid": self.kid})


@pytest.fixture(scope="module")
def issuer() -> Issuer:
    certs = os.environ.get("GUIDE_TEST_FIREBASE_CERTS", "")
    key_file = os.environ.get("E2E_FIREBASE_KEY_FILE", "")
    if not certs or not key_file:
        # CI always sets both; a skip there would hide that this stopped running.
        if os.environ.get("CI"):
            pytest.fail("GUIDE_TEST_FIREBASE_CERTS and E2E_FIREBASE_KEY_FILE must be set")
        pytest.skip("No test Firebase key: see this module's docstring")
    (kid,) = json.loads(certs)
    return Issuer(kid=kid, key=Path(key_file).read_text())


# ---------------------------------------------------------------------------
# One Mac, as Guide's AdoptionSender sends
# ---------------------------------------------------------------------------


def outcome(resp: httpx.Response) -> str:
    """`AtlasAdoption.outcome(status:body:)`."""
    if resp.status_code == 200:
        counted = resp.json().get("counted")
        if counted is True:
            return "counted"
        if counted is False:
            return "alreadyCounted"
        return "tryLater"
    if resp.status_code in (401, 403):
        return "notConnected"
    if resp.status_code in (400, 409, 422):
        return "refused"
    return "tryLater"


async def offer(client: httpx.AsyncClient, token: str) -> httpx.Response:
    request = _interaction("the offer")["request"]
    return await client.request(
        request["method"],
        f"{ATLAS}/{request['path']}",
        headers={"Authorization": "Bearer " + token},
    )


async def post_contribution(client: httpx.AsyncClient, token: str, body: dict) -> httpx.Response:
    """`AtlasAdoption.contributionRequest`: the JSON body, its content type, the token."""
    request = _interaction("a month's contribution, counted")["request"]
    return await client.request(
        request["method"],
        f"{ATLAS}/{request['path']}",
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
        # Foundation's JSONEncoder escapes "/" by default, so "chatgpt/model" goes out as
        # "chatgpt\/model". Sent the same way here; a "/" only ever appears in a string.
        content=json.dumps(body).replace("/", "\\/").encode(),
    )


async def send_month(
    client: httpx.AsyncClient,
    token: str,
    team: str,
    period: dict[str, int],
    reached: list[dict[str, str]],
) -> str:
    """`AdoptionSender.sendIfDue` for one opted-in Mac: what the organisation counts
    now, then the team, the month and the categories reached of those kinds, sorted
    (`AdoptionConsent.contribution`)."""
    resp = await offer(client, token)
    assert resp.status_code == 200, resp.text
    offered = set(resp.json()["offered_kinds"])
    kept = sorted((r for r in reached if r["kind"] in offered), key=lambda r: (r["kind"], r["id"]))
    body = {"team": team, "period": period, "reached": kept}
    return outcome(await post_contribution(client, token, body))


# ---------------------------------------------------------------------------
# A connected organisation, removed again afterwards
# ---------------------------------------------------------------------------


@dataclass
class Organisation:
    admin: TenantUser
    project: str
    period: dict[str, int]

    def device(self, issuer: Issuer, n: int | str) -> str:
        """A person's ID token. Each Mac is a different Firebase user."""
        return issuer.token(self.project, f"{self.project}-person-{n}")


async def remove_tenant(tenant_id: str) -> None:
    """Deletes the tenant and every row that names it, in the stack's own database.

    There is no API that removes a tenant, a Guide connection or Guide's counts, so
    this goes to the database CI hands the E2E step (DATABASE_URL), and touches only
    rows of the tenant this test made."""
    url = os.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")
    if not url:
        if os.environ.get("CI"):
            pytest.fail("DATABASE_URL must be set to remove what the test made")
        return
    conn = await asyncpg.connect(url)
    try:
        async with conn.transaction():
            await _delete_tenant_rows(conn, tenant_id)
    finally:
        await conn.close()


async def _delete_tenant_rows(conn: asyncpg.Connection, tenant_id: str) -> None:
    tables = [
        row["name"]
        for row in await conn.fetch(
            """
            SELECT format('%I.%I', c.table_schema, c.table_name) AS name
            FROM information_schema.columns c
            JOIN information_schema.tables t
              ON t.table_schema = c.table_schema AND t.table_name = c.table_name
            WHERE c.column_name = 'tenant_id' AND t.table_type = 'BASE TABLE'
              AND c.table_schema NOT IN ('pg_catalog', 'information_schema')
            """
        )
    ]
    # Rows that point at other rows of the tenant (a user's API key at the user) have to
    # go first. Rounds find that order, rather than a list that a new table would outdate.
    pending = set(tables)
    while pending:
        removed = set()
        for table in sorted(pending):
            try:
                async with conn.transaction():
                    # `table` is format('%I.%I') of the catalogue's own names.
                    sql = f"DELETE FROM {table} WHERE tenant_id::text = $1"  # noqa: S608
                    await conn.execute(sql, tenant_id)
                removed.add(table)
            except asyncpg.ForeignKeyViolationError:
                continue
        assert removed, f"rows of tenant {tenant_id} could not be removed from {sorted(pending)}"
        pending -= removed
    await conn.execute("DELETE FROM grc.tenants WHERE id::text = $1", tenant_id)


@pytest.fixture
async def organisation() -> AsyncIterator[Organisation]:
    run = uuid.uuid4().hex[:8]
    async with httpx.AsyncClient(timeout=30.0) as client:
        admin = await tenant_user(client, f"guide-{run}")
        project = f"e2e-guide-{run}"
        resp = await client.put(
            f"{BASE_URL}/guide/connection",
            headers=auth(admin.token),
            json={
                "firebase_project_id": project,
                "firebase_tenant_id": "",
                "offered_kinds": _interaction("the offer")["given"]["offered_kinds"],
            },
        )
        assert resp.status_code == 200, resp.text
    try:
        yield Organisation(
            admin=admin, project=project, period=_last_finished_month(datetime.now(UTC))
        )
    finally:
        await remove_tenant(admin.tenant_id)


async def report(client: httpx.AsyncClient, org: Organisation, **params: str) -> httpx.Response:
    period = f"{org.period['year']:04d}-{org.period['month']:02d}"
    return await client.get(
        f"{BASE_URL}/guide/adoption/report",
        headers=auth(org.admin.token),
        params={"period": period, **params},
    )


# ---------------------------------------------------------------------------
# The tests
# ---------------------------------------------------------------------------

# The contract's contribution: one Mac that reached a category of every kind.
CONTRACT_BODY = _interaction("a month's contribution, counted")["request"]["body"]
APP, COMPLETION, FRICTION, TOPIC = CONTRACT_BODY["reached"]
assert [APP["kind"], COMPLETION["kind"], FRICTION["kind"], TOPIC["kind"]] == [
    "app",
    "completion",
    "friction",
    "topic",
]
TEAM = CONTRACT_BODY["team"]
# A team one short of the minimum. Every Mac in it reaches every category.
SMALL_TEAM = "Legal and Compliance"
SALES_SIZE = 20


def sales_reached(n: int) -> list[dict[str, str]]:
    """What the n-th of twenty Macs in the team reached. Everyone used the app, 15
    finished a walkthrough, 10 asked about the topic, 4 stopped at the same step."""
    reached = [APP]
    if n < 15:
        reached.append(COMPLETION)
    if n < 10:
        reached.append(TOPIC)
    if n < 4:
        reached.append(FRICTION)
    return reached


async def test_a_team_of_macs_reaches_the_report_in_bands(
    organisation: Organisation, issuer: Issuer
) -> None:
    org = organisation
    async with httpx.AsyncClient(timeout=30.0) as client:
        # The first Mac sends the contract's own body, word for word, for the month.
        first = org.device(issuer, 0)
        resp = await offer(client, first)
        assert resp.status_code == 200, resp.text
        assert resp.json() == _interaction("the offer")["response"]["body"]
        resp = await post_contribution(client, first, {**CONTRACT_BODY, "period": org.period})
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"counted": True}

        for n in range(1, SALES_SIZE):
            sent = await send_month(
                client, org.device(issuer, n), TEAM, org.period, sales_reached(n)
            )
            assert sent == "counted", n

        for n in range(MINIMUM_GROUP_SIZE - 1):
            sent = await send_month(
                client,
                org.device(issuer, f"small-{n}"),
                SMALL_TEAM,
                org.period,
                [APP, COMPLETION, FRICTION, TOPIC],
            )
            assert sent == "counted", n

        # A lost response, sent again: nothing more to send, and nothing counted twice.
        assert await send_month(client, first, TEAM, org.period, sales_reached(0)) == (
            "alreadyCounted"
        )

        resp = await report(client, org)
        assert resp.status_code == 200, resp.text
        body = resp.json()

    figures = {(f["category_kind"], f["category_id"]): f for f in body["figures"]}
    assert all(f["team"] == TEAM for f in body["figures"])
    assert {k: (f["band"], f["band_lower"]) for k, f in figures.items()} == {
        ("app", APP["id"]): ("80% or more", 80),  # 20 of 20: never "all of them"
        ("completion", COMPLETION["id"]): ("70–79%", 70),  # 15 of 20
        ("topic", TOPIC["id"]): ("50–59%", 50),  # 10 of 20, the least a figure needs
    }
    # Four people stopped at the step: suppressed, and only counted as suppressed.
    assert body["suppressed_categories"] == {TEAM: 1}
    # A figure is a band. No count of people, and no team total, travels with it.
    assert all(
        set(f) == {"team", "category_kind", "category_id", "band", "band_lower"}
        for f in body["figures"]
    )
    # Nine people is one short: the team is counted as too small and not named.
    assert body["teams_too_small_count"] == 1
    assert SMALL_TEAM not in resp.text
    assert body["minimum_group_size"] == MINIMUM_GROUP_SIZE


async def test_what_guide_never_sends_is_refused(
    organisation: Organisation, issuer: Issuer
) -> None:
    org = organisation
    token = org.device(issuer, "refused")
    contribution = {**CONTRACT_BODY, "period": org.period}
    async with httpx.AsyncClient(timeout=30.0) as client:
        assert (await offer(client, token)).status_code == 200

        never_sent = {
            "a person": {**contribution, "user_id": "kari@nordlys.example"},
            "an email": {**contribution, "email": "kari@nordlys.example"},
            "a device": {**contribution, "device_id": "C02XK0AAJG5H"},
            "an exact team count": {**contribution, "contributors": 20},
            "an exact category count": {
                **contribution,
                "reached": [{**APP, "people": 20}, *contribution["reached"][1:]],
            },
            "a time finer than a month": {
                **contribution,
                "period": {**org.period, "day": 14},
            },
            "the month still running": {
                **contribution,
                "period": {"year": datetime.now(UTC).year, "month": datetime.now(UTC).month},
            },
            "free text as a topic": {
                **contribution,
                "reached": [{"kind": "topic", "id": "How do I export the Q3 forecast?"}],
            },
        }
        for why, body in never_sent.items():
            resp = await post_contribution(client, token, body)
            assert resp.status_code == 422, (why, resp.status_code, resp.text)
            assert outcome(resp) == "refused", why

        # A kind the organisation stopped offering: the contract's refusal, exactly.
        resp = await client.put(
            f"{BASE_URL}/guide/connection",
            headers=auth(org.admin.token),
            json={"firebase_project_id": org.project, "offered_kinds": ["completion"]},
        )
        assert resp.status_code == 200, resp.text
        refusal = _interaction("a kind the organisation no longer offers")["response"]
        resp = await post_contribution(client, token, contribution)
        assert resp.status_code == refusal["status"], resp.text
        assert resp.json()["error"]["code"] == refusal["body"]["error"]["code"]

        # None of that was counted: the same person's real month still is.
        assert await send_month(client, token, TEAM, org.period, [COMPLETION]) == "counted"

        # Nor can the admin ask about one person.
        resp = await report(client, org, user="kari@nordlys.example")
        assert resp.status_code == 400, resp.text
        assert resp.json()["error"]["code"] == "ADOPTION_IS_BY_TEAM"


async def test_an_organisation_that_has_not_connected_guide(issuer: Issuer) -> None:
    unconnected = _interaction("an organisation that has not connected Guide")
    token = issuer.token(f"e2e-guide-unconnected-{uuid.uuid4().hex[:8]}", "someone")
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await offer(client, token)
        assert resp.status_code == unconnected["response"]["status"]
        assert outcome(resp) == "notConnected"
        resp = await post_contribution(client, token, CONTRACT_BODY)
        assert outcome(resp) == "notConnected"


async def test_a_token_signed_by_another_key_is_refused(
    organisation: Organisation, issuer: Issuer
) -> None:
    """The stack trusts the test key and nothing else: the switch is a stand-in for
    Google's keys, not a way past checking them."""
    stranger = Issuer(
        kid=issuer.kid,
        key=rsa.generate_private_key(public_exponent=65537, key_size=2048)
        .private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        .decode(),
    )
    token = stranger.token(organisation.project, "someone")
    async with httpx.AsyncClient(timeout=30.0) as client:
        assert outcome(await offer(client, token)) == "notConnected"
        body = {**CONTRACT_BODY, "period": organisation.period}
        assert outcome(await post_contribution(client, token, body)) == "notConnected"
