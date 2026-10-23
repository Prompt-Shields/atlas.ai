"""Guide's admin console in Atlas: settings and downloads (promptly-guide N1, option A).

`GET/PUT /guide/console` holds the organisation's console document; the download
routes hand out the configuration profiles and Windows files made from it, the same
bytes Guide's file console writes (``tests/fixtures/guide_console``, copied from
promptly-guide; see ``test_guide_console_profiles.py``). Tenant admins only, scoped to
the admin's own tenant, and every change and download is audited. No MDM is called.
"""

from __future__ import annotations

import io
import json
import uuid
import zipfile
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.auth.jwt import create_access_token
from app.models.audit_log import AuditLog
from app.models.guide_console import GuideConsoleSettings
from app.models.tenant import Tenant
from app.models.user import Role, User, UserRole
from tests.conftest import TestSessionLocal, auth_header

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

CONSOLE = "/api/v1/guide/console"
PROFILES_ZIP = f"{CONSOLE}/profiles.zip"
WINDOWS_ZIP = f"{CONSOLE}/windows.zip"
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "guide_console"
UPDATED = "guide.console.updated"
DOWNLOADED = "guide.console.downloaded"


def _console(case: str = "example") -> dict:
    return json.loads((FIXTURES / case / "console.json").read_text(encoding="utf-8"))


def _fixture(case: str, kind: str, name: str) -> bytes:
    return (FIXTURES / case / kind / name).read_bytes()


async def _seed(*, role: Role = Role.TENANT_ADMIN) -> tuple[uuid.UUID, str]:
    tid, uid = uuid.uuid4(), uuid.uuid4()
    email = f"admin-{uid.hex[:8]}@acme.test"
    async with TestSessionLocal() as db:
        db.add(Tenant(id=tid, name="Acme", slug=f"acme-{tid.hex}", is_active=True))
        db.add(
            User(
                id=uid,
                email=email,
                hashed_password="x",
                full_name="Admin",
                tenant_id=tid,
                is_email_verified=True,
                is_active=True,
            )
        )
        await db.flush()
        db.add(UserRole(user_id=uid, role=role, tenant_id=tid))
        await db.commit()
    token = create_access_token(
        user_id=uid, email=email, roles=[role.value], tenant_id=tid, org_id=None
    )
    return tid, token


async def _audit(tid: uuid.UUID, event: str) -> list[AuditLog]:
    async with TestSessionLocal() as db:
        rows = await db.execute(
            select(AuditLog)
            .where(AuditLog.event_type == event, AuditLog.tenant_id == tid)
            .order_by(AuditLog.timestamp)
        )
        return list(rows.scalars())


async def _stored(tid: uuid.UUID) -> GuideConsoleSettings | None:
    async with TestSessionLocal() as db:
        return (
            await db.execute(
                select(GuideConsoleSettings).where(GuideConsoleSettings.tenant_id == tid)
            )
        ).scalar_one_or_none()


async def _save(client: AsyncClient, token: str, console: dict, note: str | None = None):
    body: dict = {"console": console}
    if note is not None:
        body["note"] = note
    return await client.put(CONSOLE, json=body, headers=auth_header(token))


# ── Settings ───────────────────────────────────────────────────────────────


async def test_nothing_saved_yet(client: AsyncClient) -> None:
    _, token = await _seed()
    resp = await client.get(CONSOLE, headers=auth_header(token))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["console"] is None
    assert body["revision"] == 0
    assert body["profiles"] == [] and body["windows_files"] == []


async def test_admin_saves_the_settings_and_sees_the_files(client: AsyncClient) -> None:
    tid, token = await _seed()
    resp = await _save(client, token, _console(), note="Finance pilot")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["console"] == _console()
    assert body["revision"] == 1
    assert body["updated_by"].startswith("admin-")
    assert body["problem"] is None
    assert body["profiles"] == [
        {"name": "promptly-guide-everyone.mobileconfig", "group": None},
        {"name": "promptly-guide-group-finance.mobileconfig", "group": "finance"},
        {"name": "promptly-guide-group-sales.mobileconfig", "group": "sales"},
    ]
    assert {f["name"] for f in body["windows_files"]} == {
        "PromptlyGuide.admx",
        "en-US/PromptlyGuide.adml",
        "promptly-guide-everyone.reg",
        "promptly-guide-groups-finance.reg",
        "promptly-guide-groups-sales.reg",
    }
    assert any("it-runbooks" in n for n in body["windows_notes"])
    assert (await client.get(CONSOLE, headers=auth_header(token))).json() == body
    assert (await _stored(tid)).console == _console()


