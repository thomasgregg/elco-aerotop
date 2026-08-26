"""Read-only BSB holiday calendars for ELCO Aerotop."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import override

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .coordinator import ElcoDataUpdateCoordinator
from .entity import ElcoAerotopEntity
from .holiday import has_holiday_source
from .models import BsbHoliday, ElcoData


def _usable_holidays(data: ElcoData, zone_number: int) -> list[BsbHoliday]:
    """Return valid controller periods in chronological order."""
    zone = data.zones.get(zone_number)
    if zone is None:
        return []
    return sorted(
        (
            holiday
            for holiday in zone.holidays
            if not holiday.deleted and not holiday.out_of_service
        ),
        key=lambda holiday: (holiday.start, holiday.end, holiday.index or -1),
    )


def _event(holiday: BsbHoliday, *, reduced: bool | None) -> CalendarEvent:
    """Convert ELCO's inclusive final day to HA's exclusive calendar end."""
    level = "Reduced" if reduced is True else "Frost protection" if reduced is False else None
    return CalendarEvent(
        start=holiday.start,
        end=holiday.end + timedelta(days=1),
        summary=f"Heating holiday ({level})" if level else "Heating holiday",
        description="ELCO BSB heating-circuit holiday period",
        uid=str(holiday.index) if holiday.index is not None else None,
    )


class ElcoHolidayCalendar(ElcoAerotopEntity, CalendarEntity):
    """A read-only calendar backed by the zone's BSB holiday periods."""

    def __init__(self, coordinator: ElcoDataUpdateCoordinator, zone_number: int) -> None:
        super().__init__(coordinator, f"zone_{zone_number}_holidays")
        self._zone_number = zone_number
        self._attr_name = f"Zone {zone_number} holidays"

    def _events(self) -> list[CalendarEvent]:
        zone = self.coordinator.data.zones[self._zone_number]
        return [
            _event(
                holiday,
                reduced=zone.use_reduced_operation_mode_on_holiday,
            )
            for holiday in _usable_holidays(self.coordinator.data, self._zone_number)
        ]

    @property
    @override
    def event(self) -> CalendarEvent | None:
        """Return the active or next holiday period."""
        now = dt_util.now()
        for event in self._events():
            start = datetime.combine(event.start, time.min, tzinfo=now.tzinfo)
            end = datetime.combine(event.end, time.min, tzinfo=now.tzinfo)
            if start <= now < end or start > now:
                return event
        return None

    @override
    async def async_get_events(
        self,
        hass: HomeAssistant,
        start_date: datetime,
        end_date: datetime,
    ) -> list[CalendarEvent]:
        """Return holiday periods overlapping a requested interval."""
        events: list[CalendarEvent] = []
        for event in self._events():
            event_start = datetime.combine(event.start, time.min, tzinfo=start_date.tzinfo)
            event_end = datetime.combine(event.end, time.min, tzinfo=start_date.tzinfo)
            if event_end > start_date and event_start < end_date:
                events.append(event)
        return events


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities,
) -> None:
    """Set up one read-only holiday calendar for each BSB zone."""
    coordinator: ElcoDataUpdateCoordinator = entry.runtime_data
    async_add_entities(
        ElcoHolidayCalendar(coordinator, zone_number)
        for zone_number, zone in coordinator.data.zones.items()
        if has_holiday_source(zone)
    )
