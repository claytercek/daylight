"""Behavioral contract for solar timing, fitting and stateless interpolation."""

from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import astral
import pytest

from custom_components.daylight.schedule import (
    BasicTiming,
    Endpoint,
    Schedule,
    ScheduleError,
    ease,
)
from custom_components.daylight.solar import SolarDay, SunEvents

DAY = date(2026, 3, 20)


class FixedSun(SunEvents):
    def __init__(self, hours: float = 12):
        super().__init__(astral.Observer(0, 0))
        self.hours = hours

    def day(self, day: date) -> SolarDay:
        noon = datetime.combine(day, time(12), UTC)
        rise = noon - timedelta(hours=self.hours / 2)
        setting = noon + timedelta(hours=self.hours / 2)
        return SolarDay(rise, setting, noon, noon - timedelta(hours=12), rise, setting)


def at(hour: float, day: date = DAY) -> datetime:
    return datetime.combine(day, time(), UTC) + timedelta(hours=hour)


@pytest.mark.parametrize("hours", [1, 3, 6, 12, 18, 23])
def test_seasonal_default_endpoints_and_night_fitting(hours):
    schedule = Schedule(FixedSun(hours))
    resolved = schedule.resolve(DAY)
    brightness = resolved.brightness
    color = resolved.color
    sunrise = 12 - hours / 2
    sunset = 12 + hours / 2
    assert (brightness.morning_start + brightness.morning_end) / 2 == at(
        sunrise
    ).timestamp()
    assert (brightness.evening_start + brightness.evening_end) / 2 == at(
        sunset
    ).timestamp()
    assert color.morning_start == at(sunrise).timestamp()
    assert color.evening_end == at(sunset).timestamp()
    assert brightness.morning_end - brightness.morning_start == pytest.approx(
        min(hours / 12, 24 - hours) * 3600
    )
    assert color.morning_end - color.morning_start == pytest.approx(hours / 6 * 3600)
    assert color.morning_end < color.evening_start
    schedule.validate(DAY, days=3)


def test_twelve_hour_day_matches_agreed_defaults():
    day = Schedule(FixedSun()).resolve(DAY)
    assert [
        getattr(day.brightness, p)
        for p in ("morning_start", "morning_end", "evening_start", "evening_end")
    ] == [at(h).timestamp() for h in (5.5, 6.5, 17.5, 18.5)]
    assert [
        getattr(day.color, p)
        for p in ("morning_start", "morning_end", "evening_start", "evening_end")
    ] == [at(h).timestamp() for h in (6, 8, 16, 18)]


def test_easing_has_exact_endpoints_and_no_overshoot():
    for shape in ("smooth", "linear"):
        assert ease(-1, shape) == 0
        assert ease(0, shape) == 0
        assert ease(0.5, shape) == 0.5
        assert ease(1, shape) == 1
        assert ease(2, shape) == 1
    assert ease(0.25) == 0.15625
    assert ease(0.25, "linear") == 0.25
    assert ease(1e-5) / 1e-5 < 0.001
    assert (1 - ease(1 - 1e-5)) / 1e-5 < 0.001


def test_shape_changes_values_not_timing():
    smooth = Schedule(FixedSun())
    linear = Schedule(FixedSun(), shapes={"brightness_morning": "linear"})
    assert (
        smooth.resolve(DAY).brightness.morning_start
        == linear.resolve(DAY).brightness.morning_start
    )
    assert smooth.evaluate(at(5.75))[0] == 0.15625
    assert linear.evaluate(at(5.75))[0] == 0.25
    assert smooth.evaluate(at(17.75)) == linear.evaluate(at(17.75))
    assert not linear.is_custom


def test_plateaus_and_exact_endpoint_values():
    schedule = Schedule(FixedSun())
    for hour, expected in (
        (0, (0, 0)),
        (5.5, (0, 0)),
        (6, (0.5, 0)),
        (8, (1, 1)),
        (12, (1, 1)),
        (16, (1, 1)),
        (18, (0.5, 0)),
        (18.5, (0, 0)),
        (23.5, (0, 0)),
    ):
        assert schedule.evaluate(at(hour)) == expected


