"""Pin the config flow UI strings to the actual voluptuous schemas.

Reads the actual native form schemas rather than a hardcoded
list of field names, so a field rename in `config_flow.py` without a matching
string update fails this test.
"""

import json
from pathlib import Path
from typing import Any

import pytest
import voluptuous as vol
from homeassistant.data_entry_flow import section

from custom_components.daylight.config import TARGET_SCHEMA
from custom_components.daylight.schedule import ENDPOINTS
from custom_components.daylight.schedule_config import (
    ENDPOINT_KIND_SCHEMA,
    ENDPOINT_SELECT_SCHEMA,
    RESET_SCHEMA,
    RUNTIME_SCHEMA,
    TIMING_MODES_SCHEMA,
    TRANSITIONS_SCHEMA,
    daily_timing_schema,
    editor_schema,
    endpoint_timing_schema,
)

_COMPONENT_DIR = Path(__file__).parent.parent / "custom_components" / "daylight"
_STRINGS_PATH = _COMPONENT_DIR / "strings.json"
_TRANSLATIONS_PATH = _COMPONENT_DIR / "translations" / "en.json"


def _assert_step_has_strings(
    schema: vol.Schema, step: dict[str, Any], path: str
) -> None:
    """Assert `step` carries a string for every field/section in `schema`.

    Plain fields live at `<path>.data.<key>`; a section contributes its own
    header at `<path>.sections.<name>.name` plus one entry per inner field at
    `<path>.sections.<name>.data.<inner>`.
    """
    for key, value in schema.schema.items():
        name = str(key)
        if isinstance(value, section):
            sections = step.get("sections", {})
            assert name in sections, f"missing {path}.sections.{name}"
            assert "name" in sections[name], f"missing {path}.sections.{name}.name"
            for inner in value.schema.schema:
                assert str(inner) in sections[name]["data"], (
                    f"missing {path}.sections.{name}.data.{inner}"
                )
        else:
            assert name in step["data"], f"missing {path}.data.{name}"


def test_hub_schema_fields_have_strings() -> None:
    strings = json.loads(_STRINGS_PATH.read_text())

    _assert_step_has_strings(
        TARGET_SCHEMA, strings["config"]["step"]["user"], "config.step.user"
    )


@pytest.mark.parametrize(
    "step_id,schema",
    [
        ("daily_timing", TIMING_MODES_SCHEMA),
        ("transitions", TRANSITIONS_SCHEMA),
        ("endpoint", ENDPOINT_SELECT_SCHEMA),
        ("endpoint_rule", ENDPOINT_KIND_SCHEMA),
        ("runtime", RUNTIME_SCHEMA),
        ("reset", RESET_SCHEMA),
        *[
            (
                "daily_details",
                daily_timing_schema({"morning_mode": morning, "evening_mode": evening}),
            )
            for morning in ("solar", "clock")
            for evening in ("solar", "clock")
        ],
        *[
            ("endpoint_details", endpoint_timing_schema(kind))
            for kind in ("clock", "solar", "seasonal")
        ],
    ],
)
def test_schedule_forms_have_strings(step_id, schema) -> None:
    steps = json.loads(_STRINGS_PATH.read_text())["options"]["step"]
    _assert_step_has_strings(
        editor_schema(schema), steps[step_id], f"options.step.{step_id}"
    )
    assert steps[step_id]["data"]["return_without_changes"] == (
        "Return without changing this page"
    )
    assert steps[step_id]["data_description"]["return_without_changes"] == (
        "To return, clear any invalid fields, select Return without changing "
        "this page, then Next. Earlier draft changes are kept."
    )


@pytest.mark.parametrize(
    "step_id,choices",
    [
        ("init", {"daily_timing", "transitions", "advanced", "save"}),
        ("advanced", {"endpoint", "runtime", "reset", "init"}),
        ("save_error", {"daily_timing", "transitions", "advanced", "save"}),
    ],
)
def test_schedule_menus_have_strings(step_id, choices) -> None:
    steps = json.loads(_STRINGS_PATH.read_text())["options"]["step"]
    assert set(steps[step_id]["menu_options"]) == choices


def test_schedule_selectors_and_guidance_have_strings() -> None:
    strings = json.loads(_STRINGS_PATH.read_text())
    assert set(strings["selector"]["endpoint"]["options"]) == set(ENDPOINTS)
    steps = strings["options"]["step"]
    assert "{date}" in steps["endpoint_details"]["description"]
    assert "{detail}" in steps["save_error"]["description"]
    assert "Save changes" in steps["init"]["description"]
    assert "draft" in steps["reset"]["description"]
    assert "reconfigure" not in strings["config"]["step"]
    assert {"page_invalid", "time_required"} <= strings["options"]["error"].keys()


def test_target_schema_fields_have_strings() -> None:
    strings = json.loads(_STRINGS_PATH.read_text())

    _assert_step_has_strings(
        TARGET_SCHEMA,
        strings["config_subentries"]["target"]["step"]["init"],
        "config_subentries.target.step.init",
    )


def test_target_errors_have_strings() -> None:
    strings = json.loads(_STRINGS_PATH.read_text())
    errors = strings["config_subentries"]["target"]["error"]

    assert "brightness_range_invalid" in errors
    assert "color_temp_range_invalid" in errors


def test_target_save_message_is_renderable() -> None:
    strings = json.loads(_STRINGS_PATH.read_text())
    message = strings["config_subentries"]["target"]["abort"]["reconfigure_successful"]
    assert message == "Target saved."


def test_translations_en_matches_strings_byte_for_byte() -> None:
    assert _TRANSLATIONS_PATH.read_bytes() == _STRINGS_PATH.read_bytes()
