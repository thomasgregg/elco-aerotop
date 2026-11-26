"""Sensors for ELCO Aerotop."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, tzinfo
from math import isfinite
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy, UnitOfPressure, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.util import dt as dt_util

from .capabilities import supports_cooling, supports_room_sensor
from .const import BSB_ENERGY_HISTORY_ADDRESSES, BSB_ENTITY_ADDRESSES, SYSTEM_TYPE_GALEVO
from .control_mapping import dhw_operation
from .coordinator import ElcoDataUpdateCoordinator
from .entity import ElcoAerotopEntity, async_setup_platform_entities
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
        value_fn: Callable[[ElcoData], date | datetime | float | int | str | None],
        *,
        temperature: bool = False,
        temperature_delta: bool = False,
        pressure: bool = False,
        entity_category: EntityCategory | None = None,
        measurement: bool = False,
        energy: bool = False,
        energy_total: bool = True,
        date_sensor: bool = False,
        timestamp_sensor: bool = False,
        available_fn: Callable[[ElcoData], bool] | None = None,
        enabled_default: bool = True,
        zone_number: int | None = None,
        translation_key: str | None = None,
        translation_placeholders: dict[str, str] | None = None,
    ) -> None:
        super().__init__(
            coordinator,
            key,
            zone_number=zone_number,
            translation_key=translation_key,
            translation_placeholders=translation_placeholders,
        )
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
            if energy_total:
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


class ElcoGalevoEstimatedJazSensor(ElcoAerotopEntity, SensorEntity):
    """Estimated yearly performance for space heating and hot water."""

    _attr_suggested_display_precision = 2

    def __init__(self, coordinator: ElcoDataUpdateCoordinator) -> None:
        super().__init__(coordinator, "galevo_estimated_jaz")

    @property
    def native_value(self) -> float | None:
        """Return delivered heat divided by electricity for the same year."""
        report = self.coordinator.galevo_annual_metering
        return report.performance_factor if report is not None else None

    @property
    def available(self) -> bool:
        """Require a complete report for the previous calendar year."""
        return super().available and self.coordinator.galevo_annual_metering is not None

    @property
    def extra_state_attributes(self) -> dict[str, int] | None:
        """Expose the calendar year represented by the value."""
        report = self.coordinator.galevo_annual_metering
        return {"year": report.year} if report is not None else None


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


def _system_item_label(data: ElcoData, item_id: str, zone: int = 0) -> str | float | None:
    """Return the localized enum label of a data item, else its raw value."""
    item = data.discovery.system_item(item_id, zone)
    if not item or item.get("error") is True or item.get("invalid") is True:
        return None
    value = _as_number(item.get("value"))
    labels = item.get("optTexts")
    options = item.get("options")
    if value is not None and isinstance(labels, list) and isinstance(options, list):
        for index, option in enumerate(options):
            if index < len(labels) and _as_number(option) == value:
                return str(labels[index])
    return value


# eWeather enum from the mobile app (data.local.models.eWeather): day values
# 0-8 plus the +128 night variants; the Weather data item carries no optTexts.
_GALEVO_WEATHER_LABELS = {
    0: "Unavailable",
    1: "Clear",
    2: "Variable",
    3: "Cloudy",
    4: "Rainy",
    5: "Rainstorm",
    6: "Snow",
    7: "Fog",
    8: "Windy",
    128: "Unavailable by night",
    129: "Clear by night",
    130: "Variable by night",
    131: "Cloudy by night",
    132: "Rainy by night",
    133: "Rainstorm by night",
    134: "Snow by night",
    135: "Fog by night",
    136: "Windy by night",
    137: "Invalid",
}


def _galevo_weather_label(data: ElcoData) -> str | float | None:
    """Return the eWeather condition name for the Weather item value."""
    value = _system_item_label(data, "Weather")
    if isinstance(value, str):
        return value
    if value is None:
        return None
    return _GALEVO_WEATHER_LABELS.get(int(value), value)


def _energy_sequence(data: ElcoData, kind: int, period: int) -> list[float | None]:
    """Return one consumption series' kWh buckets (oldest first)."""
    for sequence in data.discovery.energy_sequences:
        if sequence.get("k") == kind and sequence.get("p") == period:
            values = sequence.get("v")
            if isinstance(values, list):
                return [
                    float(v)
                    if isinstance(v, int | float) and not isinstance(v, bool) and isfinite(v)
                    else None
                    for v in values
                ]
    return []


