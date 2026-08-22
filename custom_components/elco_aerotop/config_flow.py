"""Config flow for ELCO Aerotop."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry, ConfigFlowResult
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import ElcoApiClient, ElcoAuthenticationError, ElcoConnectionError
from .const import (
    CONF_BASE_URL,
    CONF_GATEWAY_ID,
    CONF_SCAN_INTERVAL,
    DEFAULT_BASE_URL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MIN_SCAN_INTERVAL,
)


def _user_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    values = defaults or {}
    return vol.Schema(
        {
            vol.Required(CONF_USERNAME, default=values.get(CONF_USERNAME, "")): str,
            vol.Required(CONF_PASSWORD, default=values.get(CONF_PASSWORD, "")): str,
            vol.Required(CONF_GATEWAY_ID, default=values.get(CONF_GATEWAY_ID, "")): str,
            vol.Optional(
                CONF_BASE_URL,
                default=values.get(CONF_BASE_URL, DEFAULT_BASE_URL),
            ): str,
        }
    )


class ElcoAerotopConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle configuration for ELCO Aerotop."""

    VERSION = 1

    async def _validate(self, user_input: dict[str, Any]) -> None:
        session = async_create_clientsession(self.hass, auto_cleanup=False)
        client = ElcoApiClient(
            session,
            user_input[CONF_USERNAME],
            user_input[CONF_PASSWORD],
            user_input[CONF_GATEWAY_ID],
            user_input[CONF_BASE_URL],
        )
        try:
            await client.async_login()
            await client.async_get_data(use_cache=False)
        finally:
            session.detach()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle the initial user step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            user_input[CONF_USERNAME] = user_input[CONF_USERNAME].strip()
            user_input[CONF_GATEWAY_ID] = user_input[CONF_GATEWAY_ID].strip().upper()
            user_input[CONF_BASE_URL] = user_input[CONF_BASE_URL].strip().rstrip("/")
            try:
                await self._validate(user_input)
            except ElcoAuthenticationError:
                errors["base"] = "invalid_auth"
            except ElcoConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001 - config flows must turn unknown API errors into UI errors
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(user_input[CONF_GATEWAY_ID])
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"ELCO Aerotop {user_input[CONF_GATEWAY_ID]}",
                    data=user_input,
                )
        return self.async_show_form(
            step_id="user",
            data_schema=_user_schema(user_input),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Start reauthentication."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm new credentials."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            candidate = {**entry.data, **user_input}
            try:
                await self._validate(candidate)
            except ElcoAuthenticationError:
                errors["base"] = "invalid_auth"
            except ElcoConnectionError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(entry, data_updates=user_input)
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_USERNAME, default=entry.data[CONF_USERNAME]): str,
                    vol.Required(CONF_PASSWORD): str,
                }
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> ElcoAerotopOptionsFlow:
        return ElcoAerotopOptionsFlow()


class ElcoAerotopOptionsFlow(config_entries.OptionsFlow):
    """Configure optional polling behavior."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        current = self.config_entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_SCAN_INTERVAL, default=current): vol.All(
                        vol.Coerce(int), vol.Range(min=MIN_SCAN_INTERVAL, max=3600)
                    )
                }
            ),
        )
