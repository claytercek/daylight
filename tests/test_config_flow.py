"""Tests for the daylight config/subentry flow.

Every test drives the flow the way HA itself does: `async_init` then
`async_configure`, asserting on the returned `FlowResult`. `enable_custom_integrations`
is required here (unlike the pure-logic test modules) because these tests go
through `hass.config_entries`, which uses HA's loader-based discovery.
"""

from unittest.mock import patch

from homeassistant.data_entry_flow import FlowResultType, section
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.daylight.config_flow import (
    DaylightConfigFlow,
    TargetSubentryFlowHandler,
)
from custom_components.daylight.const import DOMAIN

# The section-shaped dict a real target form submission produces...
_TARGET_INPUT = {
    "entities": ["light.kitchen", "light.den"],
    "brightness": {"min_brightness_pct": 10, "max_brightness_pct": 100},
    "color_temp": {"min_color_temp_kelvin": 2000, "max_color_temp_kelvin": 6500},
    "advanced": {
        "transition": 30.0,
        "adapt_only_on_state_change": True,
        "manual_control_reset_minutes": 15,
        "separate_turn_on_commands": False,
        "send_split_delay": 0.5,
    },
}
# ...and the flat dict it is stored as, which `switch.py` reads directly.
_TARGET_DATA = {
    "entities": ["light.kitchen", "light.den"],
    "min_brightness_pct": 10,
    "max_brightness_pct": 100,
    "min_color_temp_kelvin": 2000,
    "max_color_temp_kelvin": 6500,
    "transition": 30.0,
    "adapt_only_on_state_change": True,
    "manual_control_reset_minutes": 15,
    "separate_turn_on_commands": False,
    "send_split_delay": 0.5,
}


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
    """A fully-specified section-shaped input is stored as one flat dict."""
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
                "sunrise": {
                    "sunrise_time": "07:00:00",
                    "min_sunrise_time": "06:00:00",
                    "max_sunrise_time": "08:00:00",
                    "sunrise_offset_minutes": -15,
                },
                "sunset": {
                    "sunset_time": "19:00:00",
                    "min_sunset_time": "18:00:00",
                    "max_sunset_time": "20:00:00",
                    "sunset_offset_minutes": 30,
                },
                "brightness_curve": {
                    "brightness_mode": "linear",
                    "brightness_mode_time_dark_minutes": 60,
                    "brightness_mode_time_light_minutes": 30,
                },
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
                "sunrise": {"sunrise_offset_minutes": 0},
                "sunset": {"sunset_offset_minutes": 0},
                "brightness_curve": {
                    "brightness_mode": "default",
                    "brightness_mode_time_dark_minutes": 45,
                    "brightness_mode_time_light_minutes": 45,
                },
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


async def test_hub_user_step_applies_schema_defaults(
    enable_custom_integrations, hass, hass_config_dir
) -> None:
    """Keys omitted entirely (not just the time overrides) get their default."""
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
            result["flow_id"], user_input={}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["sunrise_offset_minutes"] == 0
    assert result["data"]["sunset_offset_minutes"] == 0
    assert result["data"]["brightness_mode"] == "default"
    assert result["data"]["brightness_mode_time_dark_minutes"] == 45
    assert result["data"]["brightness_mode_time_light_minutes"] == 45
    assert result["data"]["update_interval_seconds"] == 90


async def test_hub_user_step_applies_defaults_of_an_absent_section(
    enable_custom_integrations, hass, hass_config_dir
) -> None:
    """A section the user never touched still contributes its field defaults.

    `__init__.py` reads every hub key unconditionally, so an omitted section
    must not leave holes in the stored data.
    """
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
            user_input={"sunrise": {"sunrise_time": "07:00:00"}},
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {
        "sunrise_time": "07:00:00",
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


async def test_target_add_step_applies_manual_control_reset_default(
    enable_custom_integrations, hass, hass_config_dir
) -> None:
    hass.config.config_dir = hass_config_dir
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "target"), context={"source": "user"}
    )
    advanced_without_reset_minutes = {
        key: value
        for key, value in _TARGET_INPUT["advanced"].items()
        if key != "manual_control_reset_minutes"
    }
    input_without_reset_minutes = {
        **_TARGET_INPUT,
        "advanced": advanced_without_reset_minutes,
    }
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], user_input=input_without_reset_minutes
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    subentry = next(iter(entry.subentries.values()))
    assert subentry.data["manual_control_reset_minutes"] == 0


def test_hub_declares_target_subentry_type() -> None:
    entry = MockConfigEntry(domain=DOMAIN)

    assert DaylightConfigFlow.async_get_supported_subentry_types(entry) == {
        "target": TargetSubentryFlowHandler
    }


async def test_target_add_step_shows_form(
    enable_custom_integrations, hass, hass_config_dir
) -> None:
    hass.config.config_dir = hass_config_dir
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "target"), context={"source": "user"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"


async def test_target_add_step_submit_creates_subentry(
    enable_custom_integrations, hass, hass_config_dir
) -> None:
    """The section-shaped submission is stored as one flat dict."""
    hass.config.config_dir = hass_config_dir
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "target"), context={"source": "user"}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], user_input=_TARGET_INPUT
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    subentries = list(entry.subentries.values())
    assert len(subentries) == 1
    assert subentries[0].data == _TARGET_DATA


