"""Adaptation switch behavior and manual-control integration tests."""

import datetime
from unittest.mock import patch

import pytest
from homeassistant.core import Context, State
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    async_mock_service,
    mock_restore_cache,
)

from tests.switch_support import (
    _DAY_STATE,
    _STALE_DAY_STATE,
    _STUB_KWARGS,
    HALL_LIGHT,
    KITCHEN_LIGHT,
    KITCHEN_SWITCH,
    _set_light,
    _setup,
    _switch_entity,
    _target_subentry,
    _tick,
    _turn_switch_on,
)


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
    # Resuming adapts every on member itself; this test is about the tick.
    calls.clear()

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
        device_min_color_temp_kelvin=None,
        device_max_color_temp_kelvin=None,
    )
    assert len(calls) == 1
    assert calls[0].data == {"entity_id": KITCHEN_LIGHT, **_STUB_KWARGS}


async def test_onoff_only_light_receives_no_adaptation_command(
    enable_custom_integrations, hass, hass_config_dir
) -> None:
    entry = await _setup(hass, hass_config_dir, [_target_subentry()])
    calls = async_mock_service(hass, "light", "turn_on")
    _set_light(hass, color_modes=("onoff",))
    await hass.async_block_till_done()

    await _turn_switch_on(hass)
    await _tick(hass, entry)

    assert calls == []


async def test_device_color_temperature_bounds_reach_light_command(
    enable_custom_integrations, hass, hass_config_dir
) -> None:
    entry = await _setup(hass, hass_config_dir, [_target_subentry()])
    calls = async_mock_service(hass, "light", "turn_on")
    await _turn_switch_on(hass)
    calls.clear()
    hass.states.async_set(
        KITCHEN_LIGHT,
        "on",
        {
            "supported_color_modes": ["color_temp"],
            "brightness": 128,
            "min_color_temp_kelvin": 4000,
            "max_color_temp_kelvin": 4500,
        },
        context=Context(),
    )
    await hass.async_block_till_done()

    await _tick(hass, entry)

    assert len(calls) == 1
    assert calls[0].data["color_temp_kelvin"] == 4000


async def test_group_target_receives_one_command_as_one_light(
    enable_custom_integrations, hass, hass_config_dir
) -> None:
    group = "light.living_room_group"
    await _setup(
        hass,
        hass_config_dir,
        [_target_subentry("Living Room", entities=[group])],
        lights=(group,),
    )
    calls = async_mock_service(hass, "light", "turn_on")

    await _turn_switch_on(hass, "switch.living_room_adapt")

    assert len(calls) == 1
    assert calls[0].data["entity_id"] == group


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
    # Resuming adapts every on member itself; this test is about the tick.
    calls.clear()

    with patch(
        "custom_components.daylight.switch.compute_turn_on_kwargs",
        return_value=dict(_STUB_KWARGS),
    ):
        await _tick(hass, entry)

    assert calls == []


async def test_foreign_state_change_marks_a_member_manual_and_skips_it(
    enable_custom_integrations, hass, hass_config_dir, freezer
) -> None:
    entry = await _setup(hass, hass_config_dir, [_target_subentry()])
    calls = async_mock_service(hass, "light", "turn_on")
    await _turn_switch_on(hass)
    calls.clear()

    # Well past the resume command's own suppression window, so only the
    # foreign context can be what flags this.
    freezer.tick(datetime.timedelta(seconds=60))
    # Somebody grabs the dimmer: a state change under a context this
    # integration never issued, while the light is already on.
    _set_light(hass, context=Context(), brightness=255)
    await hass.async_block_till_done()

    # Asserted explicitly, so the empty call list below is pinned to the
    # manual flag rather than to anything else that could suppress a call.
    target = _switch_entity(hass)._target
    assert target.is_manual(KITCHEN_LIGHT, now=dt_util.utcnow().timestamp()) is True

    with patch(
        "custom_components.daylight.switch.compute_turn_on_kwargs",
        return_value=dict(_STUB_KWARGS),
    ):
        await _tick(hass, entry)

    assert calls == []


