"""Sensors for ELCO Aerotop."""

from __future__ import annotations

from collections.abc import Callable

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant

from .coordinator import ElcoDataUpdateCoordinator
from .entity import ElcoAerotopEntity
from .models import ElcoData


class ElcoSensor(ElcoAerotopEntity, SensorEntity):
    """A coordinator-backed ELCO sensor."""

    def __init__(
        self,
        coordinator: ElcoDataUpdateCoordinator,
        key: str,
        name: str,
        value_fn: Callable[[ElcoData], float | str | None],
        *,
        temperature: bool = False,
    ) -> None:
        super().__init__(coordinator, key)
        self._attr_name = name
        self._value_fn = value_fn
        if temperature:
            self._attr_device_class = SensorDeviceClass.TEMPERATURE
            self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
            self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> float | str | None:
        return self._value_fn(self.coordinator.data)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities,
) -> None:
    coordinator: ElcoDataUpdateCoordinator = entry.runtime_data
    entities: list[SensorEntity] = [
        ElcoSensor(
            coordinator,
            "outside_temperature",
            "Outside temperature",
            lambda data: data.plant.outside_temperature,
            temperature=True,
        ),
        ElcoSensor(
            coordinator,
            "dhw_current_temperature",
            "Domestic hot water temperature",
            lambda data: data.plant.dhw_current_temperature,
            temperature=True,
        ),
    ]
    for zone_number in coordinator.data.zones:
        entities.extend(
            [
                ElcoSensor(
                    coordinator,
                    f"zone_{zone_number}_desired_temperature",
                    f"Zone {zone_number} desired temperature",
                    lambda data, zone=zone_number: data.zones[zone].desired_temperature,
                    temperature=True,
                ),
                ElcoSensor(
                    coordinator,
                    f"zone_{zone_number}_room_temperature",
                    f"Zone {zone_number} room temperature",
                    lambda data, zone=zone_number: data.zones[zone].room_temperature,
                    temperature=True,
                ),
                ElcoSensor(
                    coordinator,
                    f"zone_{zone_number}_mode",
                    f"Zone {zone_number} mode",
                    lambda data, zone=zone_number: (
                        data.zones[zone].mode.current_label or data.zones[zone].mode.value
                    ),
                ),
            ]
        )
    async_add_entities(entities)
