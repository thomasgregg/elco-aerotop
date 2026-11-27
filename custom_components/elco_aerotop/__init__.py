"""ELCO Aerotop integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import ElcoApiClient
from .const import (
    CONF_BASE_URL,
    CONF_GATEWAY_ID,
    CONF_SCAN_INTERVAL,
    DEFAULT_BASE_URL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    PLATFORMS,
)
from .coordinator import ElcoDataUpdateCoordinator
from .gateway_migration import async_migrate_entry as async_migrate_entry


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up ELCO Aerotop from a config entry.

    The first gateway fetch runs in the background so a slow Remocon response
    does not delay Home Assistant startup; each platform registers its
    entities as soon as the initial data lands.
    """
    api = ElcoApiClient(
        # Remocon authentication is cookie-based. A private session prevents
        # credentials from colliding when multiple accounts are configured.
        async_create_clientsession(hass),
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
        entry.data[CONF_GATEWAY_ID],
        entry.data.get(CONF_BASE_URL, DEFAULT_BASE_URL),
    )
    coordinator = ElcoDataUpdateCoordinator(
        hass,
        entry,
        api,
        entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
    )
    entry.runtime_data = coordinator
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    # Platform discovery listeners keep polling alive before entities exist.
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _clear_legacy_configuration_url(hass, entry.entry_id, api.gateway_id)
    coordinator.initial_refresh_task = entry.async_create_background_task(
        hass,
        coordinator.async_refresh(),
        f"{DOMAIN} first refresh",
        eager_start=False,
    )
    return True


def _clear_legacy_configuration_url(hass: HomeAssistant, entry_id: str, gateway_id: str) -> None:
    """Remove the configuration URL saved by releases before 0.2.3."""
    registry = dr.async_get(hass)
    for device in dr.async_entries_for_config_entry(registry, entry_id):
        if (DOMAIN, gateway_id) in device.identifiers and device.configuration_url is not None:
            registry.async_update_device(device.id, configuration_url=None)


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when polling options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload an ELCO Aerotop config entry."""
    coordinator = entry.runtime_data
    coordinator.unloading = True
    await coordinator.async_stop_background_discovery()
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unloaded:
        coordinator.unloading = False
        await coordinator.async_request_refresh()
    return unloaded
