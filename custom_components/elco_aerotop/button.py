"""Current-holiday cancellation buttons for ELCO Aerotop."""

from __future__ import annotations

from typing import override

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory

from .coordinator import ElcoDataUpdateCoordinator
from .entity import ElcoAerotopEntity, async_setup_platform_entities
from .holiday import current_holiday, has_holiday_source


class ElcoRefreshButton(ElcoAerotopEntity, ButtonEntity):
    """Trigger an immediate coordinator refresh of all gateway data."""

    _attr_icon = "mdi:refresh"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_name = "Refresh"

    def __init__(self, coordinator: ElcoDataUpdateCoordinator) -> None:
        super().__init__(coordinator, "refresh")

    @override
    async def async_press(self) -> None:
        """Fetch fresh data from the gateway right now."""
        await self.coordinator.async_request_refresh()


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
    async_setup_platform_entities(entry, async_add_entities, _create_entities)


def _create_entities(
    coordinator: ElcoDataUpdateCoordinator,
    async_add_entities,
) -> None:
    async_add_entities(
        [
            ElcoRefreshButton(coordinator),
            *(
                ElcoCancelHolidayButton(coordinator, zone_number)
                for zone_number, zone in coordinator.data.zones.items()
                if has_holiday_source(zone)
            ),
        ]
    )
