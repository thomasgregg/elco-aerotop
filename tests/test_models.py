"""Tests for tolerant Remocon data parsing."""

import json
from datetime import date
from pathlib import Path

from custom_components.elco_aerotop.capabilities import supports_cooling, supports_room_sensor
from custom_components.elco_aerotop.const import BSB_DISCOVERY_GROUPS, BSB_ENTITY_ADDRESSES
from custom_components.elco_aerotop.models import (
    BsbHoliday,
    NumericVariable,
    PlantState,
    ReadOnlyDiscovery,
    ZoneState,
    bsb_point_available,
    bsb_point_field_value,
    bsb_point_value,
)


def test_parse_web_r2_plant_names() -> None:
    plant = PlantState.parse(
        {
            "outsideTemp": 4.2,
            "heatPumpOn": True,
            "dhwStorageTemp": 48.7,
            "dhwComfortTemp": {"value": 52, "min": 40, "max": 60, "step": 1},
            "dhwReducedTemp": {"value": 45, "min": 8, "max": 60, "step": 1},
            "dhwMode": {
                "value": 1,
                "allowedOptions": [0, 1, 2],
                "allowedOptionTexts": ["Off", "On", "Eco"],
            },
        }
    )

    assert plant.outside_temperature == 4.2
    assert plant.heat_pump_on is True
    assert plant.dhw_current_temperature == 48.7
    assert plant.dhw_comfort_temperature.value == 52
    assert plant.dhw_mode.current_label == "On"
    assert plant.dhw_mode.value_for_label("Eco") == 2


def test_parse_mobile_api_aliases() -> None:
    plant = PlantState.parse(
        {
            "outTemp": "10,7",
            "hpOn": True,
            "dhwTemp": 56.5,
            "dhwComfTemp": {"value": 57, "min": 45, "max": 57, "step": 1},
            "dhwReduTemp": {"value": 45, "min": 8, "max": 57, "step": 1},
        }
    )
    zone = ZoneState.parse(
        1,
        {
            "mode": {"allowedOptions": [0, 1, 2, 3], "value": 1},
            "chComfTemp": {"value": 24, "min": 17.5, "max": 28, "step": 0.5},
            "chRedTemp": {"value": 17.5, "min": 10, "max": 24, "step": 0.5},
            "desiredRoomTemp": 17.5,
            "heatOrCoolReq": True,
        },
    )

    assert plant.outside_temperature == 10.7
    assert plant.dhw_comfort_temperature.maximum == 57
    assert zone.comfort_temperature.value == 24
    assert zone.reduced_temperature.value == 17.5
    assert zone.heat_or_cool_request is True


def test_numeric_variable_validates_boundaries_and_step() -> None:
    variable = NumericVariable(value=20, minimum=10, maximum=30, step=0.5)
    variable.validate(20.5)

    for invalid in (9.5, 30.5, 20.2):
        try:
            variable.validate(invalid)
        except ValueError:
            pass
        else:
            raise AssertionError(f"Expected {invalid} to be rejected")


def test_parse_extended_conditional_values() -> None:
    plant = PlantState.parse(
        {
            "flameSensor": True,
            "dhwEnabled": False,
            "outsideTempError": False,
            "hasOutsideTempProbe": True,
        }
    )
    zone = ZoneState.parse(
        1,
        {
            "coolComfortTemp": {"value": 24, "min": 18, "max": 30},
            "chProtectionTemp": 8,
            "roomTempError": True,
            "isCoolingActive": False,
        },
    )

    assert plant.flame_on is True
    assert plant.dhw_enabled is False
    assert plant.outside_temperature_error is False
    assert plant.has_outside_temperature_probe is True
    assert zone.cooling_comfort_temperature.value == 24
    assert zone.heating_protection_temperature == 8
    assert zone.room_temperature_error is True
    assert zone.cooling_active is False


def test_read_only_discovery_resolves_system_items() -> None:
    discovery = ReadOnlyDiscovery(
        system_items={"ZoneMeasuredTemp:1": {"id": "ZoneMeasuredTemp", "value": 22.4}}
    )

    assert discovery.system_value("ZoneMeasuredTemp", 1) == 22.4
    assert discovery.system_item("Missing", 0) is None


