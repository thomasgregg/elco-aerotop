"""Tests for verified native-control mappings."""

import json
from pathlib import Path

import pytest

from custom_components.elco_aerotop.control_mapping import (
    PRESET_PROTECTION,
    dhw_mode_for_operation,
    dhw_operation,
    dhw_operations,
    zone_hvac_mode,
    zone_hvac_modes,
    zone_mode_for_hvac,
    zone_mode_for_preset,
    zone_preset,
    zone_presets,
)
from custom_components.elco_aerotop.models import PlantState, ZoneState


def _fixture_states() -> tuple[PlantState, ZoneState]:
    fixture = json.loads((Path(__file__).parent / "fixtures" / "aerotop_one_zone.json").read_text())
    return (
        PlantState.parse(fixture["get_data"]["plantData"]),
        ZoneState.parse(1, fixture["get_data"]["zoneData"]),
    )


def test_zone_modes_map_by_code_not_translated_label() -> None:
    _, zone = _fixture_states()

    assert zone_hvac_modes(zone.mode) == ("auto", "heat")
    assert zone_hvac_mode(zone.mode) == "auto"
    assert zone_presets(zone.mode) == (PRESET_PROTECTION, "eco", "comfort")
    assert zone_preset(zone.mode) is None
    assert zone_mode_for_hvac(zone.mode, "heat") == 3
    assert zone_mode_for_preset(zone.mode, "eco") == 2


def test_zone_protection_is_a_heat_preset_not_off() -> None:
    _, zone = _fixture_states()
    protection = type(zone.mode)(value=0, options=zone.mode.options)

    assert zone_hvac_mode(protection) == "heat"
    assert zone_preset(protection) == PRESET_PROTECTION
    assert zone_mode_for_preset(protection, PRESET_PROTECTION) == 0


def test_dhw_modes_only_include_options_offered_by_gateway() -> None:
    plant, _ = _fixture_states()

    assert dhw_operations(plant.dhw_mode) == ("off", "heat_pump")
    assert dhw_operation(plant.dhw_mode) == "heat_pump"
    assert dhw_mode_for_operation(plant.dhw_mode, "off") == 0
    with pytest.raises(ValueError, match="Unsupported DHW operation"):
        dhw_mode_for_operation(plant.dhw_mode, "eco")
