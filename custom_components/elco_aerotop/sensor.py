"""Sensors for ELCO Aerotop."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfPressure, UnitOfTemperature
from homeassistant.core import HomeAssistant

from .coordinator import ElcoDataUpdateCoordinator
from .entity import ElcoAerotopEntity
from .models import ElcoData, NumericVariable


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
        pressure: bool = False,
    ) -> None:
        super().__init__(coordinator, key)
        self._attr_name = name
        self._value_fn = value_fn
        if temperature:
            self._attr_device_class = SensorDeviceClass.TEMPERATURE
            self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
            self._attr_state_class = SensorStateClass.MEASUREMENT
        elif pressure:
            self._attr_device_class = SensorDeviceClass.PRESSURE
            self._attr_native_unit_of_measurement = UnitOfPressure.BAR
            self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> float | str | None:
        return self._value_fn(self.coordinator.data)


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
    point = data.discovery.bsb_points.get(address)
    if not isinstance(point, dict):
        return None
    value = point.get("value")
    if value is None:
        value = point.get("textualValue", point.get("text"))
    if isinstance(value, dict):
        return value.get("value", value.get("text"))
    return value


def _has_system_item(data: ElcoData, item_id: str, zone: int = 0) -> bool:
    item = data.discovery.system_item(item_id, zone)
    return bool(
        item
        and item.get("available", True) is not False
        and item.get("isAvailable", True) is not False
        and _as_number(item.get("value")) is not None
    )


def _has_bsb_value(data: ElcoData, address: str, *, numeric: bool) -> bool:
    point = data.discovery.bsb_points.get(address)
    if not isinstance(point, dict):
        return False
    if point.get("ok", True) is False or point.get("isAvailable", True) is False:
        return False
    value = _bsb_value(data, address)
    return _as_number(value) is not None if numeric else value is not None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities,
) -> None:
    coordinator: ElcoDataUpdateCoordinator = entry.runtime_data
    data = coordinator.data
    entities: list[SensorEntity] = []

    if data.plant.outside_temperature is not None:
        entities.append(
            ElcoSensor(
                coordinator,
                "outside_temperature",
                "Outside temperature",
                lambda state: state.plant.outside_temperature,
                temperature=True,
            )
        )
    if data.plant.dhw_current_temperature is not None:
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
        if _has_system_item(data, item_id):
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
        if zone.room_temperature is not None:
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
            entities.append(
                ElcoSensor(
                    coordinator,
                    f"zone_{zone_number}_{field}",
                    f"Zone {zone_number} {label}",
                    lambda state, zone_id=zone_number, attribute=field: (
                        getattr(state.zones[zone_id], attribute).value
                        if isinstance(getattr(state.zones[zone_id], attribute), NumericVariable)
                        else getattr(state.zones[zone_id], attribute)
                    ),
                    temperature=True,
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
            "700",
            "heating_circuit_operating_mode_700",
            "Heating circuit 700 operating mode",
            False,
        ),
        (
            "710",
            "heating_circuit_comfort_setpoint_710",
            "Heating circuit 710 comfort setpoint",
            True,
        ),
        (
            "712",
            "heating_circuit_reduced_setpoint_712",
            "Heating circuit 712 reduced setpoint",
            True,
        ),
        (
            "714",
            "heating_circuit_frost_protection_setpoint_714",
            "Heating circuit 714 frost protection setpoint",
            True,
        ),
        (
            "720",
            "heating_curve_slope_720",
            "Heating circuit 720 heating curve slope",
            False,
        ),
        (
            "730",
            "summer_winter_heating_limit_730",
            "Heating circuit 730 summer/winter heating limit",
            True,
        ),
    )
    for address, key, name, temperature in bsb_specs:
        if not _has_bsb_value(data, address, numeric=temperature):
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
            )
        )
    async_add_entities(entities)
