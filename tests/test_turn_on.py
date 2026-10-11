"""Native LightEntity dispatch, not mocked light service registrations."""

import asyncio
import dataclasses
import datetime
import logging
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from homeassistant.components import light
from homeassistant.core import Context, ServiceCall
from homeassistant.exceptions import HomeAssistantError, Unauthorized
from homeassistant.helpers import area_registry, entity_registry, service
from homeassistant.helpers.entity_platform import EntityPlatform
from homeassistant.helpers.group import IntegrationSpecificGroup
from homeassistant.util import dt as dt_util

from custom_components.daylight.turn_on import (
    TurnOnCommand,
    TurnOnInterceptor,
    register,
)
from tests.switch_support import (
    _DAY_STATE,
    _STALE_DAY_STATE,
    _setup,
    _switch_entity,
    _target_subentry,
    _turn_switch_on,
)


class RecordingLight(light.LightEntity):
    """Capture native device commands and optionally report synchronously."""

    _attr_should_poll = False
    _attr_is_on = False
    _attr_brightness = 12
    _attr_color_temp_kelvin = 2700
    _attr_color_mode = light.ColorMode.COLOR_TEMP
    _attr_supported_features = (
        light.LightEntityFeature.TRANSITION
        | light.LightEntityFeature.FLASH
        | light.LightEntityFeature.EFFECT
    )

    def __init__(self, name):
        self._attr_supported_color_modes = {light.ColorMode.COLOR_TEMP}
        self._attr_effect_list = ["rainbow"]
        self._attr_name = name
        self._attr_unique_id = name
        self.entity_id = f"light.{name}"
        self.calls = []
        self.contexts = []
        self.off_calls = []
        self.feedback = True
        self.fail = False
        self.gate = None
        self.started = asyncio.Event()

    async def async_turn_on(self, **kwargs):
        self.calls.append(kwargs)
        self.contexts.append(self._context)
        self.started.set()
        if self.gate is not None:
            await self.gate.wait()
        if self.fail:
            raise HomeAssistantError("device failure")
        if self.feedback:
            self._attr_is_on = True
            self._attr_brightness = kwargs.get("brightness", self.brightness)
            self._attr_color_temp_kelvin = kwargs.get(
                "color_temp_kelvin", self.color_temp_kelvin
            )
            self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        self.off_calls.append(kwargs)
        self._attr_is_on = False
        self.async_write_ha_state()


class RecordingMulticastGroup(RecordingLight):
    """A group entity whose one native call reports all leaves contextlessly."""

    def __init__(self, name, members):
        super().__init__(name)
        self.members = members
        unique_ids = [member.unique_id for member in members]
        self.group = IntegrationSpecificGroup(self, unique_ids)
        zha_platform = object()
        physical_members = [
            SimpleNamespace(
                associated_entities=[
                    SimpleNamespace(
                        PLATFORM=zha_platform,
                        identifiers=SimpleNamespace(unique_id=unique_id),
                    )
                ]
            )
            for unique_id in unique_ids
        ]
        self.entity_data = SimpleNamespace(
            entity=SimpleNamespace(PLATFORM=zha_platform),
            group_proxy=SimpleNamespace(
                group=SimpleNamespace(members=physical_members)
            ),
        )

    async def async_turn_on(self, **kwargs):
        self.calls.append(kwargs)
        self.contexts.append(self._context)
        self.started.set()
        if self.gate is not None:
            await self.gate.wait()
        for member in self.members:
            member.async_set_context(Context())
            member._attr_is_on = True
            member._attr_brightness = kwargs.get("brightness", member.brightness)
            member._attr_color_temp_kelvin = kwargs.get(
                "color_temp_kelvin", member.color_temp_kelvin
            )
            member.async_write_ha_state()
        self._attr_is_on = True
        self.async_write_ha_state()
        if self.fail:
            raise HomeAssistantError("device failure")


@pytest.fixture(autouse=True)
def restore_hook(monkeypatch):
    monkeypatch.setattr(
        service, "_handle_single_entity_call", service._handle_single_entity_call
    )
    monkeypatch.setattr(
        service,
        "_resolve_entity_service_call_entities",
        service._resolve_entity_service_call_entities,
    )


async def setup_lights(hass, hass_config_dir, *, subentries=None, **settings):
    entry = await _setup(
        hass,
        hass_config_dir,
        subentries or [_target_subentry(**settings)],
        lights=(),
    )
    bulbs = [RecordingLight(name) for name in ("kitchen", "hall", "outside")]
    await hass.data[light.DATA_COMPONENT].async_add_entities(bulbs)
    await _turn_switch_on(hass)
    return entry, _switch_entity(hass), bulbs


async def setup_multicast_lights(
    hass, hass_config_dir, *, explicit_group_owner=False, **settings
):
    """Add two integration-specific ZHA groups and their six leaf entities."""
    leaves = [RecordingLight(f"group_leaf_{index}") for index in range(6)]
    groups = [
        RecordingMulticastGroup("zha_group_one", leaves[:3]),
        RecordingMulticastGroup("zha_group_two", leaves[3:]),
    ]
    subentries = [
        _target_subentry(entities=[leaf.entity_id for leaf in leaves], **settings)
    ]
    if explicit_group_owner:
        subentries.append(
            _target_subentry("Group", entities=[groups[0].entity_id])
        )
    entry = await _setup(
        hass,
        hass_config_dir,
        subentries,
        lights=(),
    )
    zha_platform = EntityPlatform(
        hass=hass,
        logger=logging.getLogger(__name__),
        domain="light",
        platform_name="zha",
        platform=None,
        scan_interval=datetime.timedelta(seconds=30),
        entity_namespace=None,
    )
    await zha_platform.async_add_entities([*leaves, *groups])
    await hass.async_block_till_done()
    await _turn_switch_on(hass)
    if explicit_group_owner:
        await _turn_switch_on(hass, "switch.group_adapt")
    return entry, _switch_entity(hass), groups, leaves


async def call(hass, service_name="turn_on", *, context=None, **params):
    await hass.services.async_call(
        "light",
        service_name,
        {"entity_id": "light.kitchen", **params},
        blocking=True,
        context=context,
    )
    await hass.async_block_till_done()


def manual(switch):
    return switch._target.is_manual("light.kitchen", now=dt_util.utcnow().timestamp())


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"brightness_pct": 3, "transition": 9},
        {"rgb_color": [255, 0, 0]},
        {"color_temp_kelvin": 2600},
        {"profile": "relax"},
    ],
)
async def test_first_command_is_fresh_and_no_reactive_duplicate(
    enable_custom_integrations, hass, hass_config_dir, params
):
    entry, switch, (bulb, _, _) = await setup_lights(hass, hass_config_dir)
    entry.runtime_data.async_set_updated_data(_STALE_DAY_STATE)
    context = Context()
    with patch.object(
        entry.runtime_data, "compute_day_state", return_value=_DAY_STATE
    ) as fresh:
        await call(hass, context=context, **params)
    fresh.assert_called_once()
    assert bulb.calls == [
        {"brightness": 178, "color_temp_kelvin": 3400, "transition": 0.0}
    ]
    assert bulb.contexts == [context]
    assert not manual(switch)
    assert not switch._target.is_own_context(bulb.entity_id, context.id)


