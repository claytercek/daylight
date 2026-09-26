"""Astronomical anchors, independent of lighting schedules and interpolation.

The one-hour polar fallback derives from Adaptive Lighting (Apache-2.0).
See NOTICE. Synthetic lighting anchors are kept separate from actual events.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta, tzinfo

import astral
import astral.sun


@dataclass(frozen=True)
class SolarDay:
    """UTC instants indexed by the observer's local calendar date."""

    sunrise: datetime | None
    sunset: datetime | None
    noon: datetime
    midnight: datetime
    rise: datetime
    set: datetime

    @property
    def daylight(self) -> float:
        """Effective daylight in seconds, including the polar fallback."""
        return (self.set - self.rise).total_seconds()

    @property
    def fallback(self) -> bool:
        return self.sunrise is None or self.sunset is None


@dataclass
class SunEvents:
    """Resolve real solar events, with synthetic anchors only when absent."""

    observer: astral.Observer
    timezone: tzinfo = UTC
    _days: dict[date, SolarDay] = field(default_factory=dict, init=False, repr=False)

    def day(self, day: date) -> SolarDay:
        if day in self._days:
            return self._days[day]
        noon = astral.sun.noon(self.observer, day, tzinfo=self.timezone).astimezone(UTC)
        midnight = astral.sun.midnight(
            self.observer, day, tzinfo=self.timezone
        ).astimezone(UTC)
        next_midnight = astral.sun.midnight(
            self.observer, day + timedelta(days=1), tzinfo=self.timezone
        ).astimezone(UTC)
        try:
            sunrise = astral.sun.sunrise(
                self.observer, day, tzinfo=self.timezone
            ).astimezone(UTC)
        except ValueError:
            sunrise = None
        try:
            sunset = astral.sun.sunset(
                self.observer, day, tzinfo=self.timezone
            ).astimezone(UTC)
        except ValueError:
            sunset = None

        polar_summer = (
            astral.sun.elevation(self.observer, noon)
            + astral.sun.elevation(self.observer, midnight)
        ) > 0
        half_hour = timedelta(minutes=30)
        rise = sunrise or (midnight + half_hour if polar_summer else noon - half_hour)
        setting = sunset or (
            next_midnight - half_hour if polar_summer else noon + half_hour
        )
        result = SolarDay(sunrise, sunset, noon, midnight, rise, setting)
        if result.daylight <= 0:
            raise ValueError(f"Cannot resolve solar day for {day.isoformat()}")
        # A year's validation plus adjacent days fits without unbounded growth.
        if len(self._days) >= 400:
            self._days.pop(next(iter(self._days)))
        self._days[day] = result
        return result

    def next_event(self, instant: datetime, event: str) -> datetime | None:
        """Next real crossing, or None if none occurs in the coming year."""
        local_day = instant.astimezone(self.timezone).date()
        # Astral's date-indexed cycles can cross calendar boundaries, notably
        # sunset west of Greenwich when HA itself uses UTC. Include prior dates
        # rather than skipping a still-upcoming crossing from yesterday's cycle.
        for offset in range(-2, 368):
            day = self.day(local_day + timedelta(days=offset))
            value = day.sunrise if event == "sunrise" else day.sunset
            if value is not None and value > instant:
                return value
        return None

    def sun_position(self, instant: datetime) -> float:
        """Legacy normalized solar-phase diagnostic, not a lighting curve."""
        local_day = instant.astimezone(self.timezone).date()
        events: list[tuple[float, str]] = []
        for offset in range(-2, 3):
            day = self.day(local_day + timedelta(days=offset))
            events.extend(
                (value.timestamp(), name)
                for name, value in (
                    ("sunrise", day.rise),
                    ("sunset", day.set),
                    ("noon", day.noon),
                    ("midnight", day.midnight),
                )
            )
        events.sort()
        now = instant.timestamp()
        index = bisect.bisect_right([value for value, _ in events], now)
        prev_ts, _ = events[index - 1]
        next_ts, next_event = events[index]
        horizon, extreme = (
            (prev_ts, next_ts)
            if next_event in ("sunrise", "sunset")
            else (next_ts, prev_ts)
        )
        sign = 1 if next_event in ("sunset", "noon") else -1
        return sign * (1 - ((now - horizon) / (horizon - extreme)) ** 2)

    def is_above_horizon(self, instant: datetime) -> bool:
        """Physical sun state; synthetic lighting anchors do not affect it."""
        return astral.sun.elevation(self.observer, instant) >= 0
