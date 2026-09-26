"""Tests for sensor.py: hub-level diagnostic sensors mirroring DayState.

Hub-level, not per-target: one `sun_position`/`day_progress` pair per hub
entry, built straight off `entry.runtime_data` -- no subentry iteration.
Coordinator construction still needs the real `hass` fixture (see
test_coordinator.py), but `coordinator.data` is set directly to a
hand-built `DayState` rather than routed through a real refresh, so these
tests don't depend on astral/CurveSettings math at all.
"""

import datetime
from unittest.mock import MagicMock

import astral
import pytest
from homeassistant.components.sensor import SensorStateClass
from homeassistant.const import PERCENTAGE
from homeassistant.helpers.entity import EntityCategory
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.daylight import sensor
from custom_components.daylight.const import DOMAIN
from custom_components.daylight.coordinator import DayCoordinator, DayState
from custom_components.daylight.schedule import Schedule
from custom_components.daylight.solar import SunEvents

UTC = datetime.UTC
_NYC_OBSERVER = astral.Observer(latitude=40.7128, longitude=-74.0060, elevation=10)


def _curve_settings() -> Schedule:
    return Schedule(SunEvents(_NYC_OBSERVER))


def _day_state(sun_position: float) -> DayState:
    return DayState(
        utc_now=datetime.datetime(2026, 6, 21, 12, 0, tzinfo=UTC),
        sun_position=sun_position,
        brightness_factor=0.0,
        color_factor=0.0,
        is_above_horizon=sun_position >= 0,
        next_sunrise=datetime.datetime(2026, 6, 22, 9, 24, tzinfo=UTC),
        next_sunset=datetime.datetime(2026, 6, 21, 0, 31, tzinfo=UTC),
    )


async def test_sun_position_native_value_mirrors_day_state(hass) -> None:
    coordinator = DayCoordinator(hass, _curve_settings())
    coordinator.data = _day_state(sun_position=0.5)

    entity = sensor.SunPositionSensor(coordinator, "entry_id")

    assert entity.native_value == 0.5


async def test_sun_position_native_value_is_none_before_first_refresh(hass) -> None:
    coordinator = DayCoordinator(hass, _curve_settings())

    entity = sensor.SunPositionSensor(coordinator, "entry_id")

    assert entity.native_value is None


async def test_sun_position_entity_attributes(hass) -> None:
    coordinator = DayCoordinator(hass, _curve_settings())

    entity = sensor.SunPositionSensor(coordinator, "entry_id")

    assert entity.state_class is SensorStateClass.MEASUREMENT
    assert entity.entity_category is EntityCategory.DIAGNOSTIC
    assert entity.unique_id == "entry_id_sun_position"


@pytest.mark.parametrize(
    ("sun_position", "expected_percent"),
    [
        (0.5, 75),
        (-0.4, 30),
        (-1.0, 0),
        (1.0, 100),
    ],
)
async def test_day_progress_native_value_rescales_sun_position(
    hass, sun_position: float, expected_percent: int
) -> None:
    coordinator = DayCoordinator(hass, _curve_settings())
    coordinator.data = _day_state(sun_position=sun_position)

    entity = sensor.DayProgressSensor(coordinator, "entry_id")

    assert entity.native_value == expected_percent


async def test_day_progress_native_value_is_none_before_first_refresh(hass) -> None:
    coordinator = DayCoordinator(hass, _curve_settings())

    entity = sensor.DayProgressSensor(coordinator, "entry_id")

    assert entity.native_value is None


async def test_day_progress_entity_attributes(hass) -> None:
    coordinator = DayCoordinator(hass, _curve_settings())

    entity = sensor.DayProgressSensor(coordinator, "entry_id")

    assert entity.state_class is SensorStateClass.MEASUREMENT
    assert entity.entity_category is EntityCategory.DIAGNOSTIC
    assert entity.native_unit_of_measurement == PERCENTAGE
    assert entity.unique_id == "entry_id_day_progress"


async def test_async_setup_entry_adds_both_hub_level_sensors(hass) -> None:
    """Hub-level: built straight off `entry.runtime_data`, no subentry
    iteration and no `config_subentry_id` passed to `async_add_entities`.
    """
    coordinator = DayCoordinator(hass, _curve_settings())
    entry = MockConfigEntry(domain=DOMAIN, entry_id="test_entry_id")
    entry.runtime_data = coordinator
    add_entities = MagicMock()

    await sensor.async_setup_entry(hass, entry, add_entities)

    add_entities.assert_called_once()
    assert add_entities.call_args.kwargs == {}
    entities = add_entities.call_args.args[0]
    unique_ids = {entity.unique_id for entity in entities}
    assert unique_ids == {"test_entry_id_sun_position", "test_entry_id_day_progress"}
