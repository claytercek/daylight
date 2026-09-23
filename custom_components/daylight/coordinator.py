"""Hub-level polling coordinator wrapping CurveSettings.

One `DayCoordinator` per curve config, shared by every target light on that
hub. It only ever emits normalized [0, 1] factors -- it has no notion of any
target's own brightness/color min-max range.
"""

from __future__ import annotations

import dataclasses
import datetime
import logging

from custom_components.daylight.color_and_brightness import CurveSettings

_LOGGER = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class DayState:
    """A snapshot of the sun/curve state at a given instant."""

    utc_now: datetime.datetime = dataclasses.field(compare=False)
    sun_position: float
    brightness_factor: float
    color_factor: float
    is_above_horizon: bool
    next_sunrise: datetime.datetime
    next_sunset: datetime.datetime