async def test_a_save_is_audited_with_every_changed_key(client: AsyncClient) -> None:
    tid, token = await _seed()
    await _save(client, token, _console(), note="Finance pilot")
    changed = _console()
    changed["groups"]["finance"]["excludedSites"].append("payroll.nordlys.no")
    resp = await _save(client, token, changed)
    assert resp.json()["revision"] == 2

    first, second = await _audit(tid, UPDATED)
    assert first.action == "create"
    first_details = json.loads(first.details)
    assert first_details["note"] == "Finance pilot"
    assert [c["change"] for c in first_details["changes"]] == ["added", "added", "added"]

    details = json.loads(second.details)
    assert second.action == "update"
    assert second.actor_email.startswith("admin-")
    assert details["revision"] == 2
    assert len(details["console_sha256"]) == 64
    assert details["changes"] == [
        {
            "profile": "promptly-guide-group-finance.mobileconfig",
            "change": "changed",
            "keys": [
                {
                    "key": "ExcludedSites.finance",
                    "from": ["ledger.nordlys.no"],
                    "to": ["ledger.nordlys.no", "payroll.nordlys.no"],
                }
            ],
        }
    ]


async def test_saving_the_same_settings_changes_and_logs_nothing(client: AsyncClient) -> None:
    tid, token = await _seed()
    await _save(client, token, _console())
    resp = await _save(client, token, _console())
    assert resp.json()["revision"] == 1
    assert len(await _audit(tid, UPDATED)) == 1


async def test_a_group_dropped_is_logged_as_its_profile_removed(client: AsyncClient) -> None:
    tid, token = await _seed()
    await _save(client, token, _console())
    fewer = _console()
    del fewer["groups"]["sales"]
    body = (await _save(client, token, fewer)).json()
    assert [p["group"] for p in body["profiles"]] == [None, "finance"]
    details = json.loads((await _audit(tid, UPDATED))[-1].details)
    assert details["changes"][0]["profile"] == "promptly-guide-group-sales.mobileconfig"
    assert details["changes"][0]["change"] == "removed"


@pytest.mark.parametrize(
    ("change", "words"),
    [
        (lambda c: c["everyone"].update({"promptTips": True}), "unknown field promptTips"),
        (lambda c: c["everyone"].update({"cloudModelEnabled": True}), "unknown field"),
        (lambda c: c["everyone"].update({"exludedApps": ["x.y"]}), "unknown field exludedApps"),
        (lambda c: c["groups"]["sales"].update({"skillPacksOff": ["gemini"]}), "no skill pack"),
        (lambda c: c["everyone"].update({"excludedWindowsApps": ["Casebook"]}), "casebook.exe"),
        (lambda c: c.pop("identifier"), "identifier"),
    ],
)
async def test_settings_the_file_console_refuses_are_refused(
    client: AsyncClient, change, words: str
) -> None:
    tid, token = await _seed()
    console = _console()
    change(console)
    resp = await _save(client, token, console)
    assert resp.status_code == 422, resp.text
    error = resp.json()["error"]
    assert error["code"] == "GUIDE_CONSOLE_INVALID"
    assert words in error["message"]
    assert await _stored(tid) is None
    assert await _audit(tid, UPDATED) == []


@pytest.mark.parametrize("body", [{}, {"console": []}, {"console": {}, "x": 1}])
async def test_a_malformed_request_is_refused(client: AsyncClient, body: dict) -> None:
    tid, token = await _seed()
    resp = await client.put(CONSOLE, json=body, headers=auth_header(token))
    assert resp.status_code == 422
    assert await _stored(tid) is None


async def test_settings_this_guide_no_longer_accepts_are_shown_without_files(
    client: AsyncClient,
) -> None:
    tid, token = await _seed()
    stale = _console()
    stale["groups"]["sales"]["skillPacksOff"] = ["a-pack-guide-dropped"]
    async with TestSessionLocal() as db:
        db.add(GuideConsoleSettings(tenant_id=tid, console=stale, revision=3))
        await db.commit()
    body = (await client.get(CONSOLE, headers=auth_header(token))).json()
    assert body["console"] == stale
    assert "no skill pack" in body["problem"]
    assert body["profiles"] == []
    resp = await client.get(PROFILES_ZIP, headers=auth_header(token))
    assert resp.status_code == 422
    assert await _audit(tid, DOWNLOADED) == []

    # Saving good settings over them works, and every profile is logged as added.
    body = (await _save(client, token, _console())).json()
    assert body["problem"] is None and body["revision"] == 4
    details = json.loads((await _audit(tid, UPDATED))[-1].details)
    assert {c["change"] for c in details["changes"]} == {"added"}


# ── Downloads: the file console's own bytes ────────────────────────────────


