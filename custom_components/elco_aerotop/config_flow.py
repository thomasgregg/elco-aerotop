"""Config flow for ELCO Aerotop."""

from __future__ import annotations

import logging
from contextlib import suppress
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
    CONFIG_ENTRY_MINOR_VERSION,
    DEFAULT_BASE_URL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MIN_SCAN_INTERVAL,
    SYSTEM_TYPE_BSB,
    SYSTEM_TYPE_GALEVO,
)
from .gateway import normalize_gateway_id, plant_gateway_ids

_LOGGER = logging.getLogger(__name__)


class _UnsupportedController(ValueError):
    """The remote account returned a known, unsupported controller family."""


class _GatewayAccessDenied(ValueError):
    """The configured gateway does not belong to the authenticated account."""


def _user_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    values = defaults or {}
    return vol.Schema(
        {
            vol.Required(CONF_USERNAME, default=values.get(CONF_USERNAME, "")): str,
            vol.Required(CONF_PASSWORD): str,
            vol.Optional(CONF_GATEWAY_ID, default=values.get(CONF_GATEWAY_ID, "")): str,
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
    MINOR_VERSION = CONFIG_ENTRY_MINOR_VERSION

    def __init__(self) -> None:
        self._credentials: dict[str, Any] = {}
        self._plants: dict[str, dict[str, Any]] = {}

    async def _validate(
        self,
        user_input: dict[str, Any],
        *,
        listed: bool = False,
        reauth: bool = False,
    ) -> None:
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
            if reauth:
                plants = await client.async_list_plants()
                if not any(client.gateway_id in plant_gateway_ids(plant) for plant in plants):
                    raise _GatewayAccessDenied
                listed = True
            try:
                features = await client.async_get_features()
            except (ElcoConnectionError, ElcoResponseError) as err:
                if (
                    not listed
                    or isinstance(err, ElcoResponseError)
                    and (err.status not in {408, 429, 500, 502, 503, 504})
                ):
                    raise
                _LOGGER.debug("Remocon features probe will retry during polling")
                return
            family = client.system_type
            if family not in (None, SYSTEM_TYPE_BSB, SYSTEM_TYPE_GALEVO):
                raise _UnsupportedController
            if not listed:
                zones = await client.async_get_zone_numbers(features)
                if family == SYSTEM_TYPE_GALEVO:
                    await client.async_get_galevo_data(features, zones)
                else:
                    await client.async_get_data(zones, use_cache=False)
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
            if identifiers := plant_gateway_ids(plant):
                discovered[identifiers[0]] = plant
        return discovered

    def _configured_gateway_ids(self) -> set[str]:
        """Recognize saved gateways even before a legacy entry has migrated."""
        identifiers = set()
        for entry in self._async_current_entries():
            value = entry.data.get(CONF_GATEWAY_ID, entry.unique_id)
            if isinstance(value, str):
                with suppress(ValueError):
                    identifiers.add(normalize_gateway_id(value))
        return identifiers

    async def _async_validate_gateway(
        self, user_input: dict[str, Any], *, listed: bool = False
    ) -> dict[str, str]:
        """Validate a gateway selection; return form errors, empty on success."""
        try:
            await self._validate(user_input, listed=listed)
        except _UnsupportedController:
            return {"base": "unsupported_controller"}
        except ElcoAuthenticationError:
            return {"base": "invalid_auth"}
        except (ElcoConnectionError, ElcoResponseError) as err:
            _LOGGER.debug("Unable to connect to Remocon during setup: %s", err)
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
        if user_input[CONF_GATEWAY_ID] in self._configured_gateway_ids():
            return self.async_abort(reason="already_configured")
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
            user_input[CONF_BASE_URL] = (
                user_input.get(CONF_BASE_URL, DEFAULT_BASE_URL).strip().rstrip("/")
            )
            manual_id = user_input.pop(CONF_GATEWAY_ID, "").strip()
            if manual_id:
                try:
                    manual_id = normalize_gateway_id(manual_id)
                except ValueError:
                    candidate = {**user_input, CONF_GATEWAY_ID: manual_id}
                    return self._show_user_form(candidate, {CONF_GATEWAY_ID: "invalid_gateway_id"})
                candidate = {**user_input, CONF_GATEWAY_ID: manual_id}
                if manual_id in self._configured_gateway_ids():
                    return self.async_abort(reason="already_configured")
                errors = await self._async_validate_gateway(candidate)
                if not errors:
                    return await self._async_create_gateway_entry(candidate, None)
                return self._show_user_form(candidate, errors)
            try:
                plants = await self._async_discover_plants(user_input)
            except ElcoAuthenticationError:
                errors["base"] = "invalid_auth"
            except (ElcoConnectionError, ElcoResponseError) as err:
                _LOGGER.debug("Unable to connect to Remocon during setup: %s", err)
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001 - config flows must turn unknown API errors into UI errors
                _LOGGER.exception("Unexpected error while validating the Remocon connection")
                errors["base"] = "unknown"
            else:
                configured = self._configured_gateway_ids()
                available = {
                    key: plant
                    for key, plant in plants.items()
                    if configured.isdisjoint(plant_gateway_ids(plant))
                }
                if not plants:
                    errors["base"] = "no_gateways"
                elif not available:
                    return self.async_abort(reason="already_configured")
                elif len(available) == 1:
                    gateway_id, plant = next(iter(available.items()))
                    user_input[CONF_GATEWAY_ID] = gateway_id
                    errors = await self._async_validate_gateway(user_input, listed=True)
                    if not errors:
                        return await self._async_create_gateway_entry(user_input, plant)
                else:
                    self._credentials = user_input
                    self._plants = available
                    return await self.async_step_gateway()
        return self._show_user_form(user_input, errors)

    def _show_user_form(self, user_input, errors: dict[str, str]) -> ConfigFlowResult:
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
            errors = await self._async_validate_gateway(candidate, listed=True)
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
                await self._validate(candidate, reauth=True)
            except _GatewayAccessDenied:
                errors["base"] = "gateway_access_denied"
            except _UnsupportedController:
                errors["base"] = "unsupported_controller"
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
