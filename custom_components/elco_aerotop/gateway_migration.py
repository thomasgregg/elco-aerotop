"""Repair legacy gateway IDs without replacing Home Assistant registry entries."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import CONF_GATEWAY_ID, CONFIG_ENTRY_MINOR_VERSION, DOMAIN
from .gateway import normalize_gateway_id

_LOGGER = logging.getLogger(__name__)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Update the stored ID and the registry keys that were derived from it."""
    if entry.version != 1:
        return False
    if entry.minor_version >= CONFIG_ENTRY_MINOR_VERSION:
        return True

    previous = entry.data.get(CONF_GATEWAY_ID, "")
    if not isinstance(previous, str):
        previous = ""
    try:
        gateway_id = normalize_gateway_id(previous)
    except ValueError:
        _LOGGER.error("Cannot migrate ELCO Aerotop because the saved gateway ID is invalid")
        return False

    previous_id = previous.upper()
    if (previous_id != gateway_id or entry.unique_id != gateway_id) and any(
        other.entry_id != entry.entry_id
        and (other.unique_id == gateway_id or other.data.get(CONF_GATEWAY_ID) == gateway_id)
        for other in hass.config_entries.async_entries(DOMAIN)
    ):
        _LOGGER.error("Cannot migrate ELCO Aerotop because this gateway is already configured")
        return False
    if previous_id != gateway_id and not _migrate_registry(hass, entry, previous_id, gateway_id):
        return False

    title = entry.title
    if title == f"ELCO Aerotop {previous}":
        title = f"ELCO Aerotop {gateway_id}"
    hass.config_entries.async_update_entry(
        entry,
        data={**entry.data, CONF_GATEWAY_ID: gateway_id},
        title=title,
        unique_id=gateway_id,
        minor_version=CONFIG_ENTRY_MINOR_VERSION,
    )
    return True


def _migrate_registry(
    hass: HomeAssistant, entry: ConfigEntry, previous_id: str, gateway_id: str
) -> bool:
    """Check conflicts before updating entity unique IDs and the device identifier."""
    entity_registry = er.async_get(hass)
    prefix = f"{previous_id}_"
    updates: dict[str, str] = {}
    for entity in er.async_entries_for_config_entry(entity_registry, entry.entry_id):
        if entity.platform != DOMAIN or not entity.unique_id.startswith(prefix):
            continue
        new_id = f"{gateway_id}_{entity.unique_id.removeprefix(prefix)}"
        if entity_registry.async_get_entity_id(entity.domain, DOMAIN, new_id) is not None:
            _LOGGER.error("Cannot migrate ELCO Aerotop because an entity key is already in use")
            return False
        updates[entity.entity_id] = new_id

    device_registry = dr.async_get(hass)
    devices = dr.async_entries_for_config_entry(device_registry, entry.entry_id)
    old_identifier = (DOMAIN, previous_id)
    new_identifier = (DOMAIN, gateway_id)
    device = next((device for device in devices if old_identifier in device.identifiers), None)
    if device is not None and (
        device.config_entries != {entry.entry_id}
        or any(other.id != device.id and new_identifier in other.identifiers for other in devices)
    ):
        _LOGGER.error("Cannot migrate ELCO Aerotop because the device is shared or duplicated")
        return False

    if device is not None:
        device_registry.async_update_device(
            device.id,
            new_identifiers=(device.identifiers - {old_identifier}) | {new_identifier},
        )
    for entity_id, new_id in updates.items():
        entity_registry.async_update_entity(entity_id, new_unique_id=new_id)
    return True
