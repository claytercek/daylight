"""Tests for the manual-control ("hand-dim") detection state machine.

Every test here drives `Target` through synthetic sequences of
`record_command`/`observe_state_change` with explicit timestamps. There is no
`hass`, no wall clock and no HA test harness involved -- that is the point of
`Target` being a plain Python object.

Timestamps are float seconds on an arbitrary epoch; the scenarios read as
`t=0`, `t=6.5` and so on.
"""

from custom_components.daylight.target import Target, TargetConfig

LIGHT = "light.kitchen"


def _target(**kwargs) -> Target:
    return Target(TargetConfig(**kwargs))


def test_state_change_with_own_context_is_not_manual() -> None:
    """Command issued with context C1 at t=0; C1 reports back at t=1 -> ours."""
    target = _target()
    target.record_command(LIGHT, "C1", {"brightness": 128}, 0.0, now=0.0)

    flagged = target.observe_state_change(LIGHT, "C1", timestamp=1.0)

    assert flagged is False
    assert target.is_manual(LIGHT, now=1.0) is False
