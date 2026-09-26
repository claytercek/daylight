"""Native form schemas and storage for the shared daily schedule."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict
from datetime import time
from typing import Any

import astral
import voluptuous as vol
from homeassistant.core import HomeAssistant
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

from .schedule import BasicTiming, Endpoint, Schedule
from .solar import SunEvents


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


LENGTH_SCHEMA = vol.Schema(
    {
        vol.Required("brightness_length", default=1): number(
            0.25, 3, 0.25, slider=True
        ),
        vol.Required("color_length", default=1): number(0.25, 3, 0.25, slider=True),
    }
)
SHAPE_SCHEMA = vol.Schema(
    {
        vol.Required(f"{track}_{period}", default="smooth"): choices(
            ["smooth", "linear"], "shape"
        )
        for track in ("brightness", "color")
        for period in ("morning", "evening")
    }
)
RUNTIME_SCHEMA = vol.Schema(
    {
        vol.Required("update_interval_seconds", default=90): vol.All(
            number(1, 3600), vol.Coerce(int)
        ),
    }
)
MODE_SCHEMA = vol.Schema(
    {
        vol.Required("mode", default="solar"): choices(
            ["solar", "clock"], "timing_mode"
        ),
    }
)


def anchor_schema(period: str, mode: str) -> vol.Schema:
    if mode == "solar":
        return vol.Schema(
            {vol.Required("offset_minutes", default=0): number(-720, 720)}
        )
    fields: dict[Any, Any] = {vol.Required("time"): TimeSelector()}
    if period == "evening":
        fields[vol.Required("next_day", default=False)] = BooleanSelector()
    return vol.Schema(fields)


def endpoint_schema(kind: str) -> vol.Schema:
    if kind == "clock":
        return vol.Schema(
            {
                vol.Required("time"): TimeSelector(),
                vol.Required("next_day", default=False): BooleanSelector(),
            }
        )
    return vol.Schema(
        {
            vol.Required("reference", default="sunrise"): choices(
                ["sunrise", "sunset", "noon", "midnight"],
                "solar_reference",
            ),
            vol.Required("offset_minutes", default=0): number(-1440, 1440, 0.1),
            vol.Required("next_day", default=False): BooleanSelector(),
        }
    )


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
