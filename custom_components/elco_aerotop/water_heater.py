"""Native domestic-hot-water entity for ELCO Aerotop."""

from __future__ import annotations

from typing import Any

from homeassistant.components.water_heater import (
    STATE_HEAT_PUMP,
    WaterHeaterEntity,
    WaterHeaterEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, STATE_OFF, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .const import SYSTEM_TYPE_GALEVO
from .control_mapping import (
    DHW_MODE_ON,
    dhw_galevo_mode_for_operation,
    dhw_galevo_operation,
    dhw_galevo_operations,
    dhw_mode_for_operation,
    dhw_operation,
    dhw_operations,
)
from .coordinator import ElcoDataUpdateCoordinator
from .entity import ElcoAerotopEntity, async_setup_platform_entities


class ElcoWaterHeater(ElcoAerotopEntity, WaterHeaterEntity):
    """Represent ELCO domestic hot water using Home Assistant's native model."""

    _attr_name = "Domestic hot water"
    _attr_temperature_unit = UnitOfTemperature.CELSIUS

    def __init__(self, coordinator: ElcoDataUpdateCoordinator) -> None:
        super().__init__(coordinator, "domestic_hot_water")
        variable = coordinator.data.plant.dhw_comfort_temperature
        self._attr_min_temp = variable.minimum if variable.minimum is not None else 5.0
        self._attr_max_temp = variable.maximum if variable.maximum is not None else 65.0
        self._attr_target_temperature_step = variable.step or 0.5
        # Galevo exposes its own mode labels (e.g. "Time Based", "Always
        # active"); BSB keeps the canonical Home Assistant operations.
        self._galevo = coordinator.data.discovery.system_type == SYSTEM_TYPE_GALEVO
        dhw_mode = coordinator.data.plant.dhw_mode
        if self._galevo:
            self._attr_operation_list = list(dhw_galevo_operations(dhw_mode))
        else:
            self._attr_operation_list = list(dhw_operations(dhw_mode))
        self._attr_supported_features = (
            WaterHeaterEntityFeature.TARGET_TEMPERATURE | WaterHeaterEntityFeature.OPERATION_MODE
        )
        if self._has_on_off_operations(dhw_mode):
            self._attr_supported_features |= WaterHeaterEntityFeature.ON_OFF

    def _has_on_off_operations(self, variable) -> bool:
        if self._galevo:
            values = {option.value for option in variable.options}
            return 0 in values and any(value != 0 for value in values)
        operations = self._attr_operation_list or []
        return STATE_OFF in operations and STATE_HEAT_PUMP in operations

    def _mode_for_operation(self, operation: str) -> int:
        variable = self.coordinator.data.plant.dhw_mode
        if self._galevo:
            return dhw_galevo_mode_for_operation(variable, operation)
        return dhw_mode_for_operation(variable, operation)

    @property
    def current_temperature(self) -> float | None:
        """Return the measured DHW storage temperature when its probe is healthy."""
        plant = self.coordinator.data.plant
        if plant.has_dhw_temperature_probe is False or plant.dhw_temperature_error is True:
            return None
        return plant.dhw_current_temperature

    @property
    def target_temperature(self) -> float | None:
        """Return the normal DHW comfort target."""
        return self.coordinator.data.plant.dhw_comfort_temperature.value

    @property
    def current_operation(self) -> str | None:
        """Return the current DHW operation (gateway label on Galevo)."""
        if self._galevo:
            return dhw_galevo_operation(self.coordinator.data.plant.dhw_mode)
        return dhw_operation(self.coordinator.data.plant.dhw_mode)

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set the DHW comfort target and optional operation atomically."""
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            raise HomeAssistantError("A target temperature is required")
        operation = kwargs.get("operation_mode")
        mode = None
        if operation is not None:
            try:
                mode = self._mode_for_operation(str(operation))
            except ValueError as err:
                raise HomeAssistantError(str(err)) from err
        await self.coordinator.async_set_dhw(comfort=float(temperature), mode=mode)

    async def async_set_operation_mode(self, operation_mode: str) -> None:
        """Set a verified ELCO DHW operating mode."""
        try:
            mode = self._mode_for_operation(operation_mode)
        except ValueError as err:
            raise HomeAssistantError(str(err)) from err
        await self.coordinator.async_set_dhw(mode=mode)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn DHW on using the controller's normal On mode."""
        if self._galevo:
            variable = self.coordinator.data.plant.dhw_mode
            label = next(
                (o.label for o in variable.options if o.value == DHW_MODE_ON),
                next((o.label for o in variable.options if o.value != 0), None),
            )
            if label is None:
                raise HomeAssistantError("The gateway offers no DHW on mode")
            await self.async_set_operation_mode(label)
            return
        await self.async_set_operation_mode(STATE_HEAT_PUMP)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn DHW off using the controller's Off mode."""
        if self._galevo:
            variable = self.coordinator.data.plant.dhw_mode
            label = next((o.label for o in variable.options if o.value == 0), None)
            if label is None:
                raise HomeAssistantError("The gateway offers no DHW off mode")
            await self.async_set_operation_mode(label)
            return
        await self.async_set_operation_mode(STATE_OFF)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities,
) -> None:
    """Set up the native domestic-hot-water entity when fully supported."""
    async_setup_platform_entities(entry, async_add_entities, _create_entities)


def _create_entities(
    coordinator: ElcoDataUpdateCoordinator,
    async_add_entities,
) -> None:
    plant = coordinator.data.plant
    galevo = coordinator.data.discovery.system_type == SYSTEM_TYPE_GALEVO
    has_operations = (
        dhw_galevo_operations(plant.dhw_mode) if galevo else dhw_operations(plant.dhw_mode)
    )
    if plant.dhw_comfort_temperature.value is None or not has_operations:
        return
    async_add_entities([ElcoWaterHeater(coordinator)])
