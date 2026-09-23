"""Hub-level polling coordinator wrapping CurveSettings.

One `DayCoordinator` per curve config, shared by every target light on that
hub. It only ever emits normalized [0, 1] factors -- it has no notion of any
target's own brightness/color min-max range.
"""

from __future__ import annotations

import dataclasses
import datetime
import logging
from collections.abc import Callable

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from custom_components.daylight.color_and_brightness import CurveSettings

_LOGGER = logging.getLogger(__name__)

DEFAULT_UPDATE_INTERVAL = datetime.timedelta(seconds=90)


@dataclasses.dataclass(frozen=True)
class DayState:
    """A snapshot of the sun/curve state at a given instant."""

    # compare=False: DataUpdateCoordinator(always_update=False) skips notifying
    # listeners when new data == old data. utc_now differs every tick, so
    # without this the equality check could never fire true even when nothing
    # else meaningfully changed. That said, brightness_factor/color_factor/
    # sun_position are themselves continuous functions of time and will
    # almost always differ tick to tick too -- this fixes correctness, it
    # doesn't make always_update=False meaningfully throttle updates here.
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

    def compute_day_state(self, utc_now: datetime.datetime) -> DayState:
        """Build a fresh `DayState` for the given instant.

        Pure and synchronous: no I/O, no `hass` access, no caching beyond
        what `CurveSettings`/`SunEvents` already do internally. Safe to call
        on demand, independent of the poll cycle or `self.data`.
        """
        sun = self.curve_settings.sun
        sun_position = sun.sun_position(utc_now)
        return DayState(
            utc_now=utc_now,
            sun_position=sun_position,
            brightness_factor=self.curve_settings.brightness_factor(utc_now),
            color_factor=self.curve_settings.color_factor(utc_now),
            is_above_horizon=sun_position >= 0,
            next_sunrise=self._next_event(sun.sunrise, utc_now),
            next_sunset=self._next_event(sun.sunset, utc_now),
        )

    @staticmethod
    def _next_event(
        event_fn: Callable[[datetime.date], datetime.datetime],
        utc_now: datetime.datetime,
    ) -> datetime.datetime:
        """Return the soonest strictly-future instant of a date-indexed event.

        `event_fn` (`SunEvents.sunrise`/`.sunset`) is indexed by *date*, but
        the instant it returns can land on a different UTC calendar date than
        the date it was queried with (e.g. an evening sunset local to a
        timezone west of UTC lands after midnight UTC, on the next day). A
        naive "look up today's, roll forward a day if it's already past"
        can therefore skip the real next occurrence: today's date-indexed
        event may already be tomorrow (UTC), while yesterday's date-indexed
        event -- never checked -- is the one still ahead. Scanning the
        surrounding three days and taking the earliest strictly-future
        candidate is immune to that.
        """
        candidates = (
            event_fn(utc_now.date() + datetime.timedelta(days=offset))
            for offset in (-1, 0, 1)
        )
        return min(candidate for candidate in candidates if candidate > utc_now)
