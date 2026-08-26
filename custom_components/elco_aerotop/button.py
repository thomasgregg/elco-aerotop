"""Current-holiday cancellation buttons for ELCO Aerotop."""

from __future__ import annotations

from typing import override

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .coordinator import ElcoDataUpdateCoordinator
from .entity import ElcoAerotopEntity
from .holiday import current_holiday, has_holiday_source


class ElcoCancelHolidayButton(ElcoAerotopEntity, ButtonEntity):
    """Cancel Remocon's current holiday for one zone."""

    _attr_icon = "mdi:calendar-remove"

    def __init__(self, coordinator: ElcoDataUpdateCoordinator, zone_number: int) -> None:
        super().__init__(coordinator, f"zone_{zone_number}_cancel_holiday")
        self._zone_number = zone_number
        self._attr_name = f"Zone {zone_number} cancel holiday"

    @property
    @override
    def available(self) -> bool:
        """Make cancellation available only while a usable holiday exists."""
        zone = self.coordinator.data.zones.get(self._zone_number)
        return super().available and zone is not None and current_holiday(zone) is not None

    @override
    async def async_press(self) -> None:
        """Cancel the current holiday without changing the zone's resulting mode."""
        await self.coordinator.async_cancel_zone_holiday(self._zone_number)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities,
) -> None:
    """Set up one cancel button for each capable BSB zone."""
    coordinator: ElcoDataUpdateCoordinator = entry.runtime_data
    async_add_entities(
        ElcoCancelHolidayButton(coordinator, zone_number)
        for zone_number, zone in coordinator.data.zones.items()
        if has_holiday_source(zone)
    )
