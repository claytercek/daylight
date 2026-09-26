"""Regressions for delayed light reports and manual-control boundaries."""

import asyncio
import datetime
from unittest.mock import patch

from homeassistant.core import Context
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_mock_service

from tests.switch_support import (
    _STUB_KWARGS,
    KITCHEN_LIGHT,
    KITCHEN_SWITCH,
    _set_light,
    _setup,
    _switch_entity,
    _target_subentry,
    _tick,
    _turn_switch_on,
)


async def test_no_op_report_does_not_extend_manual_reset(
    enable_custom_integrations, hass, hass_config_dir, freezer
) -> None:
    entry = await _setup(
        hass, hass_config_dir, [_target_subentry(manual_control_reset_minutes=1)]
    )
    calls = async_mock_service(hass, "light", "turn_on")
    await _turn_switch_on(hass)
    calls.clear()

    freezer.tick(datetime.timedelta(seconds=60))
    _set_light(hass, context=Context(), brightness=255)
    await hass.async_block_till_done()
    target = _switch_entity(hass)._target
    assert target.is_manual(KITCHEN_LIGHT, now=dt_util.utcnow().timestamp())

    # Some integrations repeat their rounded control values with a fresh
    # context and updated metadata. This report is not a new hand adjustment.
    freezer.tick(datetime.timedelta(seconds=45))
    hass.states.async_set(
        KITCHEN_LIGHT,
        "on",
        {
            "supported_color_modes": ["color_temp"],
            "brightness": 255,
            "friendly_name": "Kitchen light",
        },
        context=Context(),
    )
    await hass.async_block_till_done()

    freezer.tick(datetime.timedelta(seconds=16))
    with patch(
        "custom_components.daylight.switch.compute_turn_on_kwargs",
        return_value=dict(_STUB_KWARGS),
    ):
        await _tick(hass, entry)

    assert not target.is_manual(KITCHEN_LIGHT, now=dt_util.utcnow().timestamp())
    assert [call.data for call in calls] == [
        {"entity_id": KITCHEN_LIGHT, **_STUB_KWARGS}
    ]


async def test_group_turn_on_with_scene_values_adapts_as_one_light(
    enable_custom_integrations, hass, hass_config_dir
) -> None:
    group = "light.living_room_group"
    member = "light.living_room_lamp"
    _set_light(hass, group, state="off")
    _set_light(hass, member, state="off")
    await _setup(
        hass,
        hass_config_dir,
        [_target_subentry("Living Room", entities=[group])],
        lights=(),
    )
    calls = async_mock_service(hass, "light", "turn_on")
    await _turn_switch_on(hass, "switch.living_room_adapt")
    assert calls == []

    # Group platforms may publish the scene's values under a child context
    # after individual bulbs report. Only the configured group is observed.
    hass.states.async_set(
        group,
        "on",
        {
            "supported_color_modes": ["color_temp"],
            "brightness": 220,
            "color_temp_kelvin": 2700,
        },
        context=Context(parent_id="scene-trigger"),
    )
    await hass.async_block_till_done()

    target = _switch_entity(hass, "switch.living_room_adapt")._target
    assert not target.is_manual(group, now=dt_util.utcnow().timestamp())
    assert [call.data["entity_id"] for call in calls] == [group]
    assert hass.states.get(member).state == "off"


async def test_unavailable_light_cancels_pending_split_color_command(
    enable_custom_integrations, hass, hass_config_dir
) -> None:
    await _setup(
        hass,
        hass_config_dir,
        [_target_subentry(separate_turn_on_commands=True, send_split_delay=60.0)],
    )
    calls = async_mock_service(hass, "light", "turn_on")

    with patch(
        "custom_components.daylight.switch.compute_turn_on_kwargs",
        return_value=dict(_STUB_KWARGS),
    ):
        await hass.services.async_call(
            "switch", "turn_on", {"entity_id": KITCHEN_SWITCH}, blocking=True
        )
        await asyncio.sleep(0)
        assert len(calls) == 1
        hass.states.async_set(KITCHEN_LIGHT, "unavailable", {}, context=Context())
        await hass.async_block_till_done()

    assert len(calls) == 1
    target = _switch_entity(hass)._target
    assert not target.is_manual(KITCHEN_LIGHT, now=dt_util.utcnow().timestamp())


async def test_state_change_only_does_not_retry_when_bulb_gives_no_feedback(
    enable_custom_integrations, hass, hass_config_dir
) -> None:
    """An on-edge send is final in this mode, even if the bulb stays stale."""
    _set_light(hass, state="off")
    entry = await _setup(
        hass,
        hass_config_dir,
        [_target_subentry(adapt_only_on_state_change=True)],
        lights=(),
    )
    calls = async_mock_service(hass, "light", "turn_on")
    await _turn_switch_on(hass)
    assert calls == []

    with patch(
        "custom_components.daylight.switch.compute_turn_on_kwargs",
        return_value=dict(_STUB_KWARGS),
    ):
        _set_light(hass, state="on", context=Context(), brightness=10)
        await hass.async_block_till_done()
        assert len(calls) == 1

        # The mocked service never updates the bulb's reported controls.
        # Polling therefore cannot be mistaken for another on-edge event.
        await _tick(hass, entry)
        await _tick(hass, entry)

    assert hass.states.get(KITCHEN_LIGHT).attributes["brightness"] == 10
    assert [call.data for call in calls] == [
        {"entity_id": KITCHEN_LIGHT, **_STUB_KWARGS}
    ]
