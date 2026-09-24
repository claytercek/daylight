"""Tests for the `/api/daylight/sample_curve` and `/api/daylight/preview_fields`
HTTP preview endpoints.

Plain request/response endpoints for a not-yet-built frontend panel: the
client always initiates the request, so these are `HomeAssistantView`s
(`custom_components.daylight.http.SampleCurveView` /
`custom_components.daylight.http.PreviewFieldsView`), tested with the
`hass_client` fixture rather than `hass_ws_client`.
"""

import datetime
from unittest.mock import patch

from homeassistant.setup import async_setup_component

from custom_components.daylight.const import DOMAIN
from custom_components.daylight.http import (
    _CONF_NUM_POINTS,
    _CONF_START,
    SAMPLE_CURVE_SCHEMA,
)

_URL = "/api/daylight/sample_curve"
_PREVIEW_FIELDS_URL = "/api/daylight/preview_fields"

_VALID_PAYLOAD = {
    "min_brightness_pct": 1,
    "max_brightness_pct": 100,
    "min_color_temp_kelvin": 2000,
    "max_color_temp_kelvin": 5500,
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
            "sunrise_time": "06:30:00",
            "sunset_offset_minutes": -5,
            "brightness_mode": "linear",
            "brightness_mode_time_dark_minutes": 30,
            "brightness_mode_time_light_minutes": 60,
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


async def test_sample_curve_defaults_start_to_now(
    enable_custom_integrations, hass, hass_config_dir, hass_client
) -> None:
    await _setup(hass, enable_custom_integrations, hass_config_dir)
    client = await hass_client()

    frozen_now = datetime.datetime(2026, 6, 21, 12, 0, tzinfo=datetime.UTC)
    with patch(
        "custom_components.daylight.http.dt_util.utcnow", return_value=frozen_now
    ):
        resp = await client.post(_URL, json={**_VALID_PAYLOAD, "num_points": 1})

    assert resp.status == 200
    body = await resp.json()
    assert body["points"][0]["utc_time"] == frozen_now.isoformat()


async def test_sample_curve_rejects_missing_required_field(
    enable_custom_integrations, hass, hass_config_dir, hass_client
) -> None:
    """The voluptuous schema is the system-boundary validation."""
    await _setup(hass, enable_custom_integrations, hass_config_dir)
    client = await hass_client()

    payload = dict(_VALID_PAYLOAD)
    del payload["min_brightness_pct"]

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
            "sunrise_time": "02:15:00",
            "sunrise_offset_minutes": -1440,
            "start": "2026-06-21T00:00:00+00:00",
            "num_points": 1,
        },
    )

    assert resp.status == 400
    body = await resp.json()
    assert "not in the expected order" in body["message"]


async def test_preview_fields_returns_a_field_per_curve_shape_key(
    enable_custom_integrations, hass, hass_config_dir, hass_client
) -> None:
    """`<ha-form>`'s field list, keyed by name, for a not-yet-built frontend panel."""
    await _setup(hass, enable_custom_integrations, hass_config_dir)
    client = await hass_client()

    resp = await client.get(_PREVIEW_FIELDS_URL)

    assert resp.status == 200
    fields = await resp.json()
    assert all("name" in field and "selector" in field for field in fields)


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
    sides of the comparison.
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
