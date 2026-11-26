"""Shared ELCO Aerotop entity base."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, SYSTEM_TYPE_GALEVO
from .coordinator import ElcoDataUpdateCoordinator

_DEFAULT_OBJECT_ID_OVERRIDES = {
    "dhw_current_temperature": "domestic_hot_water_temperature",
    "dhw_operation": "domestic_hot_water_operation",
    "dhw_target_temperature": "domestic_hot_water_target_temperature",
    "galevo_estimated_jaz": "estimated_spf_previous_calendar_year",
}


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
    registered: set[str] = set()

    @callback
    def _add_new(new_entities: Iterable[Entity], update_before_add: bool = False) -> None:
        pending = [
            entity
            for entity in new_entities
            if entity.unique_id is not None and entity.unique_id not in registered
        ]
        if pending:
            registered.update(key for entity in pending if (key := entity.unique_id) is not None)
            async_add_entities(pending, update_before_add)

    @callback
    def _discover_entities() -> None:
        if coordinator.unloading or coordinator.data is None:
            return
        create_entities(coordinator, _add_new)

    entry.async_on_unload(coordinator.async_add_listener(_discover_entities))
    _discover_entities()


class ElcoAerotopEntity(CoordinatorEntity[ElcoDataUpdateCoordinator]):
    """Base entity linked to the integration coordinator."""

    _attr_has_entity_name = True

    @property
    def available(self) -> bool:
        """Require a successful coordinator update for this entity's scope."""
        return super().available and self._elco_zone_number not in (
            self.coordinator.data.unavailable_zones
        )

    @property
    def suggested_object_id(self) -> str:
        """Suggest a language-independent suffix for Home Assistant's entity ID."""
        return self._elco_object_id

    def has_registry_entry(self, entity_domain: str) -> bool:
        """Return whether this entity already has a persistent Home Assistant ID."""
        registry = er.async_get(self.coordinator.hass)
        return registry.async_get_entity_id(entity_domain, DOMAIN, self._attr_unique_id) is not None

    def __init__(
        self,
        coordinator: ElcoDataUpdateCoordinator,
        key: str,
        *,
        zone_number: int | None = None,
        translation_key: str | None = None,
        translation_placeholders: dict[str, str] | None = None,
    ) -> None:
        super().__init__(coordinator)
        self._elco_zone_number = zone_number
        if zone_number is not None:
            translation_key = translation_key or f"zone_{key}"
            translation_placeholders = {"zone": str(zone_number)}
            key = f"zone_{zone_number}_{key}"
        self._attr_translation_key = translation_key or key
        if translation_placeholders is not None:
            self._attr_translation_placeholders = translation_placeholders
        self._attr_unique_id = f"{coordinator.api.gateway_id}_{key}"
        self._elco_object_id = _DEFAULT_OBJECT_ID_OVERRIDES.get(key, key)
        metadata = coordinator.data.discovery.plant_metadata
        header = coordinator.data.discovery.plant_header
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.api.gateway_id)},
            manufacturer="ELCO",
            model=str(
                header.get("applianceModel")
                or header.get("ApplianceModel")
                or metadata.get("wheModel")
                or (
                    "Aerotop / Galevo"
                    if coordinator.data.discovery.system_type == SYSTEM_TYPE_GALEVO
                    else "Aerotop / BSB"
                )
            ),
            name=f"ELCO Aerotop {coordinator.api.gateway_id}",
            serial_number=str(metadata.get("gwSerial") or coordinator.api.gateway_id),
            sw_version=(str(metadata["gwFwVer"]) if metadata.get("gwFwVer") else None),
        )
