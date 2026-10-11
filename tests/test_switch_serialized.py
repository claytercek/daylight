"""Periodic native fades share one cancellable task and one selected entity."""

import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import Context, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_mock_service

from tests.switch_support import (
    _DAY_STATE,
    KITCHEN_LIGHT,
    _set_light,
    _setup,
    _switch_entity,
    _target_subentry,
    _turn_switch_on,
)


@pytest.fixture
async def serialized(hass, enable_custom_integrations, hass_config_dir):
    entry = await _setup(
        hass,
        hass_config_dir,
        [_target_subentry(serialized_native_fades=True, transition=120)],
        hub_data_overrides={"update_interval_seconds": 15},
    )
    calls = async_mock_service(hass, "light", "turn_on")
    await _turn_switch_on(hass)
    # Resume remains a combined snap even with the option enabled.
    assert len(calls) == 1
    assert calls[0].data["transition"] == 0
    assert {"brightness_pct", "color_temp_kelvin"} <= calls[0].data.keys()
    calls.clear()
    switch = _switch_entity(hass)
    waiting, release = asyncio.Event(), asyncio.Event()

    async def wait(_delay):
        waiting.set()
        await release.wait()
        await asyncio.sleep(0)  # Positive sleeps always yield, even if released early.

    # Mock only this module's clock, not HA's event-loop helpers.
    with patch("custom_components.daylight.switch.asyncio", wraps=asyncio) as clock:
        clock.sleep = AsyncMock(side_effect=wait)
        try:
            yield entry, switch, calls, waiting, release, clock.sleep
        finally:
            switch._cancel_pending_sends()
            await hass.async_block_till_done()
    assert not switch._send_tasks
    assert not switch._serialized_tasks


@pytest.mark.parametrize("legacy_split", [False, True])
async def test_serialized_order_total_cap_and_same_entity(
    hass, serialized, legacy_split
):
    entry, switch, calls, waiting, release, sleep = serialized
    switch._settings = replace(
        switch._settings,
        separate_turn_on_commands=legacy_split,
        send_split_delay=100,
    )
    started = dt_util.utcnow().timestamp()
    entry.runtime_data.async_set_updated_data(_DAY_STATE)
    await waiting.wait()
    assert [call.data for call in calls] == [
        {"entity_id": KITCHEN_LIGHT, "color_temp_kelvin": 3400, "transition": 7.5}
    ]
    sleep.assert_awaited_once_with(7.5)
    # Legacy split delay is not included in this mode's reporting budget.
    until = switch._target.to_dict()["entities"][KITCHEN_LIGHT]["suppress_until"]
    assert started + 17 <= until <= dt_util.utcnow().timestamp() + 17
    release.set()
    await hass.async_block_till_done()
    assert [call.data for call in calls] == [
        {"entity_id": KITCHEN_LIGHT, "color_temp_kelvin": 3400, "transition": 7.5},
        {"entity_id": KITCHEN_LIGHT, "brightness_pct": 70, "transition": 7.5},
    ]
    sleep.assert_awaited_once()  # No wait through phase two.
    assert not switch._send_tasks
    assert not switch._serialized_tasks


async def test_both_native_dispatches_are_blocking(hass, serialized):
    entry, switch, _calls, waiting, release, sleep = serialized
    entered, dispatched = asyncio.Event(), asyncio.Event()
    service_calls = []

    async def handler(call):
        service_calls.append(call)
        entered.set()
        await dispatched.wait()

    hass.services.async_register("light", "turn_on", handler)
    entry.runtime_data.async_set_updated_data(_DAY_STATE)
    await entered.wait()
    task = switch._send_tasks[KITCHEN_LIGHT]
    assert not waiting.is_set()  # Fade timer starts after CT dispatch completes.
    dispatched.set()
    await waiting.wait()
    entered.clear()
    dispatched.clear()
    release.set()
    await entered.wait()
    assert not task.done()  # Brightness dispatch also awaits the native handler.
    dispatched.set()
    await hass.async_block_till_done()
    assert len(service_calls) == 2
    sleep.assert_awaited_once_with(7.5)


