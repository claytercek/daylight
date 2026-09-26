"""The daylight integration."""

from __future__ import annotations

import datetime
import pathlib

from homeassistant.components import panel_custom
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import ConfigType

from .coordinator import DayCoordinator
from .http import PreviewTargetsView, SampleCurveView
from .schedule_config import async_schedule

PLATFORMS = (Platform.SWITCH, Platform.SENSOR)

_PANEL_JS_NAME = "daylight-curve-preview-panel.js"
_PANEL_JS_PATH = pathlib.Path(__file__).parent / "panel" / _PANEL_JS_NAME
_PANEL_URL_PATH = f"/daylight_panel/{_PANEL_JS_NAME}"


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register read-only preview views and the optional panel once per domain."""
    hass.http.register_view(SampleCurveView())
    hass.http.register_view(PreviewTargetsView())

    # cache_headers=False: the panel JS is still actively being built, so
    # dev iteration shouldn't fight a cached placeholder.
    await hass.http.async_register_static_paths(
        [StaticPathConfig(_PANEL_URL_PATH, str(_PANEL_JS_PATH), False)]
    )
    await panel_custom.async_register_panel(
        hass,
        frontend_url_path="daylight-preview",
        webcomponent_name="daylight-curve-preview-panel",
        module_url=_PANEL_URL_PATH,
        require_admin=False,
    )

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a daylight hub entry: build its coordinator, forward platforms."""
    data = entry.data
    schedule = await async_schedule(hass, data)

    update_interval = datetime.timedelta(seconds=data["update_interval_seconds"])
    coordinator = DayCoordinator(hass, schedule, update_interval, name=entry.title)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))

    return True


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the hub entry, e.g. after a target subentry add/remove/edit."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a daylight hub entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
