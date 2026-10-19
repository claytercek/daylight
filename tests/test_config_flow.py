"""Drive native setup, schedule options, and target reconfiguration."""

import json
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from threading import get_ident
from typing import Any, cast
from unittest.mock import patch

import pytest
import voluptuous as vol
from homeassistant.data_entry_flow import FlowResultType, InvalidData, section
from homeassistant.helpers.config_validation import custom_serializer
from probatio import to_field_list
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.daylight.config import TARGET_SCHEMA
from custom_components.daylight.config_flow import (
    DaylightConfigFlow,
    DaylightOptionsFlow,
    TargetSubentryFlowHandler,
)
from custom_components.daylight.const import DOMAIN
from custom_components.daylight.schedule import ENDPOINTS, Schedule
from custom_components.daylight.schedule_config import (
    RETURN_WITHOUT_CHANGES,
    RUNTIME_SCHEMA,
    TRANSITIONS_SCHEMA,
    async_schedule,
    daily_timing_schema,
    default_hub_data,
    editor_schema,
    endpoint_timing_schema,
)

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
    "serialized_native_fades": False,
}


@pytest.fixture
async def entry(hass, enable_custom_integrations, hass_config_dir):
    hass.config.config_dir = hass_config_dir
    hass.config.latitude, hass.config.longitude = 40.7, -74
    await hass.config.async_set_time_zone("America/New_York")
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={},
        options=default_hub_data(),
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


async def edit_schedule(hass, entry):
    return await hass.config_entries.options.async_init(entry.entry_id)


async def configure_schedule(hass, result, **user_input):
    return await hass.config_entries.options.async_configure(
        result["flow_id"], user_input=user_input
    )


async def choose(hass, result, step):
    return await configure_schedule(hass, result, next_step_id=step)


async def daily_form(hass, result, morning="solar", evening="solar"):
    result = await choose(hass, result, "daily_timing")
    return await configure_schedule(
        hass, result, morning_mode=morning, evening_mode=evening
    )


async def endpoint_form(hass, result, point, kind):
    if result["step_id"] != "advanced":
        result = await choose(hass, result, "advanced")
    result = await choose(hass, result, "endpoint")
    result = await configure_schedule(hass, result, endpoint=point)
    return await configure_schedule(hass, result, kind=kind)


async def save_schedule(hass, result):
    if result["step_id"] == "advanced":
        result = await choose(hass, result, "init")
    return await choose(hass, result, "save")


def schema_fields(schema) -> list[dict[str, Any]]:
    return cast(
        list[dict[str, Any]],
        to_field_list(schema, custom_serializer=custom_serializer),
    )


def initial_values(schema):
    """Match the frontend's suggested-value, default, then section initialization."""
    values = {}
    for field in schema_fields(schema):
        if "suggested_value" in field.get("description", {}):
            values[field["name"]] = field["description"]["suggested_value"]
        elif "default" in field:
            values[field["name"]] = field["default"]
        elif field.get("type") == "expandable":
            values[field["name"]] = initial_fields(field["schema"])
    return values


def initial_fields(fields):
    values = {}
    for field in fields:
        if "suggested_value" in field.get("description", {}):
            values[field["name"]] = field["description"]["suggested_value"]
        elif "default" in field:
            values[field["name"]] = field["default"]
    return values


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
    assert result["data"] == {}
    assert result["options"] == default_hub_data()
    created = result["result"]
    assert created.version == 2
    assert created.data == {}
    assert created.options == default_hub_data()
    assert next(iter(created.subentries.values())).data == TARGET_DATA


async def test_setup_requires_a_target(hass, entry):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await configure(hass, result, targets={})
    assert result["errors"] == {"base": "target_required"}


def test_shared_schedule_uses_options_not_hub_reconfigure():
    entry = MockConfigEntry(domain=DOMAIN)
    assert DaylightConfigFlow.async_supports_options_flow(entry)
    assert isinstance(
        DaylightConfigFlow.async_get_options_flow(entry), DaylightOptionsFlow
    )
    assert not hasattr(DaylightConfigFlow, "async_step_reconfigure")


