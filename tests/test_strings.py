"""Pin the config flow UI strings to the actual voluptuous schemas.

Reads the actual native form schemas rather than a hardcoded
list of field names, so a field rename in `config_flow.py` without a matching
string update fails this test.
"""

import json
from pathlib import Path
from typing import Any

import voluptuous as vol
from homeassistant.data_entry_flow import section

from custom_components.daylight.config import TARGET_SCHEMA
from custom_components.daylight.schedule_config import SCHEDULE_SCHEMA

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


def test_schedule_form_has_strings() -> None:
    step = json.loads(_STRINGS_PATH.read_text())["config"]["step"]["reconfigure"]
    _assert_step_has_strings(SCHEDULE_SCHEMA, step, "config.step.reconfigure")


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
