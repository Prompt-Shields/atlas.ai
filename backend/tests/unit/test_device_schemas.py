from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.device import DeviceRegisterIn, DeviceRegisterOut

pytestmark = [pytest.mark.unit]


def test_register_in_requires_platform():
    with pytest.raises(ValidationError):
        DeviceRegisterIn(app_version="1.0")  # missing platform


def test_register_in_rejects_unknown_platform():
    with pytest.raises(ValidationError):
        DeviceRegisterIn(platform="toaster")


def test_register_in_forbids_extra_fields():
    with pytest.raises(ValidationError):
        DeviceRegisterIn(platform="macos", bogus="x")


def test_register_out_round_trips():
    out = DeviceRegisterOut(device_id="d1", device_token="psd_abc")
    assert out.device_id == "d1" and out.device_token.startswith("psd_")
