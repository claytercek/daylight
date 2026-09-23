"""Tests for the daylight config/subentry flow.

Every test drives the flow the way HA itself does: `async_init` then
`async_configure`, asserting on the returned `FlowResult`. `enable_custom_integrations`
is required here (unlike the pure-logic test modules) because these tests go
through `hass.config_entries`, which uses HA's loader-based discovery.
"""

from unittest.mock import patch

from homeassistant.data_entry_flow import FlowResultType

from custom_components.daylight.const import DOMAIN


async def test_hub_user_step_shows_form(
    enable_custom_integrations, hass, hass_config_dir
) -> None:
    hass.config.config_dir = hass_config_dir

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"


async def test_hub_user_step_full_input_creates_entry(
    enable_custom_integrations, hass, hass_config_dir
) -> None:
    """Given a fully-specified input, the entry's `data` is the literal dict."""
    hass.config.config_dir = hass_config_dir
    with patch(
        "custom_components.daylight.async_setup_entry",
        return_value=True,
        create=True,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                "sunrise_time": "07:00:00",
                "min_sunrise_time": "06:00:00",
                "max_sunrise_time": "08:00:00",
                "sunset_time": "19:00:00",
                "min_sunset_time": "18:00:00",
                "max_sunset_time": "20:00:00",
                "sunrise_offset_minutes": -15,
                "sunset_offset_minutes": 30,
                "brightness_mode": "linear",
                "brightness_mode_time_dark_minutes": 60,
                "brightness_mode_time_light_minutes": 30,
                "update_interval_seconds": 120,
            },
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Daylight"
    assert result["data"] == {
        "sunrise_time": "07:00:00",
        "min_sunrise_time": "06:00:00",
        "max_sunrise_time": "08:00:00",
        "sunset_time": "19:00:00",
        "min_sunset_time": "18:00:00",
        "max_sunset_time": "20:00:00",
        "sunrise_offset_minutes": -15,
        "sunset_offset_minutes": 30,
        "brightness_mode": "linear",
        "brightness_mode_time_dark_minutes": 60,
        "brightness_mode_time_light_minutes": 30,
        "update_interval_seconds": 120,
    }
    assert result["result"].subentries == {}


async def test_hub_user_step_omitted_time_overrides_are_none(
    enable_custom_integrations, hass, hass_config_dir
) -> None:
    """Time overrides left blank in the form are stored as an explicit `None`."""
    hass.config.config_dir = hass_config_dir
    with patch(
        "custom_components.daylight.async_setup_entry",
        return_value=True,
        create=True,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                "sunrise_offset_minutes": 0,
                "sunset_offset_minutes": 0,
                "brightness_mode": "default",
                "brightness_mode_time_dark_minutes": 45,
                "brightness_mode_time_light_minutes": 45,
                "update_interval_seconds": 90,
            },
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {
        "sunrise_time": None,
        "min_sunrise_time": None,
        "max_sunrise_time": None,
        "sunset_time": None,
        "min_sunset_time": None,
        "max_sunset_time": None,
        "sunrise_offset_minutes": 0,
        "sunset_offset_minutes": 0,
        "brightness_mode": "default",
        "brightness_mode_time_dark_minutes": 45,
        "brightness_mode_time_light_minutes": 45,
        "update_interval_seconds": 90,
    }
