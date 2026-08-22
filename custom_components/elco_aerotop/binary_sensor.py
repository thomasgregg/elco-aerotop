"""Binary sensors for ELCO Aerotop."""

from __future__ import annotations

from collections.abc import Callable

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .coordinator import ElcoDataUpdateCoordinator
from .entity import ElcoAerotopEntity
from .models import ElcoData


class ElcoBinarySensor(ElcoAerotopEntity, BinarySensorEntity):
    def __init__(
        self,
        coordinator: ElcoDataUpdateCoordinator,
        key: str,
        name: str,
        value_fn: Callable[[ElcoData], bool | None],
    ) -> None:
        super().__init__(coordinator, key)
        self._attr_name = name
        self._attr_device_class = BinarySensorDeviceClass.RUNNING
        self._value_fn = value_fn

    @property
    def is_on(self) -> bool | None:
        return self._value_fn(self.coordinator.data)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities,
) -> None:
    coordinator: ElcoDataUpdateCoordinator = entry.runtime_data
    entities: list[BinarySensorEntity] = [
        ElcoBinarySensor(
            coordinator,
            "heat_pump_running",
            "Heat pump running",
            lambda data: data.plant.heat_pump_on,
        )
    ]
    for zone_number in coordinator.data.zones:
        entities.append(
            ElcoBinarySensor(
                coordinator,
                f"zone_{zone_number}_heat_request",
                f"Zone {zone_number} heat request",
                lambda data, zone=zone_number: data.zones[zone].heat_or_cool_request,
            )
        )
    async_add_entities(entities)
