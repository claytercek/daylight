"""Pre-dispatch errors and bulbs that accept calls without reporting state."""

from unittest.mock import patch

import voluptuous as vol
from homeassistant.core import ServiceCall
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_mock_service

from tests.switch_support import (
    _STUB_KWARGS,
    HALL_LIGHT,
    KITCHEN_LIGHT,
    _setup,
    _switch_entity,
    _target_subentry,
    _tick,
    _turn_switch_on,
)


async def test_missing_service_rolls_back_suppression_and_retries(
    enable_custom_integrations, hass, hass_config_dir, caplog
) -> None:
    entry = await _setup(hass, hass_config_dir, [_target_subentry()])
    switch = _switch_entity(hass)
    hass.services.async_remove("light", "turn_on")

    with patch(
        "custom_components.daylight.switch.compute_turn_on_kwargs",
        return_value=dict(_STUB_KWARGS),
    ):
        await _turn_switch_on(hass)
        assert "Unable to adapt light.kitchen" in caplog.text
        assert switch._target.to_dict()["entities"][KITCHEN_LIGHT] == {
            "own_context_ids": [],
            "suppress_until": 0.0,
            "manual": False,
            "manual_since": None,
        }
        calls = async_mock_service(hass, "light", "turn_on")
        await _tick(hass, entry)

    assert [call.data["entity_id"] for call in calls] == [KITCHEN_LIGHT]


async def test_second_split_dispatch_failure_keeps_first_suppression(
    enable_custom_integrations, hass, hass_config_dir, caplog
) -> None:
    entry = await _setup(
        hass,
        hass_config_dir,
        [_target_subentry(separate_turn_on_commands=True, send_split_delay=0.01)],
    )
    switch = _switch_entity(hass)
    first_calls: list[ServiceCall] = []

    async def accept_first(call: ServiceCall) -> None:
        first_calls.append(call)
        hass.services.async_remove("light", "turn_on")

    hass.services.async_register("light", "turn_on", accept_first)
    with patch(
        "custom_components.daylight.switch.compute_turn_on_kwargs",
        return_value=dict(_STUB_KWARGS),
    ):
        await _turn_switch_on(hass)
        assert len(first_calls) == 1
        assert "brightness_pct" in first_calls[0].data
        assert "Unable to adapt light.kitchen" in caplog.text
        state = switch._target.to_dict()["entities"][KITCHEN_LIGHT]
        assert len(state["own_context_ids"]) == 1
        assert state["suppress_until"] > dt_util.utcnow().timestamp()
        calls = async_mock_service(hass, "light", "turn_on")
        await _tick(hass, entry)

    assert len(calls) == 2
    assert "color_temp_kelvin" in calls[-1].data


async def test_schema_rejection_for_one_member_does_not_stop_another(
    enable_custom_integrations, hass, hass_config_dir, caplog
) -> None:
    await _setup(
        hass,
        hass_config_dir,
        [_target_subentry(entities=[KITCHEN_LIGHT, HALL_LIGHT])],
        lights=(KITCHEN_LIGHT, HALL_LIGHT),
    )
    calls: list[ServiceCall] = []

    def accept_hall(data: dict) -> dict:
        if data["entity_id"] == KITCHEN_LIGHT:
            raise vol.Invalid("kitchen command rejected")
        return data

    async def handle(call: ServiceCall) -> None:
        calls.append(call)

    hass.services.async_register("light", "turn_on", handle, schema=accept_hall)
    with patch(
        "custom_components.daylight.switch.compute_turn_on_kwargs",
        return_value=dict(_STUB_KWARGS),
    ):
        await _turn_switch_on(hass)

    assert [call.data["entity_id"] for call in calls] == [HALL_LIGHT]
    assert "Unable to adapt light.kitchen" in caplog.text
    assert _switch_entity(hass)._target.to_dict()["entities"][KITCHEN_LIGHT][
        "own_context_ids"
    ] == []


async def test_no_feedback_bulb_is_retried_and_sibling_still_adapts(
    enable_custom_integrations, hass, hass_config_dir
) -> None:
    entry = await _setup(
        hass,
        hass_config_dir,
        [_target_subentry(entities=[KITCHEN_LIGHT, HALL_LIGHT])],
        lights=(KITCHEN_LIGHT, HALL_LIGHT),
    )
    # The service accepts commands but never changes either light state, as
    # happens with an unresponsive bulb or delayed transport acknowledgement.
    calls = async_mock_service(hass, "light", "turn_on")
    with patch(
        "custom_components.daylight.switch.compute_turn_on_kwargs",
        return_value=dict(_STUB_KWARGS),
    ):
        await _turn_switch_on(hass)
        await _tick(hass, entry)

    assert [call.data["entity_id"] for call in calls] == [
        KITCHEN_LIGHT,
        HALL_LIGHT,
        KITCHEN_LIGHT,
        HALL_LIGHT,
    ]
