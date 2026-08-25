"""Native thermostat entities for ELCO Aerotop heating and cooling zones."""

from __future__ import annotations

from typing import Any

from homeassistant.components.climate import ClimateEntity
from homeassistant.components.climate.const import (
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .control_mapping import (
    zone_hvac_mode,
    zone_hvac_modes,
    zone_mode_for_hvac,
    zone_mode_for_preset,
    zone_preset,
    zone_presets,
)
from .coordinator import ElcoDataUpdateCoordinator
from .entity import ElcoAerotopEntity
from .models import NumericVariable, ZoneState


class ElcoZoneClimate(ElcoAerotopEntity, ClimateEntity):
    """Represent one ELCO heating zone as a Home Assistant thermostat."""

    _attr_temperature_unit = UnitOfTemperature.CELSIUS

    def __init__(
        self,
        coordinator: ElcoDataUpdateCoordinator,
        zone_number: int,
    ) -> None:
        super().__init__(coordinator, f"zone_{zone_number}_thermostat")
        self._zone_number = zone_number
        self._attr_name = f"Zone {zone_number} thermostat"

        variable = self._target_variable
        self._attr_min_temp = variable.minimum if variable.minimum is not None else 5.0
        self._attr_max_temp = variable.maximum if variable.maximum is not None else 35.0
        self._attr_target_temperature_step = variable.step or 0.5
        self._attr_preset_modes = list(zone_presets(self._zone.mode))
        self._attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE
        if self._attr_preset_modes:
            self._attr_supported_features |= ClimateEntityFeature.PRESET_MODE

    @property
    def _zone(self) -> ZoneState:
        return self.coordinator.data.zones[self._zone_number]

    @property
    def _is_cooling(self) -> bool:
        """Return whether ELCO currently has the zone in its cooling season."""
        return self._zone.cooling_active is True

    @property
    def _target_variable(self) -> NumericVariable:
        """Return the comfort setpoint controlled in the active season."""
        return (
            self._zone.cooling_comfort_temperature
            if self._is_cooling
            else self._zone.comfort_temperature
        )

    @property
    def hvac_modes(self) -> list[HVACMode]:
        """Expose cool only when the controller reports cooling as active."""
        return [
            HVACMode(mode) for mode in zone_hvac_modes(self._zone.mode, cooling=self._is_cooling)
        ]

    @property
    def min_temp(self) -> float:
        variable = self._target_variable
        return variable.minimum if variable.minimum is not None else 5.0

    @property
    def max_temp(self) -> float:
        variable = self._target_variable
        return variable.maximum if variable.maximum is not None else 35.0

    @property
    def target_temperature_step(self) -> float:
        return self._target_variable.step or 0.5

    @property
    def current_temperature(self) -> float | None:
        """Return a real room reading, never a flow or outdoor substitute."""
        zone = self._zone
        if zone.has_room_sensor is False or zone.room_temperature_error is True:
            return None
        return zone.room_temperature

    @property
    def target_temperature(self) -> float | None:
        """Return the active heating or cooling comfort setpoint."""
        return self._target_variable.value

    @property
    def hvac_mode(self) -> HVACMode | None:
        """Return the native HVAC mode for the current controller mode."""
        mode = zone_hvac_mode(self._zone.mode, cooling=self._is_cooling)
        return HVACMode(mode) if mode is not None else None

    @property
    def hvac_action(self) -> HVACAction | None:
        """Return the actual controller activity when it is reported."""
        zone = self._zone
        if zone.cooling_active is True:
            return HVACAction.COOLING
        if zone.heating_active is True:
            return HVACAction.HEATING
        if zone.heating_active is not None or zone.cooling_active is not None:
            return HVACAction.IDLE
        return None

    @property
    def preset_mode(self) -> str | None:
        """Return Comfort, eco, or Protection for direct controller modes."""
        return zone_preset(self._zone.mode)

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set the zone comfort temperature through the verified write path."""
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            raise HomeAssistantError("A target temperature is required")
        await self.coordinator.async_set_zone_temperature(
            self._zone_number,
            "cooling_comfort" if self._is_cooling else "comfort",
            float(temperature),
        )

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set Automatic or the best available direct operating mode."""
        try:
            mode = zone_mode_for_hvac(
                self._zone.mode,
                hvac_mode.value,
                cooling=self._is_cooling,
            )
        except ValueError as err:
            raise HomeAssistantError(str(err)) from err
        await self.coordinator.async_set_zone_mode(self._zone_number, mode)

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set a verified ELCO direct-heating mode through a native preset."""
        try:
            mode = zone_mode_for_preset(self._zone.mode, preset_mode)
        except ValueError as err:
            raise HomeAssistantError(str(err)) from err
        await self.coordinator.async_set_zone_mode(self._zone_number, mode)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities,
) -> None:
    """Set up a native thermostat for each safely controllable zone."""
    coordinator: ElcoDataUpdateCoordinator = entry.runtime_data
    entities = [
        ElcoZoneClimate(coordinator, zone_number)
        for zone_number, zone in coordinator.data.zones.items()
        if (
            (
                zone.cooling_comfort_temperature.value
                if zone.cooling_active is True
                else zone.comfort_temperature.value
            )
            is not None
            and zone_hvac_modes(zone.mode, cooling=zone.cooling_active is True)
        )
    ]
    async_add_entities(entities)
