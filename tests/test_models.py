"""Tests for tolerant Remocon data parsing."""

from custom_components.elco_aerotop.models import NumericVariable, PlantState, ZoneState


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
