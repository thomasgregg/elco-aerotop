"""Shared ELCO Aerotop entity base."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import ElcoDataUpdateCoordinator


class ElcoAerotopEntity(CoordinatorEntity[ElcoDataUpdateCoordinator]):
    """Base entity linked to the integration coordinator."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: ElcoDataUpdateCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.api.gateway_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.api.gateway_id)},
            manufacturer="ELCO",
            model="Aerotop / BSB",
            name=f"ELCO Aerotop {coordinator.api.gateway_id}",
        )