async def test_schedule_menu_and_staged_settings(hass, entry):
    original = deepcopy(dict(entry.options))
    result = await edit_schedule(hass, entry)
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "init"
    assert result["menu_options"] == ["daily_timing", "transitions", "advanced", "save"]
    result = await daily_form(hass, result)
    result = await configure_schedule(
        hass, result, morning_offset_minutes=30, evening_offset_minutes=0
    )
    result = await choose(hass, result, "transitions")
    values = initial_values(result["data_schema"])
    values["brightness"]["length"] = 1.5
    values["color"].update(length=0.5, evening_shape="linear")
    result = await configure_schedule(hass, result, **values)
    result = await choose(hass, result, "advanced")
    assert result["menu_options"] == ["endpoint", "runtime", "reset", "init"]
    result = await choose(hass, result, "runtime")
    result = await configure_schedule(hass, result, update_interval_seconds=60)
    assert entry.options == original
    result = await save_schedule(hass, result)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == ""
    assert result["data"] == entry.options
    basic = entry.options["schedule"]["basic"]
    assert basic["morning_offset_minutes"] == 30
    assert basic["brightness_length"] == 1.5
    assert basic["color_length"] == 0.5
    assert entry.options["schedule"]["shapes"]["color_evening"] == "linear"
    assert entry.options["update_interval_seconds"] == 60
    assert entry.data == {}
    assert next(iter(entry.subentries.values())).data == TARGET_DATA


async def test_cancel_discards_schedule_edits(hass, entry):
    original = deepcopy(dict(entry.options))
    result = await edit_schedule(hass, entry)
    result = await daily_form(hass, result)
    result = await configure_schedule(
        hass, result, morning_offset_minutes=30, evening_offset_minutes=-10
    )
    hass.config_entries.options.async_abort(result["flow_id"])
    assert entry.options == original
    assert entry.data == {}


async def test_schedule_save_preserves_other_options(hass, entry):
    options = {**entry.options, "other_setting": {"enabled": True}}
    hass.config_entries.async_update_entry(entry, options=options)
    result = await edit_schedule(hass, entry)
    result = await choose(hass, result, "advanced")
    result = await choose(hass, result, "runtime")
    result = await configure_schedule(hass, result, update_interval_seconds=60)
    result = await save_schedule(hass, result)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["other_setting"] == {"enabled": True}
    assert entry.options["update_interval_seconds"] == 60
    assert entry.data == {}


async def test_schedule_no_op_save(hass, entry):
    original = deepcopy(dict(entry.options))
    result = await edit_schedule(hass, entry)
    result = await daily_form(hass, result)
    result = await configure_schedule(
        hass, result, **initial_values(result["data_schema"])
    )
    result = await choose(hass, result, "transitions")
    result = await configure_schedule(
        hass, result, **initial_values(result["data_schema"])
    )
    result = await save_schedule(hass, result)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == original
    assert entry.options == original
    assert entry.data == {}


@pytest.mark.parametrize("morning", ["solar", "clock"])
@pytest.mark.parametrize("evening", ["solar", "clock"])
async def test_daily_timing_shows_only_applicable_fields(hass, entry, morning, evening):
    result = await edit_schedule(hass, entry)
    result = await daily_form(hass, result, morning, evening)
    expected = {
        "morning_time" if morning == "clock" else "morning_offset_minutes",
        "evening_time" if evening == "clock" else "evening_offset_minutes",
        RETURN_WITHOUT_CHANGES,
    }
    if evening == "clock":
        expected.add("evening_next_day")
    fields = schema_fields(result["data_schema"])
    assert {field["name"] for field in fields} == expected
    assert all(not field["required"] for field in fields)
    assert not any(field.get("type") == "expandable" for field in fields)


