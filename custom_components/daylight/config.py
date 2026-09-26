"""Shared configuration schemas and conversion to runtime curve settings."""

from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any

import astral
import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import section
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    TargetSelector,
    TargetSelectorConfig,
    TimeSelector,
)
from homeassistant.util import dt as dt_util

from .color_and_brightness import CurveSettings

# Hub schema keys.
CONF_SUNRISE_TIME = "sunrise_time"
CONF_MIN_SUNRISE_TIME = "min_sunrise_time"
CONF_MAX_SUNRISE_TIME = "max_sunrise_time"
CONF_SUNSET_TIME = "sunset_time"
CONF_MIN_SUNSET_TIME = "min_sunset_time"
CONF_MAX_SUNSET_TIME = "max_sunset_time"
CONF_SUNRISE_OFFSET_MINUTES = "sunrise_offset_minutes"
CONF_SUNSET_OFFSET_MINUTES = "sunset_offset_minutes"
CONF_BRIGHTNESS_MODE = "brightness_mode"
CONF_BRIGHTNESS_MODE_TIME_DARK_MINUTES = "brightness_mode_time_dark_minutes"
CONF_BRIGHTNESS_MODE_TIME_LIGHT_MINUTES = "brightness_mode_time_light_minutes"
CONF_UPDATE_INTERVAL_SECONDS = "update_interval_seconds"

SECTION_SUNRISE = "sunrise"
SECTION_SUNSET = "sunset"
SECTION_BRIGHTNESS_CURVE = "brightness_curve"

_OPTIONAL_TIME_KEYS = (
    CONF_SUNRISE_TIME,
    CONF_MIN_SUNRISE_TIME,
    CONF_MAX_SUNRISE_TIME,
    CONF_SUNSET_TIME,
    CONF_MIN_SUNSET_TIME,
    CONF_MAX_SUNSET_TIME,
)

# Arbitrary but documented default: how far before/after a sun event the
# linear/tanh brightness curves start/finish ramping, in minutes. Only
# meaningful when `brightness_mode` is "linear" or "tanh", but `CurveSettings`
# always requires a value, so the form always collects it.
_DEFAULT_BRIGHTNESS_MODE_TIME_MINUTES = 45
_DEFAULT_UPDATE_INTERVAL_SECONDS = 90

_BRIGHTNESS_MODE_OPTIONS = ["default", "linear", "tanh"]


def _int_box(*, minimum: int | None = None) -> vol.All:
    """An integer box selector with an optional lower bound."""
    config = NumberSelectorConfig(mode=NumberSelectorMode.BOX)
    if minimum is not None:
        config["min"] = minimum
    return vol.All(NumberSelector(config), vol.Coerce(int))


def _section_fields(schema: vol.Schema) -> dict[str, tuple[str, ...]]:
    """Map each section name in `schema` to the field names it holds.

    Read off the real schema rather than a hand-kept table, so re-grouping a
    field cannot desynchronize the flatten/nest pair from the form.
    """
    return {
        str(key): tuple(str(inner) for inner in value.schema.schema)
        for key, value in schema.schema.items()
        if isinstance(value, section)
    }


def flatten_sections(
    schema: vol.Schema, user_input: Mapping[str, Any]
) -> dict[str, Any]:
    """Collapse a section-shaped submission into the flat dict that gets stored."""
    sections = _section_fields(schema)
    flat: dict[str, Any] = {}
    for key, value in user_input.items():
        if key in sections:
            flat.update(value)
        else:
            flat[key] = value
    return flat


def nest_sections(schema: vol.Schema, data: Mapping[str, Any]) -> dict[str, Any]:
    """Re-shape flat stored `data` into `schema`'s section layout.

    `add_suggested_values_to_schema` only descends into a section when the
    suggested values are already nested under that section's name, so flat
    stored data has to be put back into this shape to repopulate a form.
    """
    sections = _section_fields(schema)
    sectioned = {field for fields in sections.values() for field in fields}
    nested: dict[str, Any] = {
        name: {field: data[field] for field in fields if field in data}
        for name, fields in sections.items()
    }
    nested.update({key: value for key, value in data.items() if key not in sectioned})
    return nested