async def test_metadata_only_report_does_not_mark_member_manual(
    enable_custom_integrations, hass, hass_config_dir, freezer
) -> None:
    await _setup(hass, hass_config_dir, [_target_subentry()])
    async_mock_service(hass, "light", "turn_on")
    await _turn_switch_on(hass)

    freezer.tick(datetime.timedelta(seconds=60))
    hass.states.async_set(
        KITCHEN_LIGHT,
        "on",
        {
            "supported_color_modes": ["color_temp"],
            "brightness": 128,
            "friendly_name": "Kitchen light",
        },
        context=Context(),
    )
    await hass.async_block_till_done()

    target = _switch_entity(hass)._target
    assert target.is_manual(KITCHEN_LIGHT, now=dt_util.utcnow().timestamp()) is False


@pytest.mark.parametrize(
    ("attribute", "value"),
    [("color_temp_kelvin", 3200), ("effect", "rainbow")],
)
async def test_color_and_effect_reports_mark_member_manual_after_grace(
    enable_custom_integrations,
    hass,
    hass_config_dir,
    freezer,
    attribute,
    value,
) -> None:
    await _setup(hass, hass_config_dir, [_target_subentry()])
    async_mock_service(hass, "light", "turn_on")
    await _turn_switch_on(hass)

    freezer.tick(datetime.timedelta(seconds=60))
    hass.states.async_set(
        KITCHEN_LIGHT,
        "on",
        {"supported_color_modes": ["color_temp"], "brightness": 128, attribute: value},
        context=Context(),
    )
    await hass.async_block_till_done()

    target = _switch_entity(hass)._target
    assert target.is_manual(KITCHEN_LIGHT, now=dt_util.utcnow().timestamp()) is True


async def test_off_light_attribute_report_does_not_mark_member_manual(
    enable_custom_integrations, hass, hass_config_dir
) -> None:
    await _setup_with_light_off(hass, hass_config_dir)
    _set_light(hass, state="off", context=Context(), brightness=255)
    await hass.async_block_till_done()

    target = _switch_entity(hass)._target
    assert target.is_manual(KITCHEN_LIGHT, now=dt_util.utcnow().timestamp()) is False


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
    calls.clear()

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
    calls.clear()

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


async def test_member_turning_on_snaps_to_current_day_values(
    enable_custom_integrations, hass, hass_config_dir
) -> None:
    entry = await _setup_with_light_off(hass, hass_config_dir)
    calls = async_mock_service(hass, "light", "turn_on")
    await _turn_switch_on(hass)

    with patch.object(
        entry.runtime_data, "compute_day_state", return_value=_DAY_STATE
    ):
        _set_light(hass, state="on", context=Context(), brightness=12)
        await hass.async_block_till_done()

    assert [call.data for call in calls] == [
        {
            "entity_id": KITCHEN_LIGHT,
            "brightness_pct": 70,
            "color_temp_kelvin": 3400,
            "transition": 0.0,
        }
    ]


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


async def test_turning_the_switch_on_forgives_flags_set_while_it_was_off(
    enable_custom_integrations, hass, hass_config_dir
) -> None:
    """Fix 6: the movie-night scene that ran while adaptation was paused."""
    await _setup(hass, hass_config_dir, [_target_subentry()])
    calls = async_mock_service(hass, "light", "turn_on")

    _set_light(hass, context=Context(), brightness=255)
    await hass.async_block_till_done()

    target = _switch_entity(hass)._target
    assert target.is_manual(KITCHEN_LIGHT, now=dt_util.utcnow().timestamp()) is True

    with patch(
        "custom_components.daylight.switch.compute_turn_on_kwargs",
        return_value=dict(_STUB_KWARGS),
    ):
        await _turn_switch_on(hass)

    # Forgiven, and put right by the resume pass itself: the scene's values
    # are gone before any tick gets a chance to run.
    assert target.is_manual(KITCHEN_LIGHT, now=dt_util.utcnow().timestamp()) is False
    assert [call.data for call in calls] == [
        {"entity_id": KITCHEN_LIGHT, **_STUB_KWARGS},
    ]


