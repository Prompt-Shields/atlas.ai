"""Seed a ready-to-use tenant user for local development.

Creates a Tenant + Organisation, a user (default role ORG_ADMIN) attached
to both, and marks the tenant's onboarding as complete — so the account
can log in and land straight on /dashboard without the wizard.

Usage (from backend/, with the venv):

    .venv/bin/python -m scripts.seed_tenant_user \
        --email demo@acme.test --password Passw0rd! --full-name "Demo User"

All flags are optional; sensible defaults are used. Re-running with the
same --email is a no-op for the user (reports the conflict and exits 0)
but will reuse/create the tenant by slug.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone

from sqlalchemy import select

from app.database import get_standalone_session
from app.errors import ConflictError
from app.models.tenant import Organisation, Tenant
from app.models.user import Role
from app.services.auth_service import create_user


def _slugify(value: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in value.lower()).strip("-")


async def seed(
    *,
    email: str,
    password: str,
    full_name: str,
    tenant_name: str,
    org_name: str,
    role: Role,
) -> None:
    async with get_standalone_session() as db:
        tenant_slug = _slugify(tenant_name)

        # Reuse the tenant if one already exists for this slug.
        existing = await db.execute(
            select(Tenant).where(Tenant.slug == tenant_slug)
        )
        tenant = existing.scalar_one_or_none()
        if tenant is None:
            tenant = Tenant(
                name=tenant_name,
                slug=tenant_slug,
                is_active=True,
                description="Seeded for local development",
                onboarding_completed_at=datetime.now(timezone.utc),
            )
            db.add(tenant)
            await db.flush()
            org = Organisation(
                tenant_id=tenant.id,
                name=org_name,
                slug=_slugify(org_name),
                is_active=True,
            )
            db.add(org)
            await db.flush()
        else:
            # Make sure the existing tenant is usable and onboarded.
            tenant.is_active = True
            if tenant.onboarding_completed_at is None:
                tenant.onboarding_completed_at = datetime.now(timezone.utc)
            org_result = await db.execute(
                select(Organisation).where(Organisation.tenant_id == tenant.id)
            )
            org = org_result.scalars().first()
            if org is None:
                org = Organisation(
                    tenant_id=tenant.id,
                    name=org_name,
                    slug=_slugify(org_name),
                    is_active=True,
                )
                db.add(org)
                await db.flush()

        try:
            user = await create_user(
                db,
                email=email,
                password=password,
                full_name=full_name,
                role=role,
                tenant_id=tenant.id,
                org_id=org.id,
                is_email_verified=True,
            )
        except ConflictError:
            print(f"⚠️  User {email!r} already exists — nothing to do.")
            return

        print("✅ Seeded account ready:")
        print(f"   email     : {user.email}")
        print(f"   password  : {password}")
        print(f"   role      : {role.value}")
        print(f"   tenant    : {tenant.name} ({tenant.slug})")
        print(f"   org       : {org.name}")
        print("   onboarding: complete")
        print("\n   Log in at http://localhost:3000/login")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    # NB: login validates the address with Pydantic EmailStr, which rejects
    # reserved TLDs (.test/.example/localhost) — use a real-looking domain.
    parser.add_argument("--email", default="demo@acme.com")
    parser.add_argument("--password", default="Passw0rd!")
    parser.add_argument("--full-name", default="Demo User")
    parser.add_argument("--tenant-name", default="Acme Corp")
    parser.add_argument("--org-name", default="Acme HQ")
    parser.add_argument(
        "--role",
        default=Role.ORG_ADMIN.value,
        choices=[r.value for r in Role],
        help="Role for the seeded user (default: ORG_ADMIN).",
    )
    args = parser.parse_args()

    asyncio.run(
        seed(
            email=args.email,
            password=args.password,
            full_name=args.full_name,
            tenant_name=args.tenant_name,
            org_name=args.org_name,
            role=Role(args.role),
        )
    )


if __name__ == "__main__":
    main()