@pytest.mark.parametrize("user_authored", [True, False])
async def test_user_context_reports_and_reused_context_edit(
    enable_custom_integrations, hass, hass_config_dir, hass_admin_user, user_authored
):
    _, switch, (bulb, _, _) = await setup_lights(hass, hass_config_dir)
    user_id = hass_admin_user.id if user_authored else None
    context = Context(user_id=user_id)
    await call(hass, context=context)
    bulb._attr_brightness = 177
    bulb.async_set_context(Context(parent_id=context.id, user_id=user_id))
    bulb.async_write_ha_state()
    await hass.async_block_till_done()
    assert not manual(switch)
    await call(hass, context=context, brightness=45)
    assert manual(switch)
    assert len(bulb.calls) == 2
    assert bulb.calls[-1] == {"brightness": 45}
    assert not switch._receipts


async def test_multi_target_isolation_and_toggle(
    enable_custom_integrations, hass, hass_config_dir
):
    entry, switch, bulbs = await setup_lights(
        hass,
        hass_config_dir,
        subentries=[
            _target_subentry(),
            _target_subentry(
                "Hall",
                entities=["light.hall"],
                min_brightness_pct=20,
                max_brightness_pct=20,
            ),
        ],
    )
    await _turn_switch_on(hass, "switch.hall_adapt")
    with patch.object(entry.runtime_data, "compute_day_state", return_value=_DAY_STATE):
        await call(hass, entity_id=[b.entity_id for b in bulbs], brightness=33)
    assert [b.calls[0]["brightness"] for b in bulbs] == [178, 51, 33]
    await call(hass, "toggle", transition=7)
    assert bulbs[0].off_calls == [{"transition": 7}]
    assert not switch._receipts
    with patch.object(entry.runtime_data, "compute_day_state", return_value=_DAY_STATE):
        await call(hass, "toggle", brightness=1)
    assert bulbs[0].calls[-1] == {
        "brightness": 178,
        "color_temp_kelvin": 3400,
        "transition": 0.0,
    }
    assert len(bulbs[0].calls) == 2


@pytest.mark.parametrize(
    "params",
    [
        {"brightness": 0},
        {"brightness_pct": 0},
        {"white": 0},
        {"brightness_step": 7},
        {"brightness_step_pct": 10},
        {"flash": "short"},
        {"effect": "rainbow"},
    ],
)
async def test_special_requests_are_not_intercepted(
    enable_custom_integrations, hass, hass_config_dir, params
):
    _, switch, (bulb, _, _) = await setup_lights(hass, hass_config_dir)
    bulb.feedback = False
    await call(hass, **params)
    assert not switch._receipts
    if next(iter(params)) in ("brightness", "brightness_pct", "white"):
        assert bulb.calls == []
        assert len(bulb.off_calls) == 1
    else:
        assert len(bulb.calls) == 1
        assert "color_temp_kelvin" not in bulb.calls[0]


async def test_rgb_keeps_caller_color(
    enable_custom_integrations, hass, hass_config_dir
):
    _, _, (bulb, _, _) = await setup_lights(hass, hass_config_dir)
    bulb._attr_supported_color_modes = {light.ColorMode.RGB}
    bulb._attr_color_mode = light.ColorMode.RGB
    bulb._attr_rgb_color = (0, 0, 255)
    bulb.async_write_ha_state()
    await hass.async_block_till_done()
    await call(hass, rgb_color=[255, 0, 0], brightness=1)
    assert bulb.calls[0]["rgb_color"] == (255, 0, 0)
    assert bulb.calls[0]["brightness"] != 1
    assert "color_temp_kelvin" not in bulb.calls[0]
    assert len(bulb.calls) == 1


@pytest.mark.parametrize("state", ["unknown", "unavailable", "on"])
async def test_non_off_states_pass_through(
    enable_custom_integrations, hass, hass_config_dir, state
):
    _, switch, (bulb, _, _) = await setup_lights(hass, hass_config_dir)
    old = hass.states.get(bulb.entity_id)
    # Avoid a synthetic entering-on reactive command in this eligibility test.
    switch._remove_member_listener()
    hass.states.async_set(bulb.entity_id, state, old.attributes)
    bulb.feedback = False
    await call(hass, brightness=33)
    assert bulb.calls == [{"brightness": 33}]
    assert not switch._receipts


async def test_failure_no_feedback_expiry_and_repeated_cycles(
    enable_custom_integrations, hass, hass_config_dir
):
    _, switch, (bulb, _, _) = await setup_lights(hass, hass_config_dir)
    switch._target.observe_state_change(bulb.entity_id, "manual", timestamp=1)
    bulb.fail = True
    with pytest.raises(HomeAssistantError, match="device failure"):
        await call(hass)
    assert len(bulb.calls) == 1
    assert not switch._receipts
    assert not switch._native_pending
    assert manual(switch)
    bulb.fail = False
    bulb.feedback = False
    await call(hass)
    assert manual(switch)
    # Run the scheduled expiry callback without wall-clock sleeps.
    receipt = switch._receipts[bulb.entity_id]
    receipt.expiry._run()
    assert not switch._receipts
    bulb.feedback = True
    for _ in range(2):
        await call(hass)
        assert not manual(switch)
        await call(hass, "turn_off")
        assert not switch._receipts
    assert len(bulb.calls) == 4


async def test_expired_context_and_foreign_user_report_are_manual(
    enable_custom_integrations, hass, hass_config_dir, hass_admin_user
):
    _, switch, (bulb, _, _) = await setup_lights(hass, hass_config_dir)
    context = Context()
    await call(hass, context=context)
    bulb.async_set_context(Context(user_id=hass_admin_user.id))
    bulb._attr_brightness = 1
    bulb.async_write_ha_state()
    await hass.async_block_till_done()
    assert manual(switch)
    await call(hass, "turn_off")
    await call(hass, context=context)
    switch._receipts[bulb.entity_id].expiry._run()
    bulb.async_set_context(context)
    bulb._attr_brightness = 2
    bulb.async_write_ha_state()
    await hass.async_block_till_done()
    assert manual(switch)


async def test_physical_and_reconnect_still_adapt(
    enable_custom_integrations, hass, hass_config_dir
):
    _, switch, (bulb, _, _) = await setup_lights(hass, hass_config_dir)
    bulb._attr_is_on = True
    bulb.async_write_ha_state()
    await hass.async_block_till_done()
    assert len(bulb.calls) == 1
    bulb._attr_available = False
    bulb.async_write_ha_state()
    await hass.async_block_till_done()
    bulb._attr_available = True
    bulb.async_write_ha_state()
    await hass.async_block_till_done()
    assert len(bulb.calls) == 2
    assert not manual(switch)


