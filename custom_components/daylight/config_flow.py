"""Native settings: useful setup defaults and an explicitly saved timing draft."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from datetime import timedelta
from typing import Any

import voluptuous as vol
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
from .schedule_config import (
    LENGTH_SCHEMA,
    MODE_SCHEMA,
    RUNTIME_SCHEMA,
    SHAPE_SCHEMA,
    anchor_schema,
    async_schedule,
    choices,
    default_hub_data,
    endpoint_schema,
)

HUB_TITLE = "Daylight"


def _target_input(
    hass: HomeAssistant, user_input: dict[str, Any]
) -> tuple[str, dict[str, Any]]:
    """Normalize and validate the same target form in setup and subentry flows."""
    data = flatten_sections(TARGET_SCHEMA, TARGET_SCHEMA(user_input))
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
    """Setup selects lights. Reconfiguration edits a draft, never live settings."""

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

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        self.draft = deepcopy(dict(self._get_reconfigure_entry().data))
        self._period = "morning"
        self._mode = "solar"
        self._point = ENDPOINTS[0]
        self._kind = "seasonal"
        schedule = await async_schedule(self.hass, self.draft)
        self._edit_day = dt_util.utcnow().astimezone(schedule.sun.timezone).date()
        return await self.async_step_menu()

    async def _summary(self) -> str:
        schedule = await async_schedule(self.hass, self.draft)
        try:
            resolved = schedule.resolve(self._edit_day)
        except ValueError as err:
            return str(err)
        status = "Custom schedule" if schedule.is_custom else "Seasonal timing"
        lines = [f"{self._edit_day.isoformat()} · {status}"]
        for name in ("brightness", "color"):
            track = getattr(resolved, name)
            values = []
            for point in (
                "morning_start",
                "morning_end",
                "evening_start",
                "evening_end",
            ):
                local = dt_util.utc_from_timestamp(getattr(track, point)).astimezone(
                    schedule.sun.timezone
                )
                suffix = " (+1 day)" if local.date() > self._edit_day else ""
                values.append(local.strftime("%H:%M") + suffix)
            lines.append(
                f"{name.title()}: {values[0]} to {values[1]}, "
                f"{values[2]} to {values[3]}"
            )
        if resolved.compressed:
            lines.append("Standard transitions shortened to fit the available time.")
        if resolved.fallback:
            lines.append(
                "Polar fallback lighting anchors are in use; "
                "these are not real sunrise/sunset events."
            )
        return "\n\n".join(lines)

    async def async_step_menu(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        options = (
            []
            if self.draft["schedule"]["endpoints"]
            else ["morning", "evening", "lengths"]
        )
        options.append("advanced")
        if self.draft["schedule"]["endpoints"]:
            options.append("restore")
        options.append("save")
        return self.async_show_menu(
            step_id="menu",
            menu_options=options,
            description_placeholders={"summary": await self._summary()},
        )

    async def async_step_advanced(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self.async_show_menu(
            step_id="advanced",
            menu_options=["customize", "shapes", "runtime", "menu"],
        )

    async def async_step_morning(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        self._period = "morning"
        return await self._timing_mode(user_input)

    async def async_step_evening(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        self._period = "evening"
        return await self._timing_mode(user_input)

    async def _timing_mode(self, user_input: dict[str, Any] | None) -> ConfigFlowResult:
        if user_input is not None:
            self._mode = user_input["mode"]
            return await self.async_step_anchor()
        mode = (
            "clock"
            if self.draft["schedule"]["basic"][f"{self._period}_time"]
            else "solar"
        )
        return self.async_show_form(
            step_id=self._period,
            data_schema=self.add_suggested_values_to_schema(
                MODE_SCHEMA, {"mode": mode}
            ),
        )

    async def async_step_anchor(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        basic = self.draft["schedule"]["basic"]
        period = self._period
        if user_input is not None:
            basic[f"{period}_time"] = user_input.get("time")
            basic[f"{period}_offset_minutes"] = user_input.get("offset_minutes", 0)
            if period == "evening":
                basic["evening_next_day"] = user_input.get("next_day", False)
            return await self.async_step_menu()
        return self.async_show_form(
            step_id="anchor",
            description_placeholders={
                "meaning": (
                    "Shift both tracks relative to sunrise/sunset. "
                    "Negative minutes are earlier."
                    if self._mode == "solar"
                    else "Start morning brightening at this time."
                    if period == "morning"
                    else "Both tracks finish reaching their night levels by this time."
                )
            },
            data_schema=self.add_suggested_values_to_schema(
                anchor_schema(period, self._mode),
                {
                    "time": basic[f"{period}_time"],
                    "offset_minutes": basic[f"{period}_offset_minutes"],
                    "next_day": basic["evening_next_day"],
                },
            ),
        )

    async def async_step_lengths(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            self.draft["schedule"]["basic"].update(user_input)
            return await self.async_step_menu()
        return self.async_show_form(
            step_id="lengths",
            description_placeholders={"summary": await self._summary()},
            data_schema=self.add_suggested_values_to_schema(
                LENGTH_SCHEMA, self.draft["schedule"]["basic"]
            ),
        )

    async def async_step_customize(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            self._point = user_input["endpoint"]
            return await self.async_step_endpoint_kind()
        return self.async_show_form(
            step_id="customize",
            description_placeholders={"summary": await self._summary()},
            data_schema=vol.Schema(
                {
                    vol.Required("endpoint"): choices(list(ENDPOINTS), "endpoint"),
                }
            ),
        )

    async def async_step_endpoint_kind(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            self._kind = user_input["kind"]
            if self._kind == "standard":
                self.draft["schedule"]["endpoints"].pop(self._point, None)
                return await self.async_step_menu()
            return await self.async_step_endpoint()
        stored = self.draft["schedule"]["endpoints"].get(self._point, {})
        return self.async_show_form(
            step_id="endpoint_kind",
            description_placeholders={"point": self._point.replace("_", " ")},
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(
                    {
                        vol.Required("kind", default="seasonal"): choices(
                            ["standard", "seasonal", "solar", "clock"],
                            "endpoint_kind",
                        ),
                    }
                ),
                {"kind": stored.get("kind", "standard")},
            ),
        )

    async def async_step_endpoint(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        schedule = await async_schedule(self.hass, self.draft)
        stored = self.draft["schedule"]["endpoints"].get(self._point, {})
        if user_input is not None:
            rule = {"kind": self._kind, "next_day": user_input.get("next_day", False)}
            if self._kind == "clock":
                rule["clock"] = user_input["time"]
            else:
                offset = user_input["offset_minutes"]
                if self._kind == "seasonal":
                    day = self._edit_day + timedelta(days=int(rule["next_day"]))
                    offset *= 12 * 3600 / schedule.sun.day(day).daylight
                rule.update(reference=user_input["reference"], offset_minutes=offset)
            self.draft["schedule"]["endpoints"][self._point] = rule
            return await self.async_step_menu()
        offset = stored.get("offset_minutes", 0)
        if stored.get("kind") == "seasonal":
            day = self._edit_day + timedelta(days=int(stored.get("next_day", False)))
            offset *= schedule.sun.day(day).daylight / (12 * 3600)
        return self.async_show_form(
            step_id="endpoint",
            description_placeholders={
                "point": self._point.replace("_", " "),
                "meaning": (
                    f"Offset on {self._edit_day.isoformat()}; "
                    "scales with daylight in other seasons."
                    if self._kind == "seasonal"
                    else "The offset stays the same number of minutes year-round."
                    if self._kind == "solar"
                    else "Exact local clock time."
                ),
            },
            data_schema=self.add_suggested_values_to_schema(
                endpoint_schema(self._kind),
                {
                    "reference": stored.get(
                        "reference", "sunset" if "evening" in self._point else "sunrise"
                    ),
                    "offset_minutes": round(offset, 1),
                    "time": stored.get("clock"),
                    "next_day": stored.get("next_day", False),
                },
            ),
        )

    async def async_step_shapes(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            self.draft["schedule"]["shapes"] = user_input
            return await self.async_step_menu()
        return self.async_show_form(
            step_id="shapes",
            data_schema=self.add_suggested_values_to_schema(
                SHAPE_SCHEMA, self.draft["schedule"]["shapes"]
            ),
        )

    async def async_step_runtime(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            self.draft.update(user_input)
            return await self.async_step_menu()
        return self.async_show_form(
            step_id="runtime",
            data_schema=self.add_suggested_values_to_schema(RUNTIME_SCHEMA, self.draft),
        )

    async def async_step_restore(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            if user_input["confirm"]:
                self.draft["schedule"]["basic"] = asdict(BasicTiming())
                self.draft["schedule"]["endpoints"] = {}
            return await self.async_step_menu()
        return self.async_show_form(
            step_id="restore",
            data_schema=vol.Schema({vol.Required("confirm", default=False): bool}),
        )

    async def async_step_save(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None and user_input.get("action") == "keep_editing":
            return await self.async_step_menu()
        errors = {}
        detail = ""
        if user_input is not None:
            schedule = await async_schedule(self.hass, self.draft)
            try:
                await self.hass.async_add_executor_job(
                    schedule.validate, self._edit_day
                )
            except (ScheduleError, ValueError) as err:
                errors["base"], detail = "schedule_invalid", str(err)
            else:
                return self.async_update_and_abort(
                    self._get_reconfigure_entry(), data=deepcopy(self.draft)
                )
        return self.async_show_form(
            step_id="save",
            errors=errors,
            description_placeholders={
                "summary": await self._summary(),
                "detail": detail,
            },
            data_schema=vol.Schema(
                {
                    vol.Required("action", default="save"): choices(
                        ["save", "keep_editing"], "save_action"
                    )
                }
            ),
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
