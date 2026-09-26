"""Preview accepts only saved identifiers, never configuration to apply."""

from copy import deepcopy
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.daylight.const import DOMAIN
from custom_components.daylight.coordinator import DayCoordinator
from custom_components.daylight.http import MAX_NUM_POINTS, SAMPLE_CURVE_SCHEMA
from custom_components.daylight.schedule_config import async_schedule, default_hub_data

from .switch_support import _target_subentry

URL = "/api/daylight/sample_curve"
CATALOG = "/api/daylight/preview_targets"


@pytest.fixture
async def preview(hass, enable_custom_integrations, hass_config_dir, hass_client):
    hass.config.config_dir = hass_config_dir
    hass.config.latitude, hass.config.longitude = 40.7128, -74.006
    await hass.config.async_set_time_zone("America/New_York")
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Daylight",
        data=default_hub_data(),
        version=2,
        subentries_data=[
            _target_subentry(
                "Kitchen",
                min_brightness_pct=10,
                max_brightness_pct=100,
                min_color_temp_kelvin=2500,
                max_color_temp_kelvin=4000,
            )
        ],
    )
    entry.add_to_hass(hass)
    return await hass_client(), entry


def payload(entry, **overrides):
    return {
        "entry_id": entry.entry_id,
        "target_id": next(iter(entry.subentries)),
        "date": "2026-06-21",
        "num_points": 5,
        **overrides,
    }


async def test_catalog_lists_saved_targets_and_home_assistant_local_date(preview):
    client, entry = preview
    with patch(
        "custom_components.daylight.http.dt_util.utcnow",
        return_value=datetime(2026, 6, 21, 2, tzinfo=UTC),
    ):
        response = await client.get(CATALOG)
    assert response.status == 200
    data = await response.json()
    assert data["today"] == "2026-06-20"
    assert data["timezone"] == "America/New_York"
    assert data["targets"] == [
        {
            "entry_id": entry.entry_id,
            "target_id": next(iter(entry.subentries)),
            "name": "Daylight: Kitchen",
        }
    ]


async def test_saved_curve_matches_runtime_and_does_not_mutate_settings(hass, preview):
    client, entry = preview
    original = deepcopy(dict(entry.data))
    response = await client.post(URL, json=payload(entry, num_points=97))
    assert response.status == 200
    data = await response.json()
    coordinator = DayCoordinator(hass, await async_schedule(hass, entry.data))
    assert len(data["points"]) == 97
    for point in data["points"]:
        state = coordinator.compute_day_state(datetime.fromisoformat(point["utc_time"]))
        assert point["brightness_pct"] == round(10 + 90 * state.brightness_factor)
        assert point["color_temp_kelvin"] == round(2500 + 1500 * state.color_factor)
    assert data["timezone"] == "America/New_York"
    assert not data["timing"]["polar_fallback"]
    assert entry.data == original


async def test_reads_newly_saved_settings_not_a_client_copy(hass, preview):
    client, entry = preview
    data = deepcopy(dict(entry.data))
    data["schedule"]["basic"].update(morning_time="09:00:00", evening_time="11:00:00")
    hass.config_entries.async_update_entry(entry, data=data)
    response = await client.post(URL, json=payload(entry))
    body = await response.json()
    assert body["timing"]["compressed"] is True
    assert body["timing"]["brightness"]["morning_start"].startswith(
        "2026-06-21T09:00:00"
    )
    assert body["timing"]["brightness"]["evening_end"].startswith("2026-06-21T11:00:00")


@pytest.mark.parametrize(
    "extra",
    [
        {"brightness": {"min_brightness_pct": 90}},
        {"schedule": {}},
        {"start": "2026-06-21T00:00:00Z"},
    ],
)
async def test_posted_settings_are_rejected(preview, extra):
    client, entry = preview
    response = await client.post(URL, json={**payload(entry), **extra})
    assert response.status == 400


@pytest.mark.parametrize("changed", [{"entry_id": "missing"}, {"target_id": "missing"}])
async def test_unknown_ids_are_not_found(preview, changed):
    client, entry = preview
    response = await client.post(URL, json=payload(entry, **changed))
    assert response.status == 404


