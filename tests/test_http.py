"""Tests for the `/api/daylight/sample_curve` and `/api/daylight/preview_fields`
HTTP preview endpoints.

Plain request/response endpoints for a not-yet-built frontend panel: the
client always initiates the request, so these are `HomeAssistantView`s
(`custom_components.daylight.http.SampleCurveView` /
`custom_components.daylight.http.PreviewFieldsView`), tested with the
`hass_client` fixture rather than `hass_ws_client`.
"""

import dataclasses
import datetime
from unittest.mock import patch

import astral
import pytest
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util

from custom_components.daylight.color_and_brightness import CurveSettings
from custom_components.daylight.const import DOMAIN
from custom_components.daylight.curve_preview import sample_curve
from custom_components.daylight.http import (
    _CONF_NUM_POINTS,
    _CONF_START,
    MAX_NUM_POINTS,
    SAMPLE_CURVE_SCHEMA,
)

_URL = "/api/daylight/sample_curve"
_PREVIEW_FIELDS_URL = "/api/daylight/preview_fields"

# Nested/sectioned shape: `brightness`/`color_temp` are `Required` sections
# (each holds a required field with no default), so every payload needs them.
_VALID_PAYLOAD = {
    "brightness": {"min_brightness_pct": 1, "max_brightness_pct": 100},
    "color_temp": {"min_color_temp_kelvin": 2000, "max_color_temp_kelvin": 5500},
}


async def _setup(hass, enable_custom_integrations, hass_config_dir) -> None:
    hass.config.config_dir = hass_config_dir
    hass.config.latitude = 40.7128
    hass.config.longitude = -74.0060
    hass.config.elevation = 10
    await hass.config.async_set_time_zone("America/New_York")

    # daylight's own async_setup ensures "http" is set up too.
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()


async def test_sample_curve_returns_points_for_full_field_set(
    enable_custom_integrations, hass, hass_config_dir, hass_client
) -> None:
    await _setup(hass, enable_custom_integrations, hass_config_dir)
    client = await hass_client()

    resp = await client.post(
        _URL,
        json={
            **_VALID_PAYLOAD,
            "sunrise": {"sunrise_time": "06:30:00"},
            "sunset": {"sunset_offset_minutes": -5},
            "brightness_curve": {
                "brightness_mode": "linear",
                "brightness_mode_time_dark_minutes": 30,
                "brightness_mode_time_light_minutes": 60,
            },
            "start": "2026-06-21T00:00:00+00:00",
            "num_points": 4,
        },
    )

    assert resp.status == 200
    body = await resp.json()
    points = body["points"]
    assert len(points) == 4
    assert points[0]["utc_time"] == "2026-06-21T00:00:00+00:00"
    for point in points:
        assert 1 <= point["brightness_pct"] <= 100
        assert 2000 <= point["color_temp_kelvin"] <= 5500

    # Both new top-level keys are ISO 8601 datetime strings.
    datetime.datetime.fromisoformat(body["sunrise"])
    datetime.datetime.fromisoformat(body["sunset"])


async def test_sample_curve_defaults_start_to_home_assistant_midnight(
    enable_custom_integrations, hass, hass_config_dir, hass_client
) -> None:
    await _setup(hass, enable_custom_integrations, hass_config_dir)
    client = await hass_client()

    frozen_now = datetime.datetime(2026, 6, 21, 2, 0, tzinfo=datetime.UTC)
    with patch(
        "custom_components.daylight.http.dt_util.utcnow", return_value=frozen_now
    ):
        resp = await client.post(_URL, json={**_VALID_PAYLOAD, "num_points": 1})

    assert resp.status == 200
    body = await resp.json()
    # UTC has reached June 21 while New York is still on June 20.
    assert body["points"][0]["utc_time"] == "2026-06-20T00:00:00-04:00"
    assert datetime.datetime.fromisoformat(body["sunrise"]).date() == datetime.date(
        2026, 6, 20
    )