async def test_clock_mode_next_day_round_trip_and_switch_back_to_sun(hass, entry):
    result = await daily_form(hass, await edit_schedule(hass, entry), "clock", "clock")
    result = await configure_schedule(
        hass,
        result,
        morning_time="07:00:00",
        evening_time="01:00:00",
        evening_next_day=True,
    )
    result = await save_schedule(hass, result)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["schedule"]["basic"]["evening_next_day"] is True
    reopened = await edit_schedule(hass, entry)
    reopened = await choose(hass, reopened, "daily_timing")
    assert initial_values(reopened["data_schema"]) == {
        "morning_mode": "clock",
        "evening_mode": "clock",
        RETURN_WITHOUT_CHANGES: False,
    }
    reopened = await configure_schedule(
        hass, reopened, morning_mode="clock", evening_mode="clock"
    )
    prefills = initial_values(reopened["data_schema"])
    assert prefills["evening_time"] == "01:00:00"
    assert prefills["evening_next_day"] is True
    reopened = await configure_schedule(hass, reopened, **prefills)
    reopened = await daily_form(hass, reopened)
    reopened = await configure_schedule(
        hass, reopened, morning_offset_minutes=-15, evening_offset_minutes=10
    )
    await save_schedule(hass, reopened)
    basic = entry.options["schedule"]["basic"]
    assert basic["morning_time"] is basic["evening_time"] is None
    assert basic["evening_next_day"] is False
    assert basic["morning_offset_minutes"] == -15


async def test_invalid_schedule_preserves_input_and_saved_data(hass, entry):
    original = deepcopy(dict(entry.options))
    result = await daily_form(hass, await edit_schedule(hass, entry), "clock")
    result = await configure_schedule(
        hass, result, morning_time="23:00:00", evening_offset_minutes=0
    )
    assert result["step_id"] == "init"
    assert entry.options == original
    result = await save_schedule(hass, result)
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "save_error"
    assert (
        "evening occurs before morning" in result["description_placeholders"]["detail"]
    )
    assert "daily_timing" in result["menu_options"]
    assert entry.options == original
    assert entry.data == {}
    result = await daily_form(hass, result, "clock")
    assert initial_values(result["data_schema"])["morning_time"] == "23:00:00"
    result = await configure_schedule(
        hass, result, morning_time="07:00:00", evening_offset_minutes=0
    )
    result = await save_schedule(hass, result)
    assert result["type"] is FlowResultType.CREATE_ENTRY


@pytest.mark.parametrize("time", [None, "", "not a time"])
async def test_clock_requires_local_time_and_allows_correction(hass, entry, time):
    result = await daily_form(hass, await edit_schedule(hass, entry), evening="clock")
    values = {
        "morning_offset_minutes": 25,
        "evening_time": time,
        "evening_next_day": True,
    }
    if time == "not a time":
        with pytest.raises(InvalidData):
            await configure_schedule(hass, result, **values)
    else:
        result = await configure_schedule(hass, result, **values)
        assert result["step_id"] == "daily_details"
        assert result["errors"] == {"evening_time": "time_required"}
        prefills = initial_values(result["data_schema"])
        assert prefills["morning_offset_minutes"] == 25
        assert prefills["evening_time"] == time
        assert prefills["evening_next_day"] is True
    assert entry.options == default_hub_data()
    result = await configure_schedule(
        hass,
        result,
        morning_offset_minutes=25,
        evening_time="01:00:00",
        evening_next_day=True,
    )
    await save_schedule(hass, result)
    assert entry.options["schedule"]["basic"]["morning_offset_minutes"] == 25


