"""Runtime publishes the same pure schedule values used by the preview."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import astral
import pytest

from custom_components.daylight.coordinator import DayCoordinator
from custom_components.daylight.schedule import Schedule
from custom_components.daylight.solar import SunEvents


def schedule(latitude=40.7128, longitude=-74.0060, zone="America/New_York"):
    return Schedule(SunEvents(astral.Observer(latitude, longitude, 10), ZoneInfo(zone)))


async def test_default_update_interval_and_schedule(hass):
    curve = schedule()
    coordinator = DayCoordinator(hass, curve)
    assert coordinator.update_interval == timedelta(seconds=90)
    assert coordinator.schedule is curve
    assert DayCoordinator(
        hass, curve, timedelta(seconds=30)
    ).update_interval == timedelta(seconds=30)


async def test_values_and_equality(hass):
    curve = schedule()
    coordinator = DayCoordinator(hass, curve)
    now = datetime(2026, 6, 21, 10, tzinfo=UTC)
    state = coordinator.compute_day_state(now)
    assert (state.brightness_factor, state.color_factor) == curve.evaluate(now)
    assert state.sun_position == curve.sun.sun_position(now)
    assert state.is_above_horizon == curve.sun.is_above_horizon(now)
    assert state == replace(state, utc_now=now + timedelta(minutes=1))
    assert state != replace(state, brightness_factor=0.123)


@pytest.mark.parametrize(
    "latitude,longitude,zone,now",
    [
        (
            40.7128,
            -74.0060,
            "America/New_York",
            datetime(2026, 6, 22, 0, 10, tzinfo=UTC),
        ),
        (35.6762, 139.6503, "Asia/Tokyo", datetime(2026, 6, 21, 20, tzinfo=UTC)),
        (40.7128, -74.0060, "UTC", datetime(2026, 6, 22, 0, 10, tzinfo=UTC)),
        (35.6762, 139.6503, "UTC", datetime(2026, 6, 21, 20, tzinfo=UTC)),
    ],
)
async def test_next_events_cross_utc_date_boundaries(
    hass, latitude, longitude, zone, now
):
    coordinator = DayCoordinator(hass, schedule(latitude, longitude, zone))
    state = coordinator.compute_day_state(now)
    assert now < state.next_sunrise < now + timedelta(days=1)
    assert now < state.next_sunset < now + timedelta(days=1)
    if longitude < 0:
        assert state.next_sunset < now + timedelta(hours=1)


async def test_polar_diagnostics_report_real_events_not_synthetic_anchors(hass):
    curve = schedule(78.2, 15.6, "Arctic/Longyearbyen")
    now = datetime(2026, 12, 21, 11, tzinfo=UTC)
    state = DayCoordinator(hass, curve).compute_day_state(now)
    assert not state.is_above_horizon
    assert state.next_sunrise is not None
    assert state.next_sunrise > now + timedelta(days=30)
    assert curve.sun.day(now.date()).fallback


async def test_refresh_uses_now_and_populates_data(hass, monkeypatch):
    now = datetime(2026, 6, 21, 10, tzinfo=UTC)
    monkeypatch.setattr(
        "custom_components.daylight.coordinator.dt_util.utcnow", lambda: now
    )
    coordinator = DayCoordinator(hass, schedule())
    assert coordinator.data is None
    await coordinator.async_refresh()
    assert coordinator.last_update_success
    assert coordinator.data == coordinator.compute_day_state(now)