def test_capability_checks_reject_zero_filled_unsupported_values() -> None:
    zone = ZoneState.parse(
        1,
        {
            "hasRoomSensor": False,
            "roomTemp": 0,
            "isCoolingActive": False,
            "coolComfortTemp": {"value": 0, "min": 0, "max": 0, "step": 0},
            "coolReducedTemp": {"value": 0, "min": 0, "max": 0, "step": 0},
        },
    )

    assert supports_room_sensor(zone) is False
    assert (
        supports_cooling({"hasTwoCoolingTemp": False, "distinctHeatCoolSetpoints": False}, zone)
        is False
    )


def test_capability_checks_accept_real_cooling_values() -> None:
    zone = ZoneState.parse(1, {"coolComfortTemp": {"value": 24}})

    assert supports_cooling({}, zone) is True


def test_room_sensor_capability_does_not_require_a_setup_time_reading() -> None:
    zone = ZoneState.parse(1, {"hasRoomSensor": True, "roomTemp": None})

    assert supports_room_sensor(zone) is True


def test_bsb_enum_value_uses_server_label() -> None:
    point = {
        "valueAsNumber": 1.0,
        "enumOptions": [
            {"value": 0, "text": "Protection"},
            {"value": 1, "text": "Automatic"},
        ],
        "osv": False,
        "anyError": False,
        "deviceFailure": False,
        "bsbErrorCode": 0,
        "commErrorCode": 0,
    }

    assert bsb_point_available(point) is True
    assert bsb_point_value(point) == "Automatic"


def test_bsb_out_of_service_value_is_unavailable() -> None:
    point = {
        "valueAsNumber": 0.0,
        "osv": True,
        "anyError": False,
        "deviceFailure": False,
        "bsbErrorCode": 0,
        "commErrorCode": 0,
    }

    assert bsb_point_available(point) is False


def test_bsb_missing_value_is_unknown_not_unavailable() -> None:
    point = {
        "valueAsNumber": None,
        "osv": False,
        "anyError": False,
        "deviceFailure": False,
        "bsbErrorCode": 0,
        "commErrorCode": 0,
    }

    assert bsb_point_available(point) is True
    assert bsb_point_value(point) is None


def test_bsb_maintenance_message_fields_are_read_by_name_with_index_fallback() -> None:
    point = {
        "address": "327836",
        "fields": [
            {"name": "Maintenance code 1", "valueAsString": "0:No maintenance required"},
            {"name": "Maintenance priority 1", "value": 0},
            {"label": "Maintenance code 2", "textualValue": "0:No maintenance required"},
            {"valueAsNumber": 0},
        ],
    }

    assert bsb_point_field_value(point, "Maintenance code 1", 0) == "0:No maintenance required"
    assert bsb_point_field_value(point, "Maintenance priority 1", 1) == 0
    assert bsb_point_field_value(point, "Maintenance code 2", 2) == "0:No maintenance required"
    assert bsb_point_field_value(point, "Maintenance priority 2", 3) == 0


def test_parse_current_bsb_holiday_fields_and_flags() -> None:
    fixture = json.loads((Path(__file__).parent / "fixtures" / "bsb_holidays.json").read_text())
    raw = fixture["holidays"][0]
    raw["index"] = 3
    raw["changed"] = True
    holiday = BsbHoliday.parse(raw)

    assert holiday is not None
    assert holiday.index == 3
    assert holiday.start == date(2027, 8, 30)
    assert holiday.end == date(2027, 9, 4)
    assert holiday.changed is True


def test_parse_legacy_bsb_holiday_fields() -> None:
    zone = ZoneState.parse(
        1,
        {
            "holidays": [
                {
                    "index": 0,
                    "from": "2027-12-20T00:00:00",
                    "to": "2027-12-27T00:00:00",
                    "osv": False,
                }
            ]
        },
    )

    assert len(zone.holidays) == 1
    assert zone.holidays[0].start == date(2027, 12, 20)
    assert zone.holidays[0].end == date(2027, 12, 27)


def test_heating_setpoint_addresses_match_live_controller_values() -> None:
    assert BSB_ENTITY_ADDRESSES["7000_maintenance_message"] == "327836"
    assert BSB_ENTITY_ADDRESSES["710"] == "2950542"
    assert BSB_ENTITY_ADDRESSES["712"] == "2950544"
    assert BSB_DISCOVERY_GROUPS["plant_auxiliary_2950542"] == ("2950542",)
    assert BSB_DISCOVERY_GROUPS["maintenance_message"] == ("327836",)
