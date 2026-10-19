"""Native form schemas and storage for the shared daily schedule."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict
from datetime import time
from typing import Any

import astral
import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import section
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    TimeSelector,
)
from homeassistant.util import dt as dt_util

from .schedule import ENDPOINTS, BasicTiming, Endpoint, Schedule
from .solar import SunEvents

RETURN_WITHOUT_CHANGES = "return_without_changes"


def default_hub_data() -> dict[str, Any]:
    return {
        "schedule": {"basic": asdict(BasicTiming()), "endpoints": {}, "shapes": {}},
        "update_interval_seconds": 90,
    }


def choices(options: list[str], key: str) -> SelectSelector:
    return SelectSelector(SelectSelectorConfig(options=options, translation_key=key))


def number(
    minimum: float, maximum: float, step: float = 1, *, slider: bool = False
) -> NumberSelector:
    return NumberSelector(
        NumberSelectorConfig(
            min=minimum,
            max=maximum,
            step=step,
            mode=NumberSelectorMode.SLIDER if slider else NumberSelectorMode.BOX,
        )
    )


TIMING_MODES_SCHEMA = vol.Schema(
    {
        vol.Required(f"{period}_mode"): choices(["solar", "clock"], "timing_mode")
        for period in ("morning", "evening")
    }
)
TRACK_TRANSITIONS_SCHEMA = vol.Schema(
    {
        vol.Required("length"): number(0.25, 3, 0.25, slider=True),
        vol.Required("morning_shape"): choices(["smooth", "linear"], "shape"),
        vol.Required("evening_shape"): choices(["smooth", "linear"], "shape"),
    }
)
TRANSITIONS_SCHEMA = vol.Schema(
    {
        vol.Required(track): section(TRACK_TRANSITIONS_SCHEMA, {"collapsed": False})
        for track in ("brightness", "color")
    }
)
RUNTIME_SCHEMA = vol.Schema(
    {
        vol.Required("update_interval_seconds"): vol.All(
            number(1, 3600), vol.Coerce(int)
        ),
    }
)


ENDPOINT_SELECT_SCHEMA = vol.Schema(
    {
        vol.Required("endpoint"): choices(list(ENDPOINTS), "endpoint"),
    }
)
ENDPOINT_KIND_SCHEMA = vol.Schema(
    {
        vol.Required("kind"): choices(
            ["standard", "seasonal", "solar", "clock"], "endpoint_kind"
        ),
    }
)
RESET_SCHEMA = vol.Schema({vol.Required("confirm", default=False): BooleanSelector()})


class EditorSchema(vol.Schema):
    """Keep native form serialization, but discard returned pages before validation."""

    def __call__(self, data: Any) -> Any:
        if isinstance(data, dict) and data.get(RETURN_WITHOUT_CHANGES) is True:
            return {RETURN_WITHOUT_CHANGES: True}
        return super().__call__(data)


def editor_schema(schema: vol.Schema, *, navigation: bool = True) -> vol.Schema:
    """Keep native selectors but allow cleared fields when returning from a page.

    Native controls still check nonblank invalid values in the frontend. Users
    can clear optional fields to return; applying uses the original strict schema.
    """
    fields: dict[Any, Any] = {}
    for key, validator in schema.schema.items():
        description = dict(key.description or {})
        if isinstance(validator, section):
            display = section(
                editor_schema(validator.schema, navigation=False), validator.options
            )
        else:
            display = vol.Any(validator, None, "")
        fields[vol.Optional(str(key), description=description)] = display
    if navigation:
        fields[vol.Optional(RETURN_WITHOUT_CHANGES, default=False)] = BooleanSelector()
        return EditorSchema(fields)
    return vol.Schema(fields)


def daily_timing_schema(modes: dict[str, str]) -> vol.Schema:
    """Show only the fields applicable to each selected timing mode."""
    fields: dict[Any, Any] = {}
    for period in ("morning", "evening"):
        if modes[f"{period}_mode"] == "solar":
            fields[vol.Required(f"{period}_offset_minutes")] = number(-720, 720)
        else:
            fields[vol.Required(f"{period}_time")] = TimeSelector()
            if period == "evening":
                fields[vol.Required("evening_next_day", default=False)] = (
                    BooleanSelector()
                )
    return vol.Schema(fields)


def endpoint_timing_schema(kind: str) -> vol.Schema:
    """Clock and sun-based rules share one editor without irrelevant controls."""
    fields: dict[Any, Any]
    if kind == "clock":
        fields = {vol.Required("time"): TimeSelector()}
    else:
        fields = {
            vol.Required("reference"): choices(
                ["sunrise", "sunset", "noon", "midnight"], "solar_reference"
            ),
            vol.Required("offset_minutes"): number(-1440, 1440, 0.1),
        }
    fields[vol.Required("next_day", default=False)] = BooleanSelector()
    return vol.Schema(fields)


async def async_schedule(hass: HomeAssistant, data: Mapping[str, Any]) -> Schedule:
    """Decode saved rules once; runtime, forms and preview use the same engine."""
    timezone = await dt_util.async_get_time_zone(hass.config.time_zone)
    if timezone is None:
        raise ValueError(f"Invalid Home Assistant timezone: {hass.config.time_zone}")
    stored = data["schedule"]
    basic = dict(stored["basic"])
    for key in ("morning_time", "evening_time"):
        value = basic.get(key)
        basic[key] = time.fromisoformat(value) if value else None
    endpoints = {}
    for key, value in stored["endpoints"].items():
        rule = dict(value)
        rule["clock"] = time.fromisoformat(rule.get("clock", "00:00:00"))
        endpoints[key] = Endpoint(**rule)
    return Schedule(
        SunEvents(
            astral.Observer(
                hass.config.latitude,
                hass.config.longitude,
                hass.config.elevation,
            ),
            timezone,
        ),
        BasicTiming(**basic),
        endpoints,
        dict(stored["shapes"]),
    )
