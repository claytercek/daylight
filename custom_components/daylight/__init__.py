"""The daylight integration."""

from __future__ import annotations

import datetime

import astral
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .color_and_brightness import CurveSettings
from .coordinator import DayCoordinator

PLATFORMS = (Platform.SWITCH, Platform.SENSOR)


def _parse_time(value: str | None) -> datetime.time | None:
    """Parse an `"HH:MM:SS"` entry-data value, passing `None` through."""
    if value is None:
        return None
    return datetime.time.fromisoformat(value)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a daylight hub entry: build its coordinator, forward platforms."""
    data = entry.data
    timezone = await dt_util.async_get_time_zone(hass.config.time_zone)

    curve_settings = CurveSettings(
        name=entry.title,
        astral_observer=astral.Observer(
            latitude=hass.config.latitude,
            longitude=hass.config.longitude,
            elevation=hass.config.elevation,
        ),
        timezone=timezone,
        sunrise_time=_parse_time(data["sunrise_time"]),
        min_sunrise_time=_parse_time(data["min_sunrise_time"]),
        max_sunrise_time=_parse_time(data["max_sunrise_time"]),
        sunset_time=_parse_time(data["sunset_time"]),
        min_sunset_time=_parse_time(data["min_sunset_time"]),
        max_sunset_time=_parse_time(data["max_sunset_time"]),
        sunrise_offset=datetime.timedelta(minutes=data["sunrise_offset_minutes"]),
        sunset_offset=datetime.timedelta(minutes=data["sunset_offset_minutes"]),
        brightness_mode=data["brightness_mode"],
        brightness_mode_time_dark=datetime.timedelta(
            minutes=data["brightness_mode_time_dark_minutes"]
        ),
        brightness_mode_time_light=datetime.timedelta(
            minutes=data["brightness_mode_time_light_minutes"]
        ),
    )

    update_interval = datetime.timedelta(seconds=data["update_interval_seconds"])
    coordinator = DayCoordinator(hass, curve_settings, update_interval)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))

    return True


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the hub entry, e.g. after a target subentry add/remove/edit."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a daylight hub entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
