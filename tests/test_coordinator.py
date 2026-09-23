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
import pytest

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


@pytest.mark.parametrize(
    ("dt", "expected"),
    [
        # sun_position ≈ -0.953 (below horizon) -- ground truth from
        # test_compute_day_state_wires_brightness_color_and_sun_position.
        (datetime.datetime(2026, 6, 21, 4, 0, tzinfo=UTC), False),
        # sun_position ≈ +0.024 (above horizon).
        (datetime.datetime(2026, 6, 21, 9, 30, tzinfo=UTC), True),
    ],
)
async def test_is_above_horizon(hass, dt: datetime.datetime, expected: bool) -> None:
    settings = _curve_settings()
    coordinator = DayCoordinator(hass, settings)
    # Confirm the ground-truth premise before trusting the assertion below.
    assert (settings.sun.sun_position(dt) >= 0) == expected

    state = coordinator.compute_day_state(dt)

    assert state.is_above_horizon is expected


async def test_next_sunrise_and_next_sunset_before_sunrise(hass) -> None:
    """Before today's sunrise: next_sunrise is today's, next_sunset is ahead.

    Ground truth: direct `SunEvents.sunrise`/`.sunset` calls, date-indexed by
    the astral library the way `color_and_brightness.py` already uses them.
    """
    settings = _curve_settings()
    coordinator = DayCoordinator(hass, settings)
    dt = datetime.datetime(2026, 6, 21, 4, 0, tzinfo=UTC)

    state = coordinator.compute_day_state(dt)

    assert state.next_sunrise == settings.sun.sunrise(datetime.date(2026, 6, 21))
    assert state.next_sunset == settings.sun.sunset(datetime.date(2026, 6, 21))


async def test_next_sunrise_rolls_forward_after_todays_sunrise(hass) -> None:
    """Once today's sunrise has passed, next_sunrise is tomorrow's."""
    settings = _curve_settings()
    coordinator = DayCoordinator(hass, settings)
    dt = datetime.datetime(2026, 6, 21, 12, 0, tzinfo=UTC)

    state = coordinator.compute_day_state(dt)

    assert state.next_sunrise == settings.sun.sunrise(datetime.date(2026, 6, 22))


async def test_next_sunset_does_not_skip_a_day_across_the_utc_date_boundary(
    hass,
) -> None:
    """Regression: a naive "today's date-indexed event, else roll forward one
    day" lookup breaks for observers west of the prime meridian, where an
    evening sunset is indexed under one UTC calendar date but the instant
    itself falls after UTC midnight, on the next one.

    At 2026-06-22 00:10 UTC (NYC), the real next sunset is only ~21 minutes
    away: `sunset(date(2026, 6, 21))`, which lands at 2026-06-22 00:31 UTC.
    A same-day lookup (`sunset(date(2026, 6, 22))`) resolves to 2026-06-23
    00:31 UTC instead -- already in the future relative to `utc_now`, so a
    naive implementation would never roll forward and would wrongly report
    a sunset almost 24 hours away instead of the correct ~21 minutes.
    """
    settings = _curve_settings()
    coordinator = DayCoordinator(hass, settings)
    dt = datetime.datetime(2026, 6, 22, 0, 10, tzinfo=UTC)

    correct = settings.sun.sunset(datetime.date(2026, 6, 21))
    naive_and_wrong = settings.sun.sunset(datetime.date(2026, 6, 22))
    assert correct < dt + datetime.timedelta(hours=1) < naive_and_wrong

    state = coordinator.compute_day_state(dt)

    assert state.next_sunset == correct


async def test_async_update_data_computes_fresh_state_from_dt_util_utcnow(
    hass, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`_async_update_data` defers to `dt_util.utcnow`, not the wall clock
    directly -- monkeypatching that one seam pins the "now" it computes
    against.
    """
    settings = _curve_settings()
    coordinator = DayCoordinator(hass, settings)
    dt = datetime.datetime(2026, 6, 21, 9, 30, tzinfo=UTC)
    monkeypatch.setattr(
        "custom_components.daylight.coordinator.dt_util.utcnow", lambda: dt
    )

    data = await coordinator._async_update_data()

    assert data.utc_now == dt
    assert data == coordinator.compute_day_state(dt)


async def test_first_refresh_populates_data(
    hass, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`coordinator.data` is None before any refresh; a refresh fills it in
    via the same `_async_update_data` -> `compute_day_state` poll path.

    (Not `async_config_entry_first_refresh`: that requires a config entry,
    which is out of scope here -- wiring a `DayCoordinator` to a config entry
    belongs to a different module.)
    """
    settings = _curve_settings()
    coordinator = DayCoordinator(hass, settings)
    dt = datetime.datetime(2026, 6, 21, 9, 30, tzinfo=UTC)
    monkeypatch.setattr(
        "custom_components.daylight.coordinator.dt_util.utcnow", lambda: dt
    )
    assert coordinator.data is None

    await coordinator.async_refresh()

    assert coordinator.last_update_success is True
    assert coordinator.data == coordinator.compute_day_state(dt)