@pytest.mark.parametrize(
    "step",
    [
        "daily_timing",
        "daily_details",
        "transitions",
        "endpoint",
        "endpoint_rule",
        "endpoint_details",
        "runtime",
        "reset",
    ],
)
async def test_backend_return_can_discard_invalid_editor_values(hass, entry, step):
    result = await daily_form(hass, await edit_schedule(hass, entry))
    result = await configure_schedule(
        hass, result, morning_offset_minutes=30, evening_offset_minutes=0
    )
    if step == "daily_details":
        result = await daily_form(hass, result, "clock", "clock")
    elif step in ("daily_timing", "transitions"):
        result = await choose(hass, result, step)
    else:
        result = await choose(hass, result, "advanced")
        if step in ("endpoint_rule", "endpoint_details"):
            result = await choose(hass, result, "endpoint")
            result = await configure_schedule(
                hass, result, endpoint="color_morning_end"
            )
            if step == "endpoint_details":
                result = await configure_schedule(hass, result, kind="clock")
        else:
            result = await choose(hass, result, step)
    assert result["step_id"] == step
    fields = schema_fields(result["data_schema"])
    navigation = next(
        field for field in fields if field["name"] == RETURN_WITHOUT_CHANGES
    )
    assert navigation["default"] is False
    assert navigation["selector"] == {"boolean": {}}
    assert all(not field["required"] for field in fields)
    json.dumps(fields)
    inputs: dict[str, Any] = {RETURN_WITHOUT_CHANGES: True}
    if step == "daily_details":
        inputs.update(morning_time="not a time", evening_time="")
    elif step == "endpoint_details":
        inputs.update(time="not a time")
    elif step == "runtime":
        inputs.update(update_interval_seconds=0)
    elif step == "transitions":
        inputs.update(brightness={"length": 0}, color={"length": "not a number"})
    elif step == "daily_timing":
        inputs.update(morning_mode="invalid mode")
    elif step == "endpoint_rule":
        inputs.update(kind="invalid rule")
    elif step == "reset":
        inputs.update(confirm=True)
    result = await configure_schedule(hass, result, **inputs)
    assert result["step_id"] == (
        "init"
        if step in ("daily_timing", "daily_details", "transitions")
        else "advanced"
    )
    assert entry.options == default_hub_data()
    result = await save_schedule(hass, result)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["schedule"]["basic"]["morning_offset_minutes"] == 30
    assert not entry.options["schedule"]["endpoints"]
    assert RETURN_WITHOUT_CHANGES not in json.dumps(dict(entry.options))


@pytest.mark.parametrize(
    "schema,values",
    [
        (RUNTIME_SCHEMA, {"update_interval_seconds": 0}),
        (endpoint_timing_schema("clock"), {"time": "bad clock", "next_day": False}),
        (
            daily_timing_schema({"morning_mode": "clock", "evening_mode": "solar"}),
            {"morning_time": "bad clock", "evening_offset_minutes": 0},
        ),
        (
            TRANSITIONS_SCHEMA,
            {
                "brightness": {
                    "length": 0,
                    "morning_shape": "smooth",
                    "evening_shape": "smooth",
                },
                "color": {
                    "length": 1,
                    "morning_shape": "smooth",
                    "evening_shape": "smooth",
                },
            },
        ),
    ],
)
def test_backend_return_short_circuits_and_preserves_native_selector_serialization(
    schema, values
):
    display = editor_schema(schema)
    assert display(values | {RETURN_WITHOUT_CHANGES: True}) == {
        RETURN_WITHOUT_CHANGES: True
    }
    with pytest.raises(vol.Invalid):
        schema(values)
    with pytest.raises(vol.Invalid):
        display(values)

    def cleared_values(values):
        return {
            key: cleared_values(value) if isinstance(value, dict) else None
            for key, value in values.items()
        }

    assert display(cleared_values(values))[RETURN_WITHOUT_CHANGES] is False

    def check_fields(fields, original_fields):
        originals = {field["name"]: field for field in original_fields}
        assert {field["name"] for field in fields} - {
            RETURN_WITHOUT_CHANGES
        } == originals.keys()
        for field in fields:
            assert not field["required"]
            if field["name"] == RETURN_WITHOUT_CHANGES:
                continue
            original = originals[field["name"]]
            if field.get("type") == "expandable":
                check_fields(field["schema"], original["schema"])
                continue
            assert field["selector"] == original["selector"]

    fields = schema_fields(display)
    check_fields(fields, schema_fields(schema))
    json.dumps(fields)


