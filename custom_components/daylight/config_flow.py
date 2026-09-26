"""Native settings for shared schedule and per-target lights."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    SubentryFlowResult,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import area_registry
from homeassistant.util import dt as dt_util

from .config import (
    CONF_AREAS,
    CONF_ENTITIES,
    CONF_MAX_BRIGHTNESS_PCT,
    CONF_MAX_COLOR_TEMP_KELVIN,
    CONF_MIN_BRIGHTNESS_PCT,
    CONF_MIN_COLOR_TEMP_KELVIN,
    CONF_TARGETS,
    TARGET_SCHEMA,
    flatten_sections,
    nest_sections,
)
from .const import DOMAIN
from .schedule import ENDPOINTS, BasicTiming, ScheduleError
from .schedule_config import SCHEDULE_SCHEMA, async_schedule, default_hub_data

HUB_TITLE = "Daylight"


def _target_input(
    hass: HomeAssistant, user_input: dict[str, Any]
) -> tuple[str, dict[str, Any]]:
    """Normalize and validate the same target form in setup and subentry flows."""
    # Supply omitted sections for validation without overriding saved form values.
    data = flatten_sections(
        TARGET_SCHEMA, TARGET_SCHEMA(nest_sections(TARGET_SCHEMA, {}) | user_input)
    )
    targets = data.pop(CONF_TARGETS)
    if targets.get("device_id") or any(
        not entity.startswith("light.") for entity in targets.get("entity_id", [])
    ):
        raise ValueError("unsupported_target")
    if not targets.get("entity_id") and not targets.get("area_id"):
        raise ValueError("target_required")
    if data[CONF_MIN_BRIGHTNESS_PCT] > data[CONF_MAX_BRIGHTNESS_PCT]:
        raise ValueError("brightness_range_invalid")
    if data[CONF_MIN_COLOR_TEMP_KELVIN] > data[CONF_MAX_COLOR_TEMP_KELVIN]:
        raise ValueError("color_temp_range_invalid")
    data[CONF_ENTITIES] = targets.get("entity_id", [])
    data[CONF_AREAS] = targets.get("area_id", [])
    registry = area_registry.async_get(hass)
    title = ", ".join(
        [
            area.name if (area := registry.async_get_area(key)) else key
            for key in data[CONF_AREAS]
        ]
        + data[CONF_ENTITIES]
    )
    return title, data


class DaylightConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up lights and save the shared schedule in a single edit form."""

    VERSION = 2

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors = {}
        if user_input is not None:
            try:
                title, target = _target_input(self.hass, user_input)
            except ValueError as err:
                errors["base"] = str(err)
            else:
                return self.async_create_entry(
                    title=HUB_TITLE,
                    data=default_hub_data(),
                    subentries=[
                        {
                            "title": title,
                            "data": target,
                            "subentry_type": "target",
                            "unique_id": None,
                        }
                    ],
                )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(TARGET_SCHEMA, user_input),
            errors=errors,
        )

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        return {"target": TargetSubentryFlowHandler}

    def _schedule_values(self, data: dict[str, Any]) -> dict[str, Any]:
        basic = data["schedule"]["basic"]
        values: dict[str, Any] = {
            period: {
                "mode": "clock" if basic[f"{period}_time"] else "solar",
                "offset_minutes": basic[f"{period}_offset_minutes"],
                **(
                    {"time": basic[f"{period}_time"]} if basic[f"{period}_time"] else {}
                ),
                **(
                    {"next_day": basic["evening_next_day"]}
                    if period == "evening"
                    else {}
                ),
            }
            for period in ("morning", "evening")
        }
        values["lengths"] = {
            key: basic[key] for key in ("brightness_length", "color_length")
        }
        values["shapes"] = data["schedule"]["shapes"]
        values["runtime"] = {"update_interval_seconds": data["update_interval_seconds"]}
        for point in ENDPOINTS:
            rule = data["schedule"]["endpoints"].get(point, {})
            offset = rule.get("offset_minutes", 0)
            if rule.get("kind") == "seasonal":
                day = self._edit_day + timedelta(days=int(rule.get("next_day", False)))
                offset *= self._sun.day(day).daylight / (12 * 3600)
            values[point] = {
                "kind": rule.get("kind", "standard"),
                "reference": rule.get(
                    "reference", "sunset" if "evening" in point else "sunrise"
                ),
                "offset_minutes": round(offset, 1),
                **({"time": rule["clock"]} if rule.get("clock") else {}),
                "next_day": rule.get("next_day", False),
            }
        values["restore"] = {"confirm": False}
        return values

    def _apply_schedule(self, data: dict[str, Any], values: dict[str, Any]) -> None:
        stored = data["schedule"]
        if values.get("restore", {}).get("confirm"):
            stored["basic"] = asdict(BasicTiming())
            stored["endpoints"] = {}
        else:
            basic = stored["basic"]
            for period in ("morning", "evening"):
                if timing := values.get(period):
                    mode = timing["mode"]
                    if mode == "clock" and not timing.get("time"):
                        raise ValueError(f"Set a local time for {period} timing.")
                    basic[f"{period}_time"] = (
                        timing.get("time") if mode == "clock" else None
                    )
                    basic[f"{period}_offset_minutes"] = (
                        timing["offset_minutes"] if mode == "solar" else 0
                    )
                    if period == "evening":
                        basic["evening_next_day"] = (
                            timing.get("next_day", False) if mode == "clock" else False
                        )
            basic.update(values.get("lengths", {}))
            for point in ENDPOINTS:
                if not (input_rule := values.get(point)):
                    continue
                kind = input_rule["kind"]
                if kind == "standard":
                    stored["endpoints"].pop(point, None)
                    continue
                rule = {"kind": kind, "next_day": input_rule["next_day"]}
                if kind == "clock":
                    if not input_rule.get("time"):
                        raise ValueError(
                            f"Set a local time for {point.replace('_', ' ')}."
                        )
                    rule["clock"] = input_rule["time"]
                else:
                    offset = input_rule["offset_minutes"]
                    if kind == "seasonal":
                        day = self._edit_day + timedelta(days=int(rule["next_day"]))
                        offset *= 12 * 3600 / self._sun.day(day).daylight
                    rule.update(
                        reference=input_rule["reference"], offset_minutes=offset
                    )
                stored["endpoints"][point] = rule
        if "shapes" in values:
            stored["shapes"] = values["shapes"]
        if "runtime" in values:
            data.update(values["runtime"])

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reconfigure_entry()
        saved = deepcopy(dict(entry.data))
        schedule = await async_schedule(self.hass, saved)
        self._sun = schedule.sun
        self._edit_day = dt_util.utcnow().astimezone(schedule.sun.timezone).date()
        values = self._schedule_values(saved)
        errors = {}
        detail = ""
        if user_input is not None:
            values.update(user_input)
            try:
                validated = SCHEDULE_SCHEMA(user_input)
                draft = deepcopy(saved)
                self._apply_schedule(draft, validated)
                schedule = await async_schedule(self.hass, draft)
                await self.hass.async_add_executor_job(
                    schedule.validate, self._edit_day
                )
            except (ScheduleError, ValueError) as err:
                errors["base"], detail = "schedule_invalid", str(err)
            else:
                return self.async_update_and_abort(entry, data=draft)
        return self.async_show_form(
            step_id="reconfigure",
            errors=errors,
            description_placeholders={"detail": detail},
            data_schema=self.add_suggested_values_to_schema(SCHEDULE_SCHEMA, values),
        )


class TargetSubentryFlowHandler(ConfigSubentryFlow):
    """Per-target light selection and ranges, independent of shared timing."""

    options: dict[str, Any]

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        self.options = {}
        return await self.async_step_init()

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        stored = self._get_reconfigure_subentry().data
        self.options = nest_sections(TARGET_SCHEMA, stored)
        self.options[CONF_TARGETS] = {
            "entity_id": stored.get(CONF_ENTITIES, []),
            "area_id": stored.get(CONF_AREAS, []),
        }
        return await self.async_step_init()

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        errors = {}
        if user_input is not None:
            try:
                title, data = _target_input(self.hass, user_input)
            except ValueError as err:
                errors["base"] = str(err)
                self.options = user_input
            else:
                if self.source == "user":
                    return self.async_create_entry(title=title, data=data)
                return self.async_update_and_abort(
                    self._get_entry(),
                    self._get_reconfigure_subentry(),
                    title=title,
                    data=data,
                )
        return self.async_show_form(
            step_id="init",
            errors=errors,
            data_schema=self.add_suggested_values_to_schema(
                TARGET_SCHEMA, self.options
            ),
        )