@pytest.mark.parametrize(
    "cancel", [None, "off", "disable", "manual", "remove", "unload"]
)
async def test_split_continuation_and_cancellation(
    enable_custom_integrations, hass, hass_config_dir, cancel
):
    entry, switch, (bulb, _, _) = await setup_lights(
        hass,
        hass_config_dir,
        separate_turn_on_commands=True,
        send_split_delay=60,
    )
    gate = asyncio.Event()
    real_sleep = asyncio.sleep

    async def delay(seconds):
        if seconds == 60:
            await gate.wait()
        else:
            await real_sleep(seconds)

    with patch("custom_components.daylight.switch.asyncio.sleep", new=delay):
        await hass.services.async_call(
            "light", "turn_on", {"entity_id": bulb.entity_id}, blocking=True
        )
        await real_sleep(0)
        task = switch._send_tasks[bulb.entity_id]
        assert len(bulb.calls) == 1
        assert "brightness" in bulb.calls[0]
        assert "color_temp_kelvin" not in bulb.calls[0]
        switch.coordinator.async_set_updated_data(_DAY_STATE)
        assert switch._send_tasks[bulb.entity_id] is task
        if cancel == "off":
            await hass.services.async_call(
                "light", "turn_off", {"entity_id": bulb.entity_id}, blocking=True
            )
        elif cancel == "disable":
            await switch.async_turn_off()
        elif cancel == "manual":
            await hass.services.async_call(
                "light",
                "turn_on",
                {"entity_id": bulb.entity_id, "brightness": 1},
                blocking=True,
            )
        elif cancel == "remove":
            switch._settings = dataclasses.replace(switch._settings, entities=())
            switch._refresh_members()
        elif cancel == "unload":
            assert await hass.config_entries.async_unload(entry.entry_id)
        gate.set()
        await hass.async_block_till_done()
    if cancel is None:
        assert len(bulb.calls) == 2
        assert set(bulb.calls[1]) == {"color_temp_kelvin", "transition"}
        assert not manual(switch)
    elif cancel == "manual":
        assert len(bulb.calls) == 2
        assert bulb.calls[1] == {"brightness": 1}
        assert manual(switch)
    else:
        assert len(bulb.calls) == 1
    assert not switch._send_tasks


@pytest.mark.parametrize("split_delay", [0, 60])
async def test_split_waits_for_late_on_report(
    enable_custom_integrations, hass, hass_config_dir, hass_admin_user, split_delay
):
    entry, switch, (bulb, _, _) = await setup_lights(
        hass,
        hass_config_dir,
        separate_turn_on_commands=True,
        send_split_delay=split_delay,
        adapt_only_on_state_change=True,
    )
    bulb.feedback = False
    delay_elapsed = asyncio.Event()
    real_sleep = asyncio.sleep

    async def delay(seconds):
        if seconds == split_delay:
            delay_elapsed.set()
            await real_sleep(0)
        else:
            await real_sleep(seconds)

    context = Context(user_id=hass_admin_user.id)
    with (
        patch.object(entry.runtime_data, "compute_day_state", return_value=_DAY_STATE),
        patch("custom_components.daylight.switch.asyncio.sleep", new=delay),
    ):
        await hass.services.async_call(
            "light", "turn_on", {"entity_id": bulb.entity_id},
            context=context, blocking=True,
        )
        await delay_elapsed.wait()
        await real_sleep(0)
        task = switch._send_tasks[bulb.entity_id]
        assert not task.done()
        assert bulb.calls == [{"brightness": 178, "transition": 0.0}]
        bulb.async_set_context(Context(parent_id=context.id, user_id=context.user_id))
        bulb._attr_is_on = True
        bulb.async_write_ha_state()
        await task
    await hass.async_block_till_done()
    # A later report must not schedule a duplicate temperature command.
    bulb._attr_brightness = 178
    bulb.async_write_ha_state()
    await hass.async_block_till_done()
    assert bulb.calls == [
        {"brightness": 178, "transition": 0.0},
        {"color_temp_kelvin": 3400, "transition": 0.0},
    ]
    assert not manual(switch)
    assert not switch._send_tasks


async def test_split_no_report_expiry_cancels_waiter(
    enable_custom_integrations, hass, hass_config_dir, freezer
):
    _, switch, (bulb, _, _) = await setup_lights(
        hass, hass_config_dir, separate_turn_on_commands=True,
        adapt_only_on_state_change=True,
    )
    bulb.feedback = False
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": bulb.entity_id}, blocking=True
    )
    await asyncio.sleep(0)
    task = switch._send_tasks[bulb.entity_id]
    receipt = switch._receipts[bulb.entity_id]
    await asyncio.sleep(0)
    assert not task.done()
    assert receipt.expires == dt_util.utcnow().timestamp() + 2
    assert receipt.expiry is not None
    freezer.tick(3)
    await asyncio.sleep(0)
    await hass.async_block_till_done()
    assert task.cancelling()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.cancelled()
    assert not switch._receipts
    assert not switch._send_tasks
    assert not switch._native_pending
    # Even a stale readiness notification cannot revive the expired sequence.
    receipt.entered_on.set()
    await hass.async_block_till_done()
    assert len(bulb.calls) == 1
    assert hass.states.get(bulb.entity_id).state == "off"


@pytest.mark.parametrize("cancel", ["off", "disable", "manual", "remove", "unload"])
async def test_split_cancellation_while_waiting_for_report(
    enable_custom_integrations, hass, hass_config_dir, hass_admin_user, cancel
):
    entry, switch, (bulb, _, _) = await setup_lights(
        hass, hass_config_dir, separate_turn_on_commands=True,
        adapt_only_on_state_change=True,
    )
    bulb.feedback = False
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": bulb.entity_id}, blocking=True
    )
    await asyncio.sleep(0)
    task = switch._send_tasks[bulb.entity_id]
    receipt = switch._receipts[bulb.entity_id]
    await asyncio.sleep(0)
    assert not task.done()
    if cancel == "off":
        await hass.services.async_call(
            "light", "turn_off", {"entity_id": bulb.entity_id}, blocking=True
        )
    elif cancel == "disable":
        await switch.async_turn_off()
    elif cancel == "manual":
        # Queue both reports before the waiter can send: readiness must not
        # override the subsequent genuine manual change.
        bulb._attr_is_on = True
        bulb.async_write_ha_state()
        bulb.async_set_context(Context(user_id=hass_admin_user.id))
        bulb._attr_brightness = 1
        bulb.async_write_ha_state()
    elif cancel == "remove":
        switch._settings = dataclasses.replace(switch._settings, entities=())
        switch._refresh_members()
    elif cancel == "unload":
        assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert task.cancelled()
    assert not switch._receipts
    assert not switch._send_tasks
    receipt.entered_on.set()
    await hass.async_block_till_done()
    assert len(bulb.calls) == 1


@pytest.mark.parametrize("fail", [False, True])
async def test_slow_native_dispatch_retains_receipt_until_completion(
    enable_custom_integrations, hass, hass_config_dir, freezer, fail
):
    entry, switch, (bulb, _, _) = await setup_lights(
        hass, hass_config_dir, separate_turn_on_commands=True,
        adapt_only_on_state_change=True,
    )
    bulb.gate = asyncio.Event()
    bulb.fail = fail
    with patch.object(entry.runtime_data, "compute_day_state", return_value=_DAY_STATE):
        task = hass.async_create_task(hass.services.async_call(
            "light", "turn_on", {"entity_id": bulb.entity_id}, blocking=True
        ))
        await bulb.started.wait()
        receipt = switch._receipts[bulb.entity_id]
        # Native execution outlasts the old (split delay + 2s) deadline.
        freezer.tick(3)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert switch._receipts.get(bulb.entity_id) is receipt
        assert receipt.expires is None
        assert receipt.expiry is None
        assert switch._native_pending == {bulb.entity_id: 1}
        assert not switch._send_tasks
        bulb.gate.set()
        if fail:
            with pytest.raises(HomeAssistantError, match="device failure"):
                await task
        else:
            await task
        await hass.async_block_till_done()
    assert not switch._native_pending
    assert not switch._send_tasks
    if fail:
        assert len(bulb.calls) == 1
        assert not switch._receipts
    else:
        assert receipt.expires == dt_util.utcnow().timestamp() + 2
        assert receipt.expiry is not None
        assert receipt.entered_on.is_set()
        assert bulb.calls == [
            {"brightness": 178, "transition": 0.0},
            {"color_temp_kelvin": 3400, "transition": 0.0},
        ]
        assert not manual(switch)
        receipt.expiry._run()
        assert not switch._receipts


