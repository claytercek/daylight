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


def test_own_context_is_registered_the_instant_record_command_returns() -> None:
    """The bookkeeping lands before the caller issues `light.turn_on`.

    A state report that races the service call -- same instant, no elapsed
    time -- must already be recognised as ours.
    """
    target = _target()
    target.record_command(LIGHT, "C1", {"brightness": 128}, 0.0, now=0.0)

    assert target.observe_state_change(LIGHT, "C1", timestamp=0.0) is False


def test_own_context_is_recognised_on_the_commanded_entity_only() -> None:
    """Context ids are tracked per entity, not globally.

    Kitchen was commanded with C1; the den was never commanded, so a C1-looking
    report on the den is somebody else's business and counts as manual.
    """
    target = _target()
    target.record_command(LIGHT, "C1", {"brightness": 128}, 0.0, now=0.0)

    assert target.observe_state_change("light.den", "C1", timestamp=100.0) is True
