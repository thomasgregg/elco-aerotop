"""Writable modes for ELCO Aerotop."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory

from .bsb_controls import HOLIDAY_OPERATING_LEVEL_ADDRESS, HOLIDAY_OPERATING_LEVEL_OPTIONS
from .coordinator import ElcoDataUpdateCoordinator
from .entity import ElcoAerotopEntity
from .models import bsb_point_value


class ElcoDhwModeSelect(ElcoAerotopEntity, SelectEntity):
    _attr_name = "Domestic hot water mode"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: ElcoDataUpdateCoordinator) -> None:
        super().__init__(coordinator, "dhw_mode")

    @property
    def current_option(self) -> str | None:
        return self.coordinator.data.plant.dhw_mode.current_label

    @property
    def options(self) -> list[str]:
        return [option.label for option in self.coordinator.data.plant.dhw_mode.options]

    async def async_select_option(self, option: str) -> None:
        value = self.coordinator.data.plant.dhw_mode.value_for_label(option)
        await self.coordinator.async_set_dhw(mode=value)


class ElcoZoneModeSelect(ElcoAerotopEntity, SelectEntity):
    """Control a heating-zone mode."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: ElcoDataUpdateCoordinator, zone_number: int) -> None:
        super().__init__(coordinator, f"zone_{zone_number}_mode")
        self._zone_number = zone_number
        self._attr_name = f"Zone {zone_number} mode"

    @property
    def current_option(self) -> str | None:
        return self.coordinator.data.zones[self._zone_number].mode.current_label

    @property
    def options(self) -> list[str]:
        return [
            option.label for option in self.coordinator.data.zones[self._zone_number].mode.options
        ]

    async def async_select_option(self, option: str) -> None:
        zone = self.coordinator.data.zones[self._zone_number]
        await self.coordinator.async_set_zone_mode(
            self._zone_number,
            zone.mode.value_for_label(option),
        )


class ElcoHolidayOperatingLevelSelect(ElcoAerotopEntity, SelectEntity):
    """Control the BSB heating-circuit 1 holiday operating level."""

    _attr_name = "Zone 1 holiday operating level"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = list(HOLIDAY_OPERATING_LEVEL_OPTIONS)

    def __init__(self, coordinator: ElcoDataUpdateCoordinator) -> None:
        super().__init__(coordinator, "zone_1_holiday_operating_level")

    @property
    def current_option(self) -> str | None:
        label = bsb_point_value(
            self.coordinator.data.discovery.bsb_points.get(HOLIDAY_OPERATING_LEVEL_ADDRESS)
        )
        if isinstance(label, str):
            normalized = label.casefold()
            if "reduced" in normalized:
                return "Reduced"
            if "frost" in normalized or "protection" in normalized:
                return "Frost protection"
        zone = self.coordinator.data.zones.get(1)
        if zone is None or zone.use_reduced_operation_mode_on_holiday is None:
            return None
        return "Reduced" if zone.use_reduced_operation_mode_on_holiday else "Frost protection"

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_set_holiday_operating_level(option)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities,
) -> None:
    coordinator: ElcoDataUpdateCoordinator = entry.runtime_data
    entities: list[SelectEntity] = []
    if coordinator.data.plant.dhw_mode.options:
        entities.append(ElcoDhwModeSelect(coordinator))
    entities.extend(
        ElcoZoneModeSelect(coordinator, zone_number)
        for zone_number, zone in coordinator.data.zones.items()
        if zone.mode.options
    )
    if 1 in coordinator.data.zones:
        entities.append(ElcoHolidayOperatingLevelSelect(coordinator))
    async_add_entities(entities)
