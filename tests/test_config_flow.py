"""Drive native HA setup/reconfiguration: defaults, drafts, rules and saving."""

from copy import deepcopy
from unittest.mock import patch

import pytest
import voluptuous as vol
from homeassistant.data_entry_flow import FlowResultType, section
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.daylight.config import TARGET_SCHEMA
from custom_components.daylight.config_flow import (
    DaylightConfigFlow,
    TargetSubentryFlowHandler,
)
from custom_components.daylight.const import DOMAIN
from custom_components.daylight.schedule_config import default_hub_data

TARGET_INPUT = {
    "targets": {"entity_id": ["light.kitchen"]},
    "brightness": {"min_brightness_pct": 10, "max_brightness_pct": 100},
    "color_temp": {"min_color_temp_kelvin": 2500, "max_color_temp_kelvin": 4000},
}
TARGET_DATA = {
    "entities": ["light.kitchen"],
    "areas": [],
    "min_brightness_pct": 10,
    "max_brightness_pct": 100,
    "min_color_temp_kelvin": 2500,
    "max_color_temp_kelvin": 4000,
    "transition": 0.0,
    "adapt_only_on_state_change": False,
    "manual_control_reset_minutes": 0,
    "separate_turn_on_commands": False,
    "send_split_delay": 0.0,
}


@pytest.fixture
async def entry(hass, enable_custom_integrations, hass_config_dir):
    hass.config.config_dir = hass_config_dir
    hass.config.latitude, hass.config.longitude = 40.7, -74
    await hass.config.async_set_time_zone("America/New_York")
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=default_hub_data(),
        version=2,
        subentries_data=[
            {
                "data": TARGET_DATA,
                "subentry_type": "target",
                "title": "Kitchen",
                "unique_id": None,
            }
        ],
    )
    entry.add_to_hass(hass)
    return entry


async def configure(hass, result, **user_input):
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input=user_input
    )


async def menu(hass, entry):
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "reconfigure", "entry_id": entry.entry_id}
    )


async def choose(hass, result, step):
    if step in {"customize", "shapes", "runtime"} and result["step_id"] == "menu":
        result = await configure(hass, result, next_step_id="advanced")
    return await configure(hass, result, next_step_id=step)