def test_basic_offsets_move_tracks_together_without_redefining_sun():
    schedule = Schedule(FixedSun(), BasicTiming(morning_offset_minutes=30))
    result = schedule.resolve(DAY)
    assert result.brightness.morning_start == at(6).timestamp()
    assert result.color.morning_start == at(6.5).timestamp()
    assert schedule.sun.day(DAY).sunrise == at(6)


def test_fixed_times_are_outside_edges_and_compress_as_a_group():
    schedule = Schedule(
        FixedSun(), BasicTiming(morning_time=time(9), evening_time=time(11))
    )
    result = schedule.resolve(DAY)
    assert result.compressed
    assert result.brightness.morning_start == at(9).timestamp()
    assert result.brightness.evening_end == at(11).timestamp()
    assert result.color.morning_end == result.color.evening_start == at(10).timestamp()
    assert result.color.evening_end < at(11).timestamp()
    schedule.validate(DAY, days=3)


def test_explicit_following_day_does_not_reset_at_midnight():
    schedule = Schedule(
        FixedSun(),
        BasicTiming(
            morning_time=time(7),
            evening_time=time(1),
            evening_next_day=True,
        ),
    )
    result = schedule.resolve(DAY)
    assert result.brightness.morning_start == at(7).timestamp()
    assert result.brightness.evening_end == at(25).timestamp()
    left = schedule.evaluate(at(24) - timedelta(microseconds=1))
    right = schedule.evaluate(at(24))
    assert left == pytest.approx(right)
    assert schedule.evaluate(at(24.5))[0] == 0.5
    schedule.validate(DAY, days=3)


def test_reversed_times_are_not_implicitly_overnight():
    schedule = Schedule(
        FixedSun(), BasicTiming(morning_time=time(9), evening_time=time(8))
    )
    with pytest.raises(ScheduleError, match="evening occurs before morning"):
        schedule.validate(DAY)


def test_too_long_following_day_schedule_rejected():
    schedule = Schedule(
        FixedSun(),
        BasicTiming(
            morning_time=time(7),
            evening_time=time(8),
            evening_next_day=True,
        ),
    )
    with pytest.raises(ScheduleError, match="following morning"):
        schedule.validate(DAY)


def test_endpoint_timing_rules_follow_sun_without_changing_kind():
    fixed = Endpoint(kind="clock", clock=time(8))
    solar = Endpoint(kind="solar", reference="sunrise", offset_minutes=60)
    seasonal = replace(solar, kind="seasonal")
    winter = FixedSun(6)
    summer = FixedSun(18)
    assert fixed.resolve(winter, DAY) == fixed.resolve(summer, DAY)
    assert solar.resolve(winter, DAY) == at(10).timestamp()
    assert solar.resolve(summer, DAY) == at(4).timestamp()
    assert seasonal.resolve(winter, DAY) == at(9.5).timestamp()
    assert seasonal.resolve(summer, DAY) == at(4.5).timestamp()


def test_custom_endpoints_are_exact_and_do_not_autofit():
    custom = {"color_morning_end": Endpoint(kind="clock", clock=time(17))}
    schedule = Schedule(FixedSun(), endpoints=custom)
    assert schedule.is_custom
    with pytest.raises(ScheduleError, match="color requires"):
        schedule.validate(DAY)


def test_custom_rules_valid_today_but_invalid_in_winter_are_rejected():
    sun = SunEvents(astral.Observer(51.5, 0), ZoneInfo("Europe/London"))
    schedule = Schedule(
        sun,
        endpoints={
            "color_morning_end": Endpoint(
                kind="solar", reference="sunrise", offset_minutes=300
            ),
            "color_evening_start": Endpoint(
                kind="solar", reference="sunset", offset_minutes=-300
            ),
        },
    )
    schedule.resolve(date(2026, 6, 21))
    with pytest.raises(ScheduleError, match=r"2026-.*color requires"):
        schedule.validate(date(2026, 6, 21))


def test_custom_join_cannot_overlap_the_next_day():
    schedule = Schedule(
        FixedSun(),
        endpoints={
            "brightness_evening_end": Endpoint(
                kind="clock", clock=time(7), next_day=True
            ),
        },
    )
    with pytest.raises(ScheduleError, match="next morning starts"):
        schedule.validate(DAY)


