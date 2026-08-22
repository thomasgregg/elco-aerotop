"""Writable temperatures for ELCO Aerotop."""

from __future__ import annotations

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory

from .coordinator import ElcoDataUpdateCoordinator
from .entity import ElcoAerotopEntity
from .models import NumericVariable


class ElcoTemperatureNumber(ElcoAerotopEntity, NumberEntity):
    _attr_device_class = NumberDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_mode = NumberMode.BOX
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: ElcoDataUpdateCoordinator,
        key: str,
        name: str,
        variable_fn,
        write_fn,
        *,
        enabled_default: bool = True,
    ) -> None:
        super().__init__(coordinator, key)
        self._attr_name = name
        self._attr_entity_registry_enabled_default = enabled_default
        self._variable_fn = variable_fn
        self._write_fn = write_fn

    @property
    def _variable(self) -> NumericVariable:
        return self._variable_fn(self.coordinator.data)

    @property
    def native_value(self) -> float | None:
        return self._variable.value

    @property
    def native_min_value(self) -> float:
        return self._variable.minimum if self._variable.minimum is not None else 5.0

    @property
    def native_max_value(self) -> float:
        return self._variable.maximum if self._variable.maximum is not None else 65.0

    @property
    def native_step(self) -> float:
        return self._variable.step if self._variable.step is not None else 0.5

    async def async_set_native_value(self, value: float) -> None:
        await self._write_fn(value)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities,
) -> None:
    coordinator: ElcoDataUpdateCoordinator = entry.runtime_data
    entities: list[NumberEntity] = []
    if coordinator.data.plant.dhw_comfort_temperature.value is not None:
        entities.append(
            ElcoTemperatureNumber(
                coordinator,
                "dhw_comfort_temperature",
                "Domestic hot water comfort temperature",
                lambda data: data.plant.dhw_comfort_temperature,
                lambda value: coordinator.async_set_dhw(comfort=value),
                enabled_default=False,
            )
        )
    if coordinator.data.plant.dhw_reduced_temperature.value is not None:
        entities.append(
            ElcoTemperatureNumber(
                coordinator,
                "dhw_reduced_temperature",
                "Domestic hot water reduced temperature",
                lambda data: data.plant.dhw_reduced_temperature,
                lambda value: coordinator.async_set_dhw(reduced=value),
            )
        )
    for zone_number in coordinator.data.zones:
        zone = coordinator.data.zones[zone_number]
        if zone.comfort_temperature.value is not None:
            entities.append(
                ElcoTemperatureNumber(
                    coordinator,
                    f"zone_{zone_number}_comfort_temperature",
                    f"Zone {zone_number} comfort temperature",
                    lambda data, zone=zone_number: data.zones[zone].comfort_temperature,
                    lambda value, zone=zone_number: coordinator.async_set_zone_temperature(
                        zone, "comfort", value
                    ),
                    enabled_default=False,
                )
            )
        if zone.reduced_temperature.value is not None:
            entities.append(
                ElcoTemperatureNumber(
                    coordinator,
                    f"zone_{zone_number}_reduced_temperature",
                    f"Zone {zone_number} reduced temperature",
                    lambda data, zone=zone_number: data.zones[zone].reduced_temperature,
                    lambda value, zone=zone_number: coordinator.async_set_zone_temperature(
                        zone, "reduced", value
                    ),
                )
            )
    async_add_entities(entities)
