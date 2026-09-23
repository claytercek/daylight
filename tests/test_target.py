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


def test_late_report_with_fresh_context_is_suppressed_during_transition() -> None:
    """A slow light echoes our command back with a context of its own.

    Command at t=0 with a 5s transition. HA only propagates our context onto
    state changes for 5s, so the post-transition report at t=6.5 carries a
    fresh context id -- but it is still within transition (5s) + grace (2s),
    so it is ours, not a human's.
    """
    target = _target()
    target.record_command(LIGHT, "C1", {"brightness": 128}, 5.0, now=0.0)

    assert target.observe_state_change(LIGHT, "C2", timestamp=6.5) is False


def test_unknown_context_after_the_window_closes_is_manual() -> None:
    """Same command, but the report arrives at t=20 -- long past 5s + 2s."""
    target = _target()
    target.record_command(LIGHT, "C1", {"brightness": 128}, 5.0, now=0.0)

    assert target.observe_state_change(LIGHT, "C3", timestamp=20.0) is True
