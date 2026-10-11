"""Split-command timing, cancellation, and suppression tests."""

import asyncio
import time
from unittest.mock import patch

from homeassistant.core import Context
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_mock_service

from tests.switch_support import (
    _DAY_STATE,
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


async def test_separate_turn_on_commands_split_brightness_from_colour(
    enable_custom_integrations, hass, hass_config_dir
) -> None:
    entry = await _setup(
        hass,
        hass_config_dir,
        [
            _target_subentry(
                transition=120.0,
                separate_turn_on_commands=True,
                send_split_delay=0.05,
            )
        ],
        hub_data_overrides={"update_interval_seconds": 15},
    )
    calls = async_mock_service(hass, "light", "turn_on")

    await _turn_switch_on(hass)
    calls.clear()
    started = time.monotonic()
    await _tick(hass, entry)
    elapsed = time.monotonic() - started

    assert [call.data for call in calls] == [
        {"entity_id": KITCHEN_LIGHT, "brightness_pct": 70, "transition": 15.0},
        {"entity_id": KITCHEN_LIGHT, "color_temp_kelvin": 3400, "transition": 15.0},
    ]
    assert elapsed >= 0.05


async def test_switch_resume_splits_instant_brightness_and_colour_commands(
    enable_custom_integrations, hass, hass_config_dir
) -> None:
    entry = await _setup(
        hass,
        hass_config_dir,
        [_target_subentry(separate_turn_on_commands=True)],
    )
    calls = async_mock_service(hass, "light", "turn_on")

    with patch.object(
        entry.runtime_data, "compute_day_state", return_value=_DAY_STATE
    ):
        await _turn_switch_on(hass)

    assert [call.data for call in calls] == [
        {"entity_id": KITCHEN_LIGHT, "brightness_pct": 70, "transition": 0.0},
        {"entity_id": KITCHEN_LIGHT, "color_temp_kelvin": 3400, "transition": 0.0},
    ]


async def test_separate_turn_on_commands_send_one_call_without_colour(
    enable_custom_integrations, hass, hass_config_dir
) -> None:
    """A light with no colour support yields no second call, and no delay."""
    entry = await _setup(
        hass,
        hass_config_dir,
        [_target_subentry(separate_turn_on_commands=True, send_split_delay=5.0)],
    )
    calls = async_mock_service(hass, "light", "turn_on")

    with patch(
        "custom_components.daylight.switch.compute_turn_on_kwargs",
        return_value={"brightness_pct": 62, "transition": 4.0},
    ):
        # Inside the patch: resuming adapts too, and a real two-part command
        # would sleep for the full 5s split delay.
        await _turn_switch_on(hass)
        calls.clear()
        await _tick(hass, entry)

    assert [call.data for call in calls] == [
        {"entity_id": KITCHEN_LIGHT, "brightness_pct": 62, "transition": 4.0},
    ]


async def test_turning_switch_off_cancels_delayed_split_command(
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
        await hass.services.async_call(
            "switch", "turn_off", {"entity_id": KITCHEN_SWITCH}, blocking=True
        )
        await hass.async_block_till_done()

    assert len(calls) == 1


async def test_unloading_switch_cancels_delayed_split_command(
    enable_custom_integrations, hass, hass_config_dir
) -> None:
    entry = await _setup(
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
        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()

    assert len(calls) == 1


async def test_frequent_ticks_do_not_starve_the_second_split_command(
    enable_custom_integrations, hass, hass_config_dir
) -> None:
    entry = await _setup(
        hass,
        hass_config_dir,
        [_target_subentry(separate_turn_on_commands=True, send_split_delay=0.05)],
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
        for _ in range(8):
            entry.runtime_data.async_set_updated_data(_DAY_STATE)
            await asyncio.sleep(0.01)

        assert any("color_temp_kelvin" in call.data for call in calls)
        await hass.services.async_call(
            "switch", "turn_off", {"entity_id": KITCHEN_SWITCH}, blocking=True
        )
        await hass.async_block_till_done()


async def test_foreign_report_during_split_delay_is_suppressed(
    enable_custom_integrations, hass, hass_config_dir
) -> None:
    """A report inside the transition window cannot prove manual takeover."""
    await _setup(
        hass,
        hass_config_dir,
        [_target_subentry(separate_turn_on_commands=True, send_split_delay=0.01)],
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
        _set_light(hass, context=Context(), brightness=255)
        await hass.async_block_till_done()

    target = _switch_entity(hass)._target
    assert target.is_manual(KITCHEN_LIGHT, now=dt_util.utcnow().timestamp()) is False
    assert len(calls) == 2


async def test_user_change_during_split_delay_cancels_second_command(
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
        _set_light(hass, context=Context(user_id="manual-user"), brightness=255)
        await hass.async_block_till_done()

    target = _switch_entity(hass)._target
    assert target.is_manual(KITCHEN_LIGHT, now=dt_util.utcnow().timestamp())
    assert len(calls) == 1


async def test_the_suppression_window_covers_the_split_send_delay(
    enable_custom_integrations, hass, hass_config_dir
) -> None:
    """A two-part send extends suppression through the delayed color report."""
    entry = await _setup(
        hass,
        hass_config_dir,
        [_target_subentry(separate_turn_on_commands=True, send_split_delay=0.1)],
    )
    calls = async_mock_service(hass, "light", "turn_on")

    with patch(
        "custom_components.daylight.switch.compute_turn_on_kwargs",
        return_value=dict(_STUB_KWARGS),
    ):
        await _turn_switch_on(hass)
        calls.clear()
        started = dt_util.utcnow().timestamp()
        await _tick(hass, entry)
        finished = dt_util.utcnow().timestamp()
        assert len(calls) == 2

    target = _switch_entity(hass)._target
    until = target.to_dict()["entities"][KITCHEN_LIGHT]["suppress_until"]
    assert started + 6.1 <= until <= finished + 6.1


async def test_single_part_send_does_not_reserve_unused_split_delay(
    enable_custom_integrations, hass, hass_config_dir
) -> None:
    await _setup(
        hass,
        hass_config_dir,
        [_target_subentry(separate_turn_on_commands=True, send_split_delay=5.0)],
    )
    async_mock_service(hass, "light", "turn_on")

    with patch(
        "custom_components.daylight.switch.compute_turn_on_kwargs",
        return_value={"brightness_pct": 62, "transition": 4.0},
    ):
        started = dt_util.utcnow().timestamp()
        await _turn_switch_on(hass)
        finished = dt_util.utcnow().timestamp()

    target = _switch_entity(hass)._target
    until = target.to_dict()["entities"][KITCHEN_LIGHT]["suppress_until"]
    assert started + 6.0 <= until <= finished + 6.0


