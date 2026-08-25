from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.directive_ack import DirectiveAckIn

pytestmark = [pytest.mark.unit]


def test_ack_requires_valid_outcome():
    with pytest.raises(ValidationError):
        DirectiveAckIn(outcome="obliterated")


def test_ack_forbids_extra_fields():
    with pytest.raises(ValidationError):
        DirectiveAckIn(outcome="shown", sneaky=1)


def test_valid_ack():
    assert DirectiveAckIn(outcome="accepted").outcome.value == "accepted"
