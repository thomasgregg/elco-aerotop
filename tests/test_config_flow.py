"""Exercise validation session ownership without a Home Assistant runtime."""

import ast
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiohttp
import pytest

from custom_components.elco_aerotop.gateway import plant_gateway_ids


class _StubGatewayAccessDenied(Exception):
    """Stand-in for the config flow's account ownership error."""


class _StubConnectionError(Exception):
    """Stand-in for ElcoConnectionError inside the exec'ed function."""


class _StubResponseError(Exception):
    """Stand-in for ElcoResponseError inside the exec'ed function."""

    status = 500


def _extract_validate():
    source = Path(__file__).parents[1] / "custom_components/elco_aerotop/config_flow.py"
    tree = ast.parse(source.read_text())
    flow = next(
        n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "ElcoAerotopConfigFlow"
    )
    return next(
        n for n in flow.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "_validate"
    )


def _exec_validate(session):
    validate = _extract_validate()
    source = Path(__file__).parents[1] / "custom_components/elco_aerotop/config_flow.py"
    client = SimpleNamespace(
        async_login=AsyncMock(),
        async_get_features=AsyncMock(),
        async_list_plants=AsyncMock(),
        gateway_id="TESTGATEWAY",
        system_type=5,
    )

    def create_session(hass, *, auto_cleanup):
        assert auto_cleanup is False
        return session

    namespace = {
        "Any": object,
        "async_create_clientsession": create_session,
        "ElcoApiClient": lambda *args: client,
        "ElcoConnectionError": _StubConnectionError,
        "ElcoResponseError": _StubResponseError,
        "_GatewayAccessDenied": _StubGatewayAccessDenied,
        "plant_gateway_ids": plant_gateway_ids,
        "_LOGGER": logging.getLogger("test"),
        "SYSTEM_TYPE_BSB": 5,
        "SYSTEM_TYPE_GALEVO": 3,
        **{
            key: key
            for key in ("CONF_USERNAME", "CONF_PASSWORD", "CONF_GATEWAY_ID", "CONF_BASE_URL")
        },
    }
    exec(compile(ast.Module(body=[validate], type_ignores=[]), str(source), "exec"), namespace)
    user_input = {key: "value" for key in namespace if key.startswith("CONF_")}
    return namespace["_validate"], client, user_input


@pytest.mark.parametrize("failure_stage", [None, "login", "data", "cancel"])
async def test_validation_detaches_session_without_closing_ha_connector(failure_stage):
    """Validation must release its session without closing HA's connector."""
    import asyncio

    session = aiohttp.ClientSession()
    connector = session.connector
    error = asyncio.CancelledError() if failure_stage == "cancel" else RuntimeError("API failed")
    validate, client, user_input = _exec_validate(session)
    if failure_stage == "login":
        client.async_login.side_effect = error
    elif failure_stage in ("data", "cancel"):
        client.async_get_features.side_effect = error

    try:
        if failure_stage:
            with pytest.raises(type(error)) as raised:
                await validate(SimpleNamespace(hass=object()), user_input, listed=True)
            assert raised.value is error
        else:
            await validate(SimpleNamespace(hass=object()), user_input, listed=True)
            client.async_get_features.assert_awaited_once_with()
        assert session.closed
        assert session.connector is None
        assert not connector.closed
    finally:
        await session.close()
        await connector.close()


@pytest.mark.parametrize("probe_error", [_StubConnectionError, _StubResponseError])
async def test_validation_tolerates_transient_probe_errors(probe_error):
    """A transient plant-scoped failure (e.g. HTTP 500) must not block setup."""
    session = aiohttp.ClientSession()
    connector = session.connector
    validate, client, user_input = _exec_validate(session)
    client.async_get_features.side_effect = probe_error("HTTP 500")

    try:
        await validate(SimpleNamespace(hass=object()), user_input, listed=True)
        client.async_login.assert_awaited_once()
        assert session.closed
        assert session.connector is None
        assert not connector.closed
    finally:
        await session.close()
        await connector.close()


@pytest.mark.parametrize("listed_serial", [" testgateway ", "UNRELATED"])
async def test_reauth_checks_the_account_gateway_id_and_serial(listed_serial):
    session = aiohttp.ClientSession()
    connector = session.connector
    validate, client, user_input = _exec_validate(session)
    client.async_list_plants.return_value = [
        {"gwId": "PLANTKEY", "gwSerial": listed_serial},
    ]

    try:
        if listed_serial == "UNRELATED":
            with pytest.raises(_StubGatewayAccessDenied):
                await validate(SimpleNamespace(hass=object()), user_input, reauth=True)
            client.async_get_features.assert_not_awaited()
        else:
            await validate(SimpleNamespace(hass=object()), user_input, reauth=True)
            client.async_get_features.assert_awaited_once()
        assert session.closed
    finally:
        await session.close()
        await connector.close()
