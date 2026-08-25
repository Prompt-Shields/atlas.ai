from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.directive import CoachingTag, NudgeCreateIn, NudgeSeverity

pytestmark = [pytest.mark.unit]


def test_nudge_requires_title_and_body():
    with pytest.raises(ValidationError):
        NudgeCreateIn(body="x", severity="low", coaching_tag="shortcut")


def test_nudge_rejects_unknown_severity():
    with pytest.raises(ValidationError):
        NudgeCreateIn(title="t", body="b", severity="apocalyptic", coaching_tag="shortcut")


def test_nudge_forbids_extra_fields():
    with pytest.raises(ValidationError):
        NudgeCreateIn(title="t", body="b", severity="low", coaching_tag="shortcut", evil="x")


def test_nudge_length_caps_enforced():
    with pytest.raises(ValidationError):
        NudgeCreateIn(title="t" * 200, body="b", severity="low", coaching_tag="shortcut")


def test_valid_nudge_roundtrips():
    n = NudgeCreateIn(title="Try ⌘K", body="Faster nav", severity="low", coaching_tag="shortcut")
    assert n.severity is NudgeSeverity.low and n.coaching_tag is CoachingTag.shortcut


def test_policy_violation_is_a_coaching_tag():
    """Risk-engine nudges report a policy violation, not a wellbeing tip.

    Wire value is asserted explicitly: on-device clients match on the string,
    so renaming the member is a breaking client-contract change.
    """
    n = NudgeCreateIn(title="t", body="b", severity="high", coaching_tag="policy_violation")
    assert n.coaching_tag is CoachingTag.policy_violation
    assert n.coaching_tag.value == "policy_violation"