async def test_setup_select_lights_and_accept_defaults(hass, entry):
    with patch("custom_components.daylight.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "user"
        result = await configure(hass, result, targets={"entity_id": ["light.kitchen"]})
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == default_hub_data()
    created = result["result"]
    assert created.version == 2
    assert next(iter(created.subentries.values())).data == TARGET_DATA


async def test_setup_requires_a_target(hass, entry):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await configure(hass, result, targets={})
    assert result["errors"] == {"base": "target_required"}


async def test_basic_edits_are_drafts_until_explicit_save(hass, entry):
    original = deepcopy(dict(entry.data))
    result = await menu(hass, entry)
    assert result["type"] is FlowResultType.MENU
    assert set(result["menu_options"]) == {
        "morning",
        "evening",
        "lengths",
        "advanced",
        "save",
    }
    result = await choose(hass, result, "morning")
    result = await configure(hass, result, mode="solar")
    result = await configure(hass, result, offset_minutes=30)
    assert entry.data == original
    result = await choose(hass, result, "lengths")
    result = await configure(hass, result, brightness_length=1.5, color_length=0.5)
    assert entry.data == original
    result = await choose(hass, result, "save")
    assert entry.data == original  # opening review does not save
    result = await configure(hass, result, action="save")
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    basic = entry.data["schedule"]["basic"]
    assert basic["morning_offset_minutes"] == 30
    assert basic["brightness_length"] == 1.5
    assert basic["color_length"] == 0.5
    assert next(iter(entry.subentries.values())).data == TARGET_DATA


async def test_cancel_discards_draft(hass, entry):
    original = deepcopy(dict(entry.data))
    result = await choose(hass, await menu(hass, entry), "lengths")
    result = await configure(hass, result, brightness_length=2, color_length=2)
    hass.config_entries.flow.async_abort(result["flow_id"])
    assert entry.data == original


async def test_clock_mode_and_next_day_round_trip(hass, entry):
    result = await choose(hass, await menu(hass, entry), "morning")
    result = await configure(hass, result, mode="clock")
    result = await configure(hass, result, time="07:00:00")
    result = await choose(hass, result, "evening")
    result = await configure(hass, result, mode="clock")
    result = await configure(hass, result, time="01:00:00", next_day=True)
    result = await configure(hass, await choose(hass, result, "save"), action="save")
    assert result["type"] is FlowResultType.ABORT
    assert entry.data["schedule"]["basic"]["evening_next_day"] is True
    result = await choose(hass, await menu(hass, entry), "evening")
    assert suggested(result["data_schema"])["mode"] == "clock"
    result = await configure(hass, result, mode="clock")
    assert suggested(result["data_schema"])["time"] == "01:00:00"


async def test_invalid_schedule_keeps_saved_data_and_allows_correction(hass, entry):
    original = deepcopy(dict(entry.data))
    result = await choose(hass, await menu(hass, entry), "morning")
    result = await configure(hass, result, mode="clock")
    result = await configure(hass, result, time="23:00:00")
    result = await configure(hass, await choose(hass, result, "save"), action="save")
    assert result["errors"] == {"base": "schedule_invalid"}
    assert (
        "evening occurs before morning" in result["description_placeholders"]["detail"]
    )
    assert entry.data == original
    result = await configure(hass, result, action="keep_editing")
    result = await choose(hass, result, "morning")
    result = await configure(hass, result, mode="solar")
    result = await configure(hass, result, offset_minutes=0)
    result = await configure(hass, await choose(hass, result, "save"), action="save")
    assert result["type"] is FlowResultType.ABORT


async def test_custom_endpoint_hides_basic_controls_without_destroying_them(
    hass, entry
):
    result = await choose(hass, await menu(hass, entry), "customize")
    result = await configure(hass, result, endpoint="color_morning_end")
    assert suggested(result["data_schema"])["kind"] == "standard"
    result = await configure(hass, result, kind="seasonal")
    result = await configure(
        hass, result, reference="sunrise", offset_minutes=120, next_day=False
    )
    assert "morning" not in result["menu_options"]
    assert "restore" in result["menu_options"]
    result = await configure(hass, await choose(hass, result, "save"), action="save")
    assert result["type"] is FlowResultType.ABORT
    rule = entry.data["schedule"]["endpoints"]["color_morning_end"]
    assert rule["kind"] == "seasonal"
    result = await choose(hass, await menu(hass, entry), "customize")
    result = await configure(hass, result, endpoint="color_morning_end")
    assert suggested(result["data_schema"])["kind"] == "seasonal"
    result = await configure(hass, result, kind="seasonal")
    assert suggested(result["data_schema"])["offset_minutes"] == pytest.approx(
        120, abs=0.1
    )


async def test_restore_requires_confirmation_and_save(hass, entry):
    data = deepcopy(dict(entry.data))
    data["schedule"]["endpoints"] = {
        "color_morning_start": {
            "kind": "solar",
            "reference": "sunrise",
            "offset_minutes": 5,
        }
    }
    hass.config_entries.async_update_entry(entry, data=data)
    result = await choose(hass, await menu(hass, entry), "restore")
    result = await configure(hass, result, confirm=False)
    assert "morning" not in result["menu_options"]
    result = await configure(hass, await choose(hass, result, "restore"), confirm=True)
    assert "morning" in result["menu_options"]
    assert entry.data["schedule"]["endpoints"]
    result = await configure(hass, await choose(hass, result, "save"), action="save")
    assert not entry.data["schedule"]["endpoints"]
    assert next(iter(entry.subentries.values())).data == TARGET_DATA


async def test_removing_last_override_restores_basic_controls(hass, entry):
    result = await choose(hass, await menu(hass, entry), "customize")
    result = await configure(hass, result, endpoint="color_morning_start")
    result = await configure(hass, result, kind="solar")
    result = await configure(
        hass, result, reference="sunrise", offset_minutes=5, next_day=False
    )
    result = await choose(hass, result, "customize")
    result = await configure(hass, result, endpoint="color_morning_start")
    assert suggested(result["data_schema"])["kind"] == "solar"
    result = await configure(hass, result, kind="standard")
    assert "morning" in result["menu_options"]


async def test_shape_only_edits_keep_basic_controls(hass, entry):
    result = await choose(hass, await menu(hass, entry), "shapes")
    result = await configure(
        hass,
        result,
        brightness_morning="linear",
        brightness_evening="smooth",
        color_morning="smooth",
        color_evening="linear",
    )
    assert "morning" in result["menu_options"]
    result = await configure(hass, await choose(hass, result, "save"), action="save")
    assert entry.data["schedule"]["shapes"]["color_evening"] == "linear"


@pytest.mark.parametrize("interval", [-1, 0, 0.5])
async def test_nonpositive_poll_interval_is_rejected(hass, entry, interval):
    result = await choose(hass, await menu(hass, entry), "runtime")
    with pytest.raises(vol.Invalid):
        result["data_schema"]({"update_interval_seconds": interval})


def test_target_subentry_type():
    assert DaylightConfigFlow.async_get_supported_subentry_types(
        MockConfigEntry(domain=DOMAIN)
    ) == {"target": TargetSubentryFlowHandler}


@pytest.mark.parametrize(
    "targets",
    [
        {"entity_id": ["light.kitchen"]},
        {"area_id": ["kitchen"]},
        {"entity_id": ["light.kitchen"], "area_id": ["kitchen"]},
    ],
)
async def test_add_target_with_defaults(hass, entry, targets):
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "target"), context={"source": "user"}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], user_input={"targets": targets}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    added = list(entry.subentries.values())[-1]
    assert added.data["entities"] == targets.get("entity_id", [])
    assert added.data["areas"] == targets.get("area_id", [])
    assert added.data["min_brightness_pct"] == 10
    assert added.data["min_color_temp_kelvin"] == 2500
    assert added.data["max_color_temp_kelvin"] == 4000
    assert added.data["transition"] == 0
    assert added.data["manual_control_reset_minutes"] == 0


