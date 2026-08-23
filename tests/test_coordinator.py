"""Tests for serialized Remocon coordinator traffic."""

from __future__ import annotations

import asyncio
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest


def _install_homeassistant_stubs() -> None:
    """Provide the small Home Assistant surface needed by coordinator unit tests."""
    homeassistant = ModuleType("homeassistant")
    config_entries = ModuleType("homeassistant.config_entries")
    core = ModuleType("homeassistant.core")
    exceptions = ModuleType("homeassistant.exceptions")
    helpers = ModuleType("homeassistant.helpers")
    update_coordinator = ModuleType("homeassistant.helpers.update_coordinator")

    class ConfigEntry:
        pass

    class HomeAssistant:
        pass

    class ConfigEntryAuthFailed(Exception):
        pass

    class HomeAssistantError(Exception):
        pass

    class UpdateFailed(Exception):
        pass

    class DataUpdateCoordinator:
        def __class_getitem__(cls, _item):
            return cls

        def __init__(self, hass, _logger, **_kwargs) -> None:
            self.hass = hass
            self.data = None

        async def async_request_refresh(self) -> None:
            self.data = await self._async_update_data()

        def async_set_updated_data(self, data) -> None:
            self.data = data

    config_entries.ConfigEntry = ConfigEntry
    core.HomeAssistant = HomeAssistant
    core.callback = lambda func: func
    exceptions.ConfigEntryAuthFailed = ConfigEntryAuthFailed
    exceptions.HomeAssistantError = HomeAssistantError
    update_coordinator.DataUpdateCoordinator = DataUpdateCoordinator
    update_coordinator.UpdateFailed = UpdateFailed

    sys.modules.setdefault("homeassistant", homeassistant)
    sys.modules.setdefault("homeassistant.config_entries", config_entries)
    sys.modules.setdefault("homeassistant.core", core)
    sys.modules.setdefault("homeassistant.exceptions", exceptions)
    sys.modules.setdefault("homeassistant.helpers", helpers)
    sys.modules.setdefault("homeassistant.helpers.update_coordinator", update_coordinator)


_install_homeassistant_stubs()

from custom_components.elco_aerotop.api import ElcoResponseError  # noqa: E402
from custom_components.elco_aerotop.coordinator import (  # noqa: E402
    ElcoDataUpdateCoordinator,
)
from custom_components.elco_aerotop.models import (  # noqa: E402
    ElcoData,
    PlantState,
    ZoneState,
)


class FakeHass:
    def async_create_background_task(self, coro, _name: str):
        return asyncio.create_task(coro)


def _data() -> ElcoData:
    plant = PlantState.parse(
        {
            "dhwComfortTemp": {"value": 49, "min": 44, "max": 55, "step": 1},
            "dhwReducedTemp": {"value": 44, "min": 8, "max": 55, "step": 1},
            "dhwMode": {"value": 1, "allowedOptions": [0, 1, 2]},
        }
    )
    zone = ZoneState.parse(
        1,
        {
            "chComfortTemp": {"value": 23, "min": 10, "max": 30, "step": 0.5},
            "chReducedTemp": {"value": 22, "min": 10, "max": 30, "step": 0.5},
            "mode": {"value": 1, "allowedOptions": [0, 1, 2, 3]},
        },
    )
    return ElcoData("GATEWAY", plant, {1: zone})


class FakeApi:
    gateway_id = "GATEWAY"
    last_features_response = None

    def __init__(self, responses: list[ElcoData | Exception] | None = None) -> None:
        self.responses = list(responses or [_data()])
        self.get_data_calls = 0
        self.write_calls: list[tuple[str, tuple, dict]] = []

    async def async_get_data(self, _zones, *, use_cache=True) -> ElcoData:
        self.get_data_calls += 1
        response = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(response, Exception):
            raise response
        return response

    async def async_get_system_items(self, _features, _zones):
        return {}

    async def async_set_dhw(self, *args, **kwargs) -> None:
        self.write_calls.append(("dhw", args, kwargs))

    async def async_set_zone_temperatures(self, *args, **kwargs) -> None:
        self.write_calls.append(("zone_temperature", args, kwargs))

    async def async_set_zone_mode(self, *args, **kwargs) -> None:
        self.write_calls.append(("zone_mode", args, kwargs))


def _coordinator(api: FakeApi | None = None) -> ElcoDataUpdateCoordinator:
    coordinator = ElcoDataUpdateCoordinator(
        FakeHass(),
        SimpleNamespace(),
        api or FakeApi(),
        3600,
    )
    coordinator._features = {}
    coordinator._zone_numbers = [1]
    coordinator.data = _data()
    return coordinator


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("write_name", "write"),
    [
        ("dhw", lambda coordinator: coordinator.async_set_dhw(comfort=48)),
        (
            "zone_temperature",
            lambda coordinator: coordinator.async_set_zone_temperature(1, "comfort", 24),
        ),
        ("zone_mode", lambda coordinator: coordinator.async_set_zone_mode(1, 2)),
    ],
)
async def test_every_write_cancels_discovery_before_gateway_access(write_name, write) -> None:
    coordinator = _coordinator()
    discovery_started = asyncio.Event()

    async def busy_discovery() -> None:
        async with coordinator._gateway_lock:
            discovery_started.set()
            await asyncio.Event().wait()

    discovery_task = asyncio.create_task(busy_discovery())
    coordinator._background_discovery_task = discovery_task
    await discovery_started.wait()
    coordinator.async_request_refresh = AsyncMock()

    await asyncio.wait_for(write(coordinator), timeout=1)

    assert discovery_task.cancelled()
    assert coordinator.api.write_calls[0][0] == write_name
    coordinator.async_request_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_fresh_data_retries_one_transient_communication_error(monkeypatch) -> None:
    api = FakeApi([ElcoResponseError("Communication error"), _data()])
    coordinator = _coordinator(api)
    sleep = AsyncMock()
    monkeypatch.setattr(asyncio, "sleep", sleep)

    result = await coordinator._async_fresh_data()

    assert result == _data()
    assert api.get_data_calls == 2
    sleep.assert_awaited_once_with(1)


@pytest.mark.asyncio
async def test_fresh_data_does_not_retry_non_transient_response_error(monkeypatch) -> None:
    api = FakeApi([ElcoResponseError("Unsupported request")])
    coordinator = _coordinator(api)
    sleep = AsyncMock()
    monkeypatch.setattr(asyncio, "sleep", sleep)

    with pytest.raises(ElcoResponseError, match="Unsupported request"):
        await coordinator._async_fresh_data()

    assert api.get_data_calls == 1
    sleep.assert_not_awaited()


@pytest.mark.asyncio
async def test_write_refresh_does_not_launch_slow_discovery() -> None:
    coordinator = _coordinator()
    coordinator._initial_discovery_complete = True
    coordinator._polls_until_slow_discovery = 0
    coordinator.start_deferred_discovery = AsyncMock()

    await coordinator._async_refresh_after_write()

    assert coordinator._skip_slow_discovery_once is False
    assert coordinator._polls_until_slow_discovery == 0
    coordinator.start_deferred_discovery.assert_not_called()
