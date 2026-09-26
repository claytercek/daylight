"""Hub polling around the same stateless schedule used by settings and preview."""

from __future__ import annotations

import dataclasses
import datetime
import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .schedule import Schedule

_LOGGER = logging.getLogger(__name__)
DEFAULT_UPDATE_INTERVAL = datetime.timedelta(seconds=90)


@dataclasses.dataclass(frozen=True)
class DayState:
    """A target-independent snapshot; sunrise/sunset are real events only."""

    utc_now: datetime.datetime = dataclasses.field(compare=False)
    sun_position: float
    brightness_factor: float
    color_factor: float
    is_above_horizon: bool
    next_sunrise: datetime.datetime | None
    next_sunset: datetime.datetime | None


class DayCoordinator(DataUpdateCoordinator[DayState]):
    """Periodically publish normalized factors without changing device behavior."""

    def __init__(
        self,
        hass: HomeAssistant,
        schedule: Schedule,
        update_interval: datetime.timedelta = DEFAULT_UPDATE_INTERVAL,
        *,
        name: str = "Daylight",
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=name,
            update_interval=update_interval,
            always_update=False,
        )
        self.schedule = schedule

    def compute_day_state(self, utc_now: datetime.datetime) -> DayState:
        """Evaluate the requested instant, independent of the poll cycle."""
        brightness, color = self.schedule.evaluate(utc_now)
        sun = self.schedule.sun
        return DayState(
            utc_now=utc_now,
            sun_position=sun.sun_position(utc_now),
            brightness_factor=brightness,
            color_factor=color,
            is_above_horizon=sun.is_above_horizon(utc_now),
            next_sunrise=sun.next_event(utc_now, "sunrise"),
            next_sunset=sun.next_event(utc_now, "sunset"),
        )

    async def _async_update_data(self) -> DayState:
        try:
            return self.compute_day_state(dt_util.utcnow())
        except ValueError as err:
            # A location/timezone change can invalidate a previously valid rule.
            # Fail the update instead of sending guessed lighting values.
            raise UpdateFailed(str(err)) from err