async def test_target_add_step_rejects_inverted_brightness_range(
    enable_custom_integrations, hass, hass_config_dir
) -> None:
    hass.config.config_dir = hass_config_dir
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "target"), context={"source": "user"}
    )
    bad_input = {
        **_TARGET_INPUT,
        "brightness": {"min_brightness_pct": 90, "max_brightness_pct": 10},
    }
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], user_input=bad_input
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"
    assert result["errors"] == {"base": "brightness_range_invalid"}
    assert entry.subentries == {}


async def test_target_add_step_rejects_inverted_color_temp_range(
    enable_custom_integrations, hass, hass_config_dir
) -> None:
    hass.config.config_dir = hass_config_dir
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "target"), context={"source": "user"}
    )
    bad_input = {
        **_TARGET_INPUT,
        "color_temp": {
            "min_color_temp_kelvin": 6500,
            "max_color_temp_kelvin": 2000,
        },
    }
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], user_input=bad_input
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"
    assert result["errors"] == {"base": "color_temp_range_invalid"}
    assert entry.subentries == {}


async def test_target_reconfigure_replaces_subentry_data(
    enable_custom_integrations, hass, hass_config_dir
) -> None:
    hass.config.config_dir = hass_config_dir
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={},
        subentries_data=[
            {
                "data": _TARGET_DATA,
                "subentry_type": "target",
                "title": "light.kitchen, light.den",
                "unique_id": None,
            }
        ],
    )
    entry.add_to_hass(hass)
    subentry_id = next(iter(entry.subentries))

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "target"),
        context={"source": "reconfigure", "subentry_id": subentry_id},
    )
    new_input = {
        **_TARGET_INPUT,
        "advanced": {**_TARGET_INPUT["advanced"], "transition": 5.0},
    }
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], user_input=new_input
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.subentries[subentry_id].data == {**_TARGET_DATA, "transition": 5.0}


def _suggested_values(schema) -> dict:
    """Read back the suggested value a shown form carries for every field."""
    values = {}
    for key, value in schema.schema.items():
        if isinstance(value, section):
            values[str(key)] = _suggested_values(value.schema)
        else:
            values[str(key)] = (key.description or {}).get("suggested_value")
    return values


async def test_target_reconfigure_form_shows_the_stored_values(
    enable_custom_integrations, hass, hass_config_dir
) -> None:
    """Stored (flat) values have to be re-nested to repopulate the sections.

    `add_suggested_values_to_schema` only descends into a section when the
    suggested values are nested under that section's name, so handing it the
    flat stored data would leave every sectioned field blank.
    """
    hass.config.config_dir = hass_config_dir
    stored = {
        **_TARGET_DATA,
        "min_brightness_pct": 7,
        "max_brightness_pct": 83,
        "min_color_temp_kelvin": 2222,
        "max_color_temp_kelvin": 5555,
        "transition": 12.5,
        "manual_control_reset_minutes": 42,
        "send_split_delay": 0.25,
    }
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={},
        subentries_data=[
            {
                "data": stored,
                "subentry_type": "target",
                "title": "light.kitchen, light.den",
                "unique_id": None,
            }
        ],
    )
    entry.add_to_hass(hass)
    subentry_id = next(iter(entry.subentries))

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "target"),
        context={"source": "reconfigure", "subentry_id": subentry_id},
    )

    assert result["type"] is FlowResultType.FORM
    assert _suggested_values(result["data_schema"]) == {
        "entities": ["light.kitchen", "light.den"],
        "brightness": {"min_brightness_pct": 7, "max_brightness_pct": 83},
        "color_temp": {
            "min_color_temp_kelvin": 2222,
            "max_color_temp_kelvin": 5555,
        },
        "advanced": {
            "transition": 12.5,
            "adapt_only_on_state_change": True,
            "manual_control_reset_minutes": 42,
            "separate_turn_on_commands": False,
            "send_split_delay": 0.25,
        },
    }


async def test_target_reconfigure_retitles_the_subentry_from_its_entities(
    enable_custom_integrations, hass, hass_config_dir
) -> None:
    """The title is derived from the entity list, so it has to track it."""
    hass.config.config_dir = hass_config_dir
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={},
        subentries_data=[
            {
                "data": _TARGET_DATA,
                "subentry_type": "target",
                "title": "light.kitchen, light.den",
                "unique_id": None,
            }
        ],
    )
    entry.add_to_hass(hass)
    subentry_id = next(iter(entry.subentries))

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "target"),
        context={"source": "reconfigure", "subentry_id": subentry_id},
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={**_TARGET_INPUT, "entities": ["light.porch"]},
    )

    assert result["type"] is FlowResultType.ABORT
    assert entry.subentries[subentry_id].title == "light.porch"
