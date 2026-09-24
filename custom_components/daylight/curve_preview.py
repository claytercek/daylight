"""Sample the daylight curve over 24 hours, for a chart preview.

This module deliberately has **no Home Assistant dependency**: no `hass`, no
I/O, no `datetime.now()` -- `start` is passed in by the caller, same as
`Target` in target.py. That keeps `sample_curve` a pure, synchronous function
that can be called on demand to back a future frontend graphical curve
preview (a custom HA panel, not built here -- this module only produces the
sampled points, not any HTTP/websocket surface for delivering them).

`start` must be timezone-aware: `CurveSettings.sun.sun_position` compares
`dt.timestamp()` against astral event timestamps, so a naive `start` would
silently compute against the wrong instant rather than raising.

A brightness/color-temp mode or manually-set sunrise/sunset time far outside
its day can make the underlying sun-event math raise `ValueError` for some
sample instants (see `SunEvents._validate_sun_event_order`); that is not
caught here and propagates to the caller, same as it does from
`CurveSettings.brightness_factor`/`.color_factor` directly.
"""

from __future__ import annotations

import dataclasses
import datetime

from custom_components.daylight.color_and_brightness import CurveSettings, lerp

# 15-minute spacing over 24h.
DEFAULT_NUM_POINTS = 96


@dataclasses.dataclass(frozen=True)
class CurvePoint:
    """One sampled point of the daylight curve.

    JSON-serializable via `dataclasses.asdict()`: `utc_time` is an ISO 8601
    string, not a `datetime`, since `datetime` itself is not JSON-serializable.
    """

    utc_time: str
    brightness_pct: int
    color_temp_kelvin: int


def sample_curve(
    curve_settings: CurveSettings,
    *,
    start: datetime.datetime,
    min_brightness_pct: int,
    max_brightness_pct: int,
    min_color_temp_kelvin: int,
    max_color_temp_kelvin: int,
    num_points: int = DEFAULT_NUM_POINTS,
) -> list[CurvePoint]:
    """Sample brightness/color-temp across the 24h window starting at `start`.

    Points are evenly spaced over a half-open 24h interval (`start` inclusive,
    `start + 24h` exclusive), so `num_points` samples never duplicate the
    first point at the end -- e.g. the default 96 points land exactly 15
    minutes apart.

    Mirrors `adaptation.compute_turn_on_kwargs`'s brightness/color-temp
    mapping (same `lerp` call, same rounding), but always includes color
    temperature regardless of any target's actual light capabilities -- this
    previews the full curve a hub can produce, not a single light's command.
    """
    step = datetime.timedelta(hours=24) / num_points
    points = []
    for i in range(num_points):
        dt = start + step * i
        brightness_pct = round(
            lerp(
                curve_settings.brightness_factor(dt),
                x1=0.0,
                x2=1.0,
                y1=min_brightness_pct,
                y2=max_brightness_pct,
            )
        )
        color_temp_kelvin = round(
            lerp(
                curve_settings.color_factor(dt),
                x1=0.0,
                x2=1.0,
                y1=min_color_temp_kelvin,
                y2=max_color_temp_kelvin,
            )
        )
        points.append(
            CurvePoint(
                utc_time=dt.isoformat(),
                brightness_pct=brightness_pct,
                color_temp_kelvin=color_temp_kelvin,
            )
        )
    return points
