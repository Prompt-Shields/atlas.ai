from __future__ import annotations

import pytest

from app.auth.device_token import generate_device_token, hash_device_token

pytestmark = [pytest.mark.unit]


def test_generate_returns_raw_and_matching_hash():
    raw, hashed = generate_device_token()
    assert isinstance(raw, str) and len(raw) >= 32
    assert hash_device_token(raw) == hashed
    assert len(hashed) == 64  # sha256 hex


def test_hash_is_deterministic_and_distinct():
    raw1, h1 = generate_device_token()
    raw2, h2 = generate_device_token()
    assert raw1 != raw2 and h1 != h2
    assert hash_device_token(raw1) == h1