async def test_sample_curve_rejects_missing_required_section(
    enable_custom_integrations, hass, hass_config_dir, hass_client
) -> None:
    """The voluptuous schema is the system-boundary validation."""
    await _setup(hass, enable_custom_integrations, hass_config_dir)
    client = await hass_client()

    payload = {"color_temp": _VALID_PAYLOAD["color_temp"]}

    resp = await client.post(_URL, json=payload)

    assert resp.status == 400


async def test_sample_curve_rejects_missing_required_field_within_section(
    enable_custom_integrations, hass, hass_config_dir, hass_client
) -> None:
    """A required section present but missing one of its required fields is
    still a 400, not just an absent section entirely.
    """
    await _setup(hass, enable_custom_integrations, hass_config_dir)
    client = await hass_client()

    payload = {
        "brightness": {"max_brightness_pct": 100},
        "color_temp": _VALID_PAYLOAD["color_temp"],
    }

    resp = await client.post(_URL, json=payload)

    assert resp.status == 400


async def test_sample_curve_invalid_sun_timing_returns_400_not_500(
    enable_custom_integrations, hass, hass_config_dir, hass_client
) -> None:
    """A manually-set sunrise time plus a day-long offset inverts sun-event
    order (`SunEvents._validate_sun_event_order`), raising `ValueError` from
    `CurveSettings.brightness_factor`. The endpoint must surface that as a
    structured 400, not let it propagate and kill the connection.
    """
    await _setup(hass, enable_custom_integrations, hass_config_dir)
    client = await hass_client()

    resp = await client.post(
        _URL,
        json={
            **_VALID_PAYLOAD,
            "sunrise": {
                "sunrise_time": "02:15:00",
                "sunrise_offset_minutes": -1440,
            },
            "start": "2026-06-21T00:00:00+00:00",
            "num_points": 1,
        },
    )

    assert resp.status == 400
    body = await resp.json()
    assert "not in the expected order" in body["message"]


async def test_sample_curve_nested_payload_matches_flat_computation(
    enable_custom_integrations, hass, hass_config_dir, hass_client
) -> None:
    """Regression guard for the flatten step (`_flatten_sections`): a
    nested/sectioned payload exercising all five sections must produce the
    same points as calling `sample_curve` directly with the equivalent flat
    values -- i.e. what the old flat POST shape would have computed.
    """
    await _setup(hass, enable_custom_integrations, hass_config_dir)
    client = await hass_client()

    start = datetime.datetime(2026, 6, 21, tzinfo=datetime.UTC)

    resp = await client.post(
        _URL,
        json={
            "sunrise": {"sunrise_time": "06:30:00"},
            "sunset": {"sunset_offset_minutes": -5},
            "brightness_curve": {
                "brightness_mode": "linear",
                "brightness_mode_time_dark_minutes": 30,
                "brightness_mode_time_light_minutes": 60,
            },
            "brightness": {"min_brightness_pct": 10, "max_brightness_pct": 90},
            "color_temp": {
                "min_color_temp_kelvin": 2200,
                "max_color_temp_kelvin": 6500,
            },
            "start": start.isoformat(),
            "num_points": 4,
        },
    )

    assert resp.status == 200
    body = await resp.json()

    astral_observer = astral.Observer(
        latitude=hass.config.latitude,
        longitude=hass.config.longitude,
        elevation=hass.config.elevation,
    )
    timezone = await dt_util.async_get_time_zone(hass.config.time_zone)
    assert timezone is not None
    curve_settings = CurveSettings(
        name="preview",
        astral_observer=astral_observer,
        timezone=timezone,
        sunrise_time=datetime.time(6, 30),
        min_sunrise_time=None,
        max_sunrise_time=None,
        sunset_time=None,
        min_sunset_time=None,
        max_sunset_time=None,
        sunrise_offset=datetime.timedelta(minutes=0),
        sunset_offset=datetime.timedelta(minutes=-5),
        brightness_mode="linear",
        brightness_mode_time_dark=datetime.timedelta(minutes=30),
        brightness_mode_time_light=datetime.timedelta(minutes=60),
    )
    expected_points = sample_curve(
        curve_settings,
        start=start,
        min_brightness_pct=10,
        max_brightness_pct=90,
        min_color_temp_kelvin=2200,
        max_color_temp_kelvin=6500,
        num_points=4,
    )

    assert body["points"] == [dataclasses.asdict(p) for p in expected_points]
    # sunrise/sunset are resolved for `start`'s calendar date in the hub's
    # own timezone, not the request's UTC offset.
    local_start = start.astimezone(timezone)
    assert body["sunrise"] == curve_settings.sun.sunrise(local_start).isoformat()
    assert body["sunset"] == curve_settings.sun.sunset(local_start).isoformat()


