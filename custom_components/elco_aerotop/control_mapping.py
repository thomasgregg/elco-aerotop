"""Verified ELCO controller-mode mappings used by native Home Assistant controls."""

from __future__ import annotations

from .models import SelectVariable

ZONE_MODE_PROTECTION = 0
ZONE_MODE_AUTOMATIC = 1
ZONE_MODE_REDUCED = 2
ZONE_MODE_COMFORT = 3

DHW_MODE_OFF = 0
DHW_MODE_ON = 1
DHW_MODE_ECO = 2

PRESET_PROTECTION = "Protection"

_ZONE_HVAC_MODES = {
    ZONE_MODE_PROTECTION: "heat",
    ZONE_MODE_AUTOMATIC: "auto",
    ZONE_MODE_REDUCED: "heat",
    ZONE_MODE_COMFORT: "heat",
}
_ZONE_PRESETS = {
    ZONE_MODE_PROTECTION: PRESET_PROTECTION,
    ZONE_MODE_REDUCED: "eco",
    ZONE_MODE_COMFORT: "comfort",
}
_DHW_OPERATIONS = {
    DHW_MODE_OFF: "off",
    DHW_MODE_ON: "heat_pump",
    DHW_MODE_ECO: "eco",
}


def _supported_values(variable: SelectVariable) -> set[int]:
    """Return values explicitly offered by the current gateway."""
    return {option.value for option in variable.options}


def zone_hvac_mode(variable: SelectVariable) -> str | None:
    """Translate the current ELCO zone mode to a Home Assistant HVAC mode."""
    return _ZONE_HVAC_MODES.get(variable.value)


def zone_hvac_modes(variable: SelectVariable) -> tuple[str, ...]:
    """Return only HVAC modes backed by options offered by the gateway."""
    supported = _supported_values(variable)
    modes: list[str] = []
    if ZONE_MODE_AUTOMATIC in supported:
        modes.append("auto")
    if supported.intersection({ZONE_MODE_PROTECTION, ZONE_MODE_REDUCED, ZONE_MODE_COMFORT}):
        modes.append("heat")
    return tuple(modes)


def zone_preset(variable: SelectVariable) -> str | None:
    """Translate the current ELCO zone mode to a Home Assistant preset."""
    return _ZONE_PRESETS.get(variable.value)


def zone_presets(variable: SelectVariable) -> tuple[str, ...]:
    """Return only presets backed by options offered by the gateway."""
    supported = _supported_values(variable)
    return tuple(preset for value, preset in _ZONE_PRESETS.items() if value in supported)


def zone_mode_for_hvac(variable: SelectVariable, hvac_mode: str) -> int:
    """Return the verified controller mode for a Home Assistant HVAC mode."""
    supported = _supported_values(variable)
    if hvac_mode == "auto" and ZONE_MODE_AUTOMATIC in supported:
        return ZONE_MODE_AUTOMATIC
    if hvac_mode == "heat":
        for value in (ZONE_MODE_COMFORT, ZONE_MODE_REDUCED, ZONE_MODE_PROTECTION):
            if value in supported:
                return value
    raise ValueError(f"Unsupported HVAC mode: {hvac_mode}")


def zone_mode_for_preset(variable: SelectVariable, preset: str) -> int:
    """Return the verified controller mode for a Home Assistant preset."""
    supported = _supported_values(variable)
    for value, mapped_preset in _ZONE_PRESETS.items():
        if preset == mapped_preset and value in supported:
            return value
    raise ValueError(f"Unsupported preset: {preset}")


def dhw_operation(variable: SelectVariable) -> str | None:
    """Translate the current ELCO DHW mode to a Home Assistant operation."""
    return _DHW_OPERATIONS.get(variable.value)


def dhw_operations(variable: SelectVariable) -> tuple[str, ...]:
    """Return only DHW operations backed by options offered by the gateway."""
    supported = _supported_values(variable)
    return tuple(operation for value, operation in _DHW_OPERATIONS.items() if value in supported)


def dhw_mode_for_operation(variable: SelectVariable, operation: str) -> int:
    """Return the verified controller mode for a Home Assistant DHW operation."""
    supported = _supported_values(variable)
    for value, mapped_operation in _DHW_OPERATIONS.items():
        if operation == mapped_operation and value in supported:
            return value
    raise ValueError(f"Unsupported DHW operation: {operation}")