# Each section's own marker matches the strength of its contents: every hub
# field is optional, so an untouched section resolves to its defaults instead
# of failing validation. `default=dict` is what makes those inner defaults
# fire -- with a bare `vol.Optional(name)` an absent section is simply left
# out of the validated result, and `__init__.py` reads every hub key
# unconditionally.
CURVE_FIELDS = {
    vol.Optional(SECTION_SUNRISE, default=dict): section(
        vol.Schema(
            {
                vol.Optional(CONF_SUNRISE_TIME): TimeSelector(),
                vol.Optional(CONF_MIN_SUNRISE_TIME): TimeSelector(),
                vol.Optional(CONF_MAX_SUNRISE_TIME): TimeSelector(),
                vol.Optional(CONF_SUNRISE_OFFSET_MINUTES, default=0): _int_box(),
            }
        )
    ),
    vol.Optional(SECTION_SUNSET, default=dict): section(
        vol.Schema(
            {
                vol.Optional(CONF_SUNSET_TIME): TimeSelector(),
                vol.Optional(CONF_MIN_SUNSET_TIME): TimeSelector(),
                vol.Optional(CONF_MAX_SUNSET_TIME): TimeSelector(),
                vol.Optional(CONF_SUNSET_OFFSET_MINUTES, default=0): _int_box(),
            }
        )
    ),
    vol.Optional(SECTION_BRIGHTNESS_CURVE, default=dict): section(
        vol.Schema(
            {
                vol.Optional(CONF_BRIGHTNESS_MODE, default="default"): SelectSelector(
                    SelectSelectorConfig(
                        options=_BRIGHTNESS_MODE_OPTIONS,
                        translation_key=CONF_BRIGHTNESS_MODE,
                    )
                ),
                vol.Optional(
                    CONF_BRIGHTNESS_MODE_TIME_DARK_MINUTES,
                    default=_DEFAULT_BRIGHTNESS_MODE_TIME_MINUTES,
                ): _int_box(),
                vol.Optional(
                    CONF_BRIGHTNESS_MODE_TIME_LIGHT_MINUTES,
                    default=_DEFAULT_BRIGHTNESS_MODE_TIME_MINUTES,
                ): _int_box(),
            }
        )
    ),
}

HUB_SCHEMA = vol.Schema(
    {
        **CURVE_FIELDS,
        vol.Optional(
            CONF_UPDATE_INTERVAL_SECONDS, default=_DEFAULT_UPDATE_INTERVAL_SECONDS
        ): _int_box(minimum=1),
    }
)


def normalize_hub_input(user_input: dict[str, Any]) -> dict[str, Any]:
    """Build the exact dict stored in the hub entry's `data`.

    The submission arrives nested by section; the stored dict is flat. Every
    key is always present, so a missing optional time override is stored as an
    explicit `None` rather than simply being absent.
    """
    data = flatten_sections(HUB_SCHEMA, user_input)
    for key in _OPTIONAL_TIME_KEYS:
        data.setdefault(key, None)
    return data


# Target subentry schema keys.
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

SECTION_BRIGHTNESS = "brightness"
SECTION_COLOR_TEMP = "color_temp"
SECTION_ADVANCED = "advanced"

_DEFAULT_MANUAL_CONTROL_RESET_MINUTES = 0
_DEFAULT_TRANSITION = 0.0
_DEFAULT_ADAPT_ONLY_ON_STATE_CHANGE = False
_DEFAULT_SEPARATE_TURN_ON_COMMANDS = False
_DEFAULT_SEND_SPLIT_DELAY = 0.0

