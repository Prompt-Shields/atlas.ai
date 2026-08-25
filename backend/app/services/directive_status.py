"""Pure mapping from an ACK outcome to the directive's resulting status.
Mirrors the ACK-outcome→status table in the device-targeted policy spec.
"""

from __future__ import annotations

from app.schemas.directive_ack import AckOutcome

_MAP = {
    AckOutcome.shown: "acknowledged",
    AckOutcome.accepted: "applied",
    AckOutcome.applied: "applied",
    AckOutcome.rejected: "rejected",
}


def status_for_ack(outcome: AckOutcome) -> str:
    return _MAP[outcome]
