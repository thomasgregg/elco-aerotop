"""Shared ELCO Aerotop entity base."""

from __future__ import annotations

from collections.abc import Callable

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import ElcoDataUpdateCoordinator


def async_setup_platform_entities(
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
    create_entities: Callable[[ElcoDataUpdateCoordinator, AddEntitiesCallback], None],
) -> None:
    """Create entities now, or lazily once the first gateway data arrives.

    The integration fetches its initial Remocon snapshot in the background so
    Home Assistant startup is not blocked; platform setup therefore runs this
    helper, which defers entity creation until ``coordinator.data`` exists.
    """
    coordinator: ElcoDataUpdateCoordinator = entry.runtime_data
    if coordinator.data is not None:
        create_entities(coordinator, async_add_entities)
        return

    listener_removed = False

    @callback
    def _remove_listener() -> None:
        # coordinator listeners pop() by id — a second call raises KeyError.
        nonlocal listener_removed
        if listener_removed:
            return
        listener_removed = True
        remove_listener()

    @callback
    def _on_first_update() -> None:
        if coordinator.data is None:
            return
        _remove_listener()
        create_entities(coordinator, async_add_entities)

    remove_listener = coordinator.async_add_listener(_on_first_update)
    entry.async_on_unload(_remove_listener)


class ElcoAerotopEntity(CoordinatorEntity[ElcoDataUpdateCoordinator]):
    """Base entity linked to the integration coordinator."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: ElcoDataUpdateCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.api.gateway_id}_{key}"
        metadata = coordinator.data.discovery.plant_metadata
        header = coordinator.data.discovery.plant_header
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.api.gateway_id)},
            manufacturer="ELCO",
            model=str(
                header.get("applianceModel")
                or header.get("ApplianceModel")
                or metadata.get("wheModel")
                or "Aerotop / BSB"
            ),
            name=f"ELCO Aerotop {coordinator.api.gateway_id}",
            serial_number=str(metadata.get("gwSerial") or coordinator.api.gateway_id),
            sw_version=(str(metadata["gwFwVer"]) if metadata.get("gwFwVer") else None),
        )
