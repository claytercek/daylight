"""Diagnostic sensors for a daylight hub."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import DayCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the hub-level diagnostic sensors for a daylight hub entry.

    Hub-level, not per-target: one `sun_position`/`day_progress` pair per
    hub entry, describing the shared sun/curve state -- not iterated over
    `entry.subentries`.
    """
    coordinator = entry.runtime_data
    async_add_entities(
        [
            SunPositionSensor(coordinator, entry.entry_id),
            DayProgressSensor(coordinator, entry.entry_id),
        ]
    )


class SunPositionSensor(CoordinatorEntity[DayCoordinator], SensorEntity):
    """Mirrors `DayState.sun_position`: [-1, 1], -1 solar midnight, +1 solar noon."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: DayCoordinator, entry_id: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry_id}_sun_position"

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.sun_position


class DayProgressSensor(CoordinatorEntity[DayCoordinator], SensorEntity):
    """Rescales `sun_position`'s [-1, 1] range into a 0-100% day/night cycle."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_native_unit_of_measurement = PERCENTAGE

    def __init__(self, coordinator: DayCoordinator, entry_id: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry_id}_day_progress"

    @property
    def native_value(self) -> int | None:
        if self.coordinator.data is None:
            return None
        return round(((self.coordinator.data.sun_position + 1) / 2) * 100)
