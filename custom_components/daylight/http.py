"""HTTP endpoints for sampling curves and describing the preview form."""

from __future__ import annotations

import dataclasses
import datetime
from http import HTTPStatus
from typing import Any

import voluptuous as vol
from aiohttp import web
from homeassistant.components.http import KEY_HASS, HomeAssistantView
from homeassistant.components.http.data_validator import RequestDataValidator
from homeassistant.helpers import config_validation as cv
from homeassistant.util import dt as dt_util
from probatio import to_field_list

from .config import (
    CONF_MAX_BRIGHTNESS_PCT,
    CONF_MAX_COLOR_TEMP_KELVIN,
    CONF_MIN_BRIGHTNESS_PCT,
    CONF_MIN_COLOR_TEMP_KELVIN,
    CURVE_FIELDS,
    RANGE_FIELDS,
    async_curve_settings,
    flatten_sections,
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


# One-minute resolution across the half-open 24-hour sampling window.
MAX_NUM_POINTS = 1440
_FORM_FIELDS = {**CURVE_FIELDS, **RANGE_FIELDS}

SAMPLE_CURVE_SCHEMA = vol.Schema(
    {
        **_FORM_FIELDS,
        vol.Optional(_CONF_START): _tz_aware_datetime,
        vol.Optional(_CONF_NUM_POINTS, default=DEFAULT_NUM_POINTS): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=MAX_NUM_POINTS)
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
        data = flatten_sections(SAMPLE_CURVE_SCHEMA, data)

        hass = request.app[KEY_HASS]

        curve_settings = await async_curve_settings(hass, data, name="preview")
        timezone = curve_settings.timezone
        start = data.get(_CONF_START)
        if start is None:
            start = (
                dt_util.utcnow()
                .astimezone(timezone)
                .replace(hour=0, minute=0, second=0, microsecond=0)
            )

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
    Assistant's own generic schema-driven form renderer) consumes. The
    frontend panel renders sliders/pickers for this
    endpoint with no field-specific JS of its own.
    """

    url = "/api/daylight/preview_fields"
    name = "api:daylight:preview_fields"

    async def get(self, request: web.Request) -> web.Response:
        """Return the `<ha-form>`-compatible field list."""
        return self.json(
            to_field_list(_FORM_FIELDS_SCHEMA, custom_serializer=cv.custom_serializer)
        )
