"""Sensors for ELCO Aerotop."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, tzinfo
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy, UnitOfPressure, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.util import dt as dt_util

from .capabilities import supports_cooling, supports_room_sensor
from .const import BSB_ENERGY_HISTORY_ADDRESSES, BSB_ENTITY_ADDRESSES
from .coordinator import ElcoDataUpdateCoordinator
from .entity import ElcoAerotopEntity
from .models import (
    ElcoData,
    NumericVariable,
    bsb_point_available,
    bsb_point_date,
    bsb_point_datetime,
    bsb_point_field_value,
    bsb_point_value,
)


class ElcoSensor(ElcoAerotopEntity, SensorEntity):
    """A coordinator-backed ELCO sensor."""

    def __init__(
        self,
        coordinator: ElcoDataUpdateCoordinator,
        key: str,
        name: str,
        value_fn: Callable[[ElcoData], date | datetime | float | int | str | None],
        *,
        temperature: bool = False,
        temperature_delta: bool = False,
        pressure: bool = False,
        entity_category: EntityCategory | None = None,
        measurement: bool = False,
        energy: bool = False,
        date_sensor: bool = False,
        timestamp_sensor: bool = False,
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
        elif temperature_delta:
            self._attr_device_class = SensorDeviceClass.TEMPERATURE_DELTA
            self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
            self._attr_state_class = SensorStateClass.MEASUREMENT
        elif pressure:
            self._attr_device_class = SensorDeviceClass.PRESSURE
            self._attr_native_unit_of_measurement = UnitOfPressure.BAR
            self._attr_state_class = SensorStateClass.MEASUREMENT
        elif measurement:
            self._attr_state_class = SensorStateClass.MEASUREMENT

        if energy:
            self._attr_device_class = SensorDeviceClass.ENERGY
            self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
            self._attr_state_class = SensorStateClass.TOTAL
        elif date_sensor:
            self._attr_device_class = SensorDeviceClass.DATE
        elif timestamp_sensor:
            self._attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def native_value(self) -> date | datetime | float | int | str | None:
        return self._value_fn(self.coordinator.data)

    @property
    def available(self) -> bool:
        """Return dynamic source availability without removing the entity."""
        return super().available and (
            self._available_fn(self.coordinator.data) if self._available_fn else True
        )


class ElcoLastSuccessfulUpdateSensor(ElcoAerotopEntity, SensorEntity):
    """Timestamp the latest successful core Remocon data capture."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_name = "Last successful update"

    def __init__(self, coordinator: ElcoDataUpdateCoordinator) -> None:
        super().__init__(coordinator, "last_successful_update")

    @property
    def native_value(self) -> datetime | None:
        """Return the latest successful capture time."""
        return self.coordinator.last_successful_update

    @property
    def available(self) -> bool:
        """Keep the last good timestamp visible during later update failures."""
        return self.native_value is not None

    async def async_added_to_hass(self) -> None:
        """Listen for captures even when all plant values are unchanged."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self.coordinator.async_add_successful_update_listener(self.async_write_ha_state)
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


def _controller_clock(data: ElcoData, timezone: tzinfo) -> datetime | None:
    value = bsb_point_datetime(data.discovery.bsb_points.get("327691"))
    return value.replace(tzinfo=timezone) if value is not None else None


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


def _mapping_value(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if mapping.get(key) not in (None, ""):
            return mapping[key]
    return None


def _plant_header_value(data: ElcoData, *keys: str) -> Any:
    return _mapping_value(data.discovery.plant_header, *keys)


def _plant_user_value(data: ElcoData, *keys: str) -> Any:
    return _mapping_value(data.discovery.plant_user_data, *keys)


def _plant_owner(data: ElcoData) -> str | None:
    first_name = _plant_user_value(data, "firstName", "FirstName")
    last_name = _plant_user_value(data, "lastName", "LastName")
    rendered = " ".join(str(value).strip() for value in (first_name, last_name) if value)
    return rendered or None


def _plant_status(data: ElcoData) -> str | None:
    status = _plant_header_value(data, "errorText", "ErrorText", "status", "Status")
    if isinstance(status, str):
        label, separator, value = status.partition(":")
        if separator and label.strip().casefold() == "status":
            return value.strip() or None
        return status.strip() or None
    error_type = _plant_header_value(data, "errorType", "ErrorType")
    if error_type == 0:
        return "OK"
    return str(error_type) if error_type is not None else None


def _appliance_serial(data: ElcoData) -> str | None:
    header_value = _plant_header_value(
        data,
        "applianceSerial",
        "ApplianceSerial",
        "productSerialNumber",
        "ProductSerialNumber",
    )
    if header_value is not None:
        return str(header_value)
    boiler_data = data.discovery.bsb_boiler_data
    if not isinstance(boiler_data, dict):
        return None
    value = _mapping_value(
        boiler_data,
        "applianceSerial",
        "serialNumber",
        "productSerialNumber",
        "serial",
    )
    return str(value) if value is not None else None


def _monitoring_section(data: ElcoData) -> dict[str, Any]:
    payload = data.discovery.automated_monitoring
    if not isinstance(payload, dict):
        return {}
    section = payload.get("automatedMonitoring")
    return section if isinstance(section, dict) else {}


def _predictive_maintenances(data: ElcoData) -> list[Any] | None:
    payload = data.discovery.automated_monitoring
    if not isinstance(payload, dict):
        return None
    notices = payload.get("predictiveMaintenances")
    return notices if isinstance(notices, list) else None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities,
) -> None:
    coordinator: ElcoDataUpdateCoordinator = entry.runtime_data
    data = coordinator.data
    features = data.discovery.features
    entities: list[SensorEntity] = [ElcoLastSuccessfulUpdateSensor(coordinator)]
    controller_timezone = dt_util.get_time_zone(hass.config.time_zone) or UTC

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

    entities.append(
        ElcoSensor(
            coordinator,
            "controller_clock",
            "Controller clock",
            lambda state: _controller_clock(state, controller_timezone),
            timestamp_sensor=True,
            entity_category=EntityCategory.DIAGNOSTIC,
            available_fn=lambda state: _bsb_available(state, "327691"),
        )
    )

    header_sensor_specs = (
        (
            "appliance_model",
            "Appliance model",
            lambda state: _plant_header_value(
                state, "applianceModel", "ApplianceModel", "model", "Model"
            ),
            True,
        ),
        ("plant_status", "Plant status", _plant_status, True),
        ("plant_owner", "Plant owner", _plant_owner, True),
        (
            "account_language",
            "Account language",
            lambda state: _plant_user_value(
                state, "emailLanguage", "EmailLanguage", "language", "Language"
            ),
            True,
        ),
        (
            "owner_phone",
            "Owner phone",
            lambda state: _plant_user_value(state, "phone", "Phone"),
            False,
        ),
        (
            "owner_mobile_phone",
            "Owner mobile phone",
            lambda state: _plant_user_value(state, "mobilePhone", "MobilePhone"),
            False,
        ),
        ("appliance_serial", "Appliance serial", _appliance_serial, True),
    )
    for key, name, value_fn, enabled_default in header_sensor_specs:
        if value_fn(data) is None:
            continue
        entities.append(
            ElcoSensor(
                coordinator,
                key,
                name,
                value_fn,
                entity_category=EntityCategory.DIAGNOSTIC,
                enabled_default=enabled_default,
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
                    entity_category=EntityCategory.DIAGNOSTIC,
                    enabled_default=False,
                )
            )

        zone_temperature_specs = (
            (
                "cooling_comfort_temperature",
                "cooling comfort temperature",
                zone.cooling_comfort_temperature.value,
                EntityCategory.CONFIG,
                False,
            ),
            (
                "cooling_reduced_temperature",
                "cooling reduced temperature",
                zone.cooling_reduced_temperature.value,
                EntityCategory.CONFIG,
                False,
            ),
            (
                "heating_protection_temperature",
                "heating protection temperature",
                zone.heating_protection_temperature,
                EntityCategory.CONFIG,
                False,
            ),
            (
                "cooling_protection_temperature",
                "cooling protection temperature",
                zone.cooling_protection_temperature,
                EntityCategory.CONFIG,
                False,
            ),
            (
                "heating_holiday_temperature",
                "heating holiday temperature",
                zone.heating_holiday_temperature,
                EntityCategory.CONFIG,
                False,
            ),
            (
                "cooling_holiday_temperature",
                "cooling holiday temperature",
                zone.cooling_holiday_temperature,
                EntityCategory.CONFIG,
                False,
            ),
        )
        for field, label, current_value, entity_category, enabled_default in zone_temperature_specs:
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
                    entity_category=entity_category,
                    enabled_default=enabled_default,
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
                    entity_category=EntityCategory.CONFIG,
                )
            )

        for item_id, key_suffix, label, entity_category, enabled_default in (
            (
                "HeatingFlowTemp",
                "heating_flow_temperature",
                "heating flow temperature",
                None,
                True,
            ),
            (
                "HeatingFlowOffset",
                "heating_flow_offset",
                "heating flow offset",
                EntityCategory.CONFIG,
                False,
            ),
            (
                "CoolingFlowTemp",
                "cooling_flow_temperature",
                "cooling flow temperature",
                None,
                True,
            ),
            (
                "CoolingFlowOffset",
                "cooling_flow_offset",
                "cooling flow offset",
                EntityCategory.CONFIG,
                False,
            ),
            ("ZoneDeroga", "derogation_temperature", "derogation temperature", None, True),
        ):
            if not _has_system_item(data, item_id, zone_number):
                continue
            is_temperature_delta = key_suffix.endswith("_offset")
            entities.append(
                ElcoSensor(
                    coordinator,
                    f"zone_{zone_number}_{key_suffix}",
                    f"Zone {zone_number} {label}",
                    lambda state, source=item_id, zone_id=zone_number: _as_number(
                        state.discovery.system_value(source, zone_id)
                    ),
                    temperature=not is_temperature_delta,
                    temperature_delta=is_temperature_delta,
                    entity_category=entity_category,
                    enabled_default=enabled_default,
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

    maintenance_address = BSB_ENTITY_ADDRESSES["7000_maintenance_message"]
    for field_index, (key_suffix, field_name) in enumerate(
        (
            ("code_1", "Maintenance code 1"),
            ("priority_1", "Maintenance priority 1"),
            ("code_2", "Maintenance code 2"),
            ("priority_2", "Maintenance priority 2"),
        )
    ):
        entities.append(
            ElcoSensor(
                coordinator,
                f"maintenance_{key_suffix}",
                field_name,
                lambda state, label=field_name, index=field_index: bsb_point_field_value(
                    state.discovery.bsb_points.get(maintenance_address), label, index
                ),
                entity_category=EntityCategory.DIAGNOSTIC,
                available_fn=lambda state: _bsb_available(state, maintenance_address),
            )
        )

    if not _has_system_item(data, "HeatingCircuitPressure"):
        pressure_address = BSB_ENTITY_ADDRESSES["heating_circuit_pressure"]
        if data.zones:
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

    for source, key, name, diagnostic in (
        (
            "heat_pump_flow_temperature",
            "heat_pump_flow_temperature",
            "Heat pump flow temperature",
            False,
        ),
        (
            "heat_pump_return_temperature",
            "heat_pump_return_temperature",
            "Heat pump return temperature",
            False,
        ),
        (
            "heat_pump_flow_setpoint",
            "heat_pump_flow_setpoint",
            "Heat pump flow setpoint",
            False,
        ),
        (
            "heat_pump_gas_temperature",
            "heat_pump_gas_temperature",
            "Heat pump gas temperature",
            True,
        ),
        (
            "source_outlet_temperature",
            "source_outlet_temperature",
            "Source outlet temperature",
            True,
        ),
        ("hot_gas_temperature", "hot_gas_temperature", "Hot gas temperature", True),
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
                entity_category=EntityCategory.DIAGNOSTIC if diagnostic else None,
                available_fn=lambda state, bsb_address=address: _bsb_available(state, bsb_address),
                enabled_default=not diagnostic,
            )
        )

    if any(supports_cooling(features, zone) for zone in data.zones.values()):
        for source, key, name in (
            (
                "cooling_2_flow_temperature",
                "cooling_2_flow_temperature",
                "Cooling circuit 2 flow temperature",
            ),
            (
                "cooling_2_flow_setpoint",
                "cooling_2_flow_setpoint",
                "Cooling circuit 2 flow setpoint",
            ),
        ):
            address = BSB_ENTITY_ADDRESSES[source]
            entities.append(
                ElcoSensor(
                    coordinator,
                    key,
                    name,
                    lambda state, bsb_address=address: _as_number(_bsb_value(state, bsb_address)),
                    temperature=True,
                    entity_category=EntityCategory.DIAGNOSTIC,
                    available_fn=lambda state, bsb_address=address: _bsb_available(
                        state, bsb_address
                    ),
                    enabled_default=False,
                )
            )

    energy_fields = (
        ("heat_delivered_heating", "Heat delivered heating"),
        ("heat_delivered_dhw", "Heat delivered DHW"),
        ("refrigeration_delivered", "Refrigeration delivered"),
        ("energy_input_heating", "Energy brought in heating"),
        ("energy_input_dhw", "Energy brought in DHW"),
        ("energy_input_cooling", "Energy brought in cooling"),
    )
    for slot, addresses in BSB_ENERGY_HISTORY_ADDRESSES.items():
        record_name = f"Annual energy record {slot}"
        date_address = addresses["record_date"]
        entities.append(
            ElcoSensor(
                coordinator,
                f"annual_energy_record_{slot}_date",
                f"{record_name} – Fixed day",
                lambda state, bsb_address=date_address: bsb_point_date(
                    state.discovery.bsb_points.get(bsb_address)
                ),
                date_sensor=True,
                entity_category=EntityCategory.DIAGNOSTIC,
                available_fn=lambda state, bsb_address=date_address: _bsb_available(
                    state, bsb_address
                ),
                enabled_default=False,
            )
        )
        factor_address = addresses["performance_factor"]
        entities.append(
            ElcoSensor(
                coordinator,
                f"annual_performance_factor_{slot}",
                f"{record_name} – Yearly performance factor",
                lambda state, bsb_address=factor_address: _as_number(
                    _bsb_value(state, bsb_address)
                ),
                measurement=True,
                entity_category=EntityCategory.DIAGNOSTIC,
                available_fn=lambda state, bsb_address=factor_address: _bsb_available(
                    state, bsb_address
                ),
                enabled_default=False,
            )
        )
        for field, label in energy_fields:
            address = addresses[field]
            entities.append(
                ElcoSensor(
                    coordinator,
                    f"annual_{field}_{slot}",
                    f"{record_name} – {label}",
                    lambda state, bsb_address=address: _as_number(_bsb_value(state, bsb_address)),
                    energy=True,
                    entity_category=EntityCategory.DIAGNOSTIC,
                    available_fn=lambda state, bsb_address=address: _bsb_available(
                        state, bsb_address
                    ),
                    enabled_default=False,
                )
            )

    monitoring_specs = (
        ("hydraulicPressure", "hydraulic_pressure", "Hydraulic pressure health"),
        ("refrigerantCircuit", "refrigerant_circuit", "Refrigerant circuit health"),
        ("circulation", "circulation", "Circulation health"),
        ("combustion", "combustion", "Combustion health"),
        ("other", "other", "Other appliance health"),
    )
    for field, key_suffix, name in monitoring_specs:
        entities.append(
            ElcoSensor(
                coordinator,
                f"automated_monitoring_{key_suffix}",
                name,
                lambda state, attribute=field: _as_number(
                    _monitoring_section(state).get(attribute)
                ),
                entity_category=EntityCategory.DIAGNOSTIC,
                measurement=True,
                available_fn=lambda state, attribute=field: (
                    _as_number(_monitoring_section(state).get(attribute)) is not None
                ),
            )
        )

    entities.append(
        ElcoSensor(
            coordinator,
            "predictive_maintenance_count",
            "Predictive maintenance notice count",
            lambda state: (
                len(notices) if (notices := _predictive_maintenances(state)) is not None else None
            ),
            entity_category=EntityCategory.DIAGNOSTIC,
            measurement=True,
            available_fn=lambda state: _predictive_maintenances(state) is not None,
        )
    )
    async_add_entities(entities)
