"""Tests for the manual-control ("hand-dim") detection state machine.

Every test here drives `Target` through synthetic sequences of
`record_command`/`observe_state_change` with explicit timestamps. There is no
`hass`, no wall clock and no HA test harness involved -- that is the point of
`Target` being a plain Python object.

Timestamps are float seconds on an arbitrary epoch; the scenarios read as
`t=0`, `t=6.5` and so on.
"""

import json

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


def test_hand_dim_sets_the_manual_flag() -> None:
    """Somebody turns the dimmer at t=20; the light stops being adapted."""
    target = _target()
    target.record_command(LIGHT, "C1", {"brightness": 128}, 5.0, now=0.0)

    target.observe_state_change(LIGHT, "HUMAN", timestamp=20.0)

    assert target.is_manual(LIGHT, now=21.0) is True


def test_manual_flag_is_not_cleared_by_a_later_recognised_report() -> None:
    """Once flagged, only an explicit clear or the auto-reset lets go."""
    target = _target()
    target.observe_state_change(LIGHT, "HUMAN", timestamp=20.0)

    target.record_command(LIGHT, "C2", {"brightness": 200}, 0.0, now=30.0)
    target.observe_state_change(LIGHT, "C2", timestamp=30.5)

    assert target.is_manual(LIGHT, now=31.0) is True


def test_untouched_entity_is_not_manual() -> None:
    target = _target()

    assert target.is_manual(LIGHT, now=0.0) is False


def test_manual_flag_auto_clears_after_the_configured_minutes() -> None:
    """Hand-dimmed at t=100 with a 15 minute reset: gone at t=100 + 900s."""
    target = _target(manual_control_reset_minutes=15)
    target.observe_state_change(LIGHT, "HUMAN", timestamp=100.0)

    assert target.is_manual(LIGHT, now=100.0 + 14 * 60) is True
    assert target.is_manual(LIGHT, now=100.0 + 15 * 60) is False


def test_manual_flag_never_auto_clears_when_reset_minutes_is_zero() -> None:
    target = _target(manual_control_reset_minutes=0)
    target.observe_state_change(LIGHT, "HUMAN", timestamp=100.0)

    assert target.is_manual(LIGHT, now=100.0 + 30 * 86400) is True


def test_detection_resumes_after_an_auto_clear() -> None:
    """The auto-clear really releases the entity, it does not just read False."""
    target = _target(manual_control_reset_minutes=15)
    target.observe_state_change(LIGHT, "HUMAN", timestamp=100.0)
    assert target.is_manual(LIGHT, now=1000.0 + 100.0) is False

    target.record_command(LIGHT, "C1", {"brightness": 128}, 0.0, now=1100.0)

    assert target.observe_state_change(LIGHT, "C1", timestamp=1100.5) is False
    assert target.is_manual(LIGHT, now=1101.0) is False


def test_clear_manual_flag_releases_one_entity() -> None:
    target = _target()
    target.observe_state_change(LIGHT, "HUMAN", timestamp=10.0)
    target.observe_state_change("light.den", "HUMAN", timestamp=10.0)

    target.clear_manual_flag(LIGHT)

    assert target.is_manual(LIGHT, now=11.0) is False
    assert target.is_manual("light.den", now=11.0) is True


def test_movie_night_off_to_on_releases_every_entity() -> None:
    """Movie night: both lights hand-dimmed, then the switch is cycled.

    The future off->on handler calls `clear_all_manual_flags`, after which
    detection starts over from scratch -- a fresh hand-dim flags again.
    """
    target = _target()
    target.observe_state_change(LIGHT, "HUMAN", timestamp=10.0)
    target.observe_state_change("light.den", "HUMAN", timestamp=12.0)

    target.clear_all_manual_flags()

    assert target.is_manual(LIGHT, now=20.0) is False
    assert target.is_manual("light.den", now=20.0) is False
    assert target.observe_state_change(LIGHT, "HUMAN2", timestamp=30.0) is True
    assert target.is_manual(LIGHT, now=31.0) is True


def test_own_context_ring_is_bounded_at_sixteen() -> None:
    """A long-running session must not accumulate context ids forever.

    17 commands, one per minute. The 1st context has fallen out of the ring;
    the 2nd (oldest survivor) and the 17th are still recognised. All reports
    land at t=10_000, long past every suppression window, so only ring
    membership can be doing the work.
    """
    target = _target()
    for i in range(17):
        target.record_command(LIGHT, f"C{i}", {"brightness": 128}, 0.0, now=i * 60.0)

    assert target.observe_state_change(LIGHT, "C0", timestamp=10_000.0) is True
    assert target.observe_state_change(LIGHT, "C1", timestamp=10_000.0) is False
    assert target.observe_state_change(LIGHT, "C16", timestamp=10_000.0) is False


def test_dumped_state_is_json_serializable() -> None:
    """A future RestoreEntity stores this verbatim, so no custom encoding."""
    target = _target(manual_control_reset_minutes=15)
    target.record_command(LIGHT, "C1", {"brightness": 128}, 5.0, now=0.0)
    target.observe_state_change("light.den", "HUMAN", timestamp=10.0)

    assert json.loads(json.dumps(target.to_dict())) == target.to_dict()


def test_restored_target_keeps_flags_contexts_and_windows() -> None:
    """Reload mid-transition: nothing the target knew is lost."""
    config = TargetConfig(manual_control_reset_minutes=15)
    before = Target(config)
    before.record_command(LIGHT, "C1", {"brightness": 128}, 5.0, now=0.0)
    before.observe_state_change("light.den", "HUMAN", timestamp=10.0)

    after = Target.from_dict(config, before.to_dict())

    assert after.is_manual("light.den", now=11.0) is True
    assert after.observe_state_change(LIGHT, "C1", timestamp=100.0) is False
    assert after.observe_state_change(LIGHT, "C2", timestamp=6.5) is False
    assert after.observe_state_change(LIGHT, "C3", timestamp=20.0) is True


def test_restored_manual_flag_still_auto_clears_on_its_original_deadline() -> None:
    config = TargetConfig(manual_control_reset_minutes=15)
    before = Target(config)
    before.observe_state_change(LIGHT, "HUMAN", timestamp=100.0)

    after = Target.from_dict(config, before.to_dict())

    assert after.is_manual(LIGHT, now=100.0 + 14 * 60) is True
    assert after.is_manual(LIGHT, now=100.0 + 15 * 60) is False


def test_restored_own_context_ring_is_still_bounded() -> None:
    """Restoration must rebuild a *bounded* ring, not a plain list."""
    config = TargetConfig()
    before = Target(config)
    for i in range(16):
        before.record_command(LIGHT, f"C{i}", {"brightness": 128}, 0.0, now=i * 60.0)

    after = Target.from_dict(config, before.to_dict())
    after.record_command(LIGHT, "C16", {"brightness": 128}, 0.0, now=10_000.0)

    assert after.observe_state_change(LIGHT, "C0", timestamp=20_000.0) is True
    assert after.observe_state_change(LIGHT, "C1", timestamp=20_000.0) is False
