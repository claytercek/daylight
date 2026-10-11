"""Shared Home Assistant setup and light state helpers for switch tests."""

import dataclasses
import datetime

from homeassistant.config_entries import ConfigSubentryData
from homeassistant.helpers import entity_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.daylight.const import DOMAIN
from custom_components.daylight.coordinator import DayState
from custom_components.daylight.schedule_config import default_hub_data

_HUB_DATA = default_hub_data()

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
HALL_LIGHT = "light.hall"

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

# Deliberately different factors from `_DAY_STATE`, to stand in for a cached
# `coordinator.data` that has gone stale since the last poll.
_STALE_DAY_STATE = dataclasses.replace(
    _DAY_STATE, brightness_factor=0.01, color_factor=0.02
)


def _target_subentry(title="Kitchen", **overrides) -> ConfigSubentryData:
    return ConfigSubentryData(
        data={**_TARGET_DATA, **overrides},
        subentry_type="target",
        title=title,
        unique_id=None,
    )


async def _setup(
    hass,
    hass_config_dir,
    subentries,
    lights=(KITCHEN_LIGHT,),
    hub_data_overrides=None,
) -> MockConfigEntry:
    """Set up a hub entry, with member lights already present.

    Seeding the member states *before* setup matters: once the switch entity
    exists it observes every member `state_changed`, so a state written
    afterwards under a foreign context would be flagged manual.
    """
    for entity_id in lights:
        _set_light(hass, entity_id)

    hass.config.config_dir = hass_config_dir
    hass.config.latitude = 40.7128
    hass.config.longitude = -74.0060
    hass.config.elevation = 10
    await hass.config.async_set_time_zone("America/New_York")

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Test Hub",
        version=2,
        data={**_HUB_DATA, **(hub_data_overrides or {})},
        subentries_data=subentries,
    )
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    return entry


def _set_light(
    hass,
    entity_id=KITCHEN_LIGHT,
    state="on",
    color_modes=("color_temp",),
    context=None,
    brightness=128,
):
    """Write a member light's state.

    `brightness` exists so consecutive writes actually differ: hass fires
    `state_reported`, not `state_changed`, when state and attributes are
    unchanged, and only the latter reaches the switch's listener.
    """
    hass.states.async_set(
        entity_id,
        state,
        {"supported_color_modes": list(color_modes), "brightness": brightness},
        context=context,
    )


def _switch_entity(hass, entity_id=KITCHEN_SWITCH):
    """Reach the live entity object, for assertions on its `Target`."""
    return hass.data[entity_component.DATA_INSTANCES]["switch"].get_entity(entity_id)


async def _turn_switch_on(hass, switch=KITCHEN_SWITCH) -> None:
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": switch}, blocking=True
    )
    # Turning on adapts every on member, and dispatches each command as a
    # task, so `blocking=True` alone does not land the service calls.
    await hass.async_block_till_done()


async def _tick(hass, entry, day_state=_DAY_STATE) -> None:
    """Push a fresh `DayState` through the coordinator, as a poll would."""
    entry.runtime_data.async_set_updated_data(day_state)
    await hass.async_block_till_done()
