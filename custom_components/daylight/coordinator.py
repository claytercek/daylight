"""Hub-level polling coordinator wrapping CurveSettings.

One `DayCoordinator` per curve config, shared by every target light on that
hub. It only ever emits normalized [0, 1] factors -- it has no notion of any
target's own brightness/color min-max range.
"""

from __future__ import annotations

import dataclasses
import datetime
import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from custom_components.daylight.color_and_brightness import CurveSettings

_LOGGER = logging.getLogger(__name__)

DEFAULT_UPDATE_INTERVAL = datetime.timedelta(seconds=90)


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


class DayCoordinator(DataUpdateCoordinator[DayState]):
    """Polls on an interval, producing a `DayState` snapshot each tick."""

    def __init__(
        self,
        hass: HomeAssistant,
        curve_settings: CurveSettings,
        update_interval: datetime.timedelta = DEFAULT_UPDATE_INTERVAL,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=curve_settings.name,
            update_interval=update_interval,
            always_update=False,
        )
        self.curve_settings = curve_settings
