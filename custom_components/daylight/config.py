"""Native per-target settings, shared by setup and target reconfiguration."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.data_entry_flow import section
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TargetSelector,
    TargetSelectorConfig,
)

CONF_ENTITIES = "entities"
CONF_AREAS = "areas"
CONF_TARGETS = "targets"
CONF_MIN_BRIGHTNESS_PCT = "min_brightness_pct"
CONF_MAX_BRIGHTNESS_PCT = "max_brightness_pct"
CONF_MIN_COLOR_TEMP_KELVIN = "min_color_temp_kelvin"
CONF_MAX_COLOR_TEMP_KELVIN = "max_color_temp_kelvin"
CONF_TRANSITION = "transition"
CONF_ADAPT_ONLY_ON_STATE_CHANGE = "adapt_only_on_state_change"
CONF_MANUAL_CONTROL_RESET_MINUTES = "manual_control_reset_minutes"
CONF_SEPARATE_TURN_ON_COMMANDS = "separate_turn_on_commands"
CONF_SEND_SPLIT_DELAY = "send_split_delay"
CONF_SERIALIZED_NATIVE_FADES = "serialized_native_fades"
SECTION_BRIGHTNESS = "brightness"
SECTION_COLOR_TEMP = "color_temp"
SECTION_ADVANCED = "advanced"


def _number(
    *,
    minimum: float = 0,
    maximum: float | None = None,
    integer: bool = False,
    slider: bool = False,
) -> vol.All:
    config = NumberSelectorConfig(
        min=minimum,
        mode=NumberSelectorMode.SLIDER if slider else NumberSelectorMode.BOX,
    )
    if maximum is not None:
        config["max"] = maximum
    if integer:
        config["step"] = 1
    return vol.All(NumberSelector(config), vol.Coerce(int if integer else float))


def _section_fields(schema: vol.Schema) -> dict[str, tuple[str, ...]]:
    return {
        str(key): tuple(str(inner) for inner in value.schema.schema)
        for key, value in schema.schema.items()
        if isinstance(value, section)
    }


def flatten_sections(schema: vol.Schema, data: Mapping[str, Any]) -> dict[str, Any]:
    sections = _section_fields(schema)
    flat = {}
    for key, value in data.items():
        if key in sections:
            flat.update(value)
        else:
            flat[key] = value
    return flat


def nest_sections(schema: vol.Schema, data: Mapping[str, Any]) -> dict[str, Any]:
    sections = _section_fields(schema)
    fields = {field for names in sections.values() for field in names}
    nested: dict[str, Any] = {
        name: {field: data[field] for field in names if field in data}
        for name, names in sections.items()
    }
    nested.update({key: value for key, value in data.items() if key not in fields})
    return nested


TARGET_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_TARGETS): TargetSelector(
            TargetSelectorConfig(entity={"domain": "light"})
        ),
        vol.Optional(SECTION_BRIGHTNESS): section(
            vol.Schema(
                {
                    vol.Optional(CONF_MIN_BRIGHTNESS_PCT, default=10): _number(
                        minimum=1, maximum=100, integer=True, slider=True
                    ),
                    vol.Optional(CONF_MAX_BRIGHTNESS_PCT, default=100): _number(
                        minimum=1, maximum=100, integer=True, slider=True
                    ),
                }
            ),
            {"collapsed": True},
        ),
        vol.Optional(SECTION_COLOR_TEMP): section(
            vol.Schema(
                {
                    vol.Optional(CONF_MIN_COLOR_TEMP_KELVIN, default=2500): _number(
                        minimum=1000, maximum=10000, integer=True
                    ),
                    vol.Optional(CONF_MAX_COLOR_TEMP_KELVIN, default=4000): _number(
                        minimum=1000, maximum=10000, integer=True
                    ),
                }
            ),
            {"collapsed": True},
        ),
        vol.Optional(SECTION_ADVANCED): section(
            vol.Schema(
                {
                    vol.Optional(CONF_TRANSITION, default=0.0): _number(),
                    vol.Optional(CONF_ADAPT_ONLY_ON_STATE_CHANGE, default=False): bool,
                    vol.Optional(CONF_MANUAL_CONTROL_RESET_MINUTES, default=0): _number(
                        integer=True
                    ),
                    vol.Optional(CONF_SEPARATE_TURN_ON_COMMANDS, default=False): bool,
                    vol.Optional(CONF_SEND_SPLIT_DELAY, default=0.0): _number(),
                    vol.Optional(CONF_SERIALIZED_NATIVE_FADES, default=False): bool,
                }
            ),
            {"collapsed": True},
        ),
    }
)