async def test_clear_invalid_transition_then_return_keeps_earlier_draft(hass, entry):
    result = await edit_schedule(hass, entry)
    result = await choose(hass, result, "advanced")
    result = await choose(hass, result, "runtime")
    result = await configure_schedule(hass, result, update_interval_seconds=60)
    result = await choose(hass, result, "init")
    result = await choose(hass, result, "transitions")
    values = initial_values(result["data_schema"])
    values["brightness"]["length"] = "not a number"
    values["color"]["length"] = "3.5"
    with pytest.raises(InvalidData):
        await configure_schedule(hass, result, **values)
    assert entry.options == default_hub_data()
    values["brightness"]["length"] = None
    values["color"]["length"] = ""
    result = await configure_schedule(
        hass, result, **(values | {RETURN_WITHOUT_CHANGES: True})
    )
    assert result["step_id"] == "init"
    result = await save_schedule(hass, result)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["update_interval_seconds"] == 60
    assert entry.options["schedule"] == default_hub_data()["schedule"]


async def test_missing_clock_endpoint_can_error_then_return_without_losing_draft(
    hass, entry
):
    result = await endpoint_form(
        hass, await edit_schedule(hass, entry), "color_morning_end", "clock"
    )
    result = await configure_schedule(hass, result, next_day=True)
    assert result["errors"] == {"time": "time_required"}
    assert initial_values(result["data_schema"])["next_day"] is True
    result = await configure_schedule(hass, result, return_without_changes=True)
    assert result["step_id"] == "advanced"
    result = await save_schedule(hass, result)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == default_hub_data()


@pytest.mark.parametrize(
    "point,reference,offset,next_day",
    [
        ("color_morning_end", "sunrise", 120, False),
        ("brightness_evening_end", "midnight", 15, True),
    ],
)
async def test_custom_endpoint_round_trip_and_remove(
    hass, entry, point, reference, offset, next_day
):
    with patch(
        "custom_components.daylight.config_flow.dt_util.utcnow",
        return_value=datetime(2026, 6, 21, 2, tzinfo=UTC),
    ):
        result = await edit_schedule(hass, entry)
    result = await endpoint_form(hass, result, point, "seasonal")
    result = await configure_schedule(
        hass, result, reference=reference, offset_minutes=offset, next_day=next_day
    )
    result = await save_schedule(hass, result)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    rule = entry.options["schedule"]["endpoints"][point]
    assert rule["kind"] == "seasonal"
    schedule = await async_schedule(hass, entry.options)
    day = date(2026, 6, 20) + timedelta(days=int(next_day))
    assert rule["offset_minutes"] == pytest.approx(
        offset * 12 * 3600 / schedule.sun.day(day).daylight
    )
    with patch(
        "custom_components.daylight.config_flow.dt_util.utcnow",
        return_value=datetime(2026, 6, 21, 2, tzinfo=UTC),
    ):
        reopened = await edit_schedule(hass, entry)
    reopened = await endpoint_form(hass, reopened, point, "seasonal")
    values = initial_values(reopened["data_schema"])
    assert float(values["offset_minutes"]) == pytest.approx(offset, abs=0.1)
    assert values["next_day"] is next_day
    assert reopened["description_placeholders"]["date"] == "2026-06-20"
    result = await configure_schedule(hass, reopened, **values)
    result = await endpoint_form(hass, result, point, "standard")
    assert entry.options["schedule"]["endpoints"]
    result = await save_schedule(hass, result)
    assert not entry.options["schedule"]["endpoints"]


