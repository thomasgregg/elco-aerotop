"""Writable current-holiday end dates for ELCO Aerotop."""

from __future__ import annotations

from datetime import date
from typing import override

from homeassistant.components.date import DateEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.util import dt as dt_util

from .const import SYSTEM_TYPE_GALEVO
from .coordinator import ElcoDataUpdateCoordinator
from .entity import ElcoAerotopEntity, async_setup_platform_entities
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


class ElcoGalevoHolidayDate(ElcoAerotopEntity, DateEntity):
    """Pending plant-holiday boundary edited locally, sent on activation.

    Galevo holidays are scheduled via ``plantData/{gw}/holiday`` with a
    start/end pair (APK ``PlantDataProxy.setHoliday``). These entities only
    edit the pending window on the coordinator — nothing reaches the
    gateway until the holiday switch is turned on.
    """

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: ElcoDataUpdateCoordinator, boundary: str) -> None:
        super().__init__(coordinator, f"holiday_{boundary}")
        self._boundary = boundary
        self._attr_name = f"Holiday {boundary}"
        self._attr_icon = "mdi:calendar-start" if boundary == "start" else "mdi:calendar-end"

    @property
    @override
    def native_value(self) -> date:
        if self._boundary == "start":
            return self.coordinator.pending_holiday_start
        return self.coordinator.pending_holiday_end

    @override
    async def async_set_value(self, value: date) -> None:
        self.coordinator.set_pending_holiday(
            start=value if self._boundary == "start" else None,
            end=value if self._boundary == "end" else None,
        )
        self.async_write_ha_state()


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities,
) -> None:
    """Set up holiday dates: per-zone BSB until-dates and the Galevo window."""
    async_setup_platform_entities(entry, async_add_entities, _create_entities)


def _create_entities(
    coordinator: ElcoDataUpdateCoordinator,
    async_add_entities,
) -> None:
    entities: list[DateEntity] = [
        ElcoHolidayUntilDate(coordinator, zone_number)
        for zone_number, zone in coordinator.data.zones.items()
        if has_holiday_source(zone)
    ]
    if (
        coordinator.data.discovery.system_type == SYSTEM_TYPE_GALEVO
        and coordinator.data.discovery.system_item("Holiday") is not None
    ):
        entities.extend(
            ElcoGalevoHolidayDate(coordinator, boundary) for boundary in ("start", "end")
        )
    async_add_entities(entities)
