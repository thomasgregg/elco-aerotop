"""Strict allowlist and validation for writable BSB controller settings."""

from __future__ import annotations

from dataclasses import dataclass
from math import isclose, isfinite
from typing import Any, Final

from .models import bsb_point_available


@dataclass(frozen=True, slots=True)
class BsbNumberControlSpec:
    """Describe one reviewed numeric BSB control."""

    key: str
    address: str
    name: str
    minimum: float
    maximum: float
    step: float
    temperature: bool = False
    maximum_address: str | None = None

    def validate(self, value: float, points: dict[str, Any]) -> None:
        """Validate a requested value against reviewed and dynamic limits."""
        if not isfinite(value):
            raise ValueError("Value must be finite")
        maximum = self.maximum
        if self.maximum_address is not None:
            dynamic_maximum = bsb_point_number(points.get(self.maximum_address))
            if dynamic_maximum is None:
                raise ValueError(f"Remocon did not return the required limit for {self.name}")
            maximum = min(maximum, dynamic_maximum)
        if value < self.minimum:
            raise ValueError(f"Value {value} is below the minimum {self.minimum}")
        if value > maximum:
            raise ValueError(f"Value {value} is above the maximum {maximum}")
        steps = (value - self.minimum) / self.step
        if not isclose(steps, round(steps), abs_tol=1e-6):
            raise ValueError(f"Value {value} does not align with step {self.step}")


HOLIDAY_OPERATING_LEVEL_ADDRESS: Final = "2950338"
HOLIDAY_OPERATING_LEVEL_OPTIONS: Final = ("Reduced", "Frost protection")

BSB_NUMBER_CONTROL_SPECS: Final = {
    "heating_circuit_frost_protection_setpoint_714": BsbNumberControlSpec(
        key="heating_circuit_frost_protection_setpoint_714",
        address="2950546",
        name="Heating circuit 714 frost protection setpoint",
        minimum=4.0,
        maximum=35.0,
        step=0.5,
        temperature=True,
        # Remocon defines line 712 as the live upper bound for line 714.
        maximum_address="2950544",
    ),
    "heating_curve_slope_720": BsbNumberControlSpec(
        key="heating_curve_slope_720",
        address="2950646",
        name="Heating circuit 720 heating curve slope",
        minimum=0.1,
        maximum=4.0,
        step=0.02,
    ),
    "summer_winter_heating_limit_730": BsbNumberControlSpec(
        key="summer_winter_heating_limit_730",
        address="2950653",
        name="Heating circuit 730 summer/winter heating limit",
        minimum=8.0,
        maximum=30.0,
        step=0.5,
        temperature=True,
    ),
}

BSB_WRITABLE_ADDRESSES: Final = frozenset(
    {
        HOLIDAY_OPERATING_LEVEL_ADDRESS,
        *(spec.address for spec in BSB_NUMBER_CONTROL_SPECS.values()),
    }
)


def bsb_point_number(point: Any) -> float | None:
    """Return a usable numeric BSB value."""
    if not bsb_point_available(point) or not isinstance(point, dict):
        return None
    value = point.get("valueAsNumber")
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        parsed = float(value)
        return parsed if isfinite(parsed) else None
    try:
        parsed = float(value)
        return parsed if isfinite(parsed) else None
    except (TypeError, ValueError):
        return None


def holiday_level_value(point: Any, option: str) -> int:
    """Resolve a stable Home Assistant holiday label using server enum text."""
    if option not in HOLIDAY_OPERATING_LEVEL_OPTIONS:
        raise ValueError(f"Unsupported holiday operating level: {option}")
    if not bsb_point_available(point) or not isinstance(point, dict):
        raise ValueError("Holiday operating level is unavailable")
    enum_options = point.get("enumOptions")
    if not isinstance(enum_options, list):
        raise ValueError("Remocon did not return holiday operating-level options")

    requested_reduced = option == "Reduced"
    for item in enum_options:
        if not isinstance(item, dict):
            continue
        label = str(item.get("text", "")).casefold()
        matches = (
            "reduced" in label if requested_reduced else ("frost" in label or "protection" in label)
        )
        value = item.get("value")
        if matches and isinstance(value, int | float) and not isinstance(value, bool):
            return int(value)
    raise ValueError(f"Remocon does not offer the holiday operating level {option}")