@pytest.mark.parametrize("kind", ["clock", "solar", "seasonal"])
async def test_all_endpoints_and_mode_specific_schemas(hass, entry, kind):
    result = await edit_schedule(hass, entry)
    for point in ENDPOINTS:
        result = await endpoint_form(hass, result, point, kind)
        fields = schema_fields(result["data_schema"])
        assert {field["name"] for field in fields} == (
            {"time", "next_day", RETURN_WITHOUT_CHANGES}
            if kind == "clock"
            else {"reference", "offset_minutes", "next_day", RETURN_WITHOUT_CHANGES}
        )
        assert all(not field["required"] for field in fields)
        values = initial_values(result["data_schema"])
        if kind == "clock":
            values["time"] = "10:00:00"
        result = await configure_schedule(hass, result, **values)
        assert result["step_id"] == "advanced"
    assert entry.options == default_hub_data()
    hass.config_entries.options.async_abort(result["flow_id"])


async def test_clock_endpoint_can_switch_to_solar_without_stale_fields(hass, entry):
    result = await endpoint_form(
        hass, await edit_schedule(hass, entry), "color_morning_end", "clock"
    )
    result = await configure_schedule(hass, result, time="10:00:00", next_day=False)
    result = await save_schedule(hass, result)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert (
        entry.options["schedule"]["endpoints"]["color_morning_end"]["clock"]
        == "10:00:00"
    )

    result = await endpoint_form(
        hass, await edit_schedule(hass, entry), "color_morning_end", "solar"
    )
    result = await configure_schedule(
        hass, result, reference="sunrise", offset_minutes=120, next_day=False
    )
    result = await save_schedule(hass, result)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["schedule"]["endpoints"]["color_morning_end"] == {
        "kind": "solar",
        "reference": "sunrise",
        "offset_minutes": 120,
        "next_day": False,
    }


async def test_seasonal_and_fixed_minute_modes_keep_displayed_reference_date_offset(
    hass, entry
):
    options = deepcopy(dict(entry.options))
    options["schedule"]["endpoints"]["color_morning_end"] = {
        "kind": "seasonal",
        "reference": "sunrise",
        "offset_minutes": 120.123456,
        "next_day": False,
    }
    hass.config_entries.async_update_entry(entry, options=options)
    schedule = await async_schedule(hass, options)
    ratio = schedule.sun.day(date(2026, 6, 20)).daylight / (12 * 3600)
    displayed_offset = round(120.123456 * ratio, 1)
    for kind in ("solar", "seasonal"):
        with patch(
            "custom_components.daylight.config_flow.dt_util.utcnow",
            return_value=datetime(2026, 6, 21, 2, tzinfo=UTC),
        ):
            result = await edit_schedule(hass, entry)
        result = await endpoint_form(hass, result, "color_morning_end", kind)
        values = initial_values(result["data_schema"])
        assert float(values["offset_minutes"]) == displayed_offset
        assert values["reference"] == "sunrise"
        result = await configure_schedule(hass, result, **values)
        result = await save_schedule(hass, result)
        assert result["type"] is FlowResultType.CREATE_ENTRY
        rule = entry.options["schedule"]["endpoints"]["color_morning_end"]
        assert rule["offset_minutes"] == pytest.approx(
            displayed_offset if kind == "solar" else displayed_offset / ratio
        )


@pytest.mark.parametrize("change_runtime", [False, True])
async def test_untouched_and_unchanged_seasonal_rules_keep_exact_canonical_values(
    hass, entry, change_runtime
):
    options = deepcopy(dict(entry.options))
    offsets = (-30, 30, -30, 30, 0, 120, -120, 0)
    endpoints = {
        point: {
            "kind": "seasonal",
            "reference": "sunset" if "evening" in point else "sunrise",
            "offset_minutes": offset + 0.0123456789,
        }
        for point, offset in zip(ENDPOINTS, offsets, strict=True)
    }
    options["schedule"]["endpoints"] = deepcopy(endpoints)
    hass.config_entries.async_update_entry(entry, options=options)
    result = await endpoint_form(
        hass, await edit_schedule(hass, entry), "color_morning_end", "seasonal"
    )
    result = await configure_schedule(
        hass, result, **initial_values(result["data_schema"])
    )
    if change_runtime:
        result = await choose(hass, result, "runtime")
        result = await configure_schedule(hass, result, update_interval_seconds=60)
    result = await save_schedule(hass, result)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["schedule"]["endpoints"] == endpoints
    if not change_runtime:
        assert entry.options == options


