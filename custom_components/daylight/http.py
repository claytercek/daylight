"""HTTP API for a live daylight curve preview.

`SampleCurveView` and `PreviewFieldsView` are plain request/response
endpoints for a not-yet-built frontend panel: the client always initiates
("user dragged a slider, give me points for these values" / "what fields
should I render"), the server never pushes anything unprompted, so these are
`HomeAssistantView`s, not websocket commands. Nothing persists -- this only
computes and returns points, the same "scratchpad" preview
`curve_preview.sample_curve` was built for.

This is the system boundary where untrusted browser input arrives: the
voluptuous schema below is the validation, mirroring the real hub/target
config flow's field set (`config_flow.HUB_SCHEMA`/`TARGET_SCHEMA`) via the
same `CONF_*` constants *and* the same `selector.*` constructs, so the two
can't drift apart -- and so `PreviewFieldsView` can serialize this schema
straight into the field list Home Assistant's own generic form renderer
(`<ha-form>`) consumes, with no field-specific frontend JS. `sample_curve`
itself stays pure and unvalidated.
"""

from __future__ import annotations

import dataclasses
import datetime
from http import HTTPStatus
from typing import Any

import astral
import voluptuous as vol
from aiohttp import web
from homeassistant.components.http import KEY_HASS, HomeAssistantView
from homeassistant.components.http.data_validator import RequestDataValidator
from homeassistant.data_entry_flow import section
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    TimeSelector,
)
from homeassistant.util import dt as dt_util
from probatio import to_field_list

from .color_and_brightness import CurveSettings
from .config_flow import (
    _BRIGHTNESS_MODE_OPTIONS,
    _DEFAULT_BRIGHTNESS_MODE_TIME_MINUTES,
    CONF_BRIGHTNESS_MODE,
    CONF_BRIGHTNESS_MODE_TIME_DARK_MINUTES,
    CONF_BRIGHTNESS_MODE_TIME_LIGHT_MINUTES,
    CONF_MAX_BRIGHTNESS_PCT,
    CONF_MAX_COLOR_TEMP_KELVIN,
    CONF_MAX_SUNRISE_TIME,
    CONF_MAX_SUNSET_TIME,
    CONF_MIN_BRIGHTNESS_PCT,
    CONF_MIN_COLOR_TEMP_KELVIN,
    CONF_MIN_SUNRISE_TIME,
    CONF_MIN_SUNSET_TIME,
    CONF_SUNRISE_OFFSET_MINUTES,
    CONF_SUNRISE_TIME,
    CONF_SUNSET_OFFSET_MINUTES,
    CONF_SUNSET_TIME,
    SECTION_BRIGHTNESS,
    SECTION_BRIGHTNESS_CURVE,
    SECTION_COLOR_TEMP,
    SECTION_SUNRISE,
    SECTION_SUNSET,
    _flatten_sections,
    _int_box,
)
from .curve_preview import DEFAULT_NUM_POINTS, sample_curve

_CONF_START = "start"
_CONF_NUM_POINTS = "num_points"


def _tz_aware_datetime(value: Any) -> datetime.datetime:
    """Validate an ISO 8601 datetime, requiring an explicit UTC offset.

    `sample_curve` compares `dt.timestamp()` against astral event
    timestamps, so a naive `start` would silently compute against the wrong
    instant rather than raising -- reject it here instead.
    """
    parsed = cv.datetime(value)
    if parsed.tzinfo is None:
        raise vol.Invalid("start must include a UTC offset")
    return parsed


def _to_time(value: str | None) -> datetime.time | None:
    """Convert a `TimeSelector`-validated string into a `datetime.time`.

    `TimeSelector.__call__` only checks that `value` parses as a time (via
    `cv.time`) and then returns the original string unchanged -- the same
    "store the string, parse it at the point of use" split `config_flow`
    uses between its schema and `__init__.py`'s `_parse_time`. `cv.time`
    itself has no `None` passthrough, so that case is handled here.
    """
    return None if value is None else cv.time(value)


