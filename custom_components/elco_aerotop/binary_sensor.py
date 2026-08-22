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
        device_class: BinarySensorDeviceClass = BinarySensorDeviceClass.RUNNING,
    ) -> None:
        super().__init__(coordinator, key)
        self._attr_name = name
        self._attr_device_class = device_class
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
    data = coordinator.data
    entities: list[BinarySensorEntity] = []

    plant_specs = (
        ("heat_pump_running", "Heat pump running", data.plant.heat_pump_on, "heat_pump_on"),
        ("flame_on", "Flame on", data.plant.flame_on, "flame_on"),
        ("dhw_enabled", "Domestic hot water enabled", data.plant.dhw_enabled, "dhw_enabled"),
    )
    for key, name, current_value, attribute in plant_specs:
        if current_value is None:
            continue
        entities.append(
            ElcoBinarySensor(
                coordinator,
                key,
                name,
                lambda state, field=attribute: getattr(state.plant, field),
            )
        )

    plant_error_specs = (
        (
            "outside_temperature_error",
            "Outside temperature sensor problem",
            data.plant.outside_temperature_error,
            "outside_temperature_error",
        ),
        (
            "dhw_temperature_error",
            "Domestic hot water temperature sensor problem",
            data.plant.dhw_temperature_error,
            "dhw_temperature_error",
        ),
    )
    for key, name, current_value, attribute in plant_error_specs:
        if current_value is None:
            continue
        entities.append(
            ElcoBinarySensor(
                coordinator,
                key,
                name,
                lambda state, field=attribute: getattr(state.plant, field),
                BinarySensorDeviceClass.PROBLEM,
            )
        )

    for zone_number, zone in data.zones.items():
        zone_specs = (
            ("heat_request", "heat request", zone.heat_or_cool_request, "heat_or_cool_request"),
            ("heating_active", "heating active", zone.heating_active, "heating_active"),
            ("cooling_active", "cooling active", zone.cooling_active, "cooling_active"),
        )
        for key_suffix, label, current_value, attribute in zone_specs:
            if current_value is None:
                continue
            entities.append(
                ElcoBinarySensor(
                    coordinator,
                    f"zone_{zone_number}_{key_suffix}",
                    f"Zone {zone_number} {label}",
                    lambda state, zone_id=zone_number, field=attribute: getattr(
                        state.zones[zone_id], field
                    ),
                )
            )
        if zone.room_temperature_error is not None:
            entities.append(
                ElcoBinarySensor(
                    coordinator,
                    f"zone_{zone_number}_room_temperature_error",
                    f"Zone {zone_number} room temperature sensor problem",
                    lambda state, zone_id=zone_number: state.zones[zone_id].room_temperature_error,
                    BinarySensorDeviceClass.PROBLEM,
                )
            )
    async_add_entities(entities)
