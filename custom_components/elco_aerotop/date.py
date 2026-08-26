"""Writable current-holiday end dates for ELCO Aerotop."""

from __future__ import annotations

from datetime import date
from typing import override

from homeassistant.components.date import DateEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .coordinator import ElcoDataUpdateCoordinator
from .entity import ElcoAerotopEntity
from .holiday import current_holiday, has_holiday_source


class ElcoHolidayUntilDate(ElcoAerotopEntity, DateEntity):
    """Set Remocon's current holiday from now through an inclusive final day."""

    _attr_icon = "mdi:calendar-end"

    def __init__(self, coordinator: ElcoDataUpdateCoordinator, zone_number: int) -> None:
        super().__init__(coordinator, f"zone_{zone_number}_holiday_until")
        self._zone_number = zone_number
        self._attr_name = f"Zone {zone_number} holiday until"

    @property
    @override
    def native_value(self) -> date | None:
        """Return the inclusive final day of Remocon's current holiday."""
        holiday = current_holiday(self.coordinator.data.zones[self._zone_number])
        return holiday.end if holiday is not None else None

    @override
    async def async_set_value(self, value: date) -> None:
        """Start or extend the current holiday using Home Assistant's local time."""
        await self.coordinator.async_set_zone_holiday(
            self._zone_number,
            value,
            starts_at=dt_util.now(),
        )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities,
) -> None:
    """Set up one current-holiday date for each capable BSB zone."""
    coordinator: ElcoDataUpdateCoordinator = entry.runtime_data
    async_add_entities(
        ElcoHolidayUntilDate(coordinator, zone_number)
        for zone_number, zone in coordinator.data.zones.items()
        if has_holiday_source(zone)
    )
