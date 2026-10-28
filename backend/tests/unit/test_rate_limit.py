"""Unit tests for app.auth.rate_limit.

Covers:
  - the limit allows N attempts, then blocks
  - the window resets once the key expires
  - a counter left without a TTL gets one on the next call
  - the per-email and per-IP limits are counted separately
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.auth import rate_limit
from app.errors import RateLimitError

pytestmark = [pytest.mark.unit]


class FakeRedis:
    """In-memory stand-in for the few redis.asyncio calls the limiter makes.

    Time is a plain number the test moves forward, so expiry needs no sleeping.
    """

    def __init__(self) -> None:
        self.now = 0.0
        self.values: dict[str, int] = {}
        self.expires_at: dict[str, float] = {}

    def advance(self, seconds: float) -> None:
        self.now += seconds
        for key, when in list(self.expires_at.items()):
            if when <= self.now:
                del self.expires_at[key]
                self.values.pop(key, None)

    def _incr(self, key: str) -> int:
        self.values[key] = self.values.get(key, 0) + 1
        return self.values[key]

    def _ttl(self, key: str) -> int:
        if key not in self.values:
            return -2
        if key not in self.expires_at:
            return -1
        return int(self.expires_at[key] - self.now)

    async def incr(self, key: str) -> int:
        return self._incr(key)

    async def ttl(self, key: str) -> int:
        return self._ttl(key)

    async def expire(self, key: str, seconds: int) -> bool:
        if key not in self.values:
            return False
        self.expires_at[key] = self.now + seconds
        return True

    def pipeline(self, transaction: bool = True) -> FakePipeline:
        return FakePipeline(self)


class FakePipeline:
    def __init__(self, redis: FakeRedis) -> None:
        self._redis = redis
        self._ops: list[tuple[str, str]] = []

    async def __aenter__(self) -> FakePipeline:
        return self

    async def __aexit__(self, *exc: object) -> None:
        self._ops.clear()

    def incr(self, key: str) -> FakePipeline:
        self._ops.append(("incr", key))
        return self

    def ttl(self, key: str) -> FakePipeline:
        self._ops.append(("ttl", key))
        return self

    async def execute(self) -> list[Any]:
        ops, self._ops = self._ops, []
        return [getattr(self._redis, f"_{name}")(key) for name, key in ops]


@pytest.fixture
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> FakeRedis:
    fake = FakeRedis()
    monkeypatch.setattr(rate_limit, "_get_redis", lambda: fake)
    # check_rate_limit returns early under APP_ENV=testing; pretend otherwise.
    monkeypatch.setattr(
        rate_limit,
        "get_settings",
        lambda: SimpleNamespace(app_env=SimpleNamespace(value="development")),
    )
    return fake


def _request(ip: str) -> Any:
    return SimpleNamespace(client=SimpleNamespace(host=ip))


async def test_allows_max_attempts_then_blocks(fake_redis: FakeRedis) -> None:
    for _ in range(3):
        await rate_limit.check_rate_limit("k", max_attempts=3, window_seconds=60)
    with pytest.raises(RateLimitError):
        await rate_limit.check_rate_limit("k", max_attempts=3, window_seconds=60)


async def test_first_call_sets_the_window(fake_redis: FakeRedis) -> None:
    await rate_limit.check_rate_limit("k", max_attempts=3, window_seconds=60)
    assert fake_redis._ttl("rate_limit:k") == 60


async def test_window_resets_after_expiry(fake_redis: FakeRedis) -> None:
    for _ in range(3):
        await rate_limit.check_rate_limit("k", max_attempts=3, window_seconds=60)
    with pytest.raises(RateLimitError):
        await rate_limit.check_rate_limit("k", max_attempts=3, window_seconds=60)

    fake_redis.advance(61)

    await rate_limit.check_rate_limit("k", max_attempts=3, window_seconds=60)
    assert fake_redis.values["rate_limit:k"] == 1


async def test_later_calls_do_not_extend_the_window(fake_redis: FakeRedis) -> None:
    await rate_limit.check_rate_limit("k", max_attempts=3, window_seconds=60)
    fake_redis.advance(30)
    await rate_limit.check_rate_limit("k", max_attempts=3, window_seconds=60)
    assert fake_redis._ttl("rate_limit:k") == 30


async def test_key_without_ttl_gets_one_on_next_call(fake_redis: FakeRedis) -> None:
    # The state a crash between INCR and EXPIRE used to leave behind:
    # a counter over the limit with no expiry.
    fake_redis.values["rate_limit:k"] = 50
    assert fake_redis._ttl("rate_limit:k") == -1

    with pytest.raises(RateLimitError):
        await rate_limit.check_rate_limit("k", max_attempts=3, window_seconds=60)
    assert fake_redis._ttl("rate_limit:k") == 60

    fake_redis.advance(61)
    await rate_limit.check_rate_limit("k", max_attempts=3, window_seconds=60)


async def test_email_and_ip_limits_are_separate(fake_redis: FakeRedis) -> None:
    # 5 per email per minute: the sixth try on one account is blocked...
    for _ in range(5):
        await rate_limit.check_login_rate_limit(_request("10.0.0.1"), "a@example.com")
    with pytest.raises(RateLimitError):
        await rate_limit.check_login_rate_limit(_request("10.0.0.1"), "a@example.com")

    # ...but the same IP can still try another account until its own 10 run out.
    for _ in range(4):
        await rate_limit.check_login_rate_limit(_request("10.0.0.1"), "b@example.com")
    with pytest.raises(RateLimitError):
        await rate_limit.check_login_rate_limit(_request("10.0.0.1"), "b@example.com")

    assert fake_redis.values["rate_limit:ip:10.0.0.1"] == 11
    assert fake_redis.values["rate_limit:email:a@example.com"] == 6
    assert fake_redis.values["rate_limit:email:b@example.com"] == 4

    # A fresh IP is not held back by the first IP's count.
    await rate_limit.check_login_rate_limit(_request("10.0.0.2"), "c@example.com")