@pytest.mark.parametrize(
    "change,error",
    [
        ({"targets": {}}, "target_required"),
        ({"targets": {"device_id": ["device"]}}, "unsupported_target"),
        ({"targets": {"entity_id": ["sensor.temperature"]}}, "unsupported_target"),
        (
            {"brightness": {"min_brightness_pct": 90, "max_brightness_pct": 10}},
            "brightness_range_invalid",
        ),
        (
            {
                "color_temp": {
                    "min_color_temp_kelvin": 6500,
                    "max_color_temp_kelvin": 2000,
                }
            },
            "color_temp_range_invalid",
        ),
    ],
)
async def test_invalid_target_does_not_save(hass, entry, change, error):
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "target"), context={"source": "user"}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], user_input={**TARGET_INPUT, **change}
    )
    assert result["errors"] == {"base": error}
    assert len(entry.subentries) == 1


def suggested(schema):
    return {
        str(key): suggested(value.schema)
        if isinstance(value, section)
        else (key.description or {}).get("suggested_value")
        for key, value in schema.schema.items()
    }


async def test_target_reconfigure_round_trip_and_retitles(hass, entry):
    subentry_id = next(iter(entry.subentries))
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "target"),
        context={"source": "reconfigure", "subentry_id": subentry_id},
    )
    values = suggested(result["data_schema"])
    assert values["targets"] == {"entity_id": ["light.kitchen"], "area_id": []}
    assert values["color_temp"]["min_color_temp_kelvin"] == 2500
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={
            **TARGET_INPUT,
            "targets": {"entity_id": ["light.porch"]},
            "advanced": {"transition": 5.0, "manual_control_reset_minutes": 15},
        },
    )
    assert result["type"] is FlowResultType.ABORT
    target = entry.subentries[subentry_id]
    assert target.title == "light.porch"
    assert target.data["transition"] == 5.0
    assert target.data["manual_control_reset_minutes"] == 15
    assert entry.data == default_hub_data()


def test_target_sections_are_optional_and_collapsed():
    for key, value in TARGET_SCHEMA.schema.items():
        if isinstance(value, section):
            assert isinstance(key, vol.Optional)
    assert (
        TARGET_SCHEMA({"targets": {"entity_id": ["light.kitchen"]}})["brightness"][
            "min_brightness_pct"
        ]
        == 10
    )
