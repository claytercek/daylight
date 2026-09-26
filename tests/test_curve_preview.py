"""Tests for curve_preview.py: 24h curve sampling for a chart preview.

No `hass` fixture -- like color_and_brightness.py/adaptation.py, this module
has zero HA runtime dependency, so these tests are plain Python against a
pure function and a frozen dataclass.
"""

import dataclasses
import datetime
import json
from datetime import UTC

import astral

from custom_components.daylight.adaptation import compute_turn_on_kwargs
from custom_components.daylight.color_and_brightness import CurveSettings
from custom_components.daylight.curve_preview import (
    DEFAULT_NUM_POINTS,
    CurvePoint,
    sample_curve,
)

# NYC, matching test_color_and_brightness.py's ground-truth observer.
_NYC_OBSERVER = astral.Observer(latitude=40.7128, longitude=-74.0060, elevation=10)

_START = datetime.datetime(2026, 6, 21, 0, 0, tzinfo=UTC)


def _curve_settings() -> CurveSettings:
    return CurveSettings(
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


def _sample(num_points: int = DEFAULT_NUM_POINTS) -> list[CurvePoint]:
    return sample_curve(
        curve_settings=_curve_settings(),
        start=_START,
        min_brightness_pct=1,
        max_brightness_pct=100,
        min_color_temp_kelvin=2000,
        max_color_temp_kelvin=5500,
        num_points=num_points,
    )


def test_default_num_points_is_96() -> None:
    points = _sample()
    assert len(points) == 96


def test_num_points_is_overridable() -> None:
    points = _sample(num_points=4)
    assert len(points) == 4


def test_points_are_evenly_spaced_across_24h_half_open() -> None:
    """96 points over 24h, half-open: 15-minute spacing, no duplicate endpoint."""
    points = _sample(num_points=4)
    times = [datetime.datetime.fromisoformat(p.utc_time) for p in points]
    assert times[0] == _START
    step = datetime.timedelta(hours=24) / 4
    for i, t in enumerate(times):
        assert t == _START + step * i


def test_brightness_and_color_temp_match_compute_turn_on_kwargs() -> None:
    """Ground truth: direct compute_turn_on_kwargs calls, not re-derived math."""
    settings = _curve_settings()
    dt = datetime.datetime(2026, 6, 21, 9, 30, tzinfo=UTC)
    points = sample_curve(
        curve_settings=settings,
        start=dt,
        min_brightness_pct=1,
        max_brightness_pct=100,
        min_color_temp_kelvin=2000,
        max_color_temp_kelvin=5500,
        num_points=1,
    )
    expected = compute_turn_on_kwargs(
        supported_color_modes={"color_temp"},
        brightness_factor=settings.brightness_factor(dt),
        color_factor=settings.color_factor(dt),
        min_brightness_pct=1,
        max_brightness_pct=100,
        min_color_temp_kelvin=2000,
        max_color_temp_kelvin=5500,
        transition=0,
    )
    assert points[0].brightness_pct == expected["brightness_pct"]
    assert points[0].color_temp_kelvin == expected["color_temp_kelvin"]


def test_first_point_utc_time_is_start() -> None:
    points = _sample(num_points=1)
    assert points[0].utc_time == _START.isoformat()


def test_points_are_json_serializable() -> None:
    points = _sample(num_points=3)
    round_tripped = json.loads(json.dumps([dataclasses.asdict(p) for p in points]))
    assert round_tripped == [dataclasses.asdict(p) for p in points]
