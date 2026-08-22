"""Binary sensors for ELCO Aerotop."""

from __future__ import annotations

from collections.abc import Callable

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory

from .capabilities import supports_cooling, supports_room_sensor
from .coordinator import ElcoDataUpdateCoordinator
from .entity import ElcoAerotopEntity
from .models import ElcoData


def _system_boolean(data: ElcoData, item_id: str) -> bool | None:
    item = data.discovery.system_item(item_id)
    if not item or item.get("error") is True or item.get("invalid") is True:
        return None
    value = item.get("value")
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return bool(value)
    return None


def _heat_pump_running(data: ElcoData) -> bool | None:
    return (
        data.plant.heat_pump_on
        if data.plant.heat_pump_on is not None
        else _system_boolean(data, "IsHeatingPumpOn")
    )


class ElcoBinarySensor(ElcoAerotopEntity, BinarySensorEntity):
    def __init__(
        self,
        coordinator: ElcoDataUpdateCoordinator,
        key: str,
        name: str,
        value_fn: Callable[[ElcoData], bool | None],
        device_class: BinarySensorDeviceClass = BinarySensorDeviceClass.RUNNING,
        entity_category: EntityCategory | None = None,
    ) -> None:
        super().__init__(coordinator, key)
        self._attr_name = name
        self._attr_device_class = device_class
        self._attr_entity_category = entity_category
        self._value_fn = value_fn

    @property
    def is_on(self) -> bool | None:
        return self._value_fn(self.coordinator.data)


class ElcoControllerErrorBinarySensor(ElcoBinarySensor):
    """Expose the controller error state and its current error records."""

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        errors = self.coordinator.data.discovery.bus_errors
        return {"errors": errors[:10]} if isinstance(errors, list) else {}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities,
) -> None:
    coordinator: ElcoDataUpdateCoordinator = entry.runtime_data
    data = coordinator.data
    features = data.discovery.features
    entities: list[BinarySensorEntity] = []

    if isinstance(data.discovery.bus_errors, list):
        entities.append(
            ElcoControllerErrorBinarySensor(
                coordinator,
                "controller_error",
                "Controller error",
                lambda state: bool(state.discovery.bus_errors),
                device_class=BinarySensorDeviceClass.PROBLEM,
                entity_category=EntityCategory.DIAGNOSTIC,
            )
        )

    if (features.get("hpSys") or _heat_pump_running(data) is True) and (
        _heat_pump_running(data) is not None
    ):
        entities.append(
            ElcoBinarySensor(
                coordinator,
                "heat_pump_running",
                "Heat pump running",
                _heat_pump_running,
            )
        )
    has_boiler = bool(
        features.get("hasBoiler")
        or features.get("convBoiler")
        or features.get("commBoiler")
        or data.plant.flame_on is True
    )
    if has_boiler and data.plant.flame_on is not None:
        entities.append(
            ElcoBinarySensor(
                coordinator,
                "flame_on",
                "Flame on",
                lambda state: state.plant.flame_on,
            )
        )
    if not features.get("dhwHidden", False) and data.plant.dhw_enabled is not None:
        entities.append(
            ElcoBinarySensor(
                coordinator,
                "dhw_enabled",
                "Domestic hot water enabled",
                lambda state: state.plant.dhw_enabled,
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
        if key == "outside_temperature_error" and data.plant.has_outside_temperature_probe is False:
            continue
        if key == "dhw_temperature_error" and data.plant.has_dhw_temperature_probe is False:
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
        has_cooling = supports_cooling(features, zone)
        zone_specs = (
            ("heat_request", "heat request", zone.heat_or_cool_request, "heat_or_cool_request"),
            ("heating_active", "heating active", zone.heating_active, "heating_active"),
            ("cooling_active", "cooling active", zone.cooling_active, "cooling_active"),
        )
        for key_suffix, label, current_value, attribute in zone_specs:
            if current_value is None:
                continue
            if key_suffix == "cooling_active" and not has_cooling:
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
        if supports_room_sensor(zone) and zone.room_temperature_error is not None:
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
