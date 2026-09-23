"""Config flow for the daylight integration.

Two flows live here:

* `DaylightConfigFlow` -- the hub entry. It collects the sun-timing and
  curve-shape knobs that `color_and_brightness.CurveSettings` needs (plus the
  hub's own `update_interval_seconds`), and stores them as a plain,
  JSON-serializable dict in the entry's `data`. It does not construct a
  `CurveSettings` itself -- `astral_observer`/`timezone` are derived from
  `hass.config` elsewhere, by whatever consumes this data.
* `TargetSubentryFlowHandler` -- one "target" subentry per light/light group.
  It collects and stores the full per-target config; it does not construct a
  `target.TargetConfig` or wire anything to `switch.py`.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    SubentryFlowResult,
)
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    TimeSelector,
)

from .const import DOMAIN

HUB_TITLE = "Daylight"

# Hub schema keys. These are local to this module -- not shared via const.py.
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


def _int_box() -> vol.All:
    """A plain integer field with no bounds, via a text-entry (box) selector.

    `NumberSelector`'s slider mode requires both `min` and `max`; these
    fields are intentionally unbounded, so box mode is used instead.
    `NumberSelector` itself always coerces to `float`, so the result is
    coerced back to `int` on top.
    """
    return vol.All(
        NumberSelector(NumberSelectorConfig(mode=NumberSelectorMode.BOX)),
        vol.Coerce(int),
    )


HUB_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_SUNRISE_TIME): TimeSelector(),
        vol.Optional(CONF_MIN_SUNRISE_TIME): TimeSelector(),
        vol.Optional(CONF_MAX_SUNRISE_TIME): TimeSelector(),
        vol.Optional(CONF_SUNSET_TIME): TimeSelector(),
        vol.Optional(CONF_MIN_SUNSET_TIME): TimeSelector(),
        vol.Optional(CONF_MAX_SUNSET_TIME): TimeSelector(),
        vol.Optional(CONF_SUNRISE_OFFSET_MINUTES, default=0): _int_box(),
        vol.Optional(CONF_SUNSET_OFFSET_MINUTES, default=0): _int_box(),
        vol.Optional(
            CONF_BRIGHTNESS_MODE, default="default"
        ): SelectSelector(SelectSelectorConfig(options=_BRIGHTNESS_MODE_OPTIONS)),
        vol.Optional(
            CONF_BRIGHTNESS_MODE_TIME_DARK_MINUTES,
            default=_DEFAULT_BRIGHTNESS_MODE_TIME_MINUTES,
        ): _int_box(),
        vol.Optional(
            CONF_BRIGHTNESS_MODE_TIME_LIGHT_MINUTES,
            default=_DEFAULT_BRIGHTNESS_MODE_TIME_MINUTES,
        ): _int_box(),
        vol.Optional(
            CONF_UPDATE_INTERVAL_SECONDS, default=_DEFAULT_UPDATE_INTERVAL_SECONDS
        ): _int_box(),
    }
)


def _normalize_hub_input(user_input: dict[str, Any]) -> dict[str, Any]:
    """Build the exact dict stored in the hub entry's `data`.

    Every key is always present, so a missing optional time override is
    stored as an explicit `None` rather than simply being absent.
    """
    data = dict(user_input)
    for key in _OPTIONAL_TIME_KEYS:
        data.setdefault(key, None)
    return data


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
                data=_normalize_hub_input(user_input),
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


# Target subentry schema keys.
CONF_ENTITIES = "entities"
CONF_MIN_BRIGHTNESS_PCT = "min_brightness_pct"
CONF_MAX_BRIGHTNESS_PCT = "max_brightness_pct"
CONF_MIN_COLOR_TEMP_KELVIN = "min_color_temp_kelvin"
CONF_MAX_COLOR_TEMP_KELVIN = "max_color_temp_kelvin"
CONF_TRANSITION = "transition"
CONF_ADAPT_ONLY_ON_STATE_CHANGE = "adapt_only_on_state_change"
CONF_MANUAL_CONTROL_RESET_MINUTES = "manual_control_reset_minutes"
CONF_SEPARATE_TURN_ON_COMMANDS = "separate_turn_on_commands"
CONF_SEND_SPLIT_DELAY = "send_split_delay"

_DEFAULT_MANUAL_CONTROL_RESET_MINUTES = 0

TARGET_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_ENTITIES): EntitySelector(
            EntitySelectorConfig(domain="light", multiple=True)
        ),
        vol.Required(CONF_MIN_BRIGHTNESS_PCT): vol.All(
            NumberSelector(
                NumberSelectorConfig(
                    min=1, max=100, mode=NumberSelectorMode.BOX
                )
            ),
            vol.Coerce(int),
        ),
        vol.Required(CONF_MAX_BRIGHTNESS_PCT): vol.All(
            NumberSelector(
                NumberSelectorConfig(
                    min=1, max=100, mode=NumberSelectorMode.BOX
                )
            ),
            vol.Coerce(int),
        ),
        vol.Required(CONF_MIN_COLOR_TEMP_KELVIN): _int_box(),
        vol.Required(CONF_MAX_COLOR_TEMP_KELVIN): _int_box(),
        vol.Required(CONF_TRANSITION): vol.All(
            NumberSelector(NumberSelectorConfig(mode=NumberSelectorMode.BOX)),
            vol.Coerce(float),
        ),
        vol.Required(CONF_ADAPT_ONLY_ON_STATE_CHANGE): bool,
        vol.Optional(
            CONF_MANUAL_CONTROL_RESET_MINUTES,
            default=_DEFAULT_MANUAL_CONTROL_RESET_MINUTES,
        ): _int_box(),
        vol.Required(CONF_SEPARATE_TURN_ON_COMMANDS): bool,
        vol.Required(CONF_SEND_SPLIT_DELAY): vol.All(
            NumberSelector(NumberSelectorConfig(mode=NumberSelectorMode.BOX)),
            vol.Coerce(float),
        ),
    }
)


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
        self.options = self._get_reconfigure_subentry().data.copy()
        return await self.async_step_init()

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Collect the target's per-light adaptation config."""
        errors: dict[str, str] = {}

        if user_input is not None:
            if user_input[CONF_MIN_BRIGHTNESS_PCT] > user_input[CONF_MAX_BRIGHTNESS_PCT]:
                errors["base"] = "brightness_range_invalid"
            elif (
                user_input[CONF_MIN_COLOR_TEMP_KELVIN]
                > user_input[CONF_MAX_COLOR_TEMP_KELVIN]
            ):
                errors["base"] = "color_temp_range_invalid"

            if not errors:
                if self._is_new:
                    return self.async_create_entry(
                        title=", ".join(user_input[CONF_ENTITIES]),
                        data=user_input,
                    )
                return self.async_update_and_abort(
                    self._get_entry(),
                    self._get_reconfigure_subentry(),
                    data=user_input,
                )

        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                TARGET_SCHEMA, self.options
            ),
            errors=errors or None,
        )
