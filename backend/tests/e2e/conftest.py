"""E2E tests drive a running stack over HTTP and never touch its database directly.

The unit-test conftest one level up creates every table before each test and drops
them after, against DATABASE_URL. Here DATABASE_URL is the running stack's own
database, so that fixture would drop the stack's tables, and with them the super admin
it seeded at start-up, after the first test. This overrides it with nothing.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def setup_database() -> None:
    return None
