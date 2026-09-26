"""Area targets resolve to independently adapted light entities."""

import datetime
from unittest.mock import patch

from homeassistant.core import Context
from homeassistant.helpers import area_registry, device_registry, entity_registry
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_mock_service

from tests.switch_support import (
    _STUB_KWARGS,
    HALL_LIGHT,
    KITCHEN_LIGHT,
    _set_light,
    _setup,
    _switch_entity,
    _target_subentry,
    _tick,
    _turn_switch_on,
)


def _register_light(hass, entity_id, *, area_id=None, device_id=None):
    registry = entity_registry.async_get(hass)
    entry = registry.async_get_or_create(
        "light", "test", entity_id, suggested_object_id=entity_id.split(".")[1],
        device_id=device_id,
    )
    if area_id is not None:
        registry.async_update_entity(entry.entity_id, area_id=area_id)
    assert entry.entity_id == entity_id


async def test_area_adapts_each_light_without_turning_off_members_on(
    enable_custom_integrations, hass, hass_config_dir
) -> None:
    area = area_registry.async_get(hass).async_create("Kitchen")
    _register_light(hass, KITCHEN_LIGHT, area_id=area.id)
    _register_light(hass, HALL_LIGHT, area_id=area.id)
    entry = await _setup(
        hass, hass_config_dir, [_target_subentry(entities=[], areas=[area.id])],
        lights=(KITCHEN_LIGHT,),
    )
    _set_light(hass, HALL_LIGHT, state="off")
    calls = async_mock_service(hass, "light", "turn_on")

    await _turn_switch_on(hass)
    assert [call.data["entity_id"] for call in calls] == [KITCHEN_LIGHT]

    calls.clear()
    _set_light(hass, HALL_LIGHT, state="on", context=Context())
    await hass.async_block_till_done()
    assert [call.data["entity_id"] for call in calls] == [HALL_LIGHT]

    calls.clear()
    await _tick(hass, entry)
    assert {call.data["entity_id"] for call in calls} == {KITCHEN_LIGHT, HALL_LIGHT}
    assert len(calls) == 2


async def test_area_member_manual_override_and_failure_do_not_block_siblings(
    enable_custom_integrations, hass, hass_config_dir, freezer
) -> None:
    area = area_registry.async_get(hass).async_create("Kitchen")
    for entity_id in (KITCHEN_LIGHT, HALL_LIGHT):
        _register_light(hass, entity_id, area_id=area.id)
    entry = await _setup(
        hass, hass_config_dir, [_target_subentry(entities=[], areas=[area.id])],
        lights=(KITCHEN_LIGHT, HALL_LIGHT),
    )
    calls = []

    async def handle(call):
        calls.append(call.data["entity_id"])
        if call.data["entity_id"] == KITCHEN_LIGHT:
            raise RuntimeError("kitchen light unreachable")

    hass.services.async_register("light", "turn_on", handle)
    with patch(
        "custom_components.daylight.switch.compute_turn_on_kwargs",
        return_value=dict(_STUB_KWARGS),
    ):
        await _turn_switch_on(hass)
        assert set(calls) == {KITCHEN_LIGHT, HALL_LIGHT}
        calls.clear()
        await _tick(hass, entry)
        assert set(calls) == {KITCHEN_LIGHT, HALL_LIGHT}

        calls.clear()
        freezer.tick(datetime.timedelta(seconds=60))
        _set_light(hass, HALL_LIGHT, context=Context(), brightness=255)
        await hass.async_block_till_done()
        assert _switch_entity(hass)._target.is_manual(
            HALL_LIGHT, now=dt_util.utcnow().timestamp()
        )
        await _tick(hass, entry)

    assert calls == [KITCHEN_LIGHT]


async def test_area_members_follow_registry_changes_and_deduplicate_explicit_lights(
    enable_custom_integrations, hass, hass_config_dir
) -> None:
    kitchen = area_registry.async_get(hass).async_create("Kitchen")
    hall = area_registry.async_get(hass).async_create("Hall")
    _register_light(hass, KITCHEN_LIGHT, area_id=kitchen.id)
    _register_light(hass, HALL_LIGHT, area_id=hall.id)
    entry = await _setup(
        hass, hass_config_dir,
        [_target_subentry(entities=[KITCHEN_LIGHT], areas=[kitchen.id])],
        lights=(KITCHEN_LIGHT, HALL_LIGHT),
    )
    calls = async_mock_service(hass, "light", "turn_on")
    await _turn_switch_on(hass)
    assert [call.data["entity_id"] for call in calls] == [KITCHEN_LIGHT]

    calls.clear()
    entity_registry.async_get(hass).async_update_entity(HALL_LIGHT, area_id=kitchen.id)
    await hass.async_block_till_done()
    assert [call.data["entity_id"] for call in calls] == [HALL_LIGHT]

    calls.clear()
    entity_registry.async_get(hass).async_update_entity(HALL_LIGHT, area_id=hall.id)
    await hass.async_block_till_done()
    await _tick(hass, entry)
    assert [call.data["entity_id"] for call in calls] == [KITCHEN_LIGHT]


async def test_device_area_is_inherited_by_its_light(
    enable_custom_integrations, hass, hass_config_dir
) -> None:
    area = area_registry.async_get(hass).async_create("Kitchen")
    entry = await _setup(
        hass, hass_config_dir, [_target_subentry(entities=[], areas=[area.id])],
    )
    device = device_registry.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={("test", "lamp")},
    )
    device_registry.async_get(hass).async_update_device(device.id, area_id=area.id)
    _register_light(hass, HALL_LIGHT, device_id=device.id)
    _set_light(hass, HALL_LIGHT)
    await hass.async_block_till_done()
    calls = async_mock_service(hass, "light", "turn_on")

    await _turn_switch_on(hass)

    assert [call.data["entity_id"] for call in calls] == [HALL_LIGHT]