# All target settings have useful defaults; selecting lights is sufficient.
RANGE_FIELDS = {
    vol.Optional(SECTION_BRIGHTNESS, default=dict): section(
        vol.Schema(
            {
                vol.Optional(CONF_MIN_BRIGHTNESS_PCT, default=10): vol.All(
                    NumberSelector(
                        NumberSelectorConfig(
                            min=1, max=100, mode=NumberSelectorMode.BOX
                        )
                    ),
                    vol.Coerce(int),
                ),
                vol.Optional(CONF_MAX_BRIGHTNESS_PCT, default=100): vol.All(
                    NumberSelector(
                        NumberSelectorConfig(
                            min=1, max=100, mode=NumberSelectorMode.BOX
                        )
                    ),
                    vol.Coerce(int),
                ),
            }
        ),
        {"collapsed": True},
    ),
    vol.Optional(SECTION_COLOR_TEMP, default=dict): section(
        vol.Schema(
            {
                vol.Optional(CONF_MIN_COLOR_TEMP_KELVIN, default=2500): _int_box(
                    minimum=1
                ),
                vol.Optional(CONF_MAX_COLOR_TEMP_KELVIN, default=4000): _int_box(
                    minimum=1
                ),
            }
        ),
        {"collapsed": True},
    ),
}

TARGET_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_TARGETS): TargetSelector(
            TargetSelectorConfig(entity={"domain": "light"})
        ),
        **RANGE_FIELDS,
        vol.Optional(SECTION_ADVANCED, default=dict): section(
            vol.Schema(
                {
                    vol.Optional(CONF_TRANSITION, default=_DEFAULT_TRANSITION): vol.All(
                        NumberSelector(
                            NumberSelectorConfig(mode=NumberSelectorMode.BOX)
                        ),
                        vol.Coerce(float),
                    ),
                    vol.Optional(
                        CONF_ADAPT_ONLY_ON_STATE_CHANGE,
                        default=_DEFAULT_ADAPT_ONLY_ON_STATE_CHANGE,
                    ): bool,
                    vol.Optional(
                        CONF_MANUAL_CONTROL_RESET_MINUTES,
                        default=_DEFAULT_MANUAL_CONTROL_RESET_MINUTES,
                    ): _int_box(),
                    vol.Optional(
                        CONF_SEPARATE_TURN_ON_COMMANDS,
                        default=_DEFAULT_SEPARATE_TURN_ON_COMMANDS,
                    ): bool,
                    vol.Optional(
                        CONF_SEND_SPLIT_DELAY, default=_DEFAULT_SEND_SPLIT_DELAY
                    ): vol.All(
                        NumberSelector(
                            NumberSelectorConfig(mode=NumberSelectorMode.BOX)
                        ),
                        vol.Coerce(float),
                    ),
                }
            ),
            {"collapsed": True},
        ),
    }
)


def _parse_time(value: str | None) -> datetime.time | None:
    """Parse a stored or validated time override."""
    return None if value is None else datetime.time.fromisoformat(value)


async def async_curve_settings(
    hass: HomeAssistant, data: Mapping[str, Any], *, name: str
) -> CurveSettings:
    """Build curve settings from flat configuration and the HA location."""
    timezone = await dt_util.async_get_time_zone(hass.config.time_zone)
    if timezone is None:
        raise ValueError(f"Invalid Home Assistant time zone: {hass.config.time_zone}")

    return CurveSettings(
        name=name,
        astral_observer=astral.Observer(
            latitude=hass.config.latitude,
            longitude=hass.config.longitude,
            elevation=hass.config.elevation,
        ),
        timezone=timezone,
        sunrise_time=_parse_time(data.get("sunrise_time")),
        min_sunrise_time=_parse_time(data.get("min_sunrise_time")),
        max_sunrise_time=_parse_time(data.get("max_sunrise_time")),
        sunset_time=_parse_time(data.get("sunset_time")),
        min_sunset_time=_parse_time(data.get("min_sunset_time")),
        max_sunset_time=_parse_time(data.get("max_sunset_time")),
        sunrise_offset=datetime.timedelta(minutes=data["sunrise_offset_minutes"]),
        sunset_offset=datetime.timedelta(minutes=data["sunset_offset_minutes"]),
        brightness_mode=data["brightness_mode"],
        brightness_mode_time_dark=datetime.timedelta(
            minutes=data["brightness_mode_time_dark_minutes"]
        ),
        brightness_mode_time_light=datetime.timedelta(
            minutes=data["brightness_mode_time_light_minutes"]
        ),
    )