async def test_turning_the_switch_on_adapts_every_on_member_at_once(
    enable_custom_integrations, hass, hass_config_dir
) -> None:
    """Fix: resuming must itself correct the members it just forgave.

    With `adapt_only_on_state_change` the periodic tick never adapts, so this
    one-shot pass is the *only* thing that can undo a movie-night scene --
    without it the member stays dim indefinitely.
    """
    await _setup(
        hass,
        hass_config_dir,
        [_target_subentry(adapt_only_on_state_change=True)],
    )
    calls = async_mock_service(hass, "light", "turn_on")

    _set_light(hass, context=Context(), brightness=12)
    await hass.async_block_till_done()
    assert calls == []

    with patch(
        "custom_components.daylight.switch.compute_turn_on_kwargs",
        return_value=dict(_STUB_KWARGS),
    ):
        await _turn_switch_on(hass)

    assert [call.data for call in calls] == [
        {"entity_id": KITCHEN_LIGHT, **_STUB_KWARGS},
    ]


async def test_switch_off_on_snaps_members_to_current_day_values(
    enable_custom_integrations, hass, hass_config_dir
) -> None:
    entry = await _setup(hass, hass_config_dir, [_target_subentry()])
    calls = async_mock_service(hass, "light", "turn_on")
    await _turn_switch_on(hass)
    calls.clear()

    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": KITCHEN_SWITCH}, blocking=True
    )
    with patch.object(
        entry.runtime_data, "compute_day_state", return_value=_DAY_STATE
    ):
        await _turn_switch_on(hass)

    assert [call.data for call in calls] == [
        {
            "entity_id": KITCHEN_LIGHT,
            "brightness_pct": 70,
            "color_temp_kelvin": 3400,
            "transition": 0.0,
        }
    ]


async def test_turning_the_switch_on_uses_a_freshly_computed_day_state(
    enable_custom_integrations, hass, hass_config_dir
) -> None:
    """`coordinator.data` can be most of an interval old by the resume."""
    entry = await _setup(hass, hass_config_dir, [_target_subentry()])
    async_mock_service(hass, "light", "turn_on")
    await _tick(hass, entry, _STALE_DAY_STATE)

    with (
        patch(
            "custom_components.daylight.switch.compute_turn_on_kwargs",
            return_value=dict(_STUB_KWARGS),
        ) as compute,
        patch.object(
            entry.runtime_data, "compute_day_state", return_value=_DAY_STATE
        ),
    ):
        await _turn_switch_on(hass)

    assert compute.call_args.kwargs["brightness_factor"] == 0.75
    assert compute.call_args.kwargs["color_factor"] == 0.4


async def test_turning_an_already_on_switch_on_keeps_manual_flags(
    enable_custom_integrations, hass, hass_config_dir, freezer
) -> None:
    """Only the off->on transition forgives; a redundant turn_on must not."""
    await _setup(hass, hass_config_dir, [_target_subentry()])
    async_mock_service(hass, "light", "turn_on")
    await _turn_switch_on(hass)

    freezer.tick(datetime.timedelta(seconds=60))
    _set_light(hass, context=Context(), brightness=255)
    await hass.async_block_till_done()

    target = _switch_entity(hass)._target
    assert target.is_manual(KITCHEN_LIGHT, now=dt_util.utcnow().timestamp()) is True

    await _turn_switch_on(hass)

    assert target.is_manual(KITCHEN_LIGHT, now=dt_util.utcnow().timestamp()) is True


async def test_manual_flags_survive_an_entry_reload(
    enable_custom_integrations, hass, hass_config_dir, freezer
) -> None:
    """Fix 5: a reload must not hand a hand-dimmed light back to adaptation."""
    entry = await _setup(hass, hass_config_dir, [_target_subentry()])
    async_mock_service(hass, "light", "turn_on")
    await _turn_switch_on(hass)

    freezer.tick(datetime.timedelta(seconds=60))
    _set_light(hass, context=Context(), brightness=255)
    await hass.async_block_till_done()

    entity_before = _switch_entity(hass)
    assert (
        entity_before._target.is_manual(
            KITCHEN_LIGHT, now=dt_util.utcnow().timestamp()
        )
        is True
    )

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    entity_after = _switch_entity(hass)
    assert entity_after is not entity_before
    assert hass.states.get(KITCHEN_SWITCH).state == "on"
    assert (
        entity_after._target.is_manual(
            KITCHEN_LIGHT, now=dt_util.utcnow().timestamp()
        )
        is True
    )