@pytest.mark.parametrize("cancel", ["off", "disable", "remove", "unload", "supersede"])
@pytest.mark.parametrize("feedback", [False, True])
async def test_invalidated_slow_native_dispatch_does_not_recreate_receipt(
    enable_custom_integrations, hass, hass_config_dir, freezer, cancel, feedback
):
    entry, switch, (bulb, _, _) = await setup_lights(
        hass, hass_config_dir, separate_turn_on_commands=True,
        adapt_only_on_state_change=True,
    )
    native_gate = bulb.gate = asyncio.Event()
    bulb.feedback = feedback
    context = Context()
    task = hass.async_create_task(hass.services.async_call(
        "light", "turn_on", {"entity_id": bulb.entity_id},
        context=context, blocking=True,
    ))
    await bulb.started.wait()
    freezer.tick(3)
    if cancel == "off":
        await hass.services.async_call(
            "light", "turn_off", {"entity_id": bulb.entity_id}, blocking=True
        )
    elif cancel == "disable":
        await switch.async_turn_off()
    elif cancel == "remove":
        switch._settings = dataclasses.replace(switch._settings, entities=())
        switch._refresh_members()
    elif cancel == "unload":
        assert await hass.config_entries.async_unload(entry.entry_id)
    elif cancel == "supersede":
        # Reusing the caller context does not make a subsequent call our own.
        bulb.gate = None
        await hass.services.async_call(
            "light", "turn_on", {"entity_id": bulb.entity_id, "flash": "short"},
            context=context, blocking=True,
        )
    assert not switch._receipts
    assert not task.done()
    # The caller owns the native task even after Daylight abandons its receipt.
    # Its device handler captured the original gate before a superseding call.
    native_gate.set()
    await task
    await hass.async_block_till_done()
    assert not switch._receipts
    assert not switch._native_pending
    assert not switch._send_tasks
    assert len(bulb.calls) == (2 if cancel == "supersede" else 1)
    assert all("color_temp_kelvin" not in params for params in bulb.calls)
    # Any on-state here is from the caller's original command, not Daylight.
    assert hass.states.get(bulb.entity_id).state == ("on" if feedback else "off")


async def test_cancelled_native_dispatch_discards_receipt(
    enable_custom_integrations, hass, hass_config_dir
):
    _, switch, (bulb, _, _) = await setup_lights(
        hass, hass_config_dir, separate_turn_on_commands=True,
    )
    bulb.gate = asyncio.Event()
    task = hass.async_create_task(hass.services.async_call(
        "light", "turn_on", {"entity_id": bulb.entity_id}, blocking=True
    ))
    await bulb.started.wait()
    assert switch._receipts
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await hass.async_block_till_done()
    assert not switch._receipts
    assert not switch._native_pending
    assert not switch._send_tasks
    assert len(bulb.calls) == 1


async def test_disable_does_not_cancel_native_task(
    enable_custom_integrations, hass, hass_config_dir
):
    _, switch, (bulb, _, _) = await setup_lights(hass, hass_config_dir)
    bulb.gate = asyncio.Event()
    task = hass.async_create_task(
        hass.services.async_call(
            "light",
            "turn_on",
            {"entity_id": bulb.entity_id},
            blocking=True,
        )
    )
    for _ in range(10):
        await asyncio.sleep(0)
        if bulb.calls:
            break
    assert bulb.calls
    switch.coordinator.async_set_updated_data(_DAY_STATE)
    assert not switch._send_tasks
    await switch.async_turn_off()
    assert not task.cancelled()
    bulb.gate.set()
    await task
    assert len(bulb.calls) == 1
    assert not switch._receipts


async def test_overlap_warns_and_passes_original(
    enable_custom_integrations, hass, hass_config_dir, caplog
):
    _, _, (bulb, _, _) = await setup_lights(
        hass,
        hass_config_dir,
        subentries=[
            _target_subentry(),
            _target_subentry("Other"),
        ],
    )
    await _turn_switch_on(hass, "switch.other_adapt")
    bulb.feedback = False
    await call(hass, brightness=33)
    assert bulb.calls == [{"brightness": 33}]
    assert "Multiple enabled Daylight targets" in caplog.text


async def test_unload_reload_and_newer_wrapper_coexist(
    enable_custom_integrations, hass, hass_config_dir, monkeypatch
):
    predecessor = service._handle_single_entity_call
    resolver_predecessor = service._resolve_entity_service_call_entities
    entry, switch, (bulb, _, _) = await setup_lights(hass, hass_config_dir)
    wrapper = service._handle_single_entity_call
    assert service._resolve_entity_service_call_entities is not resolver_predecessor
    calls = []

    async def newer(hass, entity, func, data):
        calls.append(entity.entity_id)
        return await wrapper(hass, entity, func, data)

    monkeypatch.setattr(service, "_handle_single_entity_call", newer)
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert service._handle_single_entity_call is newer
    assert service._resolve_entity_service_call_entities is resolver_predecessor
    bulb.feedback = False
    await call(hass, brightness=33)
    assert bulb.calls == [{"brightness": 33}]
    assert not switch._receipts
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await _turn_switch_on(hass)
    await call(hass, brightness=1)
    assert bulb.calls[-1]["brightness"] != 1
    assert calls.count(bulb.entity_id) == 2
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert service._handle_single_entity_call is newer
    assert service._resolve_entity_service_call_entities is resolver_predecessor
    monkeypatch.setattr(service, "_handle_single_entity_call", predecessor)


@pytest.mark.parametrize(
    "hook", ["dispatch-missing", "dispatch", "dispatch-keyword", "resolver"]
)
async def test_unsupported_hook_fails_safely(hass, monkeypatch, caplog, hook):
    async def incompatible(a):
        return a

    async def keyword_only(*, hass, entity, func, data):
        return None

    dispatch = service._handle_single_entity_call
    resolver = service._resolve_entity_service_call_entities
    if hook == "dispatch-missing":
        monkeypatch.delattr(service, "_handle_single_entity_call")
    elif hook in ("dispatch", "dispatch-keyword"):
        monkeypatch.setattr(
            service,
            "_handle_single_entity_call",
            incompatible if hook == "dispatch" else keyword_only,
        )
    else:
        monkeypatch.setattr(
            service, "_resolve_entity_service_call_entities", incompatible
        )
    interceptor = TurnOnInterceptor(hass)
    assert "signature is unsupported" in caplog.text
    assert getattr(service, "_handle_single_entity_call", None) is (
        None
        if hook == "dispatch-missing"
        else incompatible
        if hook == "dispatch"
        else keyword_only
        if hook == "dispatch-keyword"
        else dispatch
    )
    assert service._resolve_entity_service_call_entities is (
        incompatible if hook == "resolver" else resolver
    )
    assert not interceptor.owners


