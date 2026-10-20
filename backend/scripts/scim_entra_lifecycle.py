"""An Entra-shaped SCIM provisioning run, and what Guide reads after each step (promptly-guide #58).

Entra ID's provisioning service drives `/api/v1/scim/v2` in a fixed pattern: look a user
up by `userName`, create it, create a group with no members, then PATCH members in and
out; disable with a string "False"; rename by PATCH. This replays that pattern for two
people and, after every step, checks each person's groups two ways:

- **as SCIM holds them**: the groups whose members include the person, while active;
- **as Guide reads them**: `GET /api/v1/guide/groups` with the person's sign-in, when a
  way to sign them in is given.

Everything it creates has a unique prefix and is deleted at the end, so it can run
against a real tenant before a pilot:

    python -m scripts.scim_entra_lifecycle --base https://atlas.example.com \\
        --scim-token "$ATLAS_SCIM_TOKEN" --domain nordlys.example

Without Guide sign-ins that checks the SCIM side only; Guide's read of the same data is
checked by tests/unit/test_guide_scim_lifecycle.py, which runs this with test tokens.
Exit 1 when any step does not hold.
"""

from __future__ import annotations

import argparse
import asyncio
import secrets
import sys
from collections.abc import Callable
from dataclasses import dataclass, field

import httpx

SCIM = "/api/v1/scim/v2"
GUIDE_GROUPS = "/api/v1/guide/groups"
USER_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:User"
GROUP_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:Group"
PATCH_OP = "urn:ietf:params:scim:api:messages:2.0:PatchOp"


@dataclass
class StepResult:
    name: str
    expected: dict[str, list[str]]
    scim: dict[str, list[str]]
    guide: dict[str, list[str]] | None
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


