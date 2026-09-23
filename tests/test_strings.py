"""Pin the config flow UI strings to the actual voluptuous schemas.

Reads the real `HUB_SCHEMA`/`TARGET_SCHEMA` objects rather than a hardcoded
list of field names, so a field rename in `config_flow.py` without a matching
string update fails this test.
"""

import json
from pathlib import Path

import voluptuous as vol

from custom_components.daylight.config_flow import HUB_SCHEMA, TARGET_SCHEMA

_COMPONENT_DIR = Path(__file__).parent.parent / "custom_components" / "daylight"
_STRINGS_PATH = _COMPONENT_DIR / "strings.json"
_TRANSLATIONS_PATH = _COMPONENT_DIR / "translations" / "en.json"


def _schema_keys(schema: vol.Schema) -> list[str]:
    return [str(key) for key in schema.schema]


def test_hub_schema_fields_have_strings() -> None:
    strings = json.loads(_STRINGS_PATH.read_text())
    data = strings["config"]["step"]["user"]["data"]

    for key in _schema_keys(HUB_SCHEMA):
        assert key in data, f"missing config.step.user.data.{key}"


def test_target_schema_fields_have_strings() -> None:
    strings = json.loads(_STRINGS_PATH.read_text())
    data = strings["config_subentries"]["target"]["step"]["init"]["data"]

    for key in _schema_keys(TARGET_SCHEMA):
        assert key in data, f"missing config_subentries.target.step.init.data.{key}"


def test_target_errors_have_strings() -> None:
    strings = json.loads(_STRINGS_PATH.read_text())
    errors = strings["config_subentries"]["target"]["error"]

    assert "brightness_range_invalid" in errors
    assert "color_temp_range_invalid" in errors


def test_translations_en_matches_strings_byte_for_byte() -> None:
    assert _TRANSLATIONS_PATH.read_bytes() == _STRINGS_PATH.read_bytes()
