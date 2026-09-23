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

from custom_components.daylight.color_and_brightness import CurveSettings
from custom_components.daylight.coordinator import DayCoordinator, DayState

UTC = datetime.timezone.utc

# NYC, matching test_color_and_brightness.py's ground-truth observer.
_NYC_OBSERVER = astral.Observer(latitude=40.7128, longitude=-74.0060, elevation=10)


def _curve_settings(**overrides: object) -> CurveSettings:
    defaults: dict[str, object] = dict(
        name="test",
        astral_observer=_NYC_OBSERVER,
        sunrise_time=None,
        min_sunrise_time=None,
        max_sunrise_time=None,
        sunset_time=None,
        min_sunset_time=None,
        max_sunset_time=None,
        brightness_mode_time_dark=datetime.timedelta(minutes=45),
        brightness_mode_time_light=datetime.timedelta(minutes=45),
    )
    defaults.update(overrides)
    return CurveSettings(**defaults)


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


async def test_default_update_interval_is_90_seconds(hass) -> None:
    coordinator = DayCoordinator(hass, _curve_settings())
    assert coordinator.update_interval == datetime.timedelta(seconds=90)


async def test_update_interval_is_overridable(hass) -> None:
    coordinator = DayCoordinator(
        hass, _curve_settings(), update_interval=datetime.timedelta(seconds=30)
    )
    assert coordinator.update_interval == datetime.timedelta(seconds=30)


async def test_curve_settings_is_stored(hass) -> None:
    settings = _curve_settings()
    coordinator = DayCoordinator(hass, settings)
    assert coordinator.curve_settings is settings


async def test_compute_day_state_wires_brightness_color_and_sun_position(
    hass,
) -> None:
    """Ground truth: direct CurveSettings/SunEvents calls, not re-derived math.

    2026-06-21 04:00 UTC (NYC) is chosen because sun_position (-0.953),
    brightness_factor (0.047) and color_factor (0.0) are all pairwise
    distinct there, so a swapped field would fail this test.
    """
    settings = _curve_settings()
    coordinator = DayCoordinator(hass, settings)
    dt = datetime.datetime(2026, 6, 21, 4, 0, tzinfo=UTC)

    state = coordinator.compute_day_state(dt)

    assert state.sun_position == settings.sun.sun_position(dt)
    assert state.brightness_factor == settings.brightness_factor(dt)
    assert state.color_factor == settings.color_factor(dt)
    assert state.utc_now == dt