@pytest.mark.parametrize("fail", [False, True])
async def test_adapter_clones_data_and_delegates_exactly_once(hass, monkeypatch, fail):
    seen = []

    async def predecessor(hass, entity, func, data):
        seen.append(data)
        if fail:
            raise HomeAssistantError("dispatch failed")
        return {"response": "preserved"}

    monkeypatch.setattr(service, "_handle_single_entity_call", predecessor)
    owner = Mock()
    finish = Mock()
    owner.prepare_turn_on.side_effect = lambda entity_id, call: TurnOnCommand(
        {**call.data["params"], "brightness": 178}, finish
    )
    interceptor = register(hass, owner, ("light.kitchen",))
    params = {"brightness": 3, "vendor": "kept"}
    original = ServiceCall(
        hass,
        "light",
        "turn_on",
        {"entity_id": ["light.kitchen"], "params": params},
        context=Context(),
        return_response=True,
    )
    if fail:
        with pytest.raises(HomeAssistantError, match="dispatch failed"):
            await interceptor.wrapper(hass, RecordingLight("kitchen"), None, original)
    else:
        assert await interceptor.wrapper(
            hass, RecordingLight("kitchen"), None, original
        ) == {"response": "preserved"}
    assert len(seen) == 1
    assert seen[0] is not original
    assert seen[0].context is original.context
    assert seen[0].return_response is True
    assert seen[0].data["params"] == {"brightness": 178, "vendor": "kept"}
    assert original.data["params"] == {"brightness": 3, "vendor": "kept"}
    finish.assert_called_once_with(not fail)
    interceptor.remove(owner)
    assert service._handle_single_entity_call is predecessor


async def test_changed_data_shape_warns_and_delegates_unchanged(
    hass, monkeypatch, caplog
):
    seen = []

    async def predecessor(hass, entity, func, data):
        seen.append(data)

    monkeypatch.setattr(service, "_handle_single_entity_call", predecessor)
    owner = Mock()
    interceptor = register(hass, owner, ("light.kitchen",))
    original = ServiceCall(hass, "light", "turn_on", {"brightness": 3})
    await interceptor.wrapper(hass, RecordingLight("kitchen"), None, original)
    assert seen == [original]
    owner.prepare_turn_on.assert_not_called()
    assert "data shape has changed" in caplog.text
    interceptor.remove(owner)


async def test_area_resolution_and_member_refresh(
    enable_custom_integrations, hass, hass_config_dir
):
    entry, switch, (bulb, hall, outside) = await setup_lights(hass, hass_config_dir)
    area = area_registry.async_get(hass).async_create("Room")
    registry = entity_registry.async_get(hass)
    for entity in (bulb, hall):
        registry.async_update_entity(entity.entity_id, area_id=area.id)
    switch._settings = dataclasses.replace(
        switch._settings, entities=(), areas=(area.id,)
    )
    switch._refresh_members()
    with patch.object(entry.runtime_data, "compute_day_state", return_value=_DAY_STATE):
        await hass.services.async_call(
            "light", "turn_on", {"area_id": area.id, "brightness": 1}, blocking=True
        )
        await hass.async_block_till_done()
    assert bulb.calls[0]["brightness"] == hall.calls[0]["brightness"] == 178
    assert outside.calls == []
    await call(hass, "turn_off", entity_id=hall.entity_id)
    registry.async_update_entity(hall.entity_id, area_id=None)
    switch._refresh_members()
    await call(hass, entity_id=hall.entity_id, brightness=1)
    assert hall.calls[-1] == {"brightness": 1}


@pytest.mark.parametrize("groups_first", [True, False])
async def test_zha_groups_keep_multicast_with_fresh_first_command(
    enable_custom_integrations, hass, hass_config_dir, groups_first
):
    entry, switch, groups, leaves = await setup_multicast_lights(
        hass, hass_config_dir
    )
    area = area_registry.async_get(hass).async_create("Multicast room")
    registry = entity_registry.async_get(hass)
    for entity in [*groups, *leaves]:
        registry.async_update_entity(entity.entity_id, area_id=area.id)
    await hass.async_block_till_done()
    selected = [*groups, *leaves] if groups_first else [*leaves, *groups]
    context = Context()

    with patch.object(
        entry.runtime_data, "compute_day_state", return_value=_DAY_STATE
    ) as fresh:
        await hass.services.async_call(
            "light",
            "turn_on",
            {"entity_id": [entity.entity_id for entity in selected], "brightness": 1},
            context=context,
            blocking=True,
        )
        await hass.async_block_till_done()

    fresh.assert_called_once()
    assert [group.calls for group in groups] == [
        [{"brightness": 178, "color_temp_kelvin": 3400, "transition": 0.0}],
        [{"brightness": 178, "color_temp_kelvin": 3400, "transition": 0.0}],
    ]
    assert all(group.contexts == [context] for group in groups)
    assert all(leaf.calls == [] for leaf in leaves)
    assert not any(
        switch._target.is_manual(leaf.entity_id, now=dt_util.utcnow().timestamp())
        for leaf in leaves
    )
    assert set(switch._receipts) == {leaf.entity_id for leaf in leaves}
    assert not switch._native_pending

    # Once the short receipts end, periodic adaptation remains leaf-based.
    for receipt in tuple(switch._receipts.values()):
        assert receipt.expiry is not None
        receipt.expiry._run()
    entry.runtime_data.async_set_updated_data(_DAY_STATE)
    await hass.async_block_till_done()
    assert [len(group.calls) for group in groups] == [1, 1]
    assert all(len(leaf.calls) == 1 for leaf in leaves)


async def test_zha_area_call_retains_groups_and_suppresses_covered_leaves(
    enable_custom_integrations, hass, hass_config_dir
):
    entry, _, groups, leaves = await setup_multicast_lights(hass, hass_config_dir)
    area = area_registry.async_get(hass).async_create("ZHA area")
    registry = entity_registry.async_get(hass)
    for entity in [*groups, *leaves]:
        registry.async_update_entity(entity.entity_id, area_id=area.id)
    await hass.async_block_till_done()

    with patch.object(
        entry.runtime_data, "compute_day_state", return_value=_DAY_STATE
    ) as fresh:
        await hass.services.async_call(
            "light",
            "turn_on",
            {"area_id": area.id},
            blocking=True,
        )
        await hass.async_block_till_done()

    fresh.assert_called_once()
    assert all(
        group.calls
        == [{"brightness": 178, "color_temp_kelvin": 3400, "transition": 0.0}]
        for group in groups
    )
    assert all(leaf.calls == [] for leaf in leaves)


