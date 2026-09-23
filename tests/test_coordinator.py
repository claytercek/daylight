"""Tests for DayCoordinator: the hub-level polling wrapper around CurveSettings.

`compute_day_state` is a pure, synchronous method -- constructing a
`DayCoordinator` still needs the real `hass` fixture (DataUpdateCoordinator's
own `__init__` needs the HA "frame helper" set up, which the harness does),
but nothing here awaits or otherwise touches `hass`. Expected values are
computed by calling `CurveSettings`/`SunEvents` directly as ground truth, not
by re-deriving the formulas coordinator.py wraps.
"""

import datetime

import astral

from custom_components.daylight.coordinator import DayState

UTC = datetime.timezone.utc


def test_day_state_equality_ignores_utc_now() -> None:
    """Two states differing only in `utc_now` compare equal."""
    kwargs = dict(
        sun_position=0.5,
        brightness_factor=1.0,
        color_factor=0.5,
        is_above_horizon=True,
        next_sunrise=datetime.datetime(2026, 6, 22, 9, 24, tzinfo=UTC),
        next_sunset=datetime.datetime(2026, 6, 21, 0, 31, tzinfo=UTC),
    )
    a = DayState(utc_now=datetime.datetime(2026, 6, 21, 12, 0, tzinfo=UTC), **kwargs)
    b = DayState(utc_now=datetime.datetime(2026, 6, 21, 12, 1, tzinfo=UTC), **kwargs)
    assert a == b


def test_day_state_inequality_on_other_field() -> None:
    """States differing in a field other than `utc_now` are not equal."""
    kwargs = dict(
        utc_now=datetime.datetime(2026, 6, 21, 12, 0, tzinfo=UTC),
        brightness_factor=1.0,
        color_factor=0.5,
        is_above_horizon=True,
        next_sunrise=datetime.datetime(2026, 6, 22, 9, 24, tzinfo=UTC),
        next_sunset=datetime.datetime(2026, 6, 21, 0, 31, tzinfo=UTC),
    )
    a = DayState(sun_position=0.5, **kwargs)
    b = DayState(sun_position=0.6, **kwargs)
    assert a != b
