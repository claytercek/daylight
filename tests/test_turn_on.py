"""Native LightEntity dispatch, not mocked light service registrations."""

import asyncio
import dataclasses
from unittest.mock import Mock, patch

import pytest
from homeassistant.components import light
from homeassistant.core import Context, ServiceCall
from homeassistant.exceptions import HomeAssistantError, Unauthorized
from homeassistant.helpers import area_registry, entity_registry, service
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


@pytest.fixture(autouse=True)
def restore_hook(monkeypatch):
    monkeypatch.setattr(
        service, "_handle_single_entity_call", service._handle_single_entity_call
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
    entry, switch, (bulb, _, _) = await setup_lights(hass, hass_config_dir)
    wrapper = service._handle_single_entity_call
    calls = []

    async def newer(hass, entity, func, data):
        calls.append(entity.entity_id)
        return await wrapper(hass, entity, func, data)

    monkeypatch.setattr(service, "_handle_single_entity_call", newer)
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert service._handle_single_entity_call is newer
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
    monkeypatch.setattr(service, "_handle_single_entity_call", predecessor)


@pytest.mark.parametrize("missing", [True, False])
async def test_unsupported_hook_fails_safely(hass, monkeypatch, caplog, missing):
    async def incompatible(a):
        return a

    if missing:
        monkeypatch.delattr(service, "_handle_single_entity_call")
    else:
        monkeypatch.setattr(service, "_handle_single_entity_call", incompatible)
    interceptor = TurnOnInterceptor(hass)
    assert "signature is unsupported" in caplog.text
    assert getattr(service, "_handle_single_entity_call", None) is (
        None if missing else incompatible
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


async def test_ha_permissions_remain_enforced(
    enable_custom_integrations, hass, hass_config_dir, hass_read_only_user
):
    _, switch, (bulb, _, _) = await setup_lights(hass, hass_config_dir)
    with pytest.raises(Unauthorized):
        await call(hass, context=Context(user_id=hass_read_only_user.id))
    assert bulb.calls == []
    assert not switch._receipts
