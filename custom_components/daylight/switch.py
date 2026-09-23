"""Adaptation on/off switch for daylight targets.

One `AdaptSwitch` per target subentry -- there are no brightness/color/sleep
sub-switches. This module is the adapter layer: it owns no adaptation maths
(`adaptation.py`) and no manual-control state machine (`target.py`), only the
wiring between them, the coordinator, and hass.
"""

from __future__ import annotations

import dataclasses
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.core import Context, HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .adaptation import compute_turn_on_kwargs
from .coordinator import DayCoordinator, DayState
from .target import Target, TargetConfig

TARGET_SUBENTRY_TYPE = "target"


@dataclasses.dataclass(frozen=True)
class TargetSettings:
    """A target subentry's config, unpacked from its raw `data` dict."""

    entities: tuple[str, ...]
    min_brightness_pct: int
    max_brightness_pct: int
    min_color_temp_kelvin: int
    max_color_temp_kelvin: int
    transition: float
    adapt_only_on_state_change: bool
    separate_turn_on_commands: bool
    send_split_delay: float

    @classmethod
    def from_subentry_data(cls, data) -> TargetSettings:
        """Build settings from a target subentry's `data` mapping."""
        return cls(
            entities=tuple(data["entities"]),
            min_brightness_pct=data["min_brightness_pct"],
            max_brightness_pct=data["max_brightness_pct"],
            min_color_temp_kelvin=data["min_color_temp_kelvin"],
            max_color_temp_kelvin=data["max_color_temp_kelvin"],
            transition=data["transition"],
            adapt_only_on_state_change=data["adapt_only_on_state_change"],
            separate_turn_on_commands=data["separate_turn_on_commands"],
            send_split_delay=data["send_split_delay"],
        )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add one adaptation switch per target subentry of this hub entry."""
    coordinator = entry.runtime_data
    for subentry in entry.subentries.values():
        if subentry.subentry_type != TARGET_SUBENTRY_TYPE:
            continue
        async_add_entities(
            [AdaptSwitch(coordinator, subentry)],
            config_subentry_id=subentry.subentry_id,
        )


class AdaptSwitch(CoordinatorEntity[DayCoordinator], SwitchEntity, RestoreEntity):
    """Turns adaptation on/off for one target's member lights."""

    def __init__(self, coordinator: DayCoordinator, subentry: ConfigSubentry) -> None:
        super().__init__(coordinator)
        self._settings = TargetSettings.from_subentry_data(subentry.data)
        self._target = Target(
            TargetConfig(
                manual_control_reset_minutes=subentry.data[
                    "manual_control_reset_minutes"
                ]
            )
        )
        self._attr_unique_id = f"{subentry.subentry_id}_adapt"
        self._attr_name = f"{subentry.title} adapt"
        self._attr_is_on = False

    async def async_added_to_hass(self) -> None:
        """Restore the previous on/off state, defaulting to off."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None:
            self._attr_is_on = last_state.state == "on"

    async def async_turn_on(self, **kwargs) -> None:
        """Resume adaptation for this target."""
        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        """Pause adaptation for this target."""
        self._attr_is_on = False
        self.async_write_ha_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Push adapted values to every non-manual member on each poll tick."""
        super()._handle_coordinator_update()
        if not self.is_on or self._settings.adapt_only_on_state_change:
            return
        # `coordinator.data` is only safe to read here: this fires directly
        # off a fresh poll, so it cannot be stale.
        day_state = self.coordinator.data
        if day_state is None:
            return
        now = dt_util.utcnow().timestamp()
        for entity_id in self._settings.entities:
            if self._target.is_manual(entity_id, now=now):
                continue
            self._async_adapt(entity_id, day_state, now)

    @callback
    def _async_adapt(self, entity_id: str, day_state: DayState, now: float) -> None:
        """Record and dispatch one member's adaptation command."""
        state = self.hass.states.get(entity_id)
        if state is None:
            return
        kwargs = compute_turn_on_kwargs(
            supported_color_modes=set(
                state.attributes.get("supported_color_modes") or []
            ),
            brightness_factor=day_state.brightness_factor,
            color_factor=day_state.color_factor,
            min_brightness_pct=self._settings.min_brightness_pct,
            max_brightness_pct=self._settings.max_brightness_pct,
            min_color_temp_kelvin=self._settings.min_color_temp_kelvin,
            max_color_temp_kelvin=self._settings.max_color_temp_kelvin,
            transition=self._settings.transition,
        )
        context = Context()
        # Strictly before the service call, and synchronously, so the
        # resulting state report can never race this bookkeeping.
        self._target.record_command(
            entity_id,
            context.id,
            kwargs,
            transition_seconds=self._settings.transition,
            now=now,
        )
        self.hass.async_create_task(self._async_send(entity_id, kwargs, context))

    async def _async_send(
        self, entity_id: str, kwargs: dict[str, Any], context: Context
    ) -> None:
        """Issue the `light.turn_on` call(s) for one member."""
        await self.hass.services.async_call(
            "light", "turn_on", {"entity_id": entity_id, **kwargs}, context=context
        )
