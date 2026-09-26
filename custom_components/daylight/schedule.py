"""Solar timing -> four endpoints per track -> stateless normalized factors.

All arithmetic uses UTC instants. Wall-clock rules are resolved in the configured
local timezone. Sampling order and polling frequency never affect the result.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from itertools import pairwise
from typing import Literal

from .solar import SunEvents

Shape = Literal["smooth", "linear"]
TRACKS = ("brightness", "color")
POINTS = ("morning_start", "morning_end", "evening_start", "evening_end")
ENDPOINTS = tuple(f"{track}_{point}" for track in TRACKS for point in POINTS)


class ScheduleError(ValueError):
    """An invalid rule or a dated conflict that can be shown in settings."""


def ease(progress: float, shape: Shape = "smooth") -> float:
    """Exact, bounded endpoints; smoothstep joins flat plateaus at zero slope."""
    value = min(1.0, max(0.0, progress))
    return value if shape == "linear" else value * value * (3 - 2 * value)


@dataclass(frozen=True)
class Endpoint:
    """A clock rule or a signed offset from a real/effective solar anchor.

    Seasonal offsets are expressed in minutes on a twelve-hour daylight day;
    the form converts to/from today's minutes so users needn't learn fractions.
    """

    kind: Literal["solar", "seasonal", "clock"] = "seasonal"
    reference: Literal["sunrise", "sunset", "noon", "midnight"] = "sunrise"
    offset_minutes: float = 0
    clock: time = time(0)
    next_day: bool = False

    def resolve(self, sun: SunEvents, day: date) -> float:
        day += timedelta(days=int(self.next_day))
        if self.kind == "clock":
            return datetime.combine(day, self.clock, sun.timezone).timestamp()
        solar = sun.day(day)
        anchor = {
            "sunrise": solar.rise,
            "sunset": solar.set,
            "noon": solar.noon,
            "midnight": solar.midnight,
        }[self.reference]
        offset = self.offset_minutes * 60
        if self.kind == "seasonal":
            offset *= solar.daylight / (12 * 3600)
        return anchor.timestamp() + offset


@dataclass(frozen=True)
class BasicTiming:
    """Simple intent: paired morning/evening shifts and linked track lengths."""

    morning_offset_minutes: float = 0
    evening_offset_minutes: float = 0
    morning_time: time | None = None
    evening_time: time | None = None
    evening_next_day: bool = False
    brightness_length: float = 1
    color_length: float = 1


@dataclass(frozen=True)
class Track:
    """Resolved UTC seconds; only timing lives here, not solar knowledge."""

    morning_start: float
    morning_end: float
    evening_start: float
    evening_end: float
    morning_shape: Shape = "smooth"
    evening_shape: Shape = "smooth"

    def validate(self, day: date, name: str) -> None:
        if not (
            self.morning_start
            < self.morning_end
            <= self.evening_start
            < self.evening_end
        ):
            raise ScheduleError(
                f"{day.isoformat()}: {name} requires morning start < morning finish "
                "≤ evening start < evening finish. Adjust the conflicting endpoints."
            )

    def value(self, timestamp: float) -> float:
        if timestamp <= self.morning_start or timestamp >= self.evening_end:
            return 0.0
        if timestamp < self.morning_end:
            return ease(
                (timestamp - self.morning_start)
                / (self.morning_end - self.morning_start),
                self.morning_shape,
            )
        if timestamp <= self.evening_start:
            return 1.0
        return 1 - ease(
            (timestamp - self.evening_start) / (self.evening_end - self.evening_start),
            self.evening_shape,
        )


@dataclass(frozen=True)
class ResolvedDay:
    brightness: Track
    color: Track
    compressed: bool
    fallback: bool


@dataclass(frozen=True)
class _Intent:
    """Bases stay fixed during fitting; widths shrink by a shared factor."""

    morning: float
    evening: float
    brightness_width: float
    color_width: float
    scale: float


@dataclass
class Schedule:
    """Small interface for runtime, preview and seasonal validation.

    A custom endpoint overrides the corresponding fitted standard endpoint.
    Untouched endpoints keep their standard seasonal rules; custom results are
    validated, never fitted a second time. Shape-only edits remain basic.
    """

    sun: SunEvents
    basic: BasicTiming = field(default_factory=BasicTiming)
    endpoints: dict[str, Endpoint] = field(default_factory=dict)
    shapes: dict[str, Shape] = field(default_factory=dict)
    _resolved: dict[date, ResolvedDay] = field(
        default_factory=dict, init=False, repr=False
    )

    @property
    def is_custom(self) -> bool:
        return bool(self.endpoints)

    def _intent(self, day: date) -> _Intent:
        basic = self.basic
        solar = self.sun.day(day)
        brightness = solar.daylight / 12 * basic.brightness_length
        color = solar.daylight / 6 * basic.color_length
        if brightness <= 0 or color <= 0:
            raise ScheduleError("Transition lengths must be positive.")
        morning = (
            datetime.combine(day, basic.morning_time, self.sun.timezone).timestamp()
            if basic.morning_time is not None
            else solar.rise.timestamp() + basic.morning_offset_minutes * 60
        )
        evening = (
            datetime.combine(
                day + timedelta(days=int(basic.evening_next_day)),
                basic.evening_time,
                self.sun.timezone,
            ).timestamp()
            if basic.evening_time is not None
            else solar.set.timestamp() + basic.evening_offset_minutes * 60
        )
        if evening <= morning:
            raise ScheduleError(
                f"{day.isoformat()}: evening occurs before morning. "
                "Move morning earlier or evening later."
            )
        # Clock choices constrain the outside edges, solar choices the centers.
        needed = 2 * max(brightness / 2, color)
        needed += (
            brightness
            / 2
            * (
                int(basic.morning_time is not None)
                + int(basic.evening_time is not None)
            )
        )
        return _Intent(
            morning, evening, brightness, color, min(1, (evening - morning) / needed)
        )

    def _night_fit(self, left: _Intent, right: _Intent, day: date) -> float:
        gap = right.morning - left.evening
        if gap < 0:
            raise ScheduleError(
                f"{day.isoformat()}: evening extends past the following morning."
            )
        needed = (
            left.brightness_width * left.scale / 2
            if self.basic.evening_time is None
            else 0
        ) + (
            right.brightness_width * right.scale / 2
            if self.basic.morning_time is None
            else 0
        )
        if needed == 0:
            return 1
        if gap == 0:
            raise ScheduleError(
                f"{day.isoformat()}: solar transitions have no room to finish."
            )
        return min(1, gap / needed)

    def resolve(self, day: date) -> ResolvedDay:
        """Resolve a local day's exact endpoints, independently of sampling order."""
        if day in self._resolved:
            return self._resolved[day]
        before = self._intent(day - timedelta(days=1))
        intent = self._intent(day)
        after = self._intent(day + timedelta(days=1))
        night_scale = min(
            self._night_fit(before, intent, day),
            self._night_fit(intent, after, day + timedelta(days=1)),
        )
        # Only brightness extends outside the solar day. A short night must
        # not unnecessarily shorten color transitions inside daylight.
        brightness = intent.brightness_width * intent.scale * night_scale
        color = intent.color_width * intent.scale
        morning = intent.morning + (
            brightness / 2 if self.basic.morning_time is not None else 0
        )
        evening = intent.evening - (
            brightness / 2 if self.basic.evening_time is not None else 0
        )
        tracks = {}
        for name, times in (
            (
                "brightness",
                (
                    morning - brightness / 2,
                    morning + brightness / 2,
                    evening - brightness / 2,
                    evening + brightness / 2,
                ),
            ),
            ("color", (morning, morning + color, evening - color, evening)),
        ):
            points = dict(zip(POINTS, times, strict=True))
            for point in POINTS:
                if rule := self.endpoints.get(f"{name}_{point}"):
                    points[point] = rule.resolve(self.sun, day)
            track = Track(
                **points,
                morning_shape=self.shapes.get(f"{name}_morning", "smooth"),
                evening_shape=self.shapes.get(f"{name}_evening", "smooth"),
            )
            track.validate(day, name)
            tracks[name] = track
        result = ResolvedDay(
            tracks["brightness"],
            tracks["color"],
            min(intent.scale, night_scale) < 1 - 1e-9,
            self.sun.day(day).fallback,
        )
        if len(self._resolved) >= 400:
            self._resolved.pop(next(iter(self._resolved)))
        self._resolved[day] = result
        return result

    def _check_join(self, left: ResolvedDay, right: ResolvedDay, day: date) -> None:
        for name in TRACKS:
            if (
                getattr(left, name).evening_end
                > getattr(right, name).morning_start + 1e-6
            ):
                raise ScheduleError(
                    f"{day.isoformat()}: {name} evening finishes "
                    "after the next morning starts."
                )

    def validate(self, start: date, days: int = 366) -> None:
        """Check a full seasonal cycle and the joins, returning a dated error."""
        previous = self.resolve(start - timedelta(days=1))
        for offset in range(days + 1):
            day = start + timedelta(days=offset)
            current = self.resolve(day)
            self._check_join(previous, current, day)
            previous = current

    def evaluate(self, instant: datetime) -> tuple[float, float]:
        """Normalized brightness/color factors; safe across midnight and DST."""
        if instant.tzinfo is None:
            raise ValueError("Schedule evaluation requires a timezone-aware instant.")
        day = instant.astimezone(self.sun.timezone).date()
        # Signed solar offsets and explicit next-day rules may straddle dates.
        resolved = [
            self.resolve(day + timedelta(days=offset)) for offset in range(-3, 3)
        ]
        for left, right in pairwise(resolved):
            self._check_join(left, right, day)
        timestamp = instant.timestamp()
        return (
            max(item.brightness.value(timestamp) for item in resolved),
            max(item.color.value(timestamp) for item in resolved),
        )

    def describe(self, day: date) -> dict[str, object]:
        """Resolved local times and explanations shared by forms and preview."""
        resolved = self.resolve(day)
        result: dict[str, object] = {
            "compressed": resolved.compressed,
            "polar_fallback": resolved.fallback,
            "custom": self.is_custom,
        }
        for name in TRACKS:
            track = getattr(resolved, name)
            result[name] = {
                point: datetime.fromtimestamp(getattr(track, point), UTC)
                .astimezone(self.sun.timezone)
                .isoformat()
                for point in POINTS
            }
        return result