@pytest.mark.parametrize("selection", ["group", "area"])
async def test_zha_off_historical_manual_flags_still_use_fresh_multicast(
    enable_custom_integrations, hass, hass_config_dir, selection
):
    entry, switch, groups, leaves = await setup_multicast_lights(
        hass, hass_config_dir
    )
    service_data = {"entity_id": groups[0].entity_id}
    if selection == "area":
        area = area_registry.async_get(hass).async_create("Historical manual")
        registry = entity_registry.async_get(hass)
        for entity in [groups[0], *leaves[:3]]:
            registry.async_update_entity(entity.entity_id, area_id=area.id)
        await hass.async_block_till_done()
        service_data = {"area_id": area.id}
    now = dt_util.utcnow().timestamp()
    for leaf in leaves[:3]:
        switch._target.observe_state_change(
            leaf.entity_id, f"old-{leaf.entity_id}", timestamp=now
        )
        assert switch._target.is_manual(leaf.entity_id, now=now)

    gate = groups[0].gate = asyncio.Event()
    with patch.object(
        entry.runtime_data, "compute_day_state", return_value=_DAY_STATE
    ):
        task = hass.async_create_task(
            hass.services.async_call(
                "light", "turn_on", service_data, blocking=True
            )
        )
        await groups[0].started.wait()
        assert all(
            switch._target.is_manual(
                leaf.entity_id, now=dt_util.utcnow().timestamp()
            )
            for leaf in leaves[:3]
        )
        gate.set()
        await task
        await hass.async_block_till_done()

    assert groups[0].calls == [
        {"brightness": 178, "color_temp_kelvin": 3400, "transition": 0.0}
    ]
    assert all(leaf.calls == [] for leaf in leaves)
    assert not any(
        switch._target.is_manual(
            leaf.entity_id, now=dt_util.utcnow().timestamp()
        )
        for leaf in leaves[:3]
    )


async def test_batched_resolver_caller_never_filters_selected_leaves(
    enable_custom_integrations, hass, hass_config_dir
):
    _, switch, groups, leaves = await setup_multicast_lights(hass, hass_config_dir)
    selected = [groups[0], *leaves[:3]]
    seen = []

    async def batched(entities, call):
        seen.extend(entities)

    call_data = ServiceCall(
        hass,
        "light",
        "turn_on",
        {
            "entity_id": [entity.entity_id for entity in selected],
            "params": {},
        },
    )
    await service.batched_entity_service_call(
        hass,
        hass.data[light.DATA_COMPONENT]._entities,
        batched,
        call_data,
    )

    assert {entity.entity_id for entity in seen} == {
        entity.entity_id for entity in selected
    }
    assert not switch._receipts
    assert not switch._interceptor._plans


async def test_zha_group_only_call_does_not_expand_authority(
    enable_custom_integrations, hass, hass_config_dir
):
    entry, switch, groups, leaves = await setup_multicast_lights(
        hass, hass_config_dir
    )
    with patch.object(
        entry.runtime_data, "compute_day_state", return_value=_DAY_STATE
    ) as fresh:
        await hass.services.async_call(
            "light",
            "turn_on",
            {"entity_id": groups[0].entity_id},
            blocking=True,
        )
        await hass.async_block_till_done()

    fresh.assert_called_once()
    assert groups[0].calls == [
        {"brightness": 178, "color_temp_kelvin": 3400, "transition": 0.0}
    ]
    assert groups[1].calls == []
    assert all(leaf.calls == [] for leaf in leaves)
    assert set(switch._receipts) == {leaf.entity_id for leaf in leaves[:3]}


@pytest.mark.parametrize("service_name", ["turn_off", "toggle"])
async def test_zha_group_off_services_invalidate_member_receipts(
    enable_custom_integrations, hass, hass_config_dir, service_name
):
    _, switch, groups, leaves = await setup_multicast_lights(hass, hass_config_dir)
    await hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": groups[0].entity_id},
        blocking=True,
    )
    assert set(switch._receipts) == {leaf.entity_id for leaf in leaves[:3]}
    if service_name == "toggle":

        async def toggle(**kwargs):
            groups[0].off_calls.append(kwargs)

        groups[0].async_toggle = toggle

    await hass.services.async_call(
        "light",
        service_name,
        {"entity_id": groups[0].entity_id},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert not switch._receipts
    assert groups[0].off_calls == [{}]


@pytest.mark.parametrize("service_name", ["turn_off", "toggle"])
async def test_zha_group_off_services_invalidate_queued_plan(
    enable_custom_integrations, hass, hass_config_dir, service_name
):
    _, switch, groups, _ = await setup_multicast_lights(hass, hass_config_dir)
    gate = asyncio.Event()
    first = True

    async def queued_request(coro):
        nonlocal first
        if first:
            first = False
            await gate.wait()
        return await coro

    groups[0].async_request_call = queued_request
    if service_name == "toggle":

        async def toggle(**kwargs):
            groups[0].off_calls.append(kwargs)

        groups[0].async_toggle = toggle
    task = hass.async_create_task(
        hass.services.async_call(
            "light",
            "turn_on",
            {"entity_id": groups[0].entity_id, "brightness": 1},
            blocking=True,
        )
    )
    for _ in range(10):
        await asyncio.sleep(0)
        if switch._interceptor._plans:
            break

    await hass.services.async_call(
        "light",
        service_name,
        {"entity_id": groups[0].entity_id},
        blocking=True,
    )
    gate.set()
    await task
    await hass.async_block_till_done()

    assert groups[0].off_calls == [{}]
    assert groups[0].calls == [{"brightness": 1}]
    assert not switch._receipts


async def test_zha_group_reused_context_gets_a_new_invocation_plan(
    enable_custom_integrations, hass, hass_config_dir
):
    _, switch, groups, leaves = await setup_multicast_lights(hass, hass_config_dir)
    context = Context()
    await hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": groups[0].entity_id},
        context=context,
        blocking=True,
    )
    first_receipts = dict(switch._receipts)
    for entity in [groups[0], *leaves[:3]]:
        entity._attr_is_on = False
        entity.async_set_context(Context())
        entity.async_write_ha_state()
    await hass.async_block_till_done()
    switch._target.clear_all_manual_flags()
    for leaf in leaves:
        leaf.calls.clear()

    await hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": groups[0].entity_id},
        context=context,
        blocking=True,
    )
    await hass.async_block_till_done()

    assert len(groups[0].calls) == 2
    assert all(leaf.calls == [] for leaf in leaves)
    assert set(switch._receipts) == {leaf.entity_id for leaf in leaves[:3]}
    assert all(
        switch._receipts[entity_id] is not receipt
        for entity_id, receipt in first_receipts.items()
    )


async def test_zha_group_failure_after_feedback_is_never_replayed_as_unicasts(
    enable_custom_integrations, hass, hass_config_dir
):
    _, switch, groups, leaves = await setup_multicast_lights(hass, hass_config_dir)
    groups[0].fail = True

    with pytest.raises(HomeAssistantError, match="device failure"):
        await hass.services.async_call(
            "light",
            "turn_on",
            {"entity_id": groups[0].entity_id},
            blocking=True,
        )
    await hass.async_block_till_done()

    assert len(groups[0].calls) == 1
    assert all(leaf.calls == [] for leaf in leaves)
    assert not switch._native_pending
    assert set(switch._receipts) == {leaf.entity_id for leaf in leaves[:3]}
    for receipt in tuple(switch._receipts.values()):
        assert receipt.expiry is not None
        receipt.expiry._run()
    assert not switch._receipts


