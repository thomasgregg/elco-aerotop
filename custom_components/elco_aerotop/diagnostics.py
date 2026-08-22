"""Diagnostics for ELCO Aerotop."""

from __future__ import annotations

from dataclasses import asdict

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from .const import CONF_GATEWAY_ID


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> dict:
    """Return redacted diagnostics."""
    coordinator = entry.runtime_data
    return {
        "config": async_redact_data(
            entry.data,
            {CONF_USERNAME, CONF_PASSWORD, CONF_GATEWAY_ID},
        ),
        "data": asdict(coordinator.data),
    }