@pytest.mark.parametrize("case", ["example", "full"])
async def test_each_profile_downloads_as_the_file_console_wrote_it(
    client: AsyncClient, case: str
) -> None:
    _, token = await _seed()
    body = (await _save(client, token, _console(case))).json()
    for entry in body["profiles"]:
        resp = await client.get(f"{CONSOLE}/profiles/{entry['name']}", headers=auth_header(token))
        assert resp.status_code == 200, resp.text
        assert resp.headers["content-type"] == "application/x-apple-aspen-config"
        assert entry["name"] in resp.headers["content-disposition"]
        assert resp.content == _fixture(case, "mac", entry["name"])


async def test_every_profile_in_one_zip(client: AsyncClient) -> None:
    _, token = await _seed()
    await _save(client, token, _console("full"))
    resp = await client.get(PROFILES_ZIP, headers=auth_header(token))
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "application/zip"
    archive = zipfile.ZipFile(io.BytesIO(resp.content))
    names = sorted(archive.namelist())
    assert names == sorted(p.name for p in (FIXTURES / "full" / "mac").iterdir())
    for name in names:
        assert archive.read(name) == _fixture("full", "mac", name)
    # The same settings give the same archive.
    assert (await client.get(PROFILES_ZIP, headers=auth_header(token))).content == resp.content


async def test_the_windows_files_in_one_zip(client: AsyncClient) -> None:
    _, token = await _seed()
    await _save(client, token, _console("full"))
    resp = await client.get(WINDOWS_ZIP, headers=auth_header(token))
    assert resp.status_code == 200, resp.text
    archive = zipfile.ZipFile(io.BytesIO(resp.content))
    folder = FIXTURES / "full" / "windows"
    expected = {
        p.relative_to(folder).as_posix(): p.read_bytes() for p in folder.rglob("*") if p.is_file()
    }
    assert {n: archive.read(n) for n in archive.namelist()} == expected


async def test_a_download_is_audited_with_the_files_hashes(client: AsyncClient) -> None:
    tid, token = await _seed()
    await _save(client, token, _console())
    name = "promptly-guide-group-finance.mobileconfig"
    await client.get(f"{CONSOLE}/profiles/{name}", headers=auth_header(token))
    (entry,) = await _audit(tid, DOWNLOADED)
    details = json.loads(entry.details)
    assert details["revision"] == 1
    assert list(details["files"]) == [name]
    assert len(details["files"][name]) == 64


async def test_no_settings_no_download(client: AsyncClient) -> None:
    _, token = await _seed()
    for url in (
        PROFILES_ZIP,
        WINDOWS_ZIP,
        f"{CONSOLE}/profiles/promptly-guide-everyone.mobileconfig",
    ):
        assert (await client.get(url, headers=auth_header(token))).status_code == 404


async def test_a_profile_the_settings_do_not_make_is_not_found(client: AsyncClient) -> None:
    _, token = await _seed()
    await _save(client, token, _console())
    for name in ("promptly-guide-group-legal.mobileconfig", "console.json", "..%2Fx"):
        resp = await client.get(f"{CONSOLE}/profiles/{name}", headers=auth_header(token))
        assert resp.status_code == 404


# ── Who may ────────────────────────────────────────────────────────────────


async def test_an_admin_sees_and_downloads_only_their_own_tenants(client: AsyncClient) -> None:
    tid_a, token_a = await _seed()
    tid_b, token_b = await _seed()
    await _save(client, token_a, _console("full"))
    assert (await client.get(CONSOLE, headers=auth_header(token_b))).json()["console"] is None
    assert (await client.get(PROFILES_ZIP, headers=auth_header(token_b))).status_code == 404
    await _save(client, token_b, _console("example"))
    assert (await _stored(tid_a)).console == _console("full")
    assert (await _stored(tid_b)).console == _console("example")


@pytest.mark.parametrize("role", [Role.ORG_ADMIN, Role.ANALYST, Role.VIEWER])
async def test_only_tenant_admins_see_change_or_download(client: AsyncClient, role: Role) -> None:
    tid, token = await _seed(role=role)
    async with TestSessionLocal() as db:
        db.add(GuideConsoleSettings(tenant_id=tid, console=_console(), revision=1))
        await db.commit()
    assert (await client.get(CONSOLE, headers=auth_header(token))).status_code == 403
    assert (await _save(client, token, _console("full"))).status_code == 403
    for url in (
        PROFILES_ZIP,
        WINDOWS_ZIP,
        f"{CONSOLE}/profiles/promptly-guide-everyone.mobileconfig",
    ):
        assert (await client.get(url, headers=auth_header(token))).status_code == 403
    assert (await _stored(tid)).console == _console()
    assert await _audit(tid, UPDATED) == []
    assert await _audit(tid, DOWNLOADED) == []


async def test_no_token_no_reply(client: AsyncClient) -> None:
    assert (await client.get(CONSOLE)).status_code == 401
    assert (await client.put(CONSOLE, json={"console": _console()})).status_code == 401
    assert (await client.get(PROFILES_ZIP)).status_code == 401