async def test_zha_plan_waiting_for_platform_has_no_armed_receipts(
    enable_custom_integrations, hass, hass_config_dir
):
    _, switch, groups, _ = await setup_multicast_lights(hass, hass_config_dir)
    semaphore = asyncio.Semaphore(0)

    async def queued_request(coro):
        try:
            await semaphore.acquire()
        except asyncio.CancelledError:
            inner = coro.cr_frame.f_locals["coro"]
            inner.close()
            coro.close()
            raise
        return await coro

    groups[0].async_request_call = queued_request
    task = hass.async_create_task(
        hass.services.async_call(
            "light",
            "turn_on",
            {"entity_id": groups[0].entity_id},
            blocking=True,
        )
    )
    for _ in range(10):
        await asyncio.sleep(0)
        if switch._interceptor._plans:
            break
    assert switch._interceptor._plans
    assert not switch._receipts
    assert not switch._native_pending

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await hass.async_block_till_done()
    assert not switch._interceptor._plans
    assert not switch._receipts


async def test_zha_uncovered_same_call_leaf_does_not_invalidate_group_plan(
    enable_custom_integrations, hass, hass_config_dir
):
    _, _, groups, leaves = await setup_multicast_lights(hass, hass_config_dir)
    semaphore = groups[0].parallel_updates = asyncio.Semaphore(0)
    task = hass.async_create_task(
        hass.services.async_call(
            "light",
            "turn_on",
            {
                "entity_id": [groups[0].entity_id, leaves[3].entity_id],
                "brightness": 1,
            },
            blocking=True,
        )
    )
    for _ in range(10):
        await asyncio.sleep(0)
        if leaves[3].calls:
            break
    semaphore.release()
    await task
    await hass.async_block_till_done()

    assert groups[0].calls[0]["brightness"] != 1
    assert all(leaf.calls == [] for leaf in leaves[:3])
    assert len(leaves[3].calls) == 1


@pytest.mark.parametrize("changed", ["membership", "physical", "owner"])
async def test_zha_dispatch_revalidates_after_queued_changes(
    enable_custom_integrations, hass, hass_config_dir, changed
):
    _, switch, groups, leaves = await setup_multicast_lights(hass, hass_config_dir)
    semaphore = groups[0].parallel_updates = asyncio.Semaphore(0)
    task = hass.async_create_task(
        hass.services.async_call(
            "light",
            "turn_on",
            {"entity_id": groups[0].entity_id, "brightness": 1},
            blocking=True,
        )
    )
    for _ in range(10):
        await asyncio.sleep(0)
        if switch._interceptor._plans:
            break
    if changed == "membership":
        switch._settings = dataclasses.replace(
            switch._settings,
            entities=tuple(leaf.entity_id for leaf in leaves[1:]),
        )
        switch._refresh_members()
    elif changed == "physical":
        groups[0].entity_data.group_proxy.group.members.append(
            SimpleNamespace(associated_entities=[])
        )
    else:
        switch._interceptor.owners[Mock()] = (leaves[0].entity_id,)
    semaphore.release()
    await task
    await hass.async_block_till_done()

    assert groups[0].calls == [{"brightness": 1}]
    assert not switch._receipts


@pytest.mark.parametrize("stop", ["cancel", "unload"])
async def test_zha_group_pending_cleanup(
    enable_custom_integrations, hass, hass_config_dir, stop
):
    entry, switch, groups, leaves = await setup_multicast_lights(
        hass, hass_config_dir
    )
    gate = groups[0].gate = asyncio.Event()
    task = hass.async_create_task(
        hass.services.async_call(
            "light",
            "turn_on",
            {"entity_id": groups[0].entity_id},
            blocking=True,
        )
    )
    await groups[0].started.wait()
    plan = next(iter(switch._interceptor._plans.values()))
    assert plan.task is task
    assert plan.done_callback is not None
    assert set(switch._native_pending) == {leaf.entity_id for leaf in leaves[:3]}
    assert set(switch._receipts) == {leaf.entity_id for leaf in leaves[:3]}

    if stop == "cancel":
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        assert await hass.config_entries.async_unload(entry.entry_id)
        gate.set()
        await task
    await hass.async_block_till_done()

    assert plan.task is None
    assert plan.done_callback is None
    assert not switch._native_pending
    if stop == "cancel":
        assert set(switch._receipts) == {
            leaf.entity_id for leaf in leaves[:3]
        }
        for leaf in leaves[:3]:
            leaf.async_set_context(Context())
            leaf._attr_is_on = True
            leaf.async_write_ha_state()
        await hass.async_block_till_done()
        assert all(leaf.calls == [] for leaf in leaves)
        for receipt in tuple(switch._receipts.values()):
            receipt.expiry._run()
    assert not switch._receipts
    assert all(leaf.calls == [] for leaf in leaves)


@pytest.mark.parametrize(
    "unsafe",
    [
        "partial",
        "mixed",
        "manual_on",
        "unavailable",
        "capabilities",
        "physical",
        "overlap",
        "untrusted_overlap",
    ],
)
async def test_zha_unsafe_topologies_preserve_native_fallback(
    enable_custom_integrations, hass, hass_config_dir, unsafe
):
    _, switch, groups, leaves = await setup_multicast_lights(hass, hass_config_dir)
    selected_groups = groups[:1]
    if unsafe == "partial":
        switch._settings = dataclasses.replace(
            switch._settings,
            entities=tuple(leaf.entity_id for leaf in leaves if leaf is not leaves[0]),
        )
        switch._refresh_members()
    elif unsafe == "mixed":
        leaves[0]._attr_is_on = True
        leaves[0].async_write_ha_state()
        await hass.async_block_till_done()
    elif unsafe == "manual_on":
        leaves[0]._attr_is_on = True
        leaves[0].async_write_ha_state()
        await hass.async_block_till_done()
        switch._target.observe_state_change(
            leaves[0].entity_id,
            "manual",
            timestamp=dt_util.utcnow().timestamp(),
        )
    elif unsafe == "unavailable":
        leaves[0]._attr_available = False
        leaves[0].async_write_ha_state()
        await hass.async_block_till_done()
    elif unsafe == "capabilities":
        leaves[0]._attr_min_color_temp_kelvin = 2000
        leaves[0]._attr_max_color_temp_kelvin = 2500
        leaves[1]._attr_min_color_temp_kelvin = 4000
        leaves[1]._attr_max_color_temp_kelvin = 4500
        leaves[0].async_write_ha_state()
        leaves[1].async_write_ha_state()
        await hass.async_block_till_done()
    elif unsafe == "physical":
        groups[0].entity_data.group_proxy.group.members.append(
            SimpleNamespace(associated_entities=[])
        )
    else:
        overlap_ids = [
            leaves[0].unique_id,
            *[leaf.unique_id for leaf in leaves[3:]],
        ]
        groups[1].group.member_unique_ids = overlap_ids
        if unsafe == "overlap":
            platform = groups[1].entity_data.entity.PLATFORM
            groups[1].entity_data.group_proxy.group.members = [
                SimpleNamespace(
                    associated_entities=[
                        SimpleNamespace(
                            PLATFORM=platform,
                            identifiers=SimpleNamespace(unique_id=unique_id),
                        )
                    ]
                )
                for unique_id in overlap_ids
            ]
        groups[1].async_write_ha_state()
        await hass.async_block_till_done()
        selected_groups = groups

    await hass.services.async_call(
        "light",
        "turn_on",
        {
            "entity_id": [group.entity_id for group in selected_groups],
            "brightness": 1,
        },
        blocking=True,
    )
    await hass.async_block_till_done()

    assert all(group.calls == [{"brightness": 1}] for group in selected_groups)