async def test_new_periodic_update_replaces_only_serialized_task(hass, serialized):
    entry, switch, calls, waiting, release, _sleep = serialized
    entry.runtime_data.async_set_updated_data(_DAY_STATE)
    await waiting.wait()
    old = switch._send_tasks[KITCHEN_LIGHT]
    # Snaps preserve finish-old-first behavior.
    switch._async_adapt(KITCHEN_LIGHT, _DAY_STATE, snap=True)
    assert switch._send_tasks[KITCHEN_LIGHT] is old
    waiting.clear()
    entry.runtime_data.async_set_updated_data(
        replace(_DAY_STATE, brightness_factor=0.25)
    )
    new = switch._send_tasks[KITCHEN_LIGHT]
    assert new is not old
    with pytest.raises(asyncio.CancelledError):
        await old
    await waiting.wait()
    assert switch._send_tasks[KITCHEN_LIGHT] is new
    assert all("brightness_pct" not in call.data for call in calls)
    release.set()
    await hass.async_block_till_done()
    assert [call.data.get("brightness_pct") for call in calls] == [None, None, 30]


@pytest.mark.parametrize("reason", ["manual", "off", "no_command"])
async def test_superseding_tick_cancels_before_early_return(hass, serialized, reason):
    entry, switch, calls, waiting, release, _sleep = serialized
    entry.runtime_data.async_set_updated_data(_DAY_STATE)
    await waiting.wait()
    old = switch._send_tasks[KITCHEN_LIGHT]
    if reason == "manual":
        switch._target.observe_state_change(
            KITCHEN_LIGHT, "manual", user_id="user", timestamp=0
        )
    elif reason == "off":
        _set_light(hass, state="off")
    with patch.object(switch, "_adapt_kwargs", return_value={}):
        entry.runtime_data.async_set_updated_data(_DAY_STATE)
    assert KITCHEN_LIGHT not in switch._send_tasks
    with pytest.raises(asyncio.CancelledError):
        await old
    release.set()
    await hass.async_block_till_done()
    assert len(calls) == 1


@pytest.mark.parametrize("reason", ["off", "disable", "unavailable", "external"])
async def test_existing_cancellation_stops_phase_two(hass, serialized, reason):
    entry, switch, calls, waiting, release, _sleep = serialized
    entry.runtime_data.async_set_updated_data(_DAY_STATE)
    await waiting.wait()
    task = switch._send_tasks[KITCHEN_LIGHT]
    if reason == "disable":
        await switch.async_turn_off()
    elif reason == "external":
        switch.observe_light_call(
            KITCHEN_LIGHT,
            ServiceCall("light", "turn_off", {"entity_id": KITCHEN_LIGHT}),
        )
    else:
        _set_light(hass, state=reason, context=Context(user_id="manual-user"))
    await hass.async_block_till_done()
    assert task.cancelled()
    release.set()
    assert len(calls) == 1


async def test_live_off_check_prevents_relighting_even_without_cancellation(
    hass, serialized
):
    entry, switch, calls, waiting, release, _sleep = serialized
    entry.runtime_data.async_set_updated_data(_DAY_STATE)
    await waiting.wait()
    # Isolate the final _can_send check from the state listener's cancellation.
    with patch.object(switch, "_cancel_send"):
        _set_light(hass, state="off")
        release.set()
        await hass.async_block_till_done()
    assert len(calls) == 1


@pytest.mark.parametrize("failing_phase", [1, 2])
async def test_native_failure_stops_and_cleans_sequence(
    hass, serialized, failing_phase
):
    entry, switch, _calls, _waiting, release, sleep = serialized
    dispatched = []

    async def handler(call):
        dispatched.append(call)
        if len(dispatched) == failing_phase:
            raise HomeAssistantError("native dispatch failed")

    hass.services.async_register("light", "turn_on", handler)
    release.set()
    entry.runtime_data.async_set_updated_data(_DAY_STATE)
    await hass.async_block_till_done()
    assert len(dispatched) == failing_phase
    assert sleep.await_count == failing_phase - 1
    assert not switch._send_tasks
    assert not switch._serialized_tasks


@pytest.mark.parametrize("mode", ["missing", "zero", "single"])
async def test_nonqualifying_periodic_updates_are_unchanged(hass, serialized, mode):
    entry, switch, calls, _waiting, _release, sleep = serialized
    if mode == "missing":
        switch._settings = type(switch._settings).from_subentry_data(
            _target_subentry()["data"]
        )
        assert switch._settings.serialized_native_fades is False
    elif mode == "zero":
        switch._settings = replace(switch._settings, transition=0)
    else:
        _set_light(hass, color_modes=("brightness",))
        await hass.async_block_till_done()
    entry.runtime_data.async_set_updated_data(_DAY_STATE)
    await hass.async_block_till_done()
    assert len(calls) == 1
    assert "brightness_pct" in calls[0].data
    assert ("color_temp_kelvin" in calls[0].data) is (mode != "single")
    sleep.assert_not_awaited()
