"""Read-only sampling of a saved schedule over a complete local calendar day."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from .adaptation import map_level
from .schedule import Schedule

# Includes both midnights: fifteen-minute intervals on an ordinary day.
DEFAULT_NUM_POINTS = 97


@dataclass(frozen=True)
class CurvePoint:
    utc_time: str
    brightness_pct: int
    color_temp_kelvin: int


def sample_curve(
    schedule: Schedule,
    *,
    day: date,
    min_brightness_pct: int,
    max_brightness_pct: int,
    min_color_temp_kelvin: int,
    max_color_temp_kelvin: int,
    num_points: int = DEFAULT_NUM_POINTS,
) -> list[CurvePoint]:
    """Sample the actual 23/24/25-hour span, including the following midnight.

    Values use the same range mapping as commands. They describe the target's
    configured range, not a particular bulb's capability-clamped output.
    """
    if num_points < 2:
        raise ValueError("At least two points are required.")
    start = datetime.combine(day, time(), schedule.sun.timezone).astimezone(UTC)
    end = datetime.combine(
        day + timedelta(days=1),
        time(),
        schedule.sun.timezone,
    ).astimezone(UTC)
    step = (end - start) / (num_points - 1)
    points = []
    for i in range(num_points):
        instant = start + step * i
        brightness, color = schedule.evaluate(instant)
        points.append(
            CurvePoint(
                utc_time=instant.isoformat(),
                brightness_pct=map_level(
                    brightness, min_brightness_pct, max_brightness_pct
                ),
                color_temp_kelvin=map_level(
                    color, min_color_temp_kelvin, max_color_temp_kelvin
                ),
            )
        )
    return points
