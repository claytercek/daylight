"""Authenticated, read-only preview of saved hubs and targets."""

from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta
from functools import partial
from http import HTTPStatus
from typing import Any

import voluptuous as vol
from aiohttp import web
from homeassistant.components.http import KEY_HASS, HomeAssistantView
from homeassistant.components.http.data_validator import RequestDataValidator
from homeassistant.helpers import config_validation as cv
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .curve_preview import DEFAULT_NUM_POINTS, sample_curve
from .schedule_config import async_schedule

MAX_NUM_POINTS = 1441
SAMPLE_CURVE_SCHEMA = vol.Schema(
    {
        vol.Required("entry_id"): str,
        vol.Required("target_id"): str,
        vol.Optional("date"): cv.date,
        vol.Optional("num_points", default=DEFAULT_NUM_POINTS): vol.All(
            vol.Coerce(int),
            vol.Range(min=2, max=MAX_NUM_POINTS),
        ),
    }
)
_RANGE_KEYS = (
    "min_brightness_pct",
    "max_brightness_pct",
    "min_color_temp_kelvin",
    "max_color_temp_kelvin",
)


class PreviewTargetsView(HomeAssistantView):
    """List saved targets without exposing a second configuration surface."""

    url = "/api/daylight/preview_targets"
    name = "api:daylight:preview_targets"

    async def get(self, request: web.Request) -> web.Response:
        hass = request.app[KEY_HASS]
        zone = await dt_util.async_get_time_zone(hass.config.time_zone)
        targets = [
            {
                "entry_id": entry.entry_id,
                "target_id": target.subentry_id,
                "name": f"{entry.title}: {target.title}",
            }
            for entry in hass.config_entries.async_entries(DOMAIN)
            if "schedule" in entry.data
            for target in entry.subentries.values()
            if target.subentry_type == "target"
        ]
        return self.json(
            {
                "targets": targets,
                "timezone": hass.config.time_zone,
                "today": dt_util.utcnow().astimezone(zone).date().isoformat(),
            }
        )


class SampleCurveView(HomeAssistantView):
    """Evaluate only saved data; posted configuration is deliberately rejected."""

    url = "/api/daylight/sample_curve"
    name = "api:daylight:sample_curve"

    @RequestDataValidator(SAMPLE_CURVE_SCHEMA)
    async def post(self, request: web.Request, data: dict[str, Any]) -> web.Response:
        hass = request.app[KEY_HASS]
        entry = hass.config_entries.async_get_entry(data["entry_id"])
        if entry is None or entry.domain != DOMAIN or "schedule" not in entry.data:
            return self.json_message("Daylight hub not found.", HTTPStatus.NOT_FOUND)
        target = entry.subentries.get(data["target_id"])
        if target is None or target.subentry_type != "target":
            return self.json_message(
                "Target not found on this hub.", HTTPStatus.NOT_FOUND
            )
        schedule = await async_schedule(hass, entry.data)
        day = (
            data.get("date")
            or dt_util.utcnow().astimezone(schedule.sun.timezone).date()
        )
        ranges = {key: target.data[key] for key in _RANGE_KEYS}
        try:
            points = await hass.async_add_executor_job(
                partial(
                    sample_curve,
                    schedule,
                    day=day,
                    num_points=data["num_points"],
                    **ranges,
                )
            )
            timing = schedule.describe(day)
            start = datetime.fromisoformat(points[0].utc_time)
            end = datetime.fromisoformat(points[-1].utc_time)
            # Date-indexed solar cycles may straddle the selected local day.
            # Mark actual crossings inside the displayed window, not synthetic
            # anchors or a crossing belonging to tomorrow's calendar day.
            events = {}
            for name in ("sunrise", "sunset"):
                candidates = (
                    getattr(schedule.sun.day(day + timedelta(days=offset)), name)
                    for offset in range(-2, 3)
                )
                events[name] = next(
                    (
                        value.isoformat()
                        for value in candidates
                        if value is not None and start <= value < end
                    ),
                    None,
                )
        except ValueError as err:
            return self.json_message(str(err), HTTPStatus.BAD_REQUEST)
        return self.json(
            {
                "points": [dataclasses.asdict(point) for point in points],
                "date": day.isoformat(),
                "timezone": str(schedule.sun.timezone),
                **events,
                "ranges": ranges,
                "timing": timing,
            }
        )
