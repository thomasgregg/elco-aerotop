"""Tests for the strict writable-BSB allowlist and validation."""

import pytest

from custom_components.elco_aerotop.bsb_controls import (
    BSB_NUMBER_CONTROL_SPECS,
    BSB_WRITABLE_ADDRESSES,
    HOLIDAY_OPERATING_LEVEL_ADDRESS,
    bsb_point_number,
    holiday_level_value,
)


def _point(address: str, value: float, **extra):
    return {
        "address": address,
        "valueAsNumber": value,
        "osv": False,
        "anyError": False,
        "deviceFailure": False,
        "bsbErrorCode": 0,
        "commErrorCode": 0,
        **extra,
    }


def test_writable_addresses_are_limited_to_reviewed_controls() -> None:
    assert {
        "2950338",
        "2950546",
        "2950646",
        "2950653",
    } == BSB_WRITABLE_ADDRESSES


def test_frost_setpoint_uses_reduced_setpoint_as_dynamic_maximum() -> None:
    spec = BSB_NUMBER_CONTROL_SPECS["heating_circuit_frost_protection_setpoint_714"]
    points = {
        spec.address: _point(spec.address, 8),
        "2950544": _point("2950544", 18),
    }

    spec.validate(17.5, points)
    with pytest.raises(ValueError, match="above the maximum 18.0"):
        spec.validate(18.5, points)


def test_heating_curve_rejects_values_that_do_not_match_controller_step() -> None:
    spec = BSB_NUMBER_CONTROL_SPECS["heating_curve_slope_720"]

    spec.validate(0.8, {spec.address: _point(spec.address, 0.8)})
    with pytest.raises(ValueError, match="does not align with step"):
        spec.validate(0.81, {spec.address: _point(spec.address, 0.8)})


def test_holiday_level_uses_returned_enum_codes_not_assumed_codes() -> None:
    point = _point(
        HOLIDAY_OPERATING_LEVEL_ADDRESS,
        7,
        enumOptions=[
            {"value": 7, "text": "Frost protection"},
            {"value": 9, "text": "Reduced"},
        ],
    )

    assert holiday_level_value(point, "Reduced") == 9
    assert holiday_level_value(point, "Frost protection") == 7
    assert bsb_point_number(point) == 7


def test_holiday_level_rejects_an_option_not_offered_by_controller() -> None:
    point = _point(
        HOLIDAY_OPERATING_LEVEL_ADDRESS,
        7,
        enumOptions=[{"value": 7, "text": "Frost protection"}],
    )

    with pytest.raises(ValueError, match="does not offer"):
        holiday_level_value(point, "Reduced")
