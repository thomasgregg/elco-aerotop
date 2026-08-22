"""Sensors for ELCO Aerotop."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfPressure, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory

from .capabilities import supports_cooling, supports_room_sensor
from .const import BSB_ENTITY_ADDRESSES
from .coordinator import ElcoDataUpdateCoordinator
from .entity import ElcoAerotopEntity
from .models import ElcoData, NumericVariable, bsb_point_available, bsb_point_value


class ElcoSensor(ElcoAerotopEntity, SensorEntity):
    """A coordinator-backed ELCO sensor."""

    def __init__(
        self,
        coordinator: ElcoDataUpdateCoordinator,
        key: str,
        name: str,
        value_fn: Callable[[ElcoData], float | int | str | None],
        *,
        temperature: bool = False,
        pressure: bool = False,
        entity_category: EntityCategory | None = None,
        measurement: bool = False,
        available_fn: Callable[[ElcoData], bool] | None = None,
        enabled_default: bool = True,
    ) -> None:
        super().__init__(coordinator, key)
        self._attr_name = name
        self._value_fn = value_fn
        self._available_fn = available_fn
        self._attr_entity_category = entity_category
        self._attr_entity_registry_enabled_default = enabled_default
        if temperature:
            self._attr_device_class = SensorDeviceClass.TEMPERATURE
            self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
            self._attr_state_class = SensorStateClass.MEASUREMENT
        elif pressure:
            self._attr_device_class = SensorDeviceClass.PRESSURE
            self._attr_native_unit_of_measurement = UnitOfPressure.BAR
            self._attr_state_class = SensorStateClass.MEASUREMENT
        elif measurement:
            self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> float | int | str | None:
        return self._value_fn(self.coordinator.data)

    @property
    def available(self) -> bool:
        """Return dynamic source availability without removing the entity."""
        return super().available and (
            self._available_fn(self.coordinator.data) if self._available_fn else True
        )


def _as_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _bsb_value(data: ElcoData, address: str) -> Any:
    return bsb_point_value(data.discovery.bsb_points.get(address))


def _has_system_item(data: ElcoData, item_id: str, zone: int = 0) -> bool:
    item = data.discovery.system_item(item_id, zone)
    return bool(
        item
        and item.get("available", True) is not False
        and item.get("isAvailable", True) is not False
        and item.get("error", False) is not True
        and item.get("invalid", False) is not True
        and _as_number(item.get("value")) is not None
    )


def _bsb_available(data: ElcoData, address: str) -> bool:
    return bsb_point_available(data.discovery.bsb_points.get(address))


def _zone_temperature_value(data: ElcoData, zone_number: int, attribute: str) -> float | None:
    value = getattr(data.zones[zone_number], attribute)
    if isinstance(value, NumericVariable):
        value = value.value
    if attribute.endswith("holiday_temperature") and value == 0:
        return None
    return value


def _plant_location(data: ElcoData) -> str | None:
    location = data.discovery.plant_metadata.get("location")
    if not isinstance(location, dict):
        return None
    locality = " ".join(
        str(value) for value in (location.get("postalCode"), location.get("cityName")) if value
    )
    parts = [location.get("addr"), locality or None, location.get("country")]
    rendered = ", ".join(str(part) for part in parts if part)
    return rendered or None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities,
) -> None:
    coordinator: ElcoDataUpdateCoordinator = entry.runtime_data
    data = coordinator.data
    features = data.discovery.features
    entities: list[SensorEntity] = []

    metadata_specs = (
        ("gateway_serial", "Gateway serial", "gwSerial"),
        ("plant_name", "Plant name", "plantName"),
        ("gateway_firmware", "Gateway firmware", "gwFwVer"),
    )
    for key, name, field in metadata_specs:
        if not data.discovery.plant_metadata.get(field):
            continue
        entities.append(
            ElcoSensor(
                coordinator,
                key,
                name,
                lambda state, attribute=field: state.discovery.plant_metadata.get(attribute),
                entity_category=EntityCategory.DIAGNOSTIC,
            )
        )
    if _plant_location(data) is not None:
        entities.append(
            ElcoSensor(
                coordinator,
                "plant_location",
                "Plant location",
                _plant_location,
                entity_category=EntityCategory.DIAGNOSTIC,
            )
        )
    if isinstance(data.discovery.bus_errors, list):
        entities.append(
            ElcoSensor(
                coordinator,
                "controller_error_count",
                "Controller error count",
                lambda state: len(state.discovery.bus_errors),
                entity_category=EntityCategory.DIAGNOSTIC,
                measurement=True,
            )
        )

    if (
        data.plant.outside_temperature is not None
        or data.plant.has_outside_temperature_probe is True
    ):
        entities.append(
            ElcoSensor(
                coordinator,
                "outside_temperature",
                "Outside temperature",
                lambda state: state.plant.outside_temperature,
                temperature=True,
            )
        )
    if (
        data.plant.dhw_current_temperature is not None
        or data.plant.has_dhw_temperature_probe is True
    ):
        entities.append(
            ElcoSensor(
                coordinator,
                "dhw_current_temperature",
                "Domestic hot water temperature",
                lambda state: state.plant.dhw_current_temperature,
                temperature=True,
            )
        )

    system_sensor_specs = (
        ("HeatingCircuitPressure", "heating_circuit_pressure", "Heating circuit pressure", True),
        (
            "ChFlowTemp",
            "heating_circuit_flow_temperature",
            "Heating circuit flow temperature",
            False,
        ),
        (
            "ChFlowSetpointTemp",
            "heating_circuit_flow_setpoint_temperature",
            "Heating circuit flow setpoint temperature",
            False,
        ),
    )
    for item_id, key, name, is_pressure in system_sensor_specs:
        if _has_system_item(data, item_id) or item_id in {
            "ChFlowTemp",
            "ChFlowSetpointTemp",
        }:
            entities.append(
                ElcoSensor(
                    coordinator,
                    key,
                    name,
                    lambda state, source=item_id: _as_number(state.discovery.system_value(source)),
                    temperature=not is_pressure,
                    pressure=is_pressure,
                )
            )

    for zone_number, zone in data.zones.items():
        has_cooling = supports_cooling(features, zone)
        if zone.desired_temperature is not None:
            entities.append(
                ElcoSensor(
                    coordinator,
                    f"zone_{zone_number}_desired_temperature",
                    f"Zone {zone_number} desired temperature",
                    lambda state, zone_id=zone_number: state.zones[zone_id].desired_temperature,
                    temperature=True,
                )
            )
        if supports_room_sensor(zone):
            entities.append(
                ElcoSensor(
                    coordinator,
                    f"zone_{zone_number}_room_temperature",
                    f"Zone {zone_number} room temperature",
                    lambda state, zone_id=zone_number: state.zones[zone_id].room_temperature,
                    temperature=True,
                )
            )
        if zone.mode.value is not None:
            entities.append(
                ElcoSensor(
                    coordinator,
                    f"zone_{zone_number}_mode",
                    f"Zone {zone_number} mode",
                    lambda state, zone_id=zone_number: (
                        state.zones[zone_id].mode.current_label or state.zones[zone_id].mode.value
                    ),
                )
            )

        zone_temperature_specs = (
            (
                "cooling_comfort_temperature",
                "cooling comfort temperature",
                zone.cooling_comfort_temperature.value,
            ),
            (
                "cooling_reduced_temperature",
                "cooling reduced temperature",
                zone.cooling_reduced_temperature.value,
            ),
            (
                "heating_protection_temperature",
                "heating protection temperature",
                zone.heating_protection_temperature,
            ),
            (
                "cooling_protection_temperature",
                "cooling protection temperature",
                zone.cooling_protection_temperature,
            ),
            (
                "heating_holiday_temperature",
                "heating holiday temperature",
                zone.heating_holiday_temperature,
            ),
            (
                "cooling_holiday_temperature",
                "cooling holiday temperature",
                zone.cooling_holiday_temperature,
            ),
        )
        for field, label, current_value in zone_temperature_specs:
            if current_value is None:
                continue
            if field.startswith("cooling_") and not has_cooling:
                continue
            entities.append(
                ElcoSensor(
                    coordinator,
                    f"zone_{zone_number}_{field}",
                    f"Zone {zone_number} {label}",
                    lambda state, zone_id=zone_number, attribute=field: _zone_temperature_value(
                        state, zone_id, attribute
                    ),
                    temperature=True,
                )
            )

        if zone.use_reduced_operation_mode_on_holiday is not None:
            entities.append(
                ElcoSensor(
                    coordinator,
                    f"zone_{zone_number}_holiday_operating_level",
                    f"Zone {zone_number} holiday operating level",
                    lambda state, zone_id=zone_number: (
                        "Reduced"
                        if state.zones[zone_id].use_reduced_operation_mode_on_holiday
                        else "Frost protection"
                    ),
                )
            )

        for item_id, key_suffix, label in (
            ("HeatingFlowTemp", "heating_flow_temperature", "heating flow temperature"),
            ("HeatingFlowOffset", "heating_flow_offset", "heating flow offset"),
            ("CoolingFlowTemp", "cooling_flow_temperature", "cooling flow temperature"),
            ("CoolingFlowOffset", "cooling_flow_offset", "cooling flow offset"),
            ("ZoneDeroga", "derogation_temperature", "derogation temperature"),
        ):
            if not _has_system_item(data, item_id, zone_number):
                continue
            entities.append(
                ElcoSensor(
                    coordinator,
                    f"zone_{zone_number}_{key_suffix}",
                    f"Zone {zone_number} {label}",
                    lambda state, source=item_id, zone_id=zone_number: _as_number(
                        state.discovery.system_value(source, zone_id)
                    ),
                    temperature=True,
                )
            )

    bsb_specs = (
        (
            BSB_ENTITY_ADDRESSES["700"],
            "heating_circuit_operating_mode_700",
            "Heating circuit 700 operating mode",
            False,
        ),
        (
            BSB_ENTITY_ADDRESSES["710"],
            "heating_circuit_comfort_setpoint_710",
            "Heating circuit 710 comfort setpoint",
            True,
        ),
        (
            BSB_ENTITY_ADDRESSES["712"],
            "heating_circuit_reduced_setpoint_712",
            "Heating circuit 712 reduced setpoint",
            True,
        ),
        (
            BSB_ENTITY_ADDRESSES["714"],
            "heating_circuit_frost_protection_setpoint_714",
            "Heating circuit 714 frost protection setpoint",
            True,
        ),
        (
            BSB_ENTITY_ADDRESSES["720"],
            "heating_curve_slope_720",
            "Heating circuit 720 heating curve slope",
            False,
        ),
        (
            BSB_ENTITY_ADDRESSES["730"],
            "summer_winter_heating_limit_730",
            "Heating circuit 730 summer/winter heating limit",
            True,
        ),
    )
    for address, key, name, temperature in bsb_specs:
        if address not in data.discovery.bsb_points and not data.zones:
            continue
        entities.append(
            ElcoSensor(
                coordinator,
                key,
                name,
                lambda state, bsb_address=address, numeric=temperature: (
                    _as_number(_bsb_value(state, bsb_address))
                    if numeric
                    else _bsb_value(state, bsb_address)
                ),
                temperature=temperature,
                entity_category=EntityCategory.DIAGNOSTIC,
                available_fn=lambda state, bsb_address=address: _bsb_available(state, bsb_address),
                enabled_default=False,
            )
        )

    if not _has_system_item(data, "HeatingCircuitPressure"):
        pressure_address = BSB_ENTITY_ADDRESSES["heating_circuit_pressure"]
        if pressure_address in data.discovery.bsb_points or data.discovery.probe_status.get(
            "bsb_points:plant_pressure", ""
        ).startswith("unavailable"):
            entities.append(
                ElcoSensor(
                    coordinator,
                    "heating_circuit_pressure",
                    "Heating circuit pressure",
                    lambda state, bsb_address=pressure_address: _as_number(
                        _bsb_value(state, bsb_address)
                    ),
                    pressure=True,
                    available_fn=lambda state, bsb_address=pressure_address: _bsb_available(
                        state, bsb_address
                    ),
                )
            )

    for source, key, name in (
        ("heat_pump_flow_temperature", "heat_pump_flow_temperature", "Heat pump flow temperature"),
        (
            "heat_pump_return_temperature",
            "heat_pump_return_temperature",
            "Heat pump return temperature",
        ),
        ("heat_pump_flow_setpoint", "heat_pump_flow_setpoint", "Heat pump flow setpoint"),
        ("heat_pump_gas_temperature", "heat_pump_gas_temperature", "Heat pump gas temperature"),
        ("source_outlet_temperature", "source_outlet_temperature", "Source outlet temperature"),
        ("hot_gas_temperature", "hot_gas_temperature", "Hot gas temperature"),
    ):
        address = BSB_ENTITY_ADDRESSES[source]
        if address not in data.discovery.bsb_points and not features.get("hpSys", False):
            continue
        entities.append(
            ElcoSensor(
                coordinator,
                key,
                name,
                lambda state, bsb_address=address: _as_number(_bsb_value(state, bsb_address)),
                temperature=True,
                available_fn=lambda state, bsb_address=address: _bsb_available(state, bsb_address),
            )
        )
    async_add_entities(entities)
