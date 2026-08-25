"""Unit tests for app.routers.extension helpers.

The pure-function pieces — freshness windowing + payload shape. The
endpoint itself is integration territory (needs APIKey + tenant
fixtures + DB) and lives in tests/integration.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.routers.extension import (
    DEFAULT_NEXT_CHECK_IN_SECONDS,
    EXTENSION_FRESH_WINDOW,
    HeartbeatRequest,
    fresh_cutoff,
)

pytestmark = [pytest.mark.unit]


class TestFreshCutoff:
    def test_fresh_cutoff_is_now_minus_window(self) -> None:
        fixed = datetime(2026, 5, 18, 12, 0, tzinfo=UTC)
        cutoff = fresh_cutoff(now=fixed)
        assert cutoff == fixed - EXTENSION_FRESH_WINDOW

    def test_fresh_cutoff_defaults_to_real_now(self) -> None:
        before = datetime.now(UTC)
        cutoff = fresh_cutoff()
        after = datetime.now(UTC)
        # Cutoff should be between (before - WINDOW) and (after - WINDOW)
        assert before - EXTENSION_FRESH_WINDOW <= cutoff
        assert cutoff <= after - EXTENSION_FRESH_WINDOW

    def test_window_is_seven_days(self) -> None:
        """Asserts the documented default — bump deliberately if tuning."""
        assert EXTENSION_FRESH_WINDOW == timedelta(days=7)


class TestHeartbeatRequestSchema:
    def test_minimal_required_only(self) -> None:
        req = HeartbeatRequest(device_fingerprint="abc12345")
        assert req.device_fingerprint == "abc12345"
        assert req.user_email is None
        assert req.browser is None

    def test_full_payload(self) -> None:
        req = HeartbeatRequest(
            device_fingerprint="abc12345",
            user_email="alex@example.org",
            browser="Chrome",
            browser_version="142.0.7341.0",
            extension_version="0.5.1",
            os_platform="macOS",
            prompt_volume_24h=42,
            top_tool_24h="Microsoft Copilot",
        )
        # v0.2 fields parsed but documented as ignored on write
        assert req.prompt_volume_24h == 42
        assert req.top_tool_24h == "Microsoft Copilot"

    def test_fingerprint_too_short_rejected(self) -> None:
        with pytest.raises(ValidationError):
            HeartbeatRequest(device_fingerprint="x")  # < min_length=8

    def test_prompt_volume_must_be_non_negative(self) -> None:
        with pytest.raises(ValidationError):
            HeartbeatRequest(
                device_fingerprint="abc12345",
                prompt_volume_24h=-1,
            )


class TestNextCheckInDefault:
    def test_default_is_five_minutes(self) -> None:
        assert DEFAULT_NEXT_CHECK_IN_SECONDS == 300
