"""Unit tests for the heartbeat-join helper in app.routers.endpoints.

Pure-function coverage on `_is_extension_installed`. The DB-touching
`_fresh_heartbeat_emails` is integration territory (needs a tenant +
heartbeat rows + a real session).

Why this test file exists: the v0.5 swap from the 3-day-fresh
heuristic to real heartbeats changes dashboard numbers for every
tenant. The semantic shift should be obvious, not implicit — so
explicit tests on the new attribution rule guard against silent
drift if anyone tweaks the logic later.
"""

from __future__ import annotations

import pytest

from app.routers.endpoints import _is_extension_installed

pytestmark = [pytest.mark.unit]


class TestIsExtensionInstalled:
    def test_email_in_fresh_set_returns_true(self) -> None:
        assert _is_extension_installed(
            "alex@example.org",
            {"alex@example.org"},
        )

    def test_case_insensitive_match(self) -> None:
        """Heartbeats store user_email lowercased on write, but the
        device's enrolled_user_email may arrive from the MDM in any
        case. We normalise on the lookup side."""
        assert _is_extension_installed(
            "Alex@Example.Org",
            {"alex@example.org"},
        )

    def test_email_not_in_set_returns_false(self) -> None:
        assert not _is_extension_installed(
            "morgan@example.org",
            {"alex@example.org"},
        )

    def test_null_email_returns_false(self) -> None:
        """The IT lead's NULL-owner devices aren't counted —
        attribution requires a known user identifier."""
        assert not _is_extension_installed(None, {"alex@example.org"})

    def test_empty_email_returns_false(self) -> None:
        """Whitespace + empty strings can leak in from MDM data quality.
        Treat them as no-attribution rather than counting against
        an empty-string set entry."""
        assert not _is_extension_installed("", {"alex@example.org"})

    def test_empty_fresh_set_returns_false(self) -> None:
        """A tenant with no heartbeats — extension_installed_count = 0
        across the board. This is the desired v0.5 behaviour: no
        heuristic fallback, the dashboard tells truth."""
        assert not _is_extension_installed("alex@example.org", set())
