"""Tests for `async_setup_entry`/`async_unload_entry`: the hub entry lifecycle.

These exercise the real HA setup path (`hass.config_entries.async_setup`)
against a `MockConfigEntry`, not a hand-rolled call to `async_setup_entry`,
so platform forwarding (`switch.py`/`sensor.py`) is proven end to end.
Expected `CurveSettings` values are literals set explicitly on `hass.config`
and the entry's `data`, independent of how `async_setup_entry` builds them.
"""

import datetime
from unittest.mock import AsyncMock, patch

import astral
from homeassistant.config_entries import ConfigEntryState
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.daylight.color_and_brightness import CurveSettings
from custom_components.daylight.const import DOMAIN
from custom_components.daylight.coordinator import DayCoordinator

_HUB_DATA = {
    "sunrise_time": "06:30:00",
    "min_sunrise_time": None,
    "max_sunrise_time": None,
    "sunset_time": None,
    "min_sunset_time": "20:00:00",
    "max_sunset_time": None,
    "sunrise_offset_minutes": 10,
    "sunset_offset_minutes": -5,
    "brightness_mode": "linear",
    "brightness_mode_time_dark_minutes": 30,
    "brightness_mode_time_light_minutes": 60,
    "update_interval_seconds": 45,
}


async def _setup_entry(
    hass, enable_custom_integrations, hass_config_dir
) -> MockConfigEntry:
    hass.config.config_dir = hass_config_dir
    hass.config.latitude = 40.7128
    hass.config.longitude = -74.0060
    hass.config.elevation = 10
    await hass.config.async_set_time_zone("America/New_York")

    entry = MockConfigEntry(domain=DOMAIN, title="Test Hub", data=_HUB_DATA)
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    return entry


async def test_async_setup_entry_builds_coordinator_with_curve_settings(
    enable_custom_integrations, hass, hass_config_dir
) -> None:
    entry = await _setup_entry(hass, enable_custom_integrations, hass_config_dir)

    assert entry.state is ConfigEntryState.LOADED

    coordinator = entry.runtime_data
    assert isinstance(coordinator, DayCoordinator)
    assert coordinator.update_interval == datetime.timedelta(seconds=45)
    # Proof async_config_entry_first_refresh ran.
    assert coordinator.data is not None

    settings = coordinator.curve_settings
    assert isinstance(settings, CurveSettings)
    assert settings.name == "Test Hub"
    assert settings.astral_observer == astral.Observer(
        latitude=40.7128, longitude=-74.0060, elevation=10
    )
    assert settings.timezone.key == "America/New_York"
    assert settings.sunrise_time == datetime.time(6, 30, 0)
    assert settings.min_sunrise_time is None
    assert settings.max_sunrise_time is None
    assert settings.sunset_time is None
    assert settings.min_sunset_time == datetime.time(20, 0, 0)
    assert settings.max_sunset_time is None
    assert settings.sunrise_offset == datetime.timedelta(minutes=10)
    assert settings.sunset_offset == datetime.timedelta(minutes=-5)
    assert settings.brightness_mode == "linear"
    assert settings.brightness_mode_time_dark == datetime.timedelta(minutes=30)
    assert settings.brightness_mode_time_light == datetime.timedelta(minutes=60)


async def test_async_setup_entry_forwards_to_switch_and_sensor_platforms(
    enable_custom_integrations, hass, hass_config_dir
) -> None:
    """Real `async_forward_entry_setups` call reaches both stub platforms.

    Also proves `entry.runtime_data` is already set by the time the
    platforms are forwarded -- switch.py/sensor.py depend on that ordering,
    not just on the platforms eventually being called.
    """
    runtime_data_at_switch_setup: list[object] = []
    runtime_data_at_sensor_setup: list[object] = []

    async def _capture_switch(hass_, entry_, async_add_entities) -> None:
        runtime_data_at_switch_setup.append(entry_.runtime_data)

    async def _capture_sensor(hass_, entry_, async_add_entities) -> None:
        runtime_data_at_sensor_setup.append(entry_.runtime_data)

    with (
        patch(
            "custom_components.daylight.switch.async_setup_entry",
            _capture_switch,
        ),
        patch(
            "custom_components.daylight.sensor.async_setup_entry",
            _capture_sensor,
        ),
    ):
        entry = await _setup_entry(hass, enable_custom_integrations, hass_config_dir)

    assert entry.state is ConfigEntryState.LOADED
    assert runtime_data_at_switch_setup == [entry.runtime_data]
    assert runtime_data_at_sensor_setup == [entry.runtime_data]
    assert isinstance(runtime_data_at_switch_setup[0], DayCoordinator)
    assert isinstance(runtime_data_at_sensor_setup[0], DayCoordinator)


async def test_async_setup_entry_reloads_on_update_listener(
    enable_custom_integrations, hass, hass_config_dir
) -> None:
    entry = await _setup_entry(hass, enable_custom_integrations, hass_config_dir)

    with patch.object(
        hass.config_entries, "async_reload", AsyncMock(return_value=True)
    ) as mock_reload:
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, "sunrise_offset_minutes": 99}
        )
        await hass.async_block_till_done()

    mock_reload.assert_called_once_with(entry.entry_id)


async def test_entry_survives_a_real_reload(
    enable_custom_integrations, hass, hass_config_dir
) -> None:
    """A later module persists per-target state across exactly this reload."""
    entry = await _setup_entry(hass, enable_custom_integrations, hass_config_dir)
    coordinator_before_reload = entry.runtime_data

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert isinstance(entry.runtime_data, DayCoordinator)
    assert entry.runtime_data is not coordinator_before_reload


async def test_async_unload_entry(
    enable_custom_integrations, hass, hass_config_dir
) -> None:
    entry = await _setup_entry(hass, enable_custom_integrations, hass_config_dir)

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.NOT_LOADED
