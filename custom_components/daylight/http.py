"""HTTP API for a live daylight curve preview.

`SampleCurveView` is a plain request/response endpoint for a not-yet-built
frontend panel: the client always initiates ("user dragged a slider, give me
points for these values"), the server never pushes anything unprompted, so
this is a `HomeAssistantView`, not a websocket command. Nothing persists --
this only computes and returns points, the same "scratchpad" preview
`curve_preview.sample_curve` was built for.

This is the system boundary where untrusted browser input arrives: the
voluptuous schema below is the validation, mirroring the real hub/target
config flow's field set (`config_flow.HUB_SCHEMA`/`TARGET_SCHEMA`) via the
same `CONF_*` constants so the two can't drift apart. `sample_curve` itself
stays pure and unvalidated.
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
from homeassistant.helpers import config_validation as cv
from homeassistant.util import dt as dt_util

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


# Mirrors config_flow.HUB_SCHEMA's sun-timing/brightness-curve fields plus
# TARGET_SCHEMA's brightness/color-temp range -- the full field set
# `CurveSettings` and `sample_curve` need, not just the latter's bounds.
SAMPLE_CURVE_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_SUNRISE_TIME): cv.time,
        vol.Optional(CONF_MIN_SUNRISE_TIME): cv.time,
        vol.Optional(CONF_MAX_SUNRISE_TIME): cv.time,
        vol.Optional(CONF_SUNRISE_OFFSET_MINUTES, default=0): vol.Coerce(int),
        vol.Optional(CONF_SUNSET_TIME): cv.time,
        vol.Optional(CONF_MIN_SUNSET_TIME): cv.time,
        vol.Optional(CONF_MAX_SUNSET_TIME): cv.time,
        vol.Optional(CONF_SUNSET_OFFSET_MINUTES, default=0): vol.Coerce(int),
        vol.Optional(CONF_BRIGHTNESS_MODE, default="default"): vol.In(
            _BRIGHTNESS_MODE_OPTIONS
        ),
        vol.Optional(
            CONF_BRIGHTNESS_MODE_TIME_DARK_MINUTES,
            default=_DEFAULT_BRIGHTNESS_MODE_TIME_MINUTES,
        ): vol.Coerce(int),
        vol.Optional(
            CONF_BRIGHTNESS_MODE_TIME_LIGHT_MINUTES,
            default=_DEFAULT_BRIGHTNESS_MODE_TIME_MINUTES,
        ): vol.Coerce(int),
        vol.Required(CONF_MIN_BRIGHTNESS_PCT): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=100)
        ),
        vol.Required(CONF_MAX_BRIGHTNESS_PCT): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=100)
        ),
        vol.Required(CONF_MIN_COLOR_TEMP_KELVIN): vol.Coerce(int),
        vol.Required(CONF_MAX_COLOR_TEMP_KELVIN): vol.Coerce(int),
        vol.Optional(_CONF_START): _tz_aware_datetime,
        vol.Optional(_CONF_NUM_POINTS, default=DEFAULT_NUM_POINTS): vol.All(
            vol.Coerce(int), vol.Range(min=1)
        ),
    }
)


class SampleCurveView(HomeAssistantView):
    """Sample the daylight curve for the given settings, on demand."""

    url = "/api/daylight/sample_curve"
    name = "api:daylight:sample_curve"

    @RequestDataValidator(SAMPLE_CURVE_SCHEMA)
    async def post(self, request: web.Request, data: dict[str, Any]) -> web.Response:
        """Build `CurveSettings` from `data` and return sampled points."""
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
            sunrise_time=data.get(CONF_SUNRISE_TIME),
            min_sunrise_time=data.get(CONF_MIN_SUNRISE_TIME),
            max_sunrise_time=data.get(CONF_MAX_SUNRISE_TIME),
            sunset_time=data.get(CONF_SUNSET_TIME),
            min_sunset_time=data.get(CONF_MIN_SUNSET_TIME),
            max_sunset_time=data.get(CONF_MAX_SUNSET_TIME),
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

        return self.json({"points": [dataclasses.asdict(p) for p in points]})