def _set_light_unavailable(hass, entity_id=KITCHEN_LIGHT):
    """Drop a member off the network, as a Zigbee/Hue reconnect blip would."""
    hass.states.async_set(entity_id, "unavailable", {}, context=Context())


async def test_an_unavailable_blip_does_not_forgive_a_hand_dim(
    enable_custom_integrations, hass, hass_config_dir, freezer
) -> None:
    """A reconnect is not a resume gesture.

    Only an explicit off->on -- of the light, or of this switch -- hands a
    genuinely hand-dimmed member back to adaptation. Losing the network in
    between must not launder the flag away, so the blip's `unavailable -> on`
    leg has to adapt-but-not-clear: with the member already manual, that
    leaves it manual and issues nothing.
    """
    await _setup(hass, hass_config_dir, [_target_subentry()])
    calls = async_mock_service(hass, "light", "turn_on")
    await _turn_switch_on(hass)
    calls.clear()

    # Past the resume pass's own suppression window, so the hand-dim below is
    # genuinely what flags this member.
    freezer.tick(datetime.timedelta(seconds=60))
    _set_light(hass, context=Context(), brightness=255)
    await hass.async_block_till_done()
    target = _switch_entity(hass)._target
    assert target.is_manual(KITCHEN_LIGHT, now=dt_util.utcnow().timestamp()) is True
    assert calls == []

    _set_light_unavailable(hass)
    await hass.async_block_till_done()
    _set_light(hass, state="on", context=Context(), brightness=255)
    await hass.async_block_till_done()

    assert target.is_manual(KITCHEN_LIGHT, now=dt_util.utcnow().timestamp()) is True
    assert calls == []


async def test_an_unavailable_blip_alone_never_marks_a_member_manual(
    enable_custom_integrations, hass, hass_config_dir, freezer
) -> None:
    """Fix: a reconnect with no user action must leave the member adapting.

    Both legs carry a foreign context and land well past the suppression
    window, so under the old uniform observation the `on -> unavailable` leg
    flagged the member manual for good -- `unavailable -> on` was not an
    off->on, so nothing ever cleared it.
    """
    await _setup(hass, hass_config_dir, [_target_subentry()])
    calls = async_mock_service(hass, "light", "turn_on")
    await _turn_switch_on(hass)
    calls.clear()

    freezer.tick(datetime.timedelta(seconds=60))
    _set_light_unavailable(hass)
    await hass.async_block_till_done()

    with patch(
        "custom_components.daylight.switch.compute_turn_on_kwargs",
        return_value=dict(_STUB_KWARGS),
    ):
        _set_light(hass, state="on", context=Context(), brightness=255)
        await hass.async_block_till_done()

    target = _switch_entity(hass)._target
    assert target.is_manual(KITCHEN_LIGHT, now=dt_util.utcnow().timestamp()) is False
    assert [call.data for call in calls] == [
        {"entity_id": KITCHEN_LIGHT, **_STUB_KWARGS},
    ]


async def test_tick_skips_members_that_are_currently_off(
    enable_custom_integrations, hass, hass_config_dir
) -> None:
    """Adapting is a push of values, never a `turn_on`: off members stay off.

    The `on` sibling in the same tick proves the guard skips only the off
    member rather than bailing out of the whole loop.
    """
    _set_light(hass, HALL_LIGHT, state="off")
    entry = await _setup(
        hass,
        hass_config_dir,
        [_target_subentry(entities=[KITCHEN_LIGHT, HALL_LIGHT])],
        lights=(KITCHEN_LIGHT,),
    )
    calls = async_mock_service(hass, "light", "turn_on")
    await _turn_switch_on(hass)
    # Resuming adapts every on member itself; this test is about the tick.
    calls.clear()

    target = _switch_entity(hass)._target
    now = dt_util.utcnow().timestamp()
    assert target.is_manual(KITCHEN_LIGHT, now=now) is False
    assert target.is_manual(HALL_LIGHT, now=now) is False

    with patch(
        "custom_components.daylight.switch.compute_turn_on_kwargs",
        return_value=dict(_STUB_KWARGS),
    ):
        await _tick(hass, entry)

    assert [call.data for call in calls] == [
        {"entity_id": KITCHEN_LIGHT, **_STUB_KWARGS},
    ]