async def test_preview_fields_returns_expandable_sections(
    enable_custom_integrations, hass, hass_config_dir, hass_client
) -> None:
    """`<ha-form>`'s field list groups the curve-shape fields into the same
    `section()`-backed expandable groups `config_flow`'s own forms use.
    """
    await _setup(hass, enable_custom_integrations, hass_config_dir)
    client = await hass_client()

    resp = await client.get(_PREVIEW_FIELDS_URL)

    assert resp.status == 200
    fields = await resp.json()

    sections = {field["name"]: field for field in fields}
    assert set(sections) == {
        "sunrise",
        "sunset",
        "brightness_curve",
        "brightness",
        "color_temp",
    }
    for entry in sections.values():
        assert entry["type"] == "expandable"

    def names(section_name: str) -> set[str]:
        return {field["name"] for field in sections[section_name]["schema"]}

    assert names("sunrise") == {
        "sunrise_time",
        "min_sunrise_time",
        "max_sunrise_time",
        "sunrise_offset_minutes",
    }
    assert names("sunset") == {
        "sunset_time",
        "min_sunset_time",
        "max_sunset_time",
        "sunset_offset_minutes",
    }
    assert names("brightness_curve") == {
        "brightness_mode",
        "brightness_mode_time_dark_minutes",
        "brightness_mode_time_light_minutes",
    }
    assert names("brightness") == {"min_brightness_pct", "max_brightness_pct"}
    assert names("color_temp") == {
        "min_color_temp_kelvin",
        "max_color_temp_kelvin",
    }


async def test_preview_fields_matches_sample_curve_schema_minus_window_fields(
    enable_custom_integrations, hass, hass_config_dir, hass_client
) -> None:
    """Drift guard: both endpoints serialize/validate the same
    `SAMPLE_CURVE_SCHEMA`, so this should hold structurally. It exists to
    catch a future accidental divergence -- e.g. someone adding a field to
    one endpoint's logic without updating the schema -- not to reconcile two
    hand-kept field sets.

    `start`/`num_points` are endpoint-specific sampling-window knobs, not
    part of the curve shape a form would edit, so they're excluded from both
    sides of the comparison. The comparison is at the section-name level:
    both sides are keyed by section, not by the fields nested inside them.
    """
    await _setup(hass, enable_custom_integrations, hass_config_dir)
    client = await hass_client()

    resp = await client.get(_PREVIEW_FIELDS_URL)
    fields = await resp.json()

    field_names = {field["name"] for field in fields}
    schema_keys = {str(key) for key in SAMPLE_CURVE_SCHEMA.schema} - {
        _CONF_START,
        _CONF_NUM_POINTS,
    }
    assert field_names == schema_keys


@pytest.mark.parametrize("num_points", [-1, 0, MAX_NUM_POINTS + 1, 1_000_000])
async def test_sample_curve_rejects_unbounded_work(
    enable_custom_integrations, hass, hass_config_dir, hass_client, num_points
) -> None:
    await _setup(hass, enable_custom_integrations, hass_config_dir)
    client = await hass_client()

    with patch("custom_components.daylight.http.sample_curve") as sample:
        resp = await client.post(
            _URL, json={**_VALID_PAYLOAD, "num_points": num_points}
        )

    assert resp.status == 400
    sample.assert_not_called()


@pytest.mark.parametrize("num_points", [1, MAX_NUM_POINTS])
def test_sample_curve_accepts_sampling_bounds(num_points: int) -> None:
    validated = SAMPLE_CURVE_SCHEMA({**_VALID_PAYLOAD, "num_points": num_points})
    assert validated["num_points"] == num_points
