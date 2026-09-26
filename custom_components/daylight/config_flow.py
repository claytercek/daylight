"""Config and target subentry flows for the daylight integration."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    SubentryFlowResult,
)
from homeassistant.core import callback

from .config import (
    CONF_ENTITIES,
    CONF_MAX_BRIGHTNESS_PCT,
    CONF_MAX_COLOR_TEMP_KELVIN,
    CONF_MIN_BRIGHTNESS_PCT,
    CONF_MIN_COLOR_TEMP_KELVIN,
    HUB_SCHEMA,
    SECTION_BRIGHTNESS,
    SECTION_COLOR_TEMP,
    TARGET_SCHEMA,
    flatten_sections,
    nest_sections,
    normalize_hub_input,
)
from .const import DOMAIN

HUB_TITLE = "Daylight"


class DaylightConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the daylight hub config flow."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect the hub's sun-timing / curve-shape config."""
        if user_input is not None:
            return self.async_create_entry(
                title=HUB_TITLE,
                data=normalize_hub_input(user_input),
                subentries=[],
            )

        return self.async_show_form(step_id="user", data_schema=HUB_SCHEMA)

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Return subentries supported by this integration."""
        return {"target": TargetSubentryFlowHandler}


class TargetSubentryFlowHandler(ConfigSubentryFlow):
    """Flow for managing a daylight target subentry."""

    options: dict[str, Any]

    @property
    def _is_new(self) -> bool:
        """Return whether this flow is adding a new subentry."""
        return self.source == "user"

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Add a new target subentry."""
        self.options = {}
        return await self.async_step_init()

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Reconfigure an existing target subentry."""
        self.options = nest_sections(
            TARGET_SCHEMA, self._get_reconfigure_subentry().data
        )
        return await self.async_step_init()

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Collect the target's per-light adaptation config."""
        errors: dict[str, str] = {}

        if user_input is not None:
            brightness = user_input[SECTION_BRIGHTNESS]
            color_temp = user_input[SECTION_COLOR_TEMP]
            if (
                brightness[CONF_MIN_BRIGHTNESS_PCT]
                > brightness[CONF_MAX_BRIGHTNESS_PCT]
            ):
                errors["base"] = "brightness_range_invalid"
            elif (
                color_temp[CONF_MIN_COLOR_TEMP_KELVIN]
                > color_temp[CONF_MAX_COLOR_TEMP_KELVIN]
            ):
                errors["base"] = "color_temp_range_invalid"

            if not errors:
                data = flatten_sections(TARGET_SCHEMA, user_input)
                if self._is_new:
                    return self.async_create_entry(
                        title=", ".join(user_input[CONF_ENTITIES]),
                        data=data,
                    )
                return self.async_update_and_abort(
                    self._get_entry(),
                    self._get_reconfigure_subentry(),
                    title=", ".join(user_input[CONF_ENTITIES]),
                    data=data,
                )

        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                TARGET_SCHEMA, self.options
            ),
            errors=errors or None,
        )
