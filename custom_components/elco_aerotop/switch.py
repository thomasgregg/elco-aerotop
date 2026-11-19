"""Galevo toggle controls for ELCO Aerotop."""

from __future__ import annotations

from typing import override

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import SYSTEM_TYPE_GALEVO
from .coordinator import ElcoDataUpdateCoordinator
from .entity import ElcoAerotopEntity, async_setup_platform_entities


class ElcoGalevoItemSwitch(ElcoAerotopEntity, SwitchEntity):
    """Toggle one writable Galevo boolean data item (``dataItems/{gw}/set``).

    The write stays blocked by ``GALEVO_WRITES_ENABLED`` inside the API
    client — toggling raises until the flag is enabled deliberately.
    """

    def __init__(
        self,
        coordinator: ElcoDataUpdateCoordinator,
        item_id: str,
        key: str,
        name: str,
        *,
        zone_number: int = 0,
    ) -> None:
        super().__init__(coordinator, key)
        self._item_id = item_id
        self._zone_number = zone_number
        self._attr_name = name

    def _item(self) -> dict | None:
        return self.coordinator.data.discovery.system_item(self._item_id, self._zone_number)

    @property
    @override
    def is_on(self) -> bool | None:
        item = self._item()
        value = item.get("value") if item else None
        if not isinstance(value, int | float):
            return None
        return bool(value)

    async def _async_write(self, enabled: bool) -> None:
        await self.coordinator.async_set_galevo_data_item(
            f"{self._item_id}:{self._zone_number}",
            1.0 if enabled else 0.0,
        )

    @override
    async def async_turn_on(self, **kwargs) -> None:
        await self._async_write(True)

    @override
    async def async_turn_off(self, **kwargs) -> None:
        await self._async_write(False)


class ElcoGalevoHolidaySwitch(ElcoAerotopEntity, SwitchEntity):
    """Activate/cancel the plant holiday for the pending date window.

    The ``Holiday`` data item mirrors the controller's holiday flag.
    Turning on sends ``plantData/{gw}/holiday`` with the dates held by
    the ``holiday_start``/``holiday_end`` date entities (APK contract:
    ``ChangeValueDto<LocalDateTime>`` new=start, old=end); turning off
    sends the reset body. Writes stay gated by ``GALEVO_WRITES_ENABLED``.
    """

    _attr_name = "Holiday"
    _attr_icon = "mdi:beach"

    def __init__(self, coordinator: ElcoDataUpdateCoordinator) -> None:
        super().__init__(coordinator, "holiday")

    @property
    @override
    def is_on(self) -> bool | None:
        item = self.coordinator.data.discovery.system_item("Holiday")
        value = item.get("value") if item else None
        if not isinstance(value, int | float):
            return None
        return bool(value)

    @property
    @override
    def extra_state_attributes(self) -> dict[str, str]:
        return {
            "holiday_start": self.coordinator.pending_holiday_start.isoformat(),
            "holiday_end": self.coordinator.pending_holiday_end.isoformat(),
        }

    @override
    async def async_turn_on(self, **kwargs) -> None:
        await self.coordinator.async_set_galevo_holiday()

    @override
    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.async_reset_galevo_holiday()


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities,
) -> None:
    """Set up Galevo toggle controls backed by writable data items."""
    async_setup_platform_entities(entry, async_add_entities, _create_entities)


def _create_entities(
    coordinator: ElcoDataUpdateCoordinator,
    async_add_entities,
) -> None:
    if coordinator.data.discovery.system_type != SYSTEM_TYPE_GALEVO:
        return
    entities: list[SwitchEntity] = []
    for item_id, key, name in (
        ("IsQuite", "quiet_mode", "Quiet mode"),
        ("IsDhwBoost", "domestic_hot_water_boost", "Domestic hot water boost"),
        ("AutomaticThermoregulation", "automatic_thermoregulation", "Automatic thermoregulation"),
    ):
        item = coordinator.data.discovery.system_item(item_id)
        if item is not None and not item.get("readOnly"):
            entities.append(ElcoGalevoItemSwitch(coordinator, item_id, key, name))
    if coordinator.data.discovery.system_item("Holiday") is not None:
        entities.append(ElcoGalevoHolidaySwitch(coordinator))
    async_add_entities(entities)
