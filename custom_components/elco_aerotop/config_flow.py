"""Config flow for ELCO Aerotop."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry, ConfigFlowResult
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .api import (
    ElcoApiClient,
    ElcoAuthenticationError,
    ElcoConnectionError,
    ElcoResponseError,
)
from .const import (
    CONF_BASE_URL,
    CONF_GATEWAY_ID,
    CONF_SCAN_INTERVAL,
    DEFAULT_BASE_URL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MIN_SCAN_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)


def _user_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    values = defaults or {}
    return vol.Schema(
        {
            vol.Required(CONF_USERNAME, default=values.get(CONF_USERNAME, "")): str,
            vol.Required(CONF_PASSWORD, default=values.get(CONF_PASSWORD, "")): str,
            vol.Optional(
                CONF_BASE_URL,
                default=values.get(CONF_BASE_URL, DEFAULT_BASE_URL),
            ): str,
        }
    )


def _plant_label(gateway_id: str, plant: dict[str, Any]) -> str:
    name = str(plant.get("plantName") or "").strip()
    serial = str(plant.get("gwSerial") or "").strip()
    label = name or serial or gateway_id
    if serial and serial != label and serial != gateway_id:
        label = f"{label} ({serial})"
    return label


class ElcoAerotopConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle configuration for ELCO Aerotop."""

    VERSION = 1

    def __init__(self) -> None:
        self._credentials: dict[str, Any] = {}
        self._plants: dict[str, dict[str, Any]] = {}

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
            try:
                # Best-effort probe only: GetData/Features can return transient
                # controller-side HTTP 5xx that the coordinator retries at
                # runtime. plants/lite already proved the gateway is registered.
                await client.async_get_features()
            except (ElcoConnectionError, ElcoResponseError) as err:
                _LOGGER.info(
                    "Remocon gateway %s did not answer the features probe during setup: %s",
                    user_input[CONF_GATEWAY_ID],
                    err,
                )
        finally:
            session.detach()

    async def _async_discover_plants(self, user_input: dict[str, Any]) -> dict[str, dict[str, Any]]:
        """Log in and return the account's gateways keyed by gateway ID."""
        session = async_create_clientsession(self.hass, auto_cleanup=False)
        client = ElcoApiClient(
            session,
            user_input[CONF_USERNAME],
            user_input[CONF_PASSWORD],
            "",
            user_input[CONF_BASE_URL],
        )
        try:
            await client.async_login()
            plants = await client.async_list_plants()
        finally:
            session.detach()
        discovered: dict[str, dict[str, Any]] = {}
        for plant in plants:
            gateway_id = str(plant.get("gwId") or plant.get("gwSerial") or "").strip()
            if gateway_id:
                discovered[gateway_id.upper()] = plant
        return discovered

    async def _async_validate_gateway(self, user_input: dict[str, Any]) -> dict[str, str]:
        """Validate a gateway selection; return form errors, empty on success."""
        try:
            await self._validate(user_input)
        except ElcoAuthenticationError:
            return {"base": "invalid_auth"}
        except (ElcoConnectionError, ElcoResponseError) as err:
            _LOGGER.debug("Unable to connect to Remocon during setup: %s", err, exc_info=True)
            return {"base": "cannot_connect"}
        except Exception:  # noqa: BLE001 - config flows must turn unknown API errors into UI errors
            _LOGGER.exception("Unexpected error while validating the Remocon connection")
            return {"base": "unknown"}
        return {}

    async def _async_create_gateway_entry(
        self,
        user_input: dict[str, Any],
        plant: dict[str, Any] | None,
    ) -> ConfigFlowResult:
        name = str((plant or {}).get("plantName") or "").strip()
        await self.async_set_unique_id(user_input[CONF_GATEWAY_ID])
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=f"ELCO Aerotop {name or user_input[CONF_GATEWAY_ID]}",
            data=user_input,
        )

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle the initial user step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            user_input[CONF_USERNAME] = user_input[CONF_USERNAME].strip()
            user_input[CONF_BASE_URL] = user_input[CONF_BASE_URL].strip().rstrip("/")
            try:
                plants = await self._async_discover_plants(user_input)
            except ElcoAuthenticationError:
                errors["base"] = "invalid_auth"
            except (ElcoConnectionError, ElcoResponseError) as err:
                _LOGGER.debug("Unable to connect to Remocon during setup: %s", err, exc_info=True)
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001 - config flows must turn unknown API errors into UI errors
                _LOGGER.exception("Unexpected error while validating the Remocon connection")
                errors["base"] = "unknown"
            else:
                if not plants:
                    errors["base"] = "no_gateways"
                elif len(plants) == 1:
                    gateway_id, plant = next(iter(plants.items()))
                    user_input[CONF_GATEWAY_ID] = gateway_id
                    errors = await self._async_validate_gateway(user_input)
                    if not errors:
                        return await self._async_create_gateway_entry(user_input, plant)
                else:
                    self._credentials = user_input
                    self._plants = plants
                    return await self.async_step_gateway()
        return self.async_show_form(
            step_id="user",
            data_schema=_user_schema(user_input),  # ty: ignore[invalid-argument-type] HA still accepts voluptuous schemas at runtime
            errors=errors,
        )

    async def async_step_gateway(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user pick one of the discovered gateways."""
        errors: dict[str, str] = {}
        if not self._plants:
            return self.async_abort(reason="no_gateways")
        if user_input is not None:
            gateway_id = user_input[CONF_GATEWAY_ID]
            candidate = {**self._credentials, CONF_GATEWAY_ID: gateway_id}
            errors = await self._async_validate_gateway(candidate)
            if not errors:
                return await self._async_create_gateway_entry(candidate, self._plants[gateway_id])
        return self.async_show_form(
            step_id="gateway",
            data_schema=vol.Schema(  # ty: ignore[invalid-argument-type] HA still accepts voluptuous schemas at runtime
                {
                    vol.Required(CONF_GATEWAY_ID): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                SelectOptionDict(
                                    value=gateway_id,
                                    label=_plant_label(gateway_id, plant),
                                )
                                for gateway_id, plant in self._plants.items()
                            ],
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
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
            except (ElcoConnectionError, ElcoResponseError) as err:
                _LOGGER.debug("Unable to connect to Remocon during reauthentication: %s", err)
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(entry, data_updates=user_input)
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(  # ty: ignore[invalid-argument-type] HA still accepts voluptuous schemas at runtime
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
            data_schema=vol.Schema(  # ty: ignore[invalid-argument-type] HA still accepts voluptuous schemas at runtime
                {
                    vol.Optional(CONF_SCAN_INTERVAL, default=current): vol.All(
                        vol.Coerce(int), vol.Range(min=MIN_SCAN_INTERVAL, max=3600)
                    )
                }
            ),
        )
