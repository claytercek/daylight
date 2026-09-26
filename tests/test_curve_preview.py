"""Preview and live output share interpolation, range mapping and rounding."""

import dataclasses
import json
from datetime import UTC, date, datetime
from itertools import pairwise
from zoneinfo import ZoneInfo

import astral
import pytest

from custom_components.daylight.adaptation import compute_turn_on_kwargs
from custom_components.daylight.curve_preview import DEFAULT_NUM_POINTS, sample_curve
from custom_components.daylight.schedule import Schedule
from custom_components.daylight.solar import SunEvents

RANGES = {
    "min_brightness_pct": 10,
    "max_brightness_pct": 100,
    "min_color_temp_kelvin": 2500,
    "max_color_temp_kelvin": 4000,
}


def schedule():
    return Schedule(
        SunEvents(astral.Observer(40.7128, -74.006, 10), ZoneInfo("America/New_York"))
    )


def test_default_sampling_includes_both_midnights():
    points = sample_curve(schedule(), day=date(2026, 6, 21), **RANGES)
    assert len(points) == DEFAULT_NUM_POINTS == 97
    assert points[0].utc_time == "2026-06-21T04:00:00+00:00"
    assert points[-1].utc_time == "2026-06-22T04:00:00+00:00"


@pytest.mark.parametrize("day,hours", [(date(2026, 3, 8), 23), (date(2026, 11, 1), 25)])
def test_dst_samples_actual_elapsed_time_without_duplicate_instants(day, hours):
    points = sample_curve(schedule(), day=day, num_points=5, **RANGES)
    instants = [datetime.fromisoformat(point.utc_time) for point in points]
    assert (instants[-1] - instants[0]).total_seconds() == hours * 3600
    assert all(a < b for a, b in pairwise(instants))
    assert (instants[1] - instants[0]).total_seconds() == hours * 3600 / 4


def test_every_point_matches_live_command_values():
    curve = schedule()
    points = sample_curve(curve, day=date(2026, 6, 21), **RANGES)
    for point in points:
        instant = datetime.fromisoformat(point.utc_time)
        brightness, color = curve.evaluate(instant)
        expected = compute_turn_on_kwargs(
            supported_color_modes={"color_temp"},
            brightness_factor=brightness,
            color_factor=color,
            transition=0,
            **RANGES,
        )
        assert point.brightness_pct == expected["brightness_pct"]
        assert point.color_temp_kelvin == expected["color_temp_kelvin"]
        assert instant.tzinfo == UTC
    values = [dataclasses.asdict(point) for point in points]
    assert json.loads(json.dumps(values)) == values


def test_requires_enough_points_for_a_window():
    with pytest.raises(ValueError, match="two points"):
        sample_curve(schedule(), day=date(2026, 6, 21), num_points=1, **RANGES)
