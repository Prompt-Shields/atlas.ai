"""What the E2E tests share: the running stack's address, the super admin, and a
tenant user who can ingest.

Ingest takes both a JWT and an `X-API-Key`, and files the blob under the caller's own
tenant and org, so it needs a user who has both. The super admin has neither, and
`POST /users` only creates users in the caller's own tenant. So a test signs up a fresh
tenant, whose admin creates an org and a second admin inside it, and ingests as that
second admin.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import httpx

BASE_URL = os.environ.get("E2E_API_URL", "http://localhost:8001/api/v1")
# Not *.local or *.test: login takes an EmailStr, and email-validator refuses those
# special-use domains, so a super admin there could never log in.
SUPER_EMAIL = os.environ.get("SUPER_ADMIN_EMAIL", "admin@example.com")
SUPER_PASS = os.environ.get("SUPER_ADMIN_PASSWORD", "TestAdmin_P@ss1")
# Meets the user password rules: 12 or more, upper and lower case, a digit, a symbol.
TENANT_PASS = "E2e-Tenant_P@ss1"


def auth(token: str, api_key: str | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {token}"}
    if api_key:
        headers["X-API-Key"] = api_key
    return headers


async def login(client: httpx.AsyncClient, email: str, password: str) -> str:
    resp = await client.post(f"{BASE_URL}/auth/login", json={"email": email, "password": password})
    resp.raise_for_status()
    return resp.json()["access_token"]


@dataclass
class TenantUser:
    token: str
    api_key: str
    tenant_id: str
    org_id: str


async def tenant_user(client: httpx.AsyncClient, label: str) -> TenantUser:
    """A fresh tenant with an org, and an admin of both holding an API key."""
    resp = await client.post(
        f"{BASE_URL}/signup",
        json={
            "email": f"{label}-owner@example.com",
            "password": TENANT_PASS,
            "tenant_name": f"E2E {label}",
            "tenant_slug": f"e2e-{label}",
        },
    )
    assert resp.status_code == 201, resp.text
    owner = resp.json()
    tenant_id = owner["tenant_id"]

    resp = await client.post(
        f"{BASE_URL}/tenants/{tenant_id}/organisations",
        headers=auth(owner["access_token"]),
        json={"name": f"E2E {label} org", "slug": f"e2e-{label}-org"},
    )
    assert resp.status_code == 201, resp.text
    org_id = resp.json()["id"]

    email = f"{label}-admin@example.com"
    resp = await client.post(
        f"{BASE_URL}/users",
        headers=auth(owner["access_token"]),
        json={
            "email": email,
            "password": TENANT_PASS,
            "full_name": f"E2E {label} admin",
            "role": "TENANT_ADMIN",
            "org_id": org_id,
        },
    )
    assert resp.status_code == 201, resp.text
    token = await login(client, email, TENANT_PASS)

    resp = await client.post(
        f"{BASE_URL}/auth/api-keys", headers=auth(token), json={"name": f"e2e {label}"}
    )
    assert resp.status_code == 201, resp.text
    return TenantUser(token=token, api_key=resp.json()["key"], tenant_id=tenant_id, org_id=org_id)


async def ingest(client: httpx.AsyncClient, user: TenantUser, content: str, source_id: str) -> dict:
    resp = await client.post(
        f"{BASE_URL}/adapters/manual/ingest",
        headers=auth(user.token, user.api_key),
        json={"content": content, "source_type": "manual", "source_id": source_id},
    )
    assert resp.status_code == 201, resp.text
    blobs = resp.json()
    assert len(blobs) == 1
    return blobs[0]
