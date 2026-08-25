# backend/tests/integration/test_signup.py
import pytest


@pytest.mark.asyncio
async def test_signup_creates_trialing_tenant(client):
    resp = await client.post(
        "/api/v1/signup",
        json={
            "email": "founder@acme.com",
            "password": "Str0ng!Passw0rd",
            "tenant_name": "Acme",
            "tenant_slug": "acme-co",
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["access_token"] and body["tenant_id"]


@pytest.mark.asyncio
async def test_signup_duplicate_email_conflicts(client):
    payload = {
        "email": "dup@acme.com",
        "password": "Str0ng!Passw0rd",
        "tenant_name": "Acme",
        "tenant_slug": "acme-1",
    }
    await client.post("/api/v1/signup", json=payload)
    payload["tenant_slug"] = "acme-2"
    resp = await client.post("/api/v1/signup", json=payload)
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_signup_duplicate_slug_conflicts(client):
    payload = {
        "email": "a@acme.com",
        "password": "Str0ng!Passw0rd",
        "tenant_name": "Acme",
        "tenant_slug": "taken-slug",
    }
    await client.post("/api/v1/signup", json=payload)
    payload["email"] = "b@acme.com"
    resp = await client.post("/api/v1/signup", json=payload)
    assert resp.status_code == 422
