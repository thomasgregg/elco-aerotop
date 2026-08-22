"""ELCO Aerotop integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import ElcoApiClient
from .const import (
    CONF_BASE_URL,
    CONF_GATEWAY_ID,
    CONF_SCAN_INTERVAL,
    DEFAULT_BASE_URL,
    DEFAULT_SCAN_INTERVAL,
    PLATFORMS,
)
from .coordinator import ElcoDataUpdateCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up ELCO Aerotop from a config entry."""
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
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when polling options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload an ELCO Aerotop config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