class Run:
    """One run's people, groups and checks. `guide_headers(email)` signs a person in to
    Guide's endpoint; None checks the SCIM side only."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        scim_headers: dict[str, str],
        domain: str,
        guide_headers: Callable[[str], dict[str, str]] | None = None,
        prefix: str | None = None,
    ) -> None:
        self.client = client
        self.h = scim_headers
        self.guide_headers = guide_headers
        self.prefix = prefix or f"scim-check-{secrets.token_hex(3)}"
        self.domain = domain
        self.users: dict[str, str] = {}  # email -> SCIM id
        self.groups: dict[str, str] = {}  # display name -> SCIM id
        self.results: list[StepResult] = []

    def email(self, who: str) -> str:
        return f"{self.prefix}-{who}@{self.domain}"

    def group(self, name: str) -> str:
        return f"{self.prefix} {name}"

    async def _call(self, method: str, path: str, expect: int, **kwargs) -> httpx.Response:
        r = await self.client.request(method, SCIM + path, headers=self.h, **kwargs)
        if r.status_code != expect:
            raise AssertionError(f"{method} {path}: {r.status_code}, expected {expect}: {r.text}")
        return r

    async def _patch(self, path: str, *operations: dict) -> httpx.Response:
        body = {"schemas": [PATCH_OP], "Operations": list(operations)}
        return await self._call("PATCH", path, 200, json=body)

    # -- Entra's calls ------------------------------------------------------------

    async def provision_user(self, email: str, external_id: str) -> None:
        found = await self._call("GET", "/Users", 200, params={"filter": f'userName eq "{email}"'})
        if found.json()["totalResults"] != 0:
            raise AssertionError(f"{email} is already provisioned")
        r = await self._call(
            "POST",
            "/Users",
            201,
            json={
                "schemas": [USER_SCHEMA],
                "userName": email,
                "externalId": external_id,
                "active": True,
                "name": {"givenName": "Test", "familyName": "Person"},
                "emails": [{"value": email, "primary": True, "type": "work"}],
            },
        )
        self.users[email] = r.json()["id"]

    async def provision_group(self, name: str, external_id: str) -> None:
        found = await self._call(
            "GET", "/Groups", 200, params={"filter": f'displayName eq "{name}"'}
        )
        if found.json()["totalResults"] != 0:
            raise AssertionError(f"group {name} already exists")
        r = await self._call(
            "POST",
            "/Groups",
            201,
            json={"schemas": [GROUP_SCHEMA], "displayName": name, "externalId": external_id},
        )
        self.groups[name] = r.json()["id"]

    async def add_members(self, group: str, *emails: str) -> None:
        value = [{"value": self.users[e]} for e in emails]
        await self._patch(
            f"/Groups/{self.groups[group]}", {"op": "Add", "path": "members", "value": value}
        )

    async def remove_member(self, group: str, email: str) -> None:
        member = f'members[value eq "{self.users[email]}"]'
        await self._patch(f"/Groups/{self.groups[group]}", {"op": "Remove", "path": member})

    async def rename_group(self, old: str, new: str) -> None:
        await self._patch(
            f"/Groups/{self.groups[old]}", {"op": "Replace", "path": "displayName", "value": new}
        )
        self.groups[new] = self.groups.pop(old)

    async def rename_user(self, old: str, new: str) -> None:
        await self._patch(
            f"/Users/{self.users[old]}", {"op": "Replace", "path": "userName", "value": new}
        )
        self.users[new] = self.users.pop(old)

    async def set_active(self, email: str, active: bool) -> None:
        # Entra sends the boolean as a string.
        value = "True" if active else "False"
        await self._patch(
            f"/Users/{self.users[email]}", {"op": "Replace", "path": "active", "value": value}
        )

    async def delete_all(self) -> None:
        for group_id in list(self.groups.values()):
            await self._call("DELETE", f"/Groups/{group_id}", 204)
        for user_id in list(self.users.values()):
            await self._call("DELETE", f"/Users/{user_id}", 204)
        self.groups.clear()
        self.users.clear()

    # -- The checks -----------------------------------------------------------------

    async def scim_view(self, email: str) -> list[str]:
        """The groups SCIM holds the person in, while active: what Guide should read."""
        user_id = self.users.get(email)
        if user_id is None:
            return []
        user = (await self._call("GET", f"/Users/{user_id}", 200)).json()
        if not user["active"] or user["userName"].lower() != email.lower():
            return []
        names = []
        for name, group_id in self.groups.items():
            group = (await self._call("GET", f"/Groups/{group_id}", 200)).json()
            if any(m["value"] == user_id for m in group.get("members", [])):
                names.append(name)
        return sorted(names)

    async def guide_view(self, email: str) -> list[str]:
        assert self.guide_headers is not None
        r = await self.client.get(GUIDE_GROUPS, headers=self.guide_headers(email))
        if r.status_code != 200:
            raise AssertionError(f"Guide's groups for {email}: {r.status_code} {r.text}")
        return r.json()["groups"]

    async def check(self, name: str, expected: dict[str, list[str]]) -> StepResult:
        scim = {e: await self.scim_view(e) for e in expected}
        guide = {e: await self.guide_view(e) for e in expected} if self.guide_headers else None
        result = StepResult(name, expected, scim, guide)
        for email, groups in expected.items():
            want = sorted(groups)
            if scim[email] != want:
                result.problems.append(f"SCIM holds {email} in {scim[email]}, expected {want}")
            if guide is not None and guide[email] != want:
                result.problems.append(f"Guide reads {guide[email]} for {email}, expected {want}")
        self.results.append(result)
        return result


async def lifecycle(run: Run) -> list[StepResult]:
    """The run itself: two people, two groups, and what each step should leave."""
    kari, ola, kari_new = run.email("kari"), run.email("ola"), run.email("kari.n")
    sales, finance, finance_eu = run.group("Sales"), run.group("Finance"), run.group("Finance EU")
    try:
        await run.provision_user(kari, f"{run.prefix}-aad-1")
        await run.provision_user(ola, f"{run.prefix}-aad-2")
        await run.check("provisioned, in no group yet", {kari: [], ola: []})

        await run.provision_group(sales, f"{run.prefix}-g-1")
        await run.provision_group(finance, f"{run.prefix}-g-2")
        await run.add_members(sales, kari)
        await run.add_members(finance, kari, ola)
        await run.check("added to groups", {kari: [finance, sales], ola: [finance]})

        await run.remove_member(sales, kari)
        await run.add_members(sales, ola)
        await run.check("moved between groups", {kari: [finance], ola: [finance, sales]})

        await run.rename_group(finance, finance_eu)
        await run.check("a group renamed", {kari: [finance_eu], ola: [finance_eu, sales]})

        await run.rename_user(kari, kari_new)
        await run.check("an email changed", {kari: [], kari_new: [finance_eu]})

        await run.set_active(kari_new, False)
        await run.check("disabled", {kari_new: [], ola: [finance_eu, sales]})

        await run.set_active(kari_new, True)
        await run.check("enabled again", {kari_new: [finance_eu]})
    finally:
        await run.delete_all()
    await run.check("deleted", {kari_new: [], ola: []})
    return run.results


def report(results: list[StepResult]) -> str:
    lines = []
    for r in results:
        lines.append(f"{'ok  ' if r.ok else 'FAIL'} {r.name}")
        lines += [f"     {p}" for p in r.problems]
    return "\n".join(lines)


async def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", required=True, help="Atlas's address, https://...")
    parser.add_argument("--scim-token", required=True, help="the tenant's SCIM token")
    parser.add_argument("--domain", required=True, help="an email domain for the test people")
    args = parser.parse_args(argv)
    headers = {
        "Authorization": f"Bearer {args.scim_token}",
        "Content-Type": "application/scim+json",
    }
    async with httpx.AsyncClient(base_url=args.base, timeout=30) as client:
        results = await lifecycle(Run(client, headers, args.domain))
    print(report(results))
    print("SCIM side only: Guide's read of the same data is checked by the tests.")
    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(_main(sys.argv[1:])))