async def test_save_checks_later_seasons_and_endpoint_errors_can_be_repaired(
    hass, entry
):
    with patch(
        "custom_components.daylight.config_flow.dt_util.utcnow",
        return_value=datetime(2026, 6, 21, 2, tzinfo=UTC),
    ):
        result = await edit_schedule(hass, entry)
    result = await endpoint_form(hass, result, "color_morning_end", "clock")
    result = await configure_schedule(hass, result, time="06:00:00", next_day=False)
    result = await save_schedule(hass, result)
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "save_error"
    detail = result["description_placeholders"]["detail"]
    assert "color requires morning start < morning finish" in detail
    assert detail.split(":", 1)[0] > "2026-06-20"
    assert entry.options == default_hub_data()
    result = await endpoint_form(hass, result, "color_morning_end", "standard")
    result = await save_schedule(hass, result)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == default_hub_data()


async def test_reference_date_is_captured_once_and_validation_runs_only_on_save(
    hass, entry
):
    with patch(
        "custom_components.daylight.config_flow.dt_util.utcnow",
        return_value=datetime(2026, 6, 21, 2, tzinfo=UTC),
    ):
        result = await edit_schedule(hass, entry)
    loop_thread = get_ident()
    validation_threads = []
    original_validate = Schedule.validate

    def validate_in_executor(schedule, start):
        validation_threads.append(get_ident())
        return original_validate(schedule, start)

    with patch.object(
        Schedule, "validate", autospec=True, side_effect=validate_in_executor
    ) as validate:
        result = await endpoint_form(hass, result, "brightness_morning_start", "clock")
        assert result["description_placeholders"]["date"] == "2026-06-20"
        result = await configure_schedule(hass, result, time="08:00:00", next_day=False)
        # Temporarily later than the standard morning finish: repair the paired point.
        result = await endpoint_form(hass, result, "brightness_morning_end", "clock")
        result = await configure_schedule(hass, result, time="09:00:00", next_day=False)
        validate.assert_not_called()
        result = await save_schedule(hass, result)
        assert result["type"] is FlowResultType.CREATE_ENTRY
        validate.assert_called_once()
        assert validate.call_args.args[1].isoformat() == "2026-06-20"
        assert validation_threads[0] != loop_thread


async def test_transition_sections_serialize_without_nested_sections(hass, entry):
    result = await choose(hass, await edit_schedule(hass, entry), "transitions")
    fields = [
        field
        for field in schema_fields(result["data_schema"])
        if field["name"] != RETURN_WITHOUT_CHANGES
    ]
    assert {field["name"] for field in fields} == {"brightness", "color"}
    assert all(field["type"] == "expandable" and field["expanded"] for field in fields)
    for field in fields:
        assert {child["name"] for child in field["schema"]} == {
            "length",
            "morning_shape",
            "evening_shape",
        }
        assert not any(child.get("type") == "expandable" for child in field["schema"])
    json.dumps(fields)


@pytest.mark.parametrize("save_reset", [False, True])
async def test_restore_requires_confirmation(hass, entry, save_reset):
    options = deepcopy(dict(entry.options))
    options["schedule"]["endpoints"] = {
        "color_morning_start": {
            "kind": "solar",
            "reference": "sunrise",
            "offset_minutes": 5,
        }
    }
    options["schedule"]["basic"]["morning_offset_minutes"] = 10
    options["schedule"]["shapes"]["color_evening"] = "linear"
    options["update_interval_seconds"] = 60
    hass.config_entries.async_update_entry(entry, options=options)
    result = await edit_schedule(hass, entry)
    result = await choose(hass, result, "advanced")
    result = await choose(hass, result, "reset")
    assert initial_values(result["data_schema"])["confirm"] is False
    result = await configure_schedule(hass, result, confirm=False)
    result = await save_schedule(hass, result)
    assert entry.options == options
    result = await edit_schedule(hass, entry)
    result = await choose(hass, result, "advanced")
    result = await choose(hass, result, "reset")
    result = await configure_schedule(hass, result, confirm=True)
    assert entry.options["schedule"]["endpoints"]
    if not save_reset:
        hass.config_entries.options.async_abort(result["flow_id"])
        assert entry.options == options
        return
    result = await save_schedule(hass, result)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert not entry.options["schedule"]["endpoints"]
    assert entry.options["schedule"]["shapes"]["color_evening"] == "linear"
    assert entry.options["schedule"]["basic"] == default_hub_data()["schedule"]["basic"]
    assert entry.options["update_interval_seconds"] == 60
    assert next(iter(entry.subentries.values())).data == TARGET_DATA


