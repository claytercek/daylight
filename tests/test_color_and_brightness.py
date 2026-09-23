"""Tests for the pure sun-timing / curve math in color_and_brightness.py.

No `hass` fixture, no `enable_custom_integrations` — this module has zero HA
runtime dependency, so these tests are plain Python against pure functions
and a frozen dataclass.
"""

from datetime import date, datetime, timedelta, timezone

import astral
import pytest

from custom_components.daylight.color_and_brightness import (
    CurveSettings,
    SunEvents,
    clamp,
    lerp,
    scaled_tanh,
)

UTC = timezone.utc

# NYC
_NYC_OBSERVER = astral.Observer(latitude=40.7128, longitude=-74.0060, elevation=10)
# Svalbard, well above the Arctic Circle
_SVALBARD_OBSERVER = astral.Observer(latitude=78.2232, longitude=15.6267, elevation=0)


def _sun_events(observer: astral.Observer) -> SunEvents:
    return SunEvents(
        name="test",
        astral_observer=observer,
        sunrise_time=None,
        min_sunrise_time=None,
        max_sunrise_time=None,
        sunset_time=None,
        min_sunset_time=None,
        max_sunset_time=None,
    )


def _curve_settings(brightness_mode: str) -> CurveSettings:
    return CurveSettings(
        name="test",
        astral_observer=_NYC_OBSERVER,
        sunrise_time=None,
        min_sunrise_time=None,
        max_sunrise_time=None,
        sunset_time=None,
        min_sunset_time=None,
        max_sunset_time=None,
        brightness_mode_time_dark=timedelta(minutes=45),
        brightness_mode_time_light=timedelta(minutes=45),
        brightness_mode=brightness_mode,
    )


_FIVE_TIMESTAMPS = [
    datetime(2026, 6, 21, 4, 0, tzinfo=UTC),
    datetime(2026, 6, 21, 9, 30, tzinfo=UTC),
    datetime(2026, 6, 21, 16, 0, tzinfo=UTC),
    datetime(2026, 6, 21, 23, 0, tzinfo=UTC),
    datetime(2026, 6, 22, 2, 0, tzinfo=UTC),
]


@pytest.mark.parametrize(
    ("dt", "expected"),
    list(
        zip(
            _FIVE_TIMESTAMPS,
            [0.047044233382127154, 1, 1, 1, 0.4453615045971149],
            strict=True,
        )
    ),
)
def test_brightness_factor_default(dt: datetime, expected: float) -> None:
    settings = _curve_settings("default")
    assert settings.brightness_factor(dt) == pytest.approx(expected, rel=1e-6)


@pytest.mark.parametrize(
    ("dt", "expected"),
    list(
        zip(
            _FIVE_TIMESTAMPS,
            [0, 0.560507817974797, 1, 1, 0],
            strict=True,
        )
    ),
)
def test_brightness_factor_linear(dt: datetime, expected: float) -> None:
    settings = _curve_settings("linear")
    assert settings.brightness_factor(dt) == pytest.approx(expected, rel=1e-6)


@pytest.mark.parametrize(
    ("dt", "expected"),
    list(
        zip(
            _FIVE_TIMESTAMPS,
            [
                1.1515113281235223e-06,
                0.5881500832202475,
                0.9999999999942095,
                0.9974498371056668,
                0.0029923145456173805,
            ],
            strict=True,
        )
    ),
)
def test_brightness_factor_tanh(dt: datetime, expected: float) -> None:
    settings = _curve_settings("tanh")
    assert settings.brightness_factor(dt) == pytest.approx(expected, rel=1e-6)


def test_lerp_midpoint() -> None:
    assert lerp(5, x1=0, x2=10, y1=0, y2=100) == 50


def test_clamp_above_range() -> None:
    assert clamp(150, 0, 100) == 100


def test_clamp_inverted_bounds() -> None:
    # upstream explicitly supports minimum > maximum (inverted timescale)
    assert clamp(5, 100, 0) == 5


def test_scaled_tanh_endpoints_approach_y_min_and_y_max() -> None:
    # at x1/x2 the tanh curve is defined to sit at y1/y2 fractions of [y_min, y_max]
    low = scaled_tanh(-10, x1=-10, x2=10, y1=0.05, y2=0.95, y_min=0.0, y_max=1.0)
    high = scaled_tanh(10, x1=-10, x2=10, y1=0.05, y2=0.95, y_min=0.0, y_max=1.0)
    mid = scaled_tanh(0, x1=-10, x2=10, y1=0.05, y2=0.95, y_min=0.0, y_max=1.0)
    assert low == pytest.approx(0.05)
    assert high == pytest.approx(0.95)
    assert mid == pytest.approx(0.5)


def test_sunrise_and_sunset_nyc() -> None:
    sun = _sun_events(_NYC_OBSERVER)
    assert sun.sunrise(date(2026, 6, 21)) == datetime(
        2026, 6, 21, 9, 24, 33, 257783, tzinfo=UTC
    )
    assert sun.sunset(date(2026, 6, 21)) == datetime(
        2026, 6, 22, 0, 31, 13, 511454, tzinfo=UTC
    )


@pytest.mark.parametrize(
    ("dt", "expected"),
    [
        (datetime(2026, 6, 21, 4, 0, tzinfo=UTC), -0.9529557666178728),
        (datetime(2026, 6, 21, 9, 30, tzinfo=UTC), 0.02388894260608032),
        (datetime(2026, 6, 21, 16, 0, tzinfo=UTC), 0.9837701823031548),
        (datetime(2026, 6, 21, 23, 0, tzinfo=UTC), 0.3618573630445997),
        (datetime(2026, 6, 22, 2, 0, tzinfo=UTC), -0.5546384954028851),
    ],
)
def test_sun_position_nyc(dt: datetime, expected: float) -> None:
    sun = _sun_events(_NYC_OBSERVER)
    assert sun.sun_position(dt) == pytest.approx(expected, abs=1e-9)


def test_sun_position_polar_night_fallback() -> None:
    sun = _sun_events(_SVALBARD_OBSERVER)
    dt = datetime(2026, 12, 21, 12, 0, tzinfo=UTC)
    assert sun.sun_position(dt) == pytest.approx(-0.09790339087574773, abs=1e-9)


def test_sun_position_midnight_sun_fallback() -> None:
    sun = _sun_events(_SVALBARD_OBSERVER)
    dt = datetime(2026, 6, 21, 0, 0, tzinfo=UTC)
    assert sun.sun_position(dt) == pytest.approx(0.08697411312646541, abs=1e-9)
