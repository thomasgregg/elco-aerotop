"""Exercise validation session ownership without a Home Assistant runtime."""

import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiohttp
import pytest


@pytest.mark.parametrize("failure_stage", [None, "login", "data", "cancel"])
async def test_validation_detaches_session_without_closing_ha_connector(failure_stage):
    """Validation must release its session without closing HA's connector."""
    import asyncio

    source = Path(__file__).parents[1] / "custom_components/elco_aerotop/config_flow.py"
    tree = ast.parse(source.read_text())
    flow = next(
        n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "ElcoAerotopConfigFlow"
    )
    validate = next(
        n for n in flow.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "_validate"
    )
    session = aiohttp.ClientSession()
    connector = session.connector
    error = asyncio.CancelledError() if failure_stage == "cancel" else RuntimeError("API failed")
    client = SimpleNamespace(async_login=AsyncMock(), async_get_data=AsyncMock())
    if failure_stage == "login":
        client.async_login.side_effect = error
    elif failure_stage in ("data", "cancel"):
        client.async_get_data.side_effect = error

    def create_session(hass, *, auto_cleanup):
        assert auto_cleanup is False
        return session

    namespace = {
        "Any": object,
        "async_create_clientsession": create_session,
        "ElcoApiClient": lambda *args: client,
        **{
            key: key
            for key in ("CONF_USERNAME", "CONF_PASSWORD", "CONF_GATEWAY_ID", "CONF_BASE_URL")
        },
    }
    exec(compile(ast.Module(body=[validate], type_ignores=[]), str(source), "exec"), namespace)
    user_input = {key: "value" for key in namespace if key.startswith("CONF_")}
    try:
        if failure_stage:
            with pytest.raises(type(error)) as raised:
                await namespace["_validate"](SimpleNamespace(hass=object()), user_input)
            assert raised.value is error
        else:
            await namespace["_validate"](SimpleNamespace(hass=object()), user_input)
            client.async_get_data.assert_awaited_once_with(use_cache=False)
        assert session.closed
        assert session.connector is None
        assert not connector.closed
    finally:
        await session.close()
        await connector.close()
