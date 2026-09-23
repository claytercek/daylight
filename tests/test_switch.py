"""Tests for the per-target adaptation switch.

These drive the real HA setup path (`hass.config_entries.async_setup` against
a `MockConfigEntry` carrying target subentries), because `switch.py` is an
adapter: almost everything it can get wrong is in the wiring, not in any
computation it does itself.
"""

from homeassistant.config_entries import ConfigSubentryData
from homeassistant.core import State
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    mock_restore_cache,
)

from custom_components.daylight.const import DOMAIN

_HUB_DATA = {
    "sunrise_time": "06:30:00",
    "min_sunrise_time": None,
    "max_sunrise_time": None,
    "sunset_time": None,
    "min_sunset_time": None,
    "max_sunset_time": None,
    "sunrise_offset_minutes": 0,
    "sunset_offset_minutes": 0,
    "brightness_mode": "default",
    "brightness_mode_time_dark_minutes": 45,
    "brightness_mode_time_light_minutes": 45,
    "update_interval_seconds": 90,
}

_TARGET_DATA = {
    "entities": ["light.kitchen"],
    "min_brightness_pct": 10,
    "max_brightness_pct": 90,
    "min_color_temp_kelvin": 2000,
    "max_color_temp_kelvin": 5500,
    "transition": 4.0,
    "adapt_only_on_state_change": False,
    "manual_control_reset_minutes": 0,
    "separate_turn_on_commands": False,
    "send_split_delay": 0.0,
}

KITCHEN_SWITCH = "switch.kitchen_adapt"


def _target_subentry(title="Kitchen", **overrides) -> ConfigSubentryData:
    return ConfigSubentryData(
        data={**_TARGET_DATA, **overrides},
        subentry_type="target",
        title=title,
        unique_id=None,
    )


async def _setup(hass, hass_config_dir, subentries) -> MockConfigEntry:
    hass.config.config_dir = hass_config_dir
    hass.config.latitude = 40.7128
    hass.config.longitude = -74.0060
    hass.config.elevation = 10
    await hass.config.async_set_time_zone("America/New_York")

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Test Hub",
        data=_HUB_DATA,
        subentries_data=subentries,
    )
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    return entry


async def test_one_switch_entity_per_target_subentry_defaults_off(
    enable_custom_integrations, hass, hass_config_dir
) -> None:
    await _setup(
        hass,
        hass_config_dir,
        [_target_subentry("Kitchen"), _target_subentry("Office")],
    )

    assert sorted(hass.states.async_entity_ids("switch")) == [
        "switch.kitchen_adapt",
        "switch.office_adapt",
    ]
    assert hass.states.get(KITCHEN_SWITCH).state == "off"


async def test_turn_on_and_off_toggle_the_switch_state(
    enable_custom_integrations, hass, hass_config_dir
) -> None:
    await _setup(hass, hass_config_dir, [_target_subentry()])

    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": KITCHEN_SWITCH}, blocking=True
    )
    assert hass.states.get(KITCHEN_SWITCH).state == "on"

    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": KITCHEN_SWITCH}, blocking=True
    )
    assert hass.states.get(KITCHEN_SWITCH).state == "off"


async def test_switch_restores_its_previous_on_state(
    enable_custom_integrations, hass, hass_config_dir
) -> None:
    mock_restore_cache(hass, (State(KITCHEN_SWITCH, "on"),))

    await _setup(hass, hass_config_dir, [_target_subentry()])

    assert hass.states.get(KITCHEN_SWITCH).state == "on"
