"""Native settings for shared schedule and per-target lights."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from datetime import date, timedelta
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    OptionsFlow,
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
from .schedule import BasicTiming, ScheduleError
from .schedule_config import (
    ENDPOINT_KIND_SCHEMA,
    ENDPOINT_SELECT_SCHEMA,
    RESET_SCHEMA,
    RETURN_WITHOUT_CHANGES,
    RUNTIME_SCHEMA,
    TIMING_MODES_SCHEMA,
    TRANSITIONS_SCHEMA,
    async_schedule,
    daily_timing_schema,
    default_hub_data,
    editor_schema,
    endpoint_timing_schema,
)
from .solar import SunEvents

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
    """Set up lights with default shared schedule options."""

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
                    data={},
                    options=default_hub_data(),
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

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> DaylightOptionsFlow:
        """Return the shared schedule options flow."""
        return DaylightOptionsFlow()

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        return {"target": TargetSubentryFlowHandler}


class DaylightOptionsFlow(OptionsFlow):
    """Stage shared settings locally; persist only after a validated final save."""

    _draft: dict[str, Any]
    _sun: SunEvents
    _edit_day: date
    _timing_modes: dict[str, str]
    _endpoint: str
    _endpoint_kind: str
    _endpoint_original: dict[str, Any]
    _endpoint_display: dict[str, Any]

    def __init__(self) -> None:
        super().__init__()
        self._initialized = False

    def _form(
        self,
        step_id: str,
        schema: vol.Schema,
        values: dict[str, Any],
        user_input: dict[str, Any] | None = None,
        errors: dict[str, str] | None = None,
    ) -> ConfigFlowResult:
        """Offer a page-local return without requiring missing editor values."""
        return self.async_show_form(
            step_id=step_id,
            data_schema=editor_schema(
                self.add_suggested_values_to_schema(schema, values | (user_input or {}))
            ),
            errors=errors,
            description_placeholders={"date": self._edit_day.isoformat()},
            last_step=False,
        )

    @staticmethod
    def _editor_input(
        schema: vol.Schema, user_input: dict[str, Any] | None
    ) -> tuple[dict[str, Any] | None, dict[str, str]]:
        """Validate applied values only, never store the navigation control."""
        if user_input is None:
            return None, {}
        try:
            return schema(
                {
                    key: value
                    for key, value in user_input.items()
                    if key != RETURN_WITHOUT_CHANGES
                }
            ), {}
        except vol.Invalid as err:
            field = str(err.path[0]) if len(err.path) == 1 else "base"
            error = (
                "time_required"
                if field == "time" or field.endswith("_time")
                else "page_invalid"
            )
            return None, {field: error}

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if not self._initialized:
            self._draft = deepcopy(dict(self.config_entry.options))
            schedule = await async_schedule(self.hass, self._draft)
            self._sun = schedule.sun
            self._edit_day = dt_util.utcnow().astimezone(self._sun.timezone).date()
            self._initialized = True
        return self.async_show_menu(
            step_id="init",
            menu_options=["daily_timing", "transitions", "advanced", "save"],
        )

    async def async_step_daily_timing(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input and user_input.get(RETURN_WITHOUT_CHANGES):
            return await self.async_step_init()
        validated, errors = self._editor_input(TIMING_MODES_SCHEMA, user_input)
        if validated is not None:
            self._timing_modes = validated
            return await self.async_step_daily_details()
        basic = self._draft["schedule"]["basic"]
        values = {
            f"{period}_mode": "clock" if basic[f"{period}_time"] else "solar"
            for period in ("morning", "evening")
        }
        return self._form(
            "daily_timing", TIMING_MODES_SCHEMA, values, user_input, errors
        )

    async def async_step_daily_details(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        schema = daily_timing_schema(self._timing_modes)
        basic = self._draft["schedule"]["basic"]
        if user_input and user_input.get(RETURN_WITHOUT_CHANGES):
            return await self.async_step_init()
        validated, errors = self._editor_input(schema, user_input)
        if validated is not None:
            basic.update(validated)
            for period in ("morning", "evening"):
                solar = self._timing_modes[f"{period}_mode"] == "solar"
                if solar:
                    basic[f"{period}_time"] = None
                else:
                    basic[f"{period}_offset_minutes"] = 0
                if period == "evening" and solar:
                    basic["evening_next_day"] = False
            return await self.async_step_init()
        values = {
            str(key): basic[str(key)]
            for key in schema.schema
            if basic[str(key)] is not None
        }
        return self._form("daily_details", schema, values, user_input, errors)

    async def async_step_transitions(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        stored = self._draft["schedule"]
        if user_input and user_input.get(RETURN_WITHOUT_CHANGES):
            return await self.async_step_init()
        validated, errors = self._editor_input(TRANSITIONS_SCHEMA, user_input)
        if validated is not None:
            for track, settings in validated.items():
                stored["basic"][f"{track}_length"] = settings["length"]
                for period in ("morning", "evening"):
                    key = f"{track}_{period}"
                    shape = settings[f"{period}_shape"]
                    if shape != stored["shapes"].get(key, "smooth"):
                        stored["shapes"][key] = shape
            return await self.async_step_init()
        values = {
            track: {
                "length": stored["basic"][f"{track}_length"],
                **{
                    f"{period}_shape": stored["shapes"].get(
                        f"{track}_{period}", "smooth"
                    )
                    for period in ("morning", "evening")
                },
            }
            for track in ("brightness", "color")
        }
        return self._form("transitions", TRANSITIONS_SCHEMA, values, user_input, errors)

    async def async_step_advanced(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self.async_show_menu(
            step_id="advanced", menu_options=["endpoint", "runtime", "reset", "init"]
        )

    async def async_step_endpoint(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input and user_input.get(RETURN_WITHOUT_CHANGES):
            return await self.async_step_advanced()
        validated, errors = self._editor_input(ENDPOINT_SELECT_SCHEMA, user_input)
        if validated is not None:
            self._endpoint = validated["endpoint"]
            return await self.async_step_endpoint_rule()
        return self._form("endpoint", ENDPOINT_SELECT_SCHEMA, {}, user_input, errors)

    async def async_step_endpoint_rule(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        endpoints = self._draft["schedule"]["endpoints"]
        original = endpoints.get(self._endpoint, {})
        if user_input and user_input.get(RETURN_WITHOUT_CHANGES):
            return await self.async_step_advanced()
        validated, errors = self._editor_input(ENDPOINT_KIND_SCHEMA, user_input)
        if validated is not None:
            self._endpoint_kind = validated["kind"]
            if self._endpoint_kind == "standard":
                endpoints.pop(self._endpoint, None)
                return await self.async_step_advanced()
            self._endpoint_original = deepcopy(original)
            self._endpoint_display = self._endpoint_values(original)
            return await self.async_step_endpoint_details()
        return self._form(
            "endpoint_rule",
            ENDPOINT_KIND_SCHEMA,
            {"kind": original.get("kind", "standard")},
            user_input,
            errors,
        )

    def _endpoint_values(self, rule: dict[str, Any]) -> dict[str, Any]:
        """Display seasonal minutes on the flow's fixed local reference date."""
        values: dict[str, Any] = {"next_day": rule.get("next_day", False)}
        if self._endpoint_kind == "clock":
            if "clock" in rule:
                values["time"] = rule["clock"]
        else:
            offset = rule.get("offset_minutes", 0)
            if rule.get("kind") == "seasonal":
                offset *= self._daylight_ratio(values["next_day"])
                offset = round(offset, 1)
            values.update(
                reference=rule.get(
                    "reference", "sunset" if "evening" in self._endpoint else "sunrise"
                ),
                offset_minutes=offset,
            )
        return values

    def _daylight_ratio(self, next_day: bool) -> float:
        day = self._edit_day + timedelta(days=int(next_day))
        return self._sun.day(day).daylight / (12 * 3600)

    async def async_step_endpoint_details(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        schema = endpoint_timing_schema(self._endpoint_kind)
        if user_input and user_input.get(RETURN_WITHOUT_CHANGES):
            return await self.async_step_advanced()
        validated, errors = self._editor_input(schema, user_input)
        if validated is not None:
            # A rounded display must never rewrite an unedited canonical rule.
            if not (
                self._endpoint_original.get("kind") == self._endpoint_kind
                and validated == self._endpoint_display
            ):
                rule: dict[str, Any] = {
                    "kind": self._endpoint_kind,
                    "next_day": validated["next_day"],
                }
                if self._endpoint_kind == "clock":
                    rule["clock"] = validated["time"]
                else:
                    offset = validated["offset_minutes"]
                    if self._endpoint_kind == "seasonal":
                        offset /= self._daylight_ratio(validated["next_day"])
                    rule.update(reference=validated["reference"], offset_minutes=offset)
                self._draft["schedule"]["endpoints"][self._endpoint] = rule
            return await self.async_step_advanced()
        return self._form(
            "endpoint_details", schema, self._endpoint_display, user_input, errors
        )

    async def async_step_runtime(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input and user_input.get(RETURN_WITHOUT_CHANGES):
            return await self.async_step_advanced()
        validated, errors = self._editor_input(RUNTIME_SCHEMA, user_input)
        if validated is not None:
            self._draft.update(validated)
            return await self.async_step_advanced()
        return self._form(
            "runtime",
            RUNTIME_SCHEMA,
            {"update_interval_seconds": self._draft["update_interval_seconds"]},
            user_input,
            errors,
        )

    async def async_step_reset(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input and user_input.get(RETURN_WITHOUT_CHANGES):
            return await self.async_step_advanced()
        validated, errors = self._editor_input(RESET_SCHEMA, user_input)
        if validated is not None:
            if validated["confirm"]:
                self._draft["schedule"]["basic"] = asdict(BasicTiming())
                self._draft["schedule"]["endpoints"] = {}
            return await self.async_step_advanced()
        return self._form("reset", RESET_SCHEMA, {"confirm": False}, user_input, errors)

    async def async_step_save(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Validate the whole year only here, then let HA persist options once."""
        try:
            schedule = await async_schedule(self.hass, self._draft)
            await self.hass.async_add_executor_job(schedule.validate, self._edit_day)
        except (ScheduleError, ValueError) as err:
            self._save_error = str(err)
            return await self.async_step_save_error()
        return self.async_create_entry(title="", data=self._draft)

    async def async_step_save_error(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Keep every editor available when the complete schedule conflicts."""
        return self.async_show_menu(
            step_id="save_error",
            menu_options=["daily_timing", "transitions", "advanced", "save"],
            description_placeholders={"detail": self._save_error},
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