async def test_target_cannot_be_borrowed_from_another_hub(hass, preview):
    client, entry = preview
    other = MockConfigEntry(
        domain=DOMAIN,
        data=default_hub_data(),
        version=2,
        subentries_data=[_target_subentry("Other")],
    )
    other.add_to_hass(hass)
    response = await client.post(
        URL, json=payload(entry, target_id=next(iter(other.subentries)))
    )
    assert response.status == 404


async def test_unrelated_integration_is_not_a_preview_target(hass, preview):
    client, entry = preview
    other = MockConfigEntry(domain="other", data=default_hub_data())
    other.add_to_hass(hass)
    response = await client.post(URL, json=payload(entry, entry_id=other.entry_id))
    assert response.status == 404


async def test_invalid_saved_curve_returns_a_useful_error(hass, preview):
    client, entry = preview
    data = deepcopy(dict(entry.data))
    data["schedule"]["basic"]["morning_time"] = "23:00:00"
    hass.config_entries.async_update_entry(entry, data=data)
    response = await client.post(URL, json=payload(entry))
    assert response.status == 400
    assert "evening occurs before morning" in (await response.json())["message"]


async def test_polar_fallback_explained_without_fake_sunrise(hass, preview):
    client, entry = preview
    hass.config.latitude, hass.config.longitude = 78.2, 15.6
    await hass.config.async_set_time_zone("Arctic/Longyearbyen")
    response = await client.post(URL, json=payload(entry, date="2026-12-21"))
    assert response.status == 200
    data = await response.json()
    assert data["sunrise"] is data["sunset"] is None
    assert data["timing"]["polar_fallback"] is True


@pytest.mark.parametrize("day,hours", [("2026-03-08", 23), ("2026-11-01", 25)])
async def test_entire_local_day_including_dst(preview, day, hours):
    client, entry = preview
    response = await client.post(URL, json=payload(entry, date=day))
    data = await response.json()
    start, end = [
        datetime.fromisoformat(point["utc_time"])
        for point in (data["points"][0], data["points"][-1])
    ]
    assert (end - start).total_seconds() == hours * 3600


async def test_sun_markers_belong_to_selected_calendar_day(hass, preview):
    client, entry = preview
    await hass.config.async_set_time_zone("UTC")
    response = await client.post(URL, json=payload(entry, date="2026-06-22"))
    assert response.status == 200
    body = await response.json()
    # NYC sunset falls just after UTC midnight, from the previous solar cycle.
    sunset = datetime.fromisoformat(body["sunset"])
    assert sunset.date().isoformat() == "2026-06-22"
    assert sunset.hour == 0


async def test_missing_date_defaults_to_home_assistant_today(preview):
    client, entry = preview
    request = payload(entry)
    del request["date"]
    with patch(
        "custom_components.daylight.http.dt_util.utcnow",
        return_value=datetime(2026, 6, 21, 2, tzinfo=UTC),
    ):
        response = await client.post(URL, json=request)
    assert (await response.json())["date"] == "2026-06-20"


@pytest.mark.parametrize("value", [0, 1, MAX_NUM_POINTS + 1, 1000000])
async def test_sampling_work_is_bounded(preview, value):
    client, entry = preview
    with patch("custom_components.daylight.http.sample_curve") as sample:
        response = await client.post(URL, json=payload(entry, num_points=value))
    assert response.status == 400
    sample.assert_not_called()


@pytest.mark.parametrize("value", [2, MAX_NUM_POINTS])
def test_sampling_bounds(value):
    data = SAMPLE_CURVE_SCHEMA(
        {"entry_id": "hub", "target_id": "target", "num_points": value}
    )
    assert data["num_points"] == value


async def test_preview_requires_authentication(preview, hass_client_no_auth):
    _, entry = preview
    client = await hass_client_no_auth()
    assert (await client.get(CATALOG)).status == 401
    assert (await client.post(URL, json=payload(entry))).status == 401
