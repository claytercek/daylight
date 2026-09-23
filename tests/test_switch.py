"""Tests for the per-target adaptation switch.

These drive the real HA setup path (`hass.config_entries.async_setup` against
a `MockConfigEntry` carrying target subentries), because `switch.py` is an
adapter: almost everything it can get wrong is in the wiring, not in any
computation it does itself.
"""

import dataclasses
import datetime
from unittest.mock import patch

from homeassistant.config_entries import ConfigSubentryData
from homeassistant.core import Context, State
from homeassistant.helpers import entity_component
from homeassistant.util import dt as dt_util
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
    hass, hass_config_dir, subentries, lights=(KITCHEN_LIGHT,)
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
        data=_HUB_DATA,
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
    await _turn_switch_on(hass)

    with patch(
        "custom_components.daylight.switch.compute_turn_on_kwargs",
        return_value=dict(_STUB_KWARGS),
    ):
        await _tick(hass, entry)

    assert calls == []


async def test_foreign_state_change_marks_a_member_manual_and_skips_it(
    enable_custom_integrations, hass, hass_config_dir
) -> None:
    entry = await _setup(hass, hass_config_dir, [_target_subentry()])
    calls = async_mock_service(hass, "light", "turn_on")
    await _turn_switch_on(hass)

    # Somebody grabs the dimmer: a state change under a context this
    # integration never issued.
    _set_light(hass, context=Context(), brightness=255)
    await hass.async_block_till_done()

    with patch(
        "custom_components.daylight.switch.compute_turn_on_kwargs",
        return_value=dict(_STUB_KWARGS),
    ):
        await _tick(hass, entry)

    assert calls == []


async def test_members_are_observed_even_while_the_switch_is_off(
    enable_custom_integrations, hass, hass_config_dir
) -> None:
    await _setup(hass, hass_config_dir, [_target_subentry()])
    async_mock_service(hass, "light", "turn_on")
    _set_light(hass, context=Context(), brightness=255)
    await hass.async_block_till_done()

    target = _switch_entity(hass)._target
    assert target.is_manual(KITCHEN_LIGHT, now=dt_util.utcnow().timestamp()) is True


async def test_an_echo_of_our_own_command_never_marks_a_member_manual(
    enable_custom_integrations, hass, hass_config_dir, freezer
) -> None:
    """Past the suppression window, only the context id can clear the echo."""
    entry = await _setup(hass, hass_config_dir, [_target_subentry()])
    calls = async_mock_service(hass, "light", "turn_on")
    await _turn_switch_on(hass)

    with patch(
        "custom_components.daylight.switch.compute_turn_on_kwargs",
        return_value=dict(_STUB_KWARGS),
    ):
        await _tick(hass, entry)
        assert len(calls) == 1

        # Well past `transition` (4s) + target.py's grace (2s), so
        # suppression cannot be what spares this report.
        freezer.tick(datetime.timedelta(seconds=60))
        _set_light(hass, context=calls[0].context, brightness=255)
        await hass.async_block_till_done()

        await _tick(hass, entry)

    assert len(calls) == 2


async def test_a_late_foreign_report_does_mark_a_member_manual(
    enable_custom_integrations, hass, hass_config_dir, freezer
) -> None:
    """The mirror of the echo test: same timing, a context we never issued."""
    entry = await _setup(hass, hass_config_dir, [_target_subentry()])
    calls = async_mock_service(hass, "light", "turn_on")
    await _turn_switch_on(hass)

    with patch(
        "custom_components.daylight.switch.compute_turn_on_kwargs",
        return_value=dict(_STUB_KWARGS),
    ):
        await _tick(hass, entry)
        assert len(calls) == 1

        freezer.tick(datetime.timedelta(seconds=60))
        _set_light(hass, context=Context(), brightness=255)
        await hass.async_block_till_done()

        await _tick(hass, entry)

    assert len(calls) == 1


async def _setup_with_light_off(hass, hass_config_dir, **subentry_overrides):
    """Set up a hub whose only member light starts out off."""
    _set_light(hass, state="off")
    return await _setup(
        hass,
        hass_config_dir,
        [_target_subentry(**subentry_overrides)],
        lights=(),
    )


async def test_member_turning_on_while_the_switch_is_on_is_corrected(
    enable_custom_integrations, hass, hass_config_dir
) -> None:
    """Fix 1: an off->on member is adapted at once and is not left manual."""
    await _setup_with_light_off(hass, hass_config_dir)
    calls = async_mock_service(hass, "light", "turn_on")
    await _turn_switch_on(hass)

    with patch(
        "custom_components.daylight.switch.compute_turn_on_kwargs",
        return_value=dict(_STUB_KWARGS),
    ):
        _set_light(hass, state="on", context=Context(), brightness=255)
        await hass.async_block_till_done()

    target = _switch_entity(hass)._target
    assert target.is_manual(KITCHEN_LIGHT, now=dt_util.utcnow().timestamp()) is False
    assert len(calls) == 1
    assert calls[0].data == {"entity_id": KITCHEN_LIGHT, **_STUB_KWARGS}


async def test_member_turning_on_while_the_switch_is_off_stays_manual(
    enable_custom_integrations, hass, hass_config_dir
) -> None:
    """Fix 6's setup: the identical event, switch off, leaves the flag set."""
    await _setup_with_light_off(hass, hass_config_dir)
    calls = async_mock_service(hass, "light", "turn_on")

    with patch(
        "custom_components.daylight.switch.compute_turn_on_kwargs",
        return_value=dict(_STUB_KWARGS),
    ):
        _set_light(hass, state="on", context=Context(), brightness=255)
        await hass.async_block_till_done()

    target = _switch_entity(hass)._target
    assert target.is_manual(KITCHEN_LIGHT, now=dt_util.utcnow().timestamp()) is True
    assert calls == []


async def test_off_to_on_correction_uses_a_freshly_computed_day_state(
    enable_custom_integrations, hass, hass_config_dir
) -> None:
    """Fix 3: `coordinator.data` can be stale by the time a light comes up."""
    entry = await _setup_with_light_off(hass, hass_config_dir)
    async_mock_service(hass, "light", "turn_on")
    await _turn_switch_on(hass)
    await _tick(hass, entry, _STALE_DAY_STATE)

    with (
        patch(
            "custom_components.daylight.switch.compute_turn_on_kwargs",
            return_value=dict(_STUB_KWARGS),
        ) as compute,
        patch.object(
            entry.runtime_data, "compute_day_state", return_value=_DAY_STATE
        ) as compute_day_state,
    ):
        _set_light(hass, state="on", context=Context(), brightness=255)
        await hass.async_block_till_done()

    assert compute_day_state.call_count == 1
    assert compute.call_args.kwargs["brightness_factor"] == 0.75
    assert compute.call_args.kwargs["color_factor"] == 0.4


async def test_off_to_on_correction_fires_even_with_adapt_only_on_state_change(
    enable_custom_integrations, hass, hass_config_dir
) -> None:
    """In that mode the off->on event is the *only* adaptation trigger."""
    await _setup_with_light_off(
        hass, hass_config_dir, adapt_only_on_state_change=True
    )
    calls = async_mock_service(hass, "light", "turn_on")
    await _turn_switch_on(hass)

    with patch(
        "custom_components.daylight.switch.compute_turn_on_kwargs",
        return_value=dict(_STUB_KWARGS),
    ):
        _set_light(hass, state="on", context=Context(), brightness=255)
        await hass.async_block_till_done()

    assert len(calls) == 1