# Mirrors config_flow.HUB_SCHEMA's sun-timing/brightness-curve sections plus
# TARGET_SCHEMA's brightness/color-temp range sections -- the full field set
# `CurveSettings` and `sample_curve` need, not just the latter's bounds. Uses
# the identical selector constructs and `section()` groupings those schemas
# do, so a generic selector-aware form renderer (see `PreviewFieldsView`)
# gets the same sliders/pickers, grouped into the same expandable sections,
# the config flow's own form does. `custom_serializer` (passed to
# `to_field_list` below) already knows how to serialize `section()` markers,
# so no changes are needed there -- only the grouping of fields here.
_FORM_FIELDS: dict[Any, Any] = {
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
                vol.Optional(
                    CONF_BRIGHTNESS_MODE, default="default"
                ): SelectSelector(
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
    # Required, not `Optional(..., default=dict)`: each holds a required
    # field with no default, same as `config_flow.TARGET_SCHEMA`.
    vol.Required(SECTION_BRIGHTNESS): section(
        vol.Schema(
            {
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
            }
        )
    ),
    vol.Required(SECTION_COLOR_TEMP): section(
        vol.Schema(
            {
                vol.Required(CONF_MIN_COLOR_TEMP_KELVIN): _int_box(),
                vol.Required(CONF_MAX_COLOR_TEMP_KELVIN): _int_box(),
            }
        )
    ),
}

SAMPLE_CURVE_SCHEMA = vol.Schema(
    {
        **_FORM_FIELDS,
        vol.Optional(_CONF_START): _tz_aware_datetime,
        vol.Optional(_CONF_NUM_POINTS, default=DEFAULT_NUM_POINTS): vol.All(
            vol.Coerce(int), vol.Range(min=1)
        ),
    }
)

# `start`/`num_points` are endpoint-specific sampling-window knobs, not part
# of the curve shape a form would edit, so `PreviewFieldsView` serializes
# only this subset of `SAMPLE_CURVE_SCHEMA`.
_FORM_FIELDS_SCHEMA = vol.Schema(_FORM_FIELDS)


class SampleCurveView(HomeAssistantView):
    """Sample the daylight curve for the given settings, on demand."""

    url = "/api/daylight/sample_curve"
    name = "api:daylight:sample_curve"

    @RequestDataValidator(SAMPLE_CURVE_SCHEMA)
    async def post(self, request: web.Request, data: dict[str, Any]) -> web.Response:
        """Build `CurveSettings` from `data` and return sampled points."""
        # `data` arrives nested by section (`SAMPLE_CURVE_SCHEMA`'s shape);
        # flatten it back to the flat shape the rest of this method expects,
        # same as `config_flow` does with its own section-shaped submissions.
        data = _flatten_sections(SAMPLE_CURVE_SCHEMA, data)

        hass = request.app[KEY_HASS]

        # Mirrors __init__.py's real hub construction: the observer always
        # comes from hass.config, never from the request.
        astral_observer = astral.Observer(
            latitude=hass.config.latitude,
            longitude=hass.config.longitude,
            elevation=hass.config.elevation,
        )
        timezone = (
            await dt_util.async_get_time_zone(hass.config.time_zone) or dt_util.UTC
        )

        curve_settings = CurveSettings(
            name="preview",
            astral_observer=astral_observer,
            timezone=timezone,
            sunrise_time=_to_time(data.get(CONF_SUNRISE_TIME)),
            min_sunrise_time=_to_time(data.get(CONF_MIN_SUNRISE_TIME)),
            max_sunrise_time=_to_time(data.get(CONF_MAX_SUNRISE_TIME)),
            sunset_time=_to_time(data.get(CONF_SUNSET_TIME)),
            min_sunset_time=_to_time(data.get(CONF_MIN_SUNSET_TIME)),
            max_sunset_time=_to_time(data.get(CONF_MAX_SUNSET_TIME)),
            sunrise_offset=datetime.timedelta(
                minutes=data[CONF_SUNRISE_OFFSET_MINUTES]
            ),
            sunset_offset=datetime.timedelta(minutes=data[CONF_SUNSET_OFFSET_MINUTES]),
            brightness_mode=data[CONF_BRIGHTNESS_MODE],
            brightness_mode_time_dark=datetime.timedelta(
                minutes=data[CONF_BRIGHTNESS_MODE_TIME_DARK_MINUTES]
            ),
            brightness_mode_time_light=datetime.timedelta(
                minutes=data[CONF_BRIGHTNESS_MODE_TIME_LIGHT_MINUTES]
            ),
        )

        start = data.get(_CONF_START) or dt_util.utcnow()

        try:
            points = sample_curve(
                curve_settings,
                start=start,
                min_brightness_pct=data[CONF_MIN_BRIGHTNESS_PCT],
                max_brightness_pct=data[CONF_MAX_BRIGHTNESS_PCT],
                min_color_temp_kelvin=data[CONF_MIN_COLOR_TEMP_KELVIN],
                max_color_temp_kelvin=data[CONF_MAX_COLOR_TEMP_KELVIN],
                num_points=data[_CONF_NUM_POINTS],
            )
        except ValueError as err:
            return self.json_message(str(err), HTTPStatus.BAD_REQUEST)

        # Known limitation: only the start date's sunrise/sunset are
        # resolved, using `start`'s calendar date in the hub's own timezone
        # (not the request's UTC offset). A window that crosses midnight
        # (via `start`/`num_points`) will not surface the second day's sun
        # times.
        local_start = start.astimezone(timezone)
        sunrise = curve_settings.sun.sunrise(local_start)
        sunset = curve_settings.sun.sunset(local_start)

        return self.json(
            {
                "points": [dataclasses.asdict(p) for p in points],
                "sunrise": sunrise.isoformat(),
                "sunset": sunset.isoformat(),
            }
        )


class PreviewFieldsView(HomeAssistantView):
    """Describe `SampleCurveView`'s curve-shape fields for a generic form.

    Serializes `_FORM_FIELDS_SCHEMA` -- the same selector-built schema
    `SampleCurveView` validates against, minus the `start`/`num_points`
    sampling-window knobs -- into the field-list shape `<ha-form>` (Home
    Assistant's own generic schema-driven form renderer) consumes. A
    not-yet-built frontend panel can then render sliders/pickers for this
    endpoint with no field-specific JS of its own.
    """

    url = "/api/daylight/preview_fields"
    name = "api:daylight:preview_fields"

    async def get(self, request: web.Request) -> web.Response:
        """Return the `<ha-form>`-compatible field list."""
        return self.json(
            to_field_list(_FORM_FIELDS_SCHEMA, custom_serializer=cv.custom_serializer)
        )