def test_sampling_is_order_independent_and_timezone_aware():
    schedule = Schedule(FixedSun())
    instants = [at(hour / 4) for hour in range(96)]
    values = [schedule.evaluate(instant) for instant in instants]
    assert [schedule.evaluate(instant) for instant in reversed(instants)] == list(
        reversed(values)
    )
    assert all(0 <= value <= 1 for pair in values for value in pair)
    with pytest.raises(ValueError, match="timezone-aware"):
        schedule.evaluate(datetime(2026, 3, 20))


@pytest.mark.parametrize("day", [date(2026, 3, 8), date(2026, 11, 1)])
def test_clock_rules_resolve_local_time_across_dst(day):
    zone = ZoneInfo("America/New_York")
    schedule = Schedule(
        SunEvents(astral.Observer(40.7, -74), zone),
        BasicTiming(
            morning_time=time(7),
            evening_time=time(22),
        ),
    )
    schedule.validate(day, days=3)
    result = schedule.resolve(day)
    assert datetime.fromtimestamp(result.brightness.morning_start, zone).hour == 7
    assert datetime.fromtimestamp(result.brightness.evening_end, zone).hour == 22


@pytest.mark.parametrize(
    "day,clock,expected_hour",
    [
        (date(2026, 3, 8), time(2, 30), 3),
        (date(2026, 11, 1), time(1, 30), 1),
    ],
)
def test_dst_gap_and_repeated_clock_time_use_documented_resolution(
    day, clock, expected_hour
):
    zone = ZoneInfo("America/New_York")
    sun = SunEvents(astral.Observer(40.7, -74), zone)
    value = Endpoint(kind="clock", clock=clock).resolve(sun, day)
    local = datetime.fromtimestamp(value, zone)
    assert (local.hour, local.minute, local.fold) == (expected_hour, 30, 0)


@pytest.mark.parametrize("brightness,color", [(0.25, 3), (3, 0.25), (3, 3)])
def test_independent_length_extremes_fit_across_the_year(brightness, color):
    sun = SunEvents(astral.Observer(40.7, -74), ZoneInfo("America/New_York"))
    Schedule(
        sun, BasicTiming(brightness_length=brightness, color_length=color)
    ).validate(DAY)


def test_real_sunset_from_previous_date_index_is_not_skipped():
    sun = SunEvents(astral.Observer(40.7, -74), UTC)
    instant = datetime(2026, 6, 22, 0, 10, tzinfo=UTC)
    sunset = sun.next_event(instant, "sunset")
    assert sunset is not None
    assert instant < sunset < instant + timedelta(hours=1)


@pytest.mark.parametrize(
    "latitude,longitude,zone",
    [
        (40.7, -74, "America/New_York"),
        (35.7, 139.7, "Asia/Tokyo"),
        (78.2, 15.6, "Arctic/Longyearbyen"),
        (-78.2, 15.6, "UTC"),
        (66.6, 25.7, "Europe/Helsinki"),
    ],
)
def test_whole_year_defaults_stay_ordered_including_polar_boundaries(
    latitude, longitude, zone
):
    schedule = Schedule(SunEvents(astral.Observer(latitude, longitude), ZoneInfo(zone)))
    schedule.validate(date(2026, 1, 1))


@pytest.mark.parametrize(
    "day,daylight", [(date(2026, 12, 21), 3600), (date(2026, 6, 21), 23 * 3600)]
)
def test_polar_fallback_is_explicit_not_reported_as_real_events(day, daylight):
    sun = SunEvents(astral.Observer(78.2, 15.6), ZoneInfo("Arctic/Longyearbyen"))
    solar = sun.day(day)
    assert solar.sunrise is solar.sunset is None
    assert solar.fallback
    assert solar.daylight == pytest.approx(daylight, abs=60)
    assert Schedule(sun).describe(day)["polar_fallback"] is True
    assert (
        sun.next_event(datetime.combine(day, time(), sun.timezone), "sunrise")
        is not None
    )