async def test_selected_software_group_makes_zha_plan_fail_native(
    enable_custom_integrations, hass, hass_config_dir
):
    _, _, groups, _ = await setup_multicast_lights(hass, hass_config_dir)
    software = RecordingLight("software_group")
    software.group = IntegrationSpecificGroup(software, [])
    await hass.data[light.DATA_COMPONENT].async_add_entities([software])
    await hass.async_block_till_done()

    await hass.services.async_call(
        "light",
        "turn_on",
        {
            "entity_id": [groups[0].entity_id, software.entity_id],
            "brightness": 1,
        },
        blocking=True,
    )
    await hass.async_block_till_done()

    assert groups[0].calls == [{"brightness": 1}]


async def test_zha_final_kwargs_include_entity_default_profiles(
    enable_custom_integrations, hass, hass_config_dir
):
    _, _, groups, leaves = await setup_multicast_lights(hass, hass_config_dir)
    for entity in [groups[0], *leaves[:3]]:
        entity._attr_supported_color_modes = {light.ColorMode.RGB}
        entity._attr_color_mode = light.ColorMode.RGB
        entity.async_write_ha_state()
    hass.data[light.DATA_PROFILES].data[f"{groups[0].entity_id}.default"] = (
        light.Profile("group-default", 0.2, 0.3, None)
    )
    await hass.async_block_till_done()

    await hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": groups[0].entity_id, "brightness": 1},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert len(groups[0].calls) == 1
    assert groups[0].calls[0]["brightness"] == 1
    assert "rgb_color" in groups[0].calls[0]


async def test_zha_explicit_group_owner_keeps_existing_semantics(
    enable_custom_integrations, hass, hass_config_dir
):
    _, leaf_switch, groups, leaves = await setup_multicast_lights(
        hass, hass_config_dir, explicit_group_owner=True
    )
    group_switch = _switch_entity(hass, "switch.group_adapt")

    await hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": groups[0].entity_id, "brightness": 1},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert len(groups[0].calls) == 1
    assert groups[0].calls[0]["brightness"] != 1
    assert set(groups[0].calls[0]) == {
        "brightness",
        "color_temp_kelvin",
        "transition",
    }
    assert not leaf_switch._receipts
    assert set(group_switch._receipts) == {groups[0].entity_id}
    assert all(leaf.entity_id not in group_switch._receipts for leaf in leaves)


async def test_zha_split_setting_preserves_existing_group_fallback(
    enable_custom_integrations, hass, hass_config_dir
):
    leaves = [RecordingLight(f"split_group_leaf_{index}") for index in range(2)]
    group = RecordingMulticastGroup("split_zha_group", leaves)
    await _setup(
        hass,
        hass_config_dir,
        [
            _target_subentry(
                entities=[leaf.entity_id for leaf in leaves],
                separate_turn_on_commands=True,
            )
        ],
        lights=(),
    )
    zha_platform = EntityPlatform(
        hass=hass,
        logger=logging.getLogger(__name__),
        domain="light",
        platform_name="zha",
        platform=None,
        scan_interval=datetime.timedelta(seconds=30),
        entity_namespace=None,
    )
    await zha_platform.async_add_entities([*leaves, group])
    await hass.async_block_till_done()
    await _turn_switch_on(hass)

    await hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": group.entity_id, "brightness": 1},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert group.calls == [{"brightness": 1}]


async def test_group_remains_one_native_entity(
    enable_custom_integrations, hass, hass_config_dir
):
    _, _, (bulb, hall, _) = await setup_lights(hass, hass_config_dir)
    bulb._attr_extra_state_attributes = {"entity_id": [hall.entity_id]}
    bulb.async_write_ha_state()
    await call(hass)
    assert len(bulb.calls) == 1
    assert hall.calls == []
    assert not hall.is_on


async def test_disable_restores_hook_and_leaves_native_values(
    enable_custom_integrations, hass, hass_config_dir
):
    predecessor = service._handle_single_entity_call
    _, switch, (bulb, _, _) = await setup_lights(hass, hass_config_dir)
    assert service._handle_single_entity_call is not predecessor
    await switch.async_turn_off()
    assert service._handle_single_entity_call is predecessor
    await call(hass, brightness=3)
    assert bulb.calls == [{"brightness": 3}]


async def test_onoff_capability_has_no_receipt(
    enable_custom_integrations, hass, hass_config_dir
):
    _, switch, (bulb, _, _) = await setup_lights(hass, hass_config_dir)
    bulb._attr_supported_color_modes = {light.ColorMode.ONOFF}
    bulb._attr_color_mode = light.ColorMode.ONOFF
    bulb.async_write_ha_state()
    await call(hass)
    assert bulb.calls == [{}]
    assert not switch._receipts


async def test_context_free_reporting_grace_is_bounded(
    enable_custom_integrations, hass, hass_config_dir, freezer
):
    _, switch, (bulb, _, _) = await setup_lights(hass, hass_config_dir)
    await call(hass)
    bulb.async_set_context(Context())
    bulb._attr_brightness = 1
    bulb.async_write_ha_state()
    await hass.async_block_till_done()
    assert not manual(switch)
    freezer.tick(3)
    bulb.async_set_context(Context())
    bulb._attr_brightness = 2
    bulb.async_write_ha_state()
    await hass.async_block_till_done()
    assert manual(switch)


async def test_zha_restricted_user_can_use_authorized_group_without_leaf_access(
    enable_custom_integrations, hass, hass_config_dir, hass_read_only_user
):
    _, switch, groups, leaves = await setup_multicast_lights(hass, hass_config_dir)
    hass_read_only_user.mock_policy(
        {
            "entities": {
                "entity_ids": {groups[0].entity_id: {"control": True}}
            }
        }
    )

    await hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": groups[0].entity_id},
        context=Context(user_id=hass_read_only_user.id),
        blocking=True,
    )
    await hass.async_block_till_done()

    assert len(groups[0].calls) == 1
    assert all(leaf.calls == [] for leaf in leaves)
    assert set(switch._receipts) == {leaf.entity_id for leaf in leaves[:3]}


async def test_zha_group_permissions_remain_enforced_before_planning(
    enable_custom_integrations, hass, hass_config_dir, hass_read_only_user
):
    _, switch, groups, leaves = await setup_multicast_lights(hass, hass_config_dir)
    with pytest.raises(Unauthorized):
        await hass.services.async_call(
            "light",
            "turn_on",
            {"entity_id": groups[0].entity_id},
            context=Context(user_id=hass_read_only_user.id),
            blocking=True,
        )
    assert groups[0].calls == []
    assert all(leaf.calls == [] for leaf in leaves)
    assert not switch._receipts


async def test_ha_permissions_remain_enforced(
    enable_custom_integrations, hass, hass_config_dir, hass_read_only_user
):
    _, switch, (bulb, _, _) = await setup_lights(hass, hass_config_dir)
    with pytest.raises(Unauthorized):
        await call(hass, context=Context(user_id=hass_read_only_user.id))
    assert bulb.calls == []
    assert not switch._receipts
