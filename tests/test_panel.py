"""Tests for the `daylight-preview` custom frontend panel registration.

Registration-only: the panel's real content (canvas, sliders, calls to the
`sample_curve`/`preview_fields` endpoints) is a separate follow-up, so this
only proves the pipeline end to end -- the panel is registered with the
frontend, and the JS module it points at is actually served.
"""

from homeassistant.components import frontend
from homeassistant.setup import async_setup_component

from custom_components.daylight.const import DOMAIN

_MODULE_URL = "/daylight_panel/daylight-curve-preview-panel.js"
_FRONTEND_URL_PATH = "daylight-preview"


async def _setup(hass, enable_custom_integrations, hass_config_dir) -> None:
    hass.config.config_dir = hass_config_dir
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()


async def test_async_setup_registers_the_preview_panel(
    enable_custom_integrations, hass, hass_config_dir
) -> None:
    await _setup(hass, enable_custom_integrations, hass_config_dir)

    assert frontend.async_panel_exists(hass, _FRONTEND_URL_PATH)

    # Not just "a panel exists" -- it must point at the same module_url the
    # static path below actually serves, otherwise the sidebar entry loads
    # nothing.
    panel = hass.data[frontend.DATA_PANELS][_FRONTEND_URL_PATH]
    assert panel.config["_panel_custom"]["module_url"] == _MODULE_URL


async def test_panel_module_js_is_served(
    enable_custom_integrations, hass, hass_config_dir, hass_client
) -> None:
    await _setup(hass, enable_custom_integrations, hass_config_dir)
    client = await hass_client()

    resp = await client.get(_MODULE_URL)

    assert resp.status == 200
    body = await resp.text()
    assert "daylight-curve-preview-panel" in body
