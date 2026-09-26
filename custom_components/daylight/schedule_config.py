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


def _timing_schema(*, evening: bool) -> vol.Schema:
    fields: dict[Any, Any] = {
        vol.Required("mode", default="solar"): choices(
            ["solar", "clock"], "timing_mode"
        ),
        vol.Required("offset_minutes", default=0): number(-720, 720),
        vol.Optional("time"): vol.Any(TimeSelector(), "", None),
    }
    if evening:
        fields[vol.Required("next_day", default=False)] = BooleanSelector()
    return vol.Schema(fields)


def _endpoint_schema(point: str) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required("kind", default="standard"): choices(
                ["standard", "seasonal", "solar", "clock"], "endpoint_kind"
            ),
            vol.Required(
                "reference", default="sunset" if "evening" in point else "sunrise"
            ): choices(["sunrise", "sunset", "noon", "midnight"], "solar_reference"),
            vol.Required("offset_minutes", default=0): number(-1440, 1440, 0.1),
            vol.Optional("time"): vol.Any(TimeSelector(), "", None),
            vol.Required("next_day", default=False): BooleanSelector(),
        }
    )


SCHEDULE_SCHEMA = vol.Schema(
    {
        vol.Optional("morning"): section(
            _timing_schema(evening=False), {"collapsed": True}
        ),
        vol.Optional("evening"): section(
            _timing_schema(evening=True), {"collapsed": True}
        ),
        vol.Optional("lengths"): section(LENGTH_SCHEMA, {"collapsed": True}),
        vol.Optional("shapes"): section(SHAPE_SCHEMA, {"collapsed": True}),
        vol.Optional("runtime"): section(RUNTIME_SCHEMA, {"collapsed": True}),
        **{
            vol.Optional(point): section(_endpoint_schema(point), {"collapsed": True})
            for point in ENDPOINTS
        },
        vol.Optional("restore"): section(
            vol.Schema({vol.Required("confirm", default=False): BooleanSelector()}),
            {"collapsed": True},
        ),
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
