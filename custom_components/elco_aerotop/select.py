"""Writable modes for ELCO Aerotop."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .coordinator import ElcoDataUpdateCoordinator
from .entity import ElcoAerotopEntity


class ElcoDhwModeSelect(ElcoAerotopEntity, SelectEntity):
    _attr_name = "Domestic hot water mode"

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
    async_add_entities(entities)
