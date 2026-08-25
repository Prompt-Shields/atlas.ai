from __future__ import annotations

import pytest

from app.schemas.directive_ack import AckOutcome
from app.services.directive_status import status_for_ack

pytestmark = [pytest.mark.unit]


def test_shown_maps_to_acknowledged():
    assert status_for_ack(AckOutcome.shown) == "acknowledged"


def test_accepted_and_applied_map_to_applied():
    assert status_for_ack(AckOutcome.accepted) == "applied"
    assert status_for_ack(AckOutcome.applied) == "applied"


def test_rejected_maps_to_rejected():
    assert status_for_ack(AckOutcome.rejected) == "rejected"