def _energy_sum(values: list[float | None], bucket_count: int) -> float | None:
    present = [value for value in values if value is not None]
    if len(values) != bucket_count or len(present) != bucket_count:
        return None
    return sum(present)


def _energy_value(data: ElcoData, kind: int, period: int, window: str) -> float | None:
    values = _energy_sequence(data, kind, period)
    # The monthly report ends with the latest completed month.
    if window == "last_month":
        return values[-1] if values else None
    if window == "last_12_complete_months":
        return _energy_sum(values[-12:], 12)
    return _energy_sum(values, 24 if period == 1 else 7)


def _menu_item_value(data: ElcoData, item_id: str) -> float | None:
    item = data.discovery.menu_items.get(item_id)
    if not isinstance(item, dict) or item.get("error") is True:
        return None
    return _as_number(item.get("value"))


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
    async_setup_platform_entities(entry, async_add_entities, _create_entities)


def _create_entities(
    coordinator: ElcoDataUpdateCoordinator,
    async_add_entities,
) -> None:
    data = coordinator.data
    features = data.discovery.features
    entities: list[SensorEntity] = [ElcoLastSuccessfulUpdateSensor(coordinator)]
    controller_timezone = dt_util.get_time_zone(coordinator.hass.config.time_zone) or UTC
    # Entities backed by BSB data-point addresses (controller clock, heating-
    # circuit parameters, heat-pump temperatures, energy history) have no
    # equivalent on Galevo; register them only on BSB/unknown families.
    bsb_family = data.discovery.system_type != SYSTEM_TYPE_GALEVO

    metadata_specs = (
        ("gateway_serial", "gwSerial"),
        ("plant_name", "plantName"),
        ("gateway_firmware", "gwFwVer"),
    )
    for key, field in metadata_specs:
        if not data.discovery.plant_metadata.get(field):
            continue
        entities.append(
            ElcoSensor(
                coordinator,
                key,
                lambda state, attribute=field: state.discovery.plant_metadata.get(attribute),
                entity_category=EntityCategory.DIAGNOSTIC,
            )
        )

    if bsb_family:
        entities.append(
            ElcoSensor(
                coordinator,
                "controller_clock",
                lambda state: _controller_clock(state, controller_timezone),
                timestamp_sensor=True,
                entity_category=EntityCategory.DIAGNOSTIC,
                available_fn=lambda state: _bsb_available(state, "327691"),
            )
        )

    header_sensor_specs = (
        (
            "appliance_model",
            lambda state: _plant_header_value(
                state, "applianceModel", "ApplianceModel", "model", "Model"
            ),
            True,
        ),
        ("plant_status", _plant_status, True),
        ("plant_owner", _plant_owner, True),
        (
            "account_language",
            lambda state: _plant_user_value(
                state, "emailLanguage", "EmailLanguage", "language", "Language"
            ),
            True,
        ),
        (
            "owner_phone",
            lambda state: _plant_user_value(state, "phone", "Phone"),
            False,
        ),
        (
            "owner_mobile_phone",
            lambda state: _plant_user_value(state, "mobilePhone", "MobilePhone"),
            False,
        ),
        ("appliance_serial", _appliance_serial, True),
    )
    for key, value_fn, enabled_default in header_sensor_specs:
        if value_fn(data) is None:
            continue
        entities.append(
            ElcoSensor(
                coordinator,
                key,
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
                _plant_location,
                entity_category=EntityCategory.DIAGNOSTIC,
            )
        )
    if isinstance(data.discovery.bus_errors, list):
        entities.append(
            ElcoSensor(
                coordinator,
                "controller_error_count",
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
                lambda state: state.plant.dhw_current_temperature,
                temperature=True,
            )
        )

    system_sensor_specs = (
        ("HeatingCircuitPressure", "heating_circuit_pressure", True),
        (
            "ChFlowTemp",
            "heating_circuit_flow_temperature",
            False,
        ),
        (
            "ChFlowSetpointTemp",
            "heating_circuit_flow_setpoint_temperature",
            False,
        ),
    )
    for item_id, key, is_pressure in system_sensor_specs:
        if _has_system_item(data, item_id) or (
            bsb_family and item_id in {"ChFlowTemp", "ChFlowSetpointTemp"}
        ):
            entities.append(
                ElcoSensor(
                    coordinator,
                    key,
                    lambda state, source=item_id: _as_number(state.discovery.system_value(source)),
                    temperature=not is_pressure,
                    pressure=is_pressure,
                )
            )

    if _has_system_item(data, "Weather"):
        entities.append(
            ElcoSensor(
                coordinator,
                "weather",
                _galevo_weather_label,
                entity_category=EntityCategory.DIAGNOSTIC,
            )
        )

    for item_id, key, entity_category, enabled_default in (
        (
            "BufferControlMode",
            "buffer_control_mode",
            EntityCategory.DIAGNOSTIC,
            False,
        ),
    ):
        if not _has_system_item(data, item_id):
            continue
        entities.append(
            ElcoSensor(
                coordinator,
                key,
                lambda state, source=item_id: _system_item_label(state, source),
                entity_category=entity_category,
                enabled_default=enabled_default,
            )
        )

    for item_id, key in (
        (
            "BufferTimeProgComfortHeatingTemp",
            "buffer_comfort_heating_temperature",
        ),
        (
            "BufferTimeProgEconomyHeatingTemp",
            "buffer_economy_heating_temperature",
        ),
        (
            "BufferTimeProgComfortCoolingTemp",
            "buffer_comfort_cooling_temperature",
        ),
        (
            "BufferTimeProgEconomyCoolingTemp",
            "buffer_economy_cooling_temperature",
        ),
    ):
        if not _has_system_item(data, item_id):
            continue
        entities.append(
            ElcoSensor(
                coordinator,
                key,
                lambda state, source=item_id: _as_number(state.discovery.system_value(source)),
                temperature=True,
                entity_category=EntityCategory.DIAGNOSTIC,
                enabled_default=False,
            )
        )

    # Bus-device inventory (Galevo remote/plants/{gw}/busDevices) arrives via
    # deferred discovery; availability follows the item list.
    if data.discovery.probe_status.get("bus_devices") not in (
        "unsupported:bsb_system",
        "unsupported:system_family",
    ):
        entities.append(
            ElcoSensor(
                coordinator,
                "bus_devices",
                lambda state: (
                    ", ".join(
                        str(device.get("name"))
                        for device in state.discovery.bus_devices
                        if device.get("name")
                    )
                    or None
                ),
                entity_category=EntityCategory.DIAGNOSTIC,
                available_fn=lambda state: bool(state.discovery.bus_devices),
            )
        )

    if not bsb_family:
        estimated_jaz = ElcoGalevoEstimatedJazSensor(coordinator)
        if coordinator.galevo_annual_metering is not None or estimated_jaz.has_registry_entry(
            "sensor"
        ):
            entities.append(estimated_jaz)

        for kind, slug in ((1, "heating"), (2, "dhw"), (3, "cooling")):
            for window, period in (
                ("last_24_hours", 1),
                ("last_7_days", 2),
                ("last_month", 4),
                ("last_12_complete_months", 4),
            ):
                if not _energy_sequence(data, kind, period):
                    continue
                entities.append(
                    ElcoSensor(
                        coordinator,
                        f"energy_{slug}_{window}",
                        lambda state, kind=kind, period=period, window=window: _energy_value(
                            state, kind, period, window
                        ),
                        energy=True,
                        energy_total=False,
                        available_fn=lambda state, kind=kind, period=period, window=window: (
                            _energy_value(state, kind, period, window) is not None
                        ),
                    )
                )

        for item_id, key, is_temperature, enabled_default in (
            ("119", "gateway_signal_level", False, True),
            ("130", "online_outdoor_temperature", True, False),
        ):
            if _menu_item_value(data, item_id) is None:
                continue
            entities.append(
                ElcoSensor(
                    coordinator,
                    key,
                    lambda state, source=item_id: _menu_item_value(state, source),
                    temperature=is_temperature,
                    measurement=not is_temperature,
                    entity_category=EntityCategory.DIAGNOSTIC,
                    available_fn=lambda state, source=item_id: (
                        _menu_item_value(state, source) is not None
                    ),
                    enabled_default=enabled_default,
                )
            )

        if _has_system_item(data, "PlantMode"):
            entities.append(
                ElcoSensor(
                    coordinator,
                    "plant_mode_status",
                    lambda state: _system_item_label(state, "PlantMode"),
                )
            )
        if data.plant.dhw_mode.value is not None:
            entities.append(
                ElcoSensor(
                    coordinator,
                    "dhw_operation",
                    lambda state: dhw_operation(state.plant.dhw_mode),
                )
            )
        if data.plant.dhw_target_temperature.value is not None:
            entities.append(
                ElcoSensor(
                    coordinator,
                    "dhw_target_temperature",
                    lambda state: state.plant.dhw_target_temperature.value,
                    temperature=True,
                )
            )

    for zone_number, zone in data.zones.items():
        has_cooling = supports_cooling(features, zone)
        if zone.desired_temperature is not None:
            entities.append(
                ElcoSensor(
                    coordinator,
                    "desired_temperature",
                    lambda state, zone_id=zone_number: state.zones[zone_id].desired_temperature,
                    temperature=True,
                    zone_number=zone_number,
                )
            )
        if supports_room_sensor(zone):
            entities.append(
                ElcoSensor(
                    coordinator,
                    "room_temperature",
                    lambda state, zone_id=zone_number: state.zones[zone_id].room_temperature,
                    temperature=True,
                    zone_number=zone_number,
                )
            )
        if _has_system_item(data, "ZoneName", zone_number):
            entities.append(
                ElcoSensor(
                    coordinator,
                    "name",
                    lambda state, zone_id=zone_number: _system_item_label(
                        state, "ZoneName", zone_id
                    ),
                    entity_category=EntityCategory.DIAGNOSTIC,
                    zone_number=zone_number,
                )
            )
        if zone.mode.value is not None:
            entities.append(
                ElcoSensor(
                    coordinator,
                    "mode",
                    lambda state, zone_id=zone_number: (
                        state.zones[zone_id].mode.current_label or state.zones[zone_id].mode.value
                    ),
                    entity_category=EntityCategory.DIAGNOSTIC,
                    enabled_default=False,
                    zone_number=zone_number,
                )
            )

        zone_temperature_specs = (
            (
                "cooling_comfort_temperature",
                zone.cooling_comfort_temperature.value,
                EntityCategory.DIAGNOSTIC,
                False,
            ),
            (
                "cooling_reduced_temperature",
                zone.cooling_reduced_temperature.value,
                EntityCategory.DIAGNOSTIC,
                False,
            ),
            (
                "heating_protection_temperature",
                zone.heating_protection_temperature,
                EntityCategory.DIAGNOSTIC,
                False,
            ),
            (
                "cooling_protection_temperature",
                zone.cooling_protection_temperature,
                EntityCategory.DIAGNOSTIC,
                False,
            ),
            (
                "heating_holiday_temperature",
                zone.heating_holiday_temperature,
                EntityCategory.DIAGNOSTIC,
                False,
            ),
            (
                "cooling_holiday_temperature",
                zone.cooling_holiday_temperature,
                EntityCategory.DIAGNOSTIC,
                False,
            ),
        )
        for field, current_value, entity_category, enabled_default in zone_temperature_specs:
            if current_value is None:
                continue
            if field.startswith("cooling_") and not has_cooling:
                continue
            entities.append(
                ElcoSensor(
                    coordinator,
                    field,
                    lambda state, zone_id=zone_number, attribute=field: _zone_temperature_value(
                        state, zone_id, attribute
                    ),
                    temperature=True,
                    entity_category=entity_category,
                    enabled_default=enabled_default,
                    zone_number=zone_number,
                )
            )

        if zone.use_reduced_operation_mode_on_holiday is not None:
            entities.append(
                ElcoSensor(
                    coordinator,
                    "holiday_operating_level",
                    lambda state, zone_id=zone_number: (
                        "Reduced"
                        if state.zones[zone_id].use_reduced_operation_mode_on_holiday
                        else "Frost protection"
                    ),
                    entity_category=EntityCategory.DIAGNOSTIC,
                    zone_number=zone_number,
                )
            )

        for item_id, key_suffix, entity_category, enabled_default in (
            (
                "HeatingFlowTemp",
                "heating_flow_temperature",
                None,
                True,
            ),
            (
                "HeatingFlowOffset",
                "heating_flow_offset",
                EntityCategory.DIAGNOSTIC,
                False,
            ),
            (
                "CoolingFlowTemp",
                "cooling_flow_temperature",
                None,
                True,
            ),
            (
                "CoolingFlowOffset",
                "cooling_flow_offset",
                EntityCategory.DIAGNOSTIC,
                False,
            ),
            ("ZoneDeroga", "derogation_temperature", None, True),
        ):
            if not _has_system_item(data, item_id, zone_number):
                continue
            is_temperature_delta = key_suffix.endswith("_offset")
            entities.append(
                ElcoSensor(
                    coordinator,
                    key_suffix,
                    lambda state, source=item_id, zone_id=zone_number: _as_number(
                        state.discovery.system_value(source, zone_id)
                    ),
                    temperature=not is_temperature_delta,
                    temperature_delta=is_temperature_delta,
                    entity_category=entity_category,
                    enabled_default=enabled_default,
                    zone_number=zone_number,
                )
            )

    # --- BSB-only entities (data-point addresses; no Galevo equivalent) ---
    bsb_specs = (
        (
            BSB_ENTITY_ADDRESSES["700"],
            "heating_circuit_operating_mode_700",
            False,
        ),
        (
            BSB_ENTITY_ADDRESSES["710"],
            "heating_circuit_comfort_setpoint_710",
            True,
        ),
        (
            BSB_ENTITY_ADDRESSES["712"],
            "heating_circuit_reduced_setpoint_712",
            True,
        ),
        (
            BSB_ENTITY_ADDRESSES["714"],
            "heating_circuit_frost_protection_setpoint_714",
            True,
        ),
        (
            BSB_ENTITY_ADDRESSES["720"],
            "heating_curve_slope_720",
            False,
        ),
        (
            BSB_ENTITY_ADDRESSES["730"],
            "summer_winter_heating_limit_730",
            True,
        ),
    )
    for address, key, temperature in bsb_specs if bsb_family else ():
        if address not in data.discovery.bsb_points and not data.zones:
            continue
        entities.append(
            ElcoSensor(
                coordinator,
                key,
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
        if bsb_family
        else ()
    ):
        entities.append(
            ElcoSensor(
                coordinator,
                f"maintenance_{key_suffix}",
                lambda state, label=field_name, index=field_index: bsb_point_field_value(
                    state.discovery.bsb_points.get(maintenance_address), label, index
                ),
                entity_category=EntityCategory.DIAGNOSTIC,
                available_fn=lambda state: _bsb_available(state, maintenance_address),
            )
        )

    if not _has_system_item(data, "HeatingCircuitPressure"):
        pressure_address = BSB_ENTITY_ADDRESSES["heating_circuit_pressure"]
        if bsb_family and data.zones:
            entities.append(
                ElcoSensor(
                    coordinator,
                    "heating_circuit_pressure",
                    lambda state, bsb_address=pressure_address: _as_number(
                        _bsb_value(state, bsb_address)
                    ),
                    pressure=True,
                    available_fn=lambda state, bsb_address=pressure_address: _bsb_available(
                        state, bsb_address
                    ),
                )
            )

    for source, key, diagnostic in (
        (
            (
                "heat_pump_flow_temperature",
                "heat_pump_flow_temperature",
                False,
            ),
            (
                "heat_pump_return_temperature",
                "heat_pump_return_temperature",
                False,
            ),
            (
                "heat_pump_flow_setpoint",
                "heat_pump_flow_setpoint",
                False,
            ),
            (
                "heat_pump_gas_temperature",
                "heat_pump_gas_temperature",
                True,
            ),
            (
                "source_outlet_temperature",
                "source_outlet_temperature",
                True,
            ),
            (
                "source_outlet_temperature_minimum",
                "source_outlet_temperature_minimum",
                True,
            ),
            ("hot_gas_temperature", "hot_gas_temperature", True),
        )
        if bsb_family
        else ()
    ):
        address = BSB_ENTITY_ADDRESSES[source]
        if address not in data.discovery.bsb_points and not features.get("hpSys", False):
            continue
        entities.append(
            ElcoSensor(
                coordinator,
                key,
                lambda state, bsb_address=address: _as_number(_bsb_value(state, bsb_address)),
                temperature=True,
                entity_category=EntityCategory.DIAGNOSTIC if diagnostic else None,
                available_fn=lambda state, bsb_address=address: _bsb_available(state, bsb_address),
                enabled_default=not diagnostic,
            )
        )

    if bsb_family and any(supports_cooling(features, zone) for zone in data.zones.values()):
        for source, key in (
            (
                "cooling_2_flow_temperature",
                "cooling_2_flow_temperature",
            ),
            (
                "cooling_2_flow_setpoint",
                "cooling_2_flow_setpoint",
            ),
        ):
            address = BSB_ENTITY_ADDRESSES[source]
            entities.append(
                ElcoSensor(
                    coordinator,
                    key,
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
        "heat_delivered_heating",
        "heat_delivered_dhw",
        "refrigeration_delivered",
        "energy_input_heating",
        "energy_input_dhw",
        "energy_input_cooling",
    )
    for slot, addresses in BSB_ENERGY_HISTORY_ADDRESSES.items() if bsb_family else ():
        date_address = addresses["record_date"]
        entities.append(
            ElcoSensor(
                coordinator,
                f"annual_energy_record_{slot}_date",
                lambda state, bsb_address=date_address: bsb_point_date(
                    state.discovery.bsb_points.get(bsb_address)
                ),
                date_sensor=True,
                entity_category=EntityCategory.DIAGNOSTIC,
                available_fn=lambda state, bsb_address=date_address: _bsb_available(
                    state, bsb_address
                ),
                enabled_default=False,
                translation_key="annual_energy_record_date",
                translation_placeholders={"slot": str(slot)},
            )
        )
        factor_address = addresses["performance_factor"]
        entities.append(
            ElcoSensor(
                coordinator,
                f"annual_performance_factor_{slot}",
                lambda state, bsb_address=factor_address: _as_number(
                    _bsb_value(state, bsb_address)
                ),
                measurement=True,
                entity_category=EntityCategory.DIAGNOSTIC,
                available_fn=lambda state, bsb_address=factor_address: _bsb_available(
                    state, bsb_address
                ),
                enabled_default=False,
                translation_key="annual_performance_factor",
                translation_placeholders={"slot": str(slot)},
            )
        )
        for field in energy_fields:
            address = addresses[field]
            entities.append(
                ElcoSensor(
                    coordinator,
                    f"annual_{field}_{slot}",
                    lambda state, bsb_address=address: _as_number(_bsb_value(state, bsb_address)),
                    energy=True,
                    entity_category=EntityCategory.DIAGNOSTIC,
                    available_fn=lambda state, bsb_address=address: _bsb_available(
                        state, bsb_address
                    ),
                    enabled_default=False,
                    translation_key=f"annual_{field}",
                    translation_placeholders={"slot": str(slot)},
                )
            )

    monitoring_specs = (
        ("hydraulicPressure", "hydraulic_pressure"),
        ("refrigerantCircuit", "refrigerant_circuit"),
        ("circulation", "circulation"),
        ("combustion", "combustion"),
        ("other", "other"),
    )
    for field, key_suffix in monitoring_specs if bsb_family else ():
        entities.append(
            ElcoSensor(
                coordinator,
                f"automated_monitoring_{key_suffix}",
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

    if bsb_family:
        entities.append(
            ElcoSensor(
                coordinator,
                "predictive_maintenance_count",
                lambda state: (
                    len(notices)
                    if (notices := _predictive_maintenances(state)) is not None
                    else None
                ),
                entity_category=EntityCategory.DIAGNOSTIC,
                measurement=True,
                available_fn=lambda state: _predictive_maintenances(state) is not None,
            )
        )
    async_add_entities(entities)
