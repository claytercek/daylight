"""Tests for the daylight config/subentry flow.

Every test drives the flow the way HA itself does: `async_init` then
`async_configure`, asserting on the returned `FlowResult`. `enable_custom_integrations`
is required here (unlike the pure-logic test modules) because these tests go
through `hass.config_entries`, which uses HA's loader-based discovery.
"""

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
