"""Diagnostics for ELCO Aerotop."""

from __future__ import annotations

from dataclasses import asdict

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from .const import CONF_GATEWAY_ID
from .diagnostic_utils import sanitize_diagnostics, schema_inventory


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> dict:
    """Return redacted diagnostics."""
    coordinator = entry.runtime_data
    raw_data = asdict(coordinator.data)
    secrets = {
        str(entry.data.get(CONF_USERNAME, "")),
        str(entry.data.get(CONF_PASSWORD, "")),
        str(entry.data.get(CONF_GATEWAY_ID, "")),
    }
    return {
        "config": async_redact_data(
            entry.data,
            {CONF_USERNAME, CONF_PASSWORD, CONF_GATEWAY_ID},
        ),
        "response_schema": schema_inventory(raw_data, secrets),
        "data": sanitize_diagnostics(raw_data, secrets),
    }