@pytest.mark.parametrize("interval", [-1, 0, 0.5])
async def test_nonpositive_poll_interval_is_rejected(hass, entry, interval):
    result = await edit_schedule(hass, entry)
    result = await choose(hass, result, "advanced")
    result = await choose(hass, result, "runtime")
    with pytest.raises(vol.Invalid):
        RUNTIME_SCHEMA({"update_interval_seconds": interval})
    with pytest.raises(InvalidData):
        await configure_schedule(hass, result, update_interval_seconds=interval)
    assert entry.options == default_hub_data()


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


async def test_target_reconfigure_prefills_saved_values(hass, entry):
    subentry_id = next(iter(entry.subentries))
    saved = {
        **TARGET_DATA,
        "min_brightness_pct": 35,
        "max_color_temp_kelvin": 5000,
        "transition": 4.5,
        "adapt_only_on_state_change": True,
        "serialized_native_fades": True,
    }
    hass.config_entries.async_update_subentry(
        entry, entry.subentries[subentry_id], data=saved
    )
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "target"),
        context={"source": "reconfigure", "subentry_id": subentry_id},
    )
    fields = {field["name"]: field for field in schema_fields(result["data_schema"])}
    assert fields["targets"]["description"]["suggested_value"] == {
        "entity_id": ["light.kitchen"],
        "area_id": [],
    }
    for name, expected in (
        ("brightness", {"min_brightness_pct": 35, "max_brightness_pct": 100}),
        ("color_temp", {"min_color_temp_kelvin": 2500, "max_color_temp_kelvin": 5000}),
        (
            "advanced",
            {
                "transition": 4.5,
                "adapt_only_on_state_change": True,
                "serialized_native_fades": True,
            },
        ),
    ):
        assert "default" not in fields[name]
        children = {field["name"]: field for field in fields[name]["schema"]}
        for key, value in expected.items():
            assert children[key]["description"]["suggested_value"] == value

    user_input = {"targets": fields["targets"]["description"]["suggested_value"]}
    for name in ("brightness", "color_temp", "advanced"):
        user_input[name] = initial_fields(fields[name]["schema"])
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], user_input=user_input
    )
    assert result["type"] is FlowResultType.ABORT
    assert entry.subentries[subentry_id].data == saved


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
            "advanced": {
                "transition": 5.0,
                "manual_control_reset_minutes": 15,
                "serialized_native_fades": True,
            },
        },
    )
    assert result["type"] is FlowResultType.ABORT
    target = entry.subentries[subentry_id]
    assert target.title == "light.porch"
    assert target.data["transition"] == 5.0
    assert target.data["manual_control_reset_minutes"] == 15
    assert target.data["serialized_native_fades"] is True
    assert entry.options == default_hub_data()
    assert entry.data == {}


def test_target_sections_are_optional_and_collapsed():
    for key, value in TARGET_SCHEMA.schema.items():
        if isinstance(value, section):
            assert isinstance(key, vol.Optional)
    assert "brightness" not in TARGET_SCHEMA(
        {"targets": {"entity_id": ["light.kitchen"]}}
    )
    assert (
        TARGET_SCHEMA({"targets": {"entity_id": ["light.kitchen"]}, "brightness": {}})[
            "brightness"
        ]["min_brightness_pct"]
        == 10
    )
