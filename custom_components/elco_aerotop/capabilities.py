"""Capability checks that reject unsupported sentinel values."""

from __future__ import annotations

from typing import Any

from .models import ZoneState


def supports_cooling(features: dict[str, Any], zone: ZoneState) -> bool:
    """Return whether cooling is genuinely supported instead of merely zero-filled."""
    return bool(
        features.get("hasTwoCoolingTemp")
        or features.get("distinctHeatCoolSetpoints")
        or zone.cooling_active is True
        or (zone.cooling_comfort_temperature.value or 0) != 0
        or (zone.cooling_reduced_temperature.value or 0) != 0
    )


def supports_room_sensor(zone: ZoneState) -> bool:
    """Return whether a room value should be exposed."""
    return zone.has_room_sensor is True or (
        zone.has_room_sensor is not False and zone.room_temperature is not None
    )
