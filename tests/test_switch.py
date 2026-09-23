"""Tests for the per-target adaptation switch.

These drive the real HA setup path (`hass.config_entries.async_setup` against
a `MockConfigEntry` carrying target subentries), because `switch.py` is an
adapter: almost everything it can get wrong is in the wiring, not in any
computation it does itself.
"""

import datetime
from unittest.mock import patch

from homeassistant.config_entries import ConfigSubentryData
from homeassistant.core import State
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_mock_service,
    mock_restore_cache,
)

from custom_components.daylight.const import DOMAIN
from custom_components.daylight.coordinator import DayState

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
KITCHEN_LIGHT = "light.kitchen"

# Stand-in for whatever `adaptation.compute_turn_on_kwargs` would really
# return. `switch.py` must forward these keys verbatim, so the expected
# service-call data below is this literal, never a recomputation.
_STUB_KWARGS = {
    "brightness_pct": 62,
    "color_temp_kelvin": 3200,
    "transition": 4.0,
}

# Literal factors, not read back off the coordinator: the assertion is that
# these exact values reach `compute_turn_on_kwargs`.
_DAY_STATE = DayState(
    utc_now=datetime.datetime(2024, 6, 1, 15, 0, tzinfo=datetime.UTC),
    sun_position=0.5,
    brightness_factor=0.75,
    color_factor=0.4,
    is_above_horizon=True,
    next_sunrise=datetime.datetime(2024, 6, 2, 9, 30, tzinfo=datetime.UTC),
    next_sunset=datetime.datetime(2024, 6, 1, 23, 30, tzinfo=datetime.UTC),
)


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


def _set_light(hass, entity_id=KITCHEN_LIGHT, state="on", color_modes=("color_temp",)):
    hass.states.async_set(
        entity_id, state, {"supported_color_modes": list(color_modes)}
    )


async def _turn_switch_on(hass, switch=KITCHEN_SWITCH) -> None:
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": switch}, blocking=True
    )


async def _tick(hass, entry, day_state=_DAY_STATE) -> None:
    """Push a fresh `DayState` through the coordinator, as a poll would."""
    entry.runtime_data.async_set_updated_data(day_state)
    await hass.async_block_till_done()


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


async def test_coordinator_tick_adapts_each_member_light(
    enable_custom_integrations, hass, hass_config_dir
) -> None:
    entry = await _setup(hass, hass_config_dir, [_target_subentry()])
    calls = async_mock_service(hass, "light", "turn_on")
    _set_light(hass)
    await _turn_switch_on(hass)

    with patch(
        "custom_components.daylight.switch.compute_turn_on_kwargs",
        return_value=dict(_STUB_KWARGS),
    ) as compute:
        await _tick(hass, entry)

    compute.assert_called_once_with(
        supported_color_modes={"color_temp"},
        brightness_factor=0.75,
        color_factor=0.4,
        min_brightness_pct=10,
        max_brightness_pct=90,
        min_color_temp_kelvin=2000,
        max_color_temp_kelvin=5500,
        transition=4.0,
    )
    assert len(calls) == 1
    assert calls[0].data == {"entity_id": KITCHEN_LIGHT, **_STUB_KWARGS}


async def test_coordinator_tick_does_nothing_while_the_switch_is_off(
    enable_custom_integrations, hass, hass_config_dir
) -> None:
    entry = await _setup(hass, hass_config_dir, [_target_subentry()])
    calls = async_mock_service(hass, "light", "turn_on")
    _set_light(hass)

    with patch(
        "custom_components.daylight.switch.compute_turn_on_kwargs",
        return_value=dict(_STUB_KWARGS),
    ):
        await _tick(hass, entry)

    assert calls == []


async def test_coordinator_tick_does_nothing_when_adapt_only_on_state_change(
    enable_custom_integrations, hass, hass_config_dir
) -> None:
    entry = await _setup(
        hass,
        hass_config_dir,
        [_target_subentry(adapt_only_on_state_change=True)],
    )
    calls = async_mock_service(hass, "light", "turn_on")
    _set_light(hass)
    await _turn_switch_on(hass)

    with patch(
        "custom_components.daylight.switch.compute_turn_on_kwargs",
        return_value=dict(_STUB_KWARGS),
    ):
        await _tick(hass, entry)

    assert calls == []
