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
        def __init__(self, *args, retry_after=None) -> None:
            super().__init__(*args)
            self.retry_after = retry_after

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

from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError  # noqa: E402
from homeassistant.helpers.update_coordinator import UpdateFailed  # noqa: E402

from custom_components.elco_aerotop.api import (  # noqa: E402
    ElcoAuthenticationError,
    ElcoConnectionError,
    ElcoResponseError,
)
from custom_components.elco_aerotop.coordinator import (  # noqa: E402
    ElcoDataUpdateCoordinator,
    _OptionalDiscoveryAborted,
)
from custom_components.elco_aerotop.models import (  # noqa: E402
    ElcoData,
    PlantState,
    ReadOnlyDiscovery,
    ZoneState,
)


class FakeHass:
    def async_create_background_task(self, coro, _name: str):
        return asyncio.create_task(coro)


def _data(
    *,
    zone_comfort: float = 23,
    zone_reduced: float = 22,
    zone_mode: int = 1,
    dhw_comfort: float = 49,
    dhw_reduced: float = 44,
    dhw_mode: int = 1,
    cooling_active: bool = False,
    cooling_comfort: float = 24,
    cooling_reduced: float = 28,
) -> ElcoData:
    plant = PlantState.parse(
        {
            "dhwComfortTemp": {"value": dhw_comfort, "min": 44, "max": 55, "step": 1},
            "dhwReducedTemp": {"value": dhw_reduced, "min": 8, "max": 55, "step": 1},
            "dhwMode": {"value": dhw_mode, "allowedOptions": [0, 1, 2]},
        }
    )
    zone = ZoneState.parse(
        1,
        {
            "chComfortTemp": {"value": zone_comfort, "min": 10, "max": 30, "step": 0.5},
            "chReducedTemp": {"value": zone_reduced, "min": 10, "max": 30, "step": 0.5},
            "coolComfortTemp": {
                "value": cooling_comfort,
                "min": 18,
                "max": 30,
                "step": 0.5,
            },
            "coolReducedTemp": {
                "value": cooling_reduced,
                "min": 18,
                "max": 30,
                "step": 0.5,
            },
            "isCoolingActive": cooling_active,
            "mode": {"value": zone_mode, "allowedOptions": [0, 1, 2, 3]},
        },
    )
    return ElcoData("GATEWAY", plant, {1: zone})


def _bsb_points(*, slope: float = 0.8, holiday_level: int = 0) -> dict[str, dict]:
    def point(address: str, value: float, **extra) -> dict:
        return {
            "address": address,
            "valueAsNumber": value,
            "valueAsString": None,
            "osv": False,
            "anyError": False,
            "deviceFailure": False,
            "bsbErrorCode": 0,
            "commErrorCode": 0,
            **extra,
        }

    return {
        "2950338": point(
            "2950338",
            holiday_level,
            enumOptions=[
                {"value": 0, "text": "Frost protection"},
                {"value": 1, "text": "Reduced"},
            ],
        ),
        "2950544": point("2950544", 18),
        "2950546": point("2950546", 8),
        "2950646": point("2950646", slope),
        "2950653": point("2950653", 18),
    }


class FakeApi:
    gateway_id = "GATEWAY"
    last_features_response = None

    def __init__(
        self,
        responses: list[ElcoData | Exception] | None = None,
        *,
        write_responses: list[Exception | None] | None = None,
        bsb_responses: list[dict[str, dict] | Exception] | None = None,
    ) -> None:
        self.responses = list(responses or [_data()])
        self.write_responses = list(write_responses or [None])
        self.get_data_calls = 0
        self.get_data_arguments: list[tuple[list[int], bool]] = []
        self.write_calls: list[tuple[str, tuple, dict]] = []
        self.bsb_responses = list(bsb_responses or [_bsb_points()])
        self.bsb_read_arguments: list[tuple[str, ...]] = []
        self.bsb_read_commands: list[bool] = []

    async def async_get_data(self, zones, *, use_cache=True) -> ElcoData:
        self.get_data_calls += 1
        self.get_data_arguments.append((list(zones), use_cache))
        response = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(response, Exception):
            raise response
        return response

    async def async_get_system_items(self, _features, _zones):
        return {}

    def _record_write(self, name: str, args: tuple, kwargs: dict) -> None:
        self.write_calls.append((name, args, kwargs))
        response = (
            self.write_responses.pop(0)
            if len(self.write_responses) > 1
            else self.write_responses[0]
        )
        if isinstance(response, Exception):
            raise response

    async def async_set_dhw(self, *args, **kwargs) -> None:
        self._record_write("dhw", args, kwargs)

    async def async_set_zone_temperatures(self, *args, **kwargs) -> None:
        self._record_write("zone_temperature", args, kwargs)

    async def async_set_zone_mode(self, *args, **kwargs) -> None:
        self._record_write("zone_mode", args, kwargs)

    async def async_get_bsb_points(
        self,
        addresses,
        *,
        command: bool = False,
    ) -> dict[str, dict]:
        self.bsb_read_arguments.append(tuple(addresses))
        self.bsb_read_commands.append(command)
        response = (
            self.bsb_responses.pop(0) if len(self.bsb_responses) > 1 else self.bsb_responses[0]
        )
        if isinstance(response, Exception):
            raise response
        return {address: response[address] for address in addresses if address in response}

    async def async_write_bsb_point(self, *args, **kwargs) -> None:
        self._record_write("bsb_point", args, kwargs)


def _coordinator(api: FakeApi | None = None) -> ElcoDataUpdateCoordinator:
    coordinator = ElcoDataUpdateCoordinator(
        FakeHass(),
        SimpleNamespace(),
        api or FakeApi(),
        3600,
    )
    coordinator._features = {}
    coordinator._zone_numbers = [1]
    coordinator._polls_until_slow_discovery = 1
    coordinator.data = _data()
    return coordinator


def _multi_zone_data() -> ElcoData:
    data = _data()
    zone_2 = ZoneState.parse(
        2,
        {
            "chComfortTemp": {"value": 21, "min": 10, "max": 30, "step": 0.5},
            "chReducedTemp": {"value": 18, "min": 10, "max": 30, "step": 0.5},
            "mode": {"value": 1, "allowedOptions": [0, 1, 2, 3]},
        },
    )
    return ElcoData("GATEWAY", data.plant, {1: data.zones[1], 2: zone_2})


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
@pytest.mark.parametrize(
    "write",
    [
        lambda coordinator: coordinator.async_set_dhw(comfort=48),
        lambda coordinator: coordinator.async_set_zone_temperature(1, "comfort", 24),
        lambda coordinator: coordinator.async_set_zone_mode(1, 2),
    ],
    ids=["dhw", "zone-temperature", "zone-mode"],
)
async def test_every_pre_write_read_fetches_one_zone_uncached(write) -> None:
    api = FakeApi([_multi_zone_data()])
    coordinator = _coordinator(api)
    coordinator._zone_numbers = [1, 2]
    coordinator.async_request_refresh = AsyncMock()

    await write(coordinator)

    assert api.get_data_arguments == [([1], False)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("write", "reconciled_data"),
    [
        (
            lambda coordinator: coordinator.async_set_zone_temperature(1, "comfort", 24),
            _data(zone_comfort=24),
        ),
        (lambda coordinator: coordinator.async_set_dhw(comfort=48), _data(dhw_comfort=48)),
        (lambda coordinator: coordinator.async_set_zone_mode(1, 2), _data(zone_mode=2)),
    ],
    ids=["zone-temperature", "dhw", "zone-mode"],
)
async def test_ambiguous_write_is_confirmed_by_fresh_read(write, reconciled_data) -> None:
    api = FakeApi(
        [_data(), reconciled_data],
        write_responses=[ElcoConnectionError("response lost")],
    )
    coordinator = _coordinator(api)
    coordinator.async_request_refresh = AsyncMock()

    await write(coordinator)

    assert api.get_data_arguments == [([1], False), ([1], False)]
    assert len(api.write_calls) == 1
    coordinator.async_request_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_unconfirmed_ambiguous_write_refreshes_then_reports_failure() -> None:
    api = FakeApi(
        [_data(), _data()],
        write_responses=[ElcoResponseError("server failed", status=503)],
    )
    coordinator = _coordinator(api)
    coordinator.async_request_refresh = AsyncMock()

    with pytest.raises(HomeAssistantError, match="was not confirmed"):
        await coordinator.async_set_zone_temperature(1, "comfort", 24)

    assert len(api.write_calls) == 1
    coordinator.async_request_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_ambiguous_write_with_failed_reconciliation_reports_unknown_outcome() -> None:
    api = FakeApi(
        [_data(), ElcoConnectionError("reconciliation failed")],
        write_responses=[ElcoConnectionError("response lost")],
    )
    coordinator = _coordinator(api)
    coordinator.async_request_refresh = AsyncMock()

    with pytest.raises(HomeAssistantError, match="may have been applied"):
        await coordinator.async_set_zone_mode(1, 2)

    assert len(api.write_calls) == 1
    coordinator.async_request_refresh.assert_not_awaited()


@pytest.mark.asyncio
async def test_ambiguous_write_honors_retry_after_without_reconciliation() -> None:
    api = FakeApi(
        [_data()],
        write_responses=[ElcoResponseError("service unavailable", status=503, retry_after=120)],
    )
    coordinator = _coordinator(api)
    coordinator.async_request_refresh = AsyncMock()

    with pytest.raises(HomeAssistantError, match="requested a delay"):
        await coordinator.async_set_zone_mode(1, 2)

    assert api.get_data_calls == 1
    assert len(api.write_calls) == 1
    coordinator.async_request_refresh.assert_not_awaited()


@pytest.mark.asyncio
async def test_bsb_number_write_reads_validates_writes_and_reads_back() -> None:
    before = _bsb_points(slope=0.8)
    after = _bsb_points(slope=1.0)
    api = FakeApi(bsb_responses=[before, after])
    coordinator = _coordinator(api)
    coordinator._slow_discovery = ReadOnlyDiscovery(bsb_points=before)
    coordinator.async_request_refresh = AsyncMock()

    await coordinator.async_set_bsb_number("heating_curve_slope_720", 1.0)

    assert api.bsb_read_arguments == [("2950646",), ("2950646",)]
    assert api.bsb_read_commands == [True, True]
    assert api.write_calls[0][0] == "bsb_point"
    assert api.write_calls[0][1] == (before["2950646"], 1.0)
    assert coordinator._slow_discovery.bsb_points["2950646"]["valueAsNumber"] == 1.0
    coordinator.async_request_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_frost_setpoint_write_reads_its_dynamic_reduced_limit() -> None:
    before = _bsb_points()
    after = _bsb_points()
    after["2950546"] = {**after["2950546"], "valueAsNumber": 10}
    api = FakeApi(bsb_responses=[before, after])
    coordinator = _coordinator(api)
    coordinator.async_request_refresh = AsyncMock()

    await coordinator.async_set_bsb_number("heating_circuit_frost_protection_setpoint_714", 10)

    assert api.bsb_read_arguments == [
        ("2950546", "2950544"),
        ("2950546", "2950544"),
    ]


@pytest.mark.asyncio
async def test_holiday_level_write_uses_fresh_server_enum_code() -> None:
    before = _bsb_points(holiday_level=0)
    after = _bsb_points(holiday_level=1)
    api = FakeApi(bsb_responses=[before, after])
    coordinator = _coordinator(api)
    coordinator.async_request_refresh = AsyncMock()

    await coordinator.async_set_holiday_operating_level("Reduced")

    assert api.write_calls[0][1] == (before["2950338"], 1)
    assert api.bsb_read_arguments == [("2950338",), ("2950338",)]
    coordinator.async_request_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_bsb_write_is_not_replayed_after_ambiguous_response() -> None:
    api = FakeApi(
        write_responses=[ElcoConnectionError("response lost")],
        bsb_responses=[_bsb_points(slope=0.8), _bsb_points(slope=1.0)],
    )
    coordinator = _coordinator(api)
    coordinator.async_request_refresh = AsyncMock()

    await coordinator.async_set_bsb_number("heating_curve_slope_720", 1.0)

    assert len(api.write_calls) == 1
    assert len(api.bsb_read_arguments) == 2
    coordinator.async_request_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_cooling_temperature_write_preserves_the_cooling_pair() -> None:
    api = FakeApi([_data(cooling_active=True)])
    coordinator = _coordinator(api)
    coordinator.async_request_refresh = AsyncMock()

    await coordinator.async_set_zone_temperature(1, "cooling_comfort", 23.5)

    name, args, kwargs = api.write_calls[0]
    assert name == "zone_temperature"
    assert args[0].cooling_active is True
    assert kwargs == {"comfort": 23.5, "reduced": 28, "cooling": True}
    coordinator.async_request_refresh.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [None, 400], ids=["application-rejection", "http-400"])
async def test_permanent_write_response_error_is_not_reconciled(status: int | None) -> None:
    api = FakeApi(
        [_data()],
        write_responses=[ElcoResponseError("invalid request", status=status)],
    )
    coordinator = _coordinator(api)
    coordinator.async_request_refresh = AsyncMock()

    with pytest.raises(HomeAssistantError, match="invalid request"):
        await coordinator.async_set_zone_mode(1, 2)

    assert api.get_data_calls == 1
    assert len(api.write_calls) == 1
    coordinator.async_request_refresh.assert_not_awaited()


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
@pytest.mark.parametrize(
    "error",
    [
        ElcoAuthenticationError("Remocon session expired"),
        ElcoConnectionError("Unable to communicate with Remocon"),
    ],
    ids=["authentication", "connection"],
)
async def test_fresh_data_does_not_retry_authentication_or_connection_errors(
    monkeypatch,
    error: Exception,
) -> None:
    api = FakeApi([error])
    coordinator = _coordinator(api)
    sleep = AsyncMock()
    monkeypatch.setattr(asyncio, "sleep", sleep)

    with pytest.raises(type(error), match=str(error)):
        await coordinator._async_fresh_data()

    assert api.get_data_calls == 1
    assert api.get_data_arguments == [([1], False)]
    sleep.assert_not_awaited()


@pytest.mark.asyncio
async def test_core_poll_schedules_early_recovery_after_communication_error(
    monkeypatch,
) -> None:
    api = FakeApi([ElcoResponseError("Communication error")])
    coordinator = _coordinator(api)
    sleep = AsyncMock()
    monkeypatch.setattr(asyncio, "sleep", sleep)

    with pytest.raises(UpdateFailed, match="Communication error") as raised:
        await coordinator._async_update_data()

    assert raised.value.retry_after == 60
    assert api.get_data_calls == 1
    sleep.assert_not_awaited()


@pytest.mark.asyncio
async def test_core_poll_does_not_retry_other_response_errors(monkeypatch) -> None:
    api = FakeApi([ElcoResponseError("Unsupported request")])
    coordinator = _coordinator(api)
    sleep = AsyncMock()
    monkeypatch.setattr(asyncio, "sleep", sleep)

    with pytest.raises(UpdateFailed, match="Unsupported request") as raised:
        await coordinator._async_update_data()

    assert raised.value.retry_after is None
    assert api.get_data_calls == 1
    sleep.assert_not_awaited()


@pytest.mark.asyncio
async def test_core_poll_does_not_retry_authentication_errors(monkeypatch) -> None:
    api = FakeApi([ElcoAuthenticationError("Remocon session expired")])
    coordinator = _coordinator(api)
    sleep = AsyncMock()
    monkeypatch.setattr(asyncio, "sleep", sleep)

    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()

    assert api.get_data_calls == 1
    sleep.assert_not_awaited()


@pytest.mark.asyncio
async def test_core_poll_does_not_retry_connection_errors(monkeypatch) -> None:
    api = FakeApi([ElcoConnectionError("Unable to communicate with Remocon")])
    coordinator = _coordinator(api)
    sleep = AsyncMock()
    monkeypatch.setattr(asyncio, "sleep", sleep)

    with pytest.raises(UpdateFailed, match="Unable to communicate with Remocon") as raised:
        await coordinator._async_update_data()

    assert raised.value.retry_after == 60
    assert api.get_data_calls == 1
    sleep.assert_not_awaited()


@pytest.mark.asyncio
async def test_core_poll_uses_bounded_recovery_backoff() -> None:
    api = FakeApi(
        [
            ElcoConnectionError("failure 1"),
            ElcoConnectionError("failure 2"),
            ElcoConnectionError("failure 3"),
            ElcoConnectionError("failure 4"),
        ]
    )
    coordinator = _coordinator(api)
    delays = []

    for _attempt in range(4):
        with pytest.raises(UpdateFailed) as raised:
            await coordinator._async_update_data()
        delays.append(raised.value.retry_after)

    assert delays == [60, 300, 900, 3600]


@pytest.mark.asyncio
async def test_successful_core_poll_resets_recovery_backoff() -> None:
    api = FakeApi(
        [
            ElcoConnectionError("first failure"),
            _data(),
            ElcoConnectionError("failure after recovery"),
        ]
    )
    coordinator = _coordinator(api)

    with pytest.raises(UpdateFailed) as first_failure:
        await coordinator._async_update_data()
    await coordinator._async_update_data()
    with pytest.raises(UpdateFailed) as failure_after_success:
        await coordinator._async_update_data()

    assert first_failure.value.retry_after == 60
    assert failure_after_success.value.retry_after == 60


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "expected_delay"),
    [
        (ElcoResponseError("rate limited", status=429), 300),
        (ElcoResponseError("retry later", status=503, retry_after=120), 120),
    ],
)
async def test_core_poll_honors_rate_limit_delay(
    error: ElcoResponseError,
    expected_delay: float,
) -> None:
    coordinator = _coordinator(FakeApi([error]))

    with pytest.raises(UpdateFailed) as raised:
        await coordinator._async_update_data()

    assert raised.value.retry_after == expected_delay


@pytest.mark.asyncio
async def test_core_poll_requests_complete_multi_zone_snapshot(monkeypatch) -> None:
    api = FakeApi([_multi_zone_data()])
    coordinator = _coordinator(api)
    coordinator._zone_numbers = [1, 2]
    sleep = AsyncMock()
    monkeypatch.setattr(asyncio, "sleep", sleep)

    result = await coordinator._async_update_data()

    assert list(result.zones) == [1, 2]
    assert api.get_data_arguments == [([1, 2], True)]
    sleep.assert_not_awaited()


@pytest.mark.asyncio
async def test_optional_discovery_aborts_on_connection_failure() -> None:
    coordinator = _coordinator()
    status = {}
    request = AsyncMock(side_effect=ElcoConnectionError("cloud unavailable"))

    with pytest.raises(_OptionalDiscoveryAborted):
        await coordinator._async_optional_probe(
            "probe",
            request(),
            status,
            circuit_breaker=True,
        )

    assert status["probe"] == "unavailable:ElcoConnectionError"


@pytest.mark.asyncio
async def test_optional_discovery_aborts_on_probe_timeout() -> None:
    coordinator = _coordinator()
    status = {}

    with pytest.raises(_OptionalDiscoveryAborted):
        await coordinator._async_optional_probe(
            "probe",
            asyncio.sleep(1),
            status,
            timeout_seconds=0,
            circuit_breaker=True,
        )

    assert status["probe"] == "unavailable:TimeoutError"


@pytest.mark.asyncio
async def test_optional_discovery_aborts_after_two_consecutive_gateway_failures() -> None:
    coordinator = _coordinator()
    status = {}

    first = await coordinator._async_optional_probe(
        "first",
        AsyncMock(side_effect=ElcoResponseError("bad gateway", status=502))(),
        status,
        circuit_breaker=True,
    )
    with pytest.raises(_OptionalDiscoveryAborted):
        await coordinator._async_optional_probe(
            "second",
            AsyncMock(side_effect=ElcoResponseError("unavailable", status=503))(),
            status,
            circuit_breaker=True,
        )

    assert first is None
    assert status["first"] == "unavailable:ElcoResponseError"
    assert status["second"] == "unavailable:ElcoResponseError"


@pytest.mark.asyncio
async def test_optional_endpoint_specific_500_does_not_open_circuit() -> None:
    coordinator = _coordinator()
    status = {}

    for index in range(3):
        result = await coordinator._async_optional_probe(
            f"probe-{index}",
            AsyncMock(side_effect=ElcoResponseError("unsupported", status=500))(),
            status,
            circuit_breaker=True,
        )
        assert result is None

    assert coordinator._optional_transient_failures == 0


@pytest.mark.asyncio
async def test_optional_rate_limit_stops_discovery_until_retry_after() -> None:
    coordinator = _coordinator()
    error = ElcoResponseError("rate limited", status=429, retry_after=120)
    refresh = AsyncMock(side_effect=_OptionalDiscoveryAborted(error))
    coordinator._async_refresh_deferred_discovery = refresh
    before = asyncio.get_running_loop().time()

    await coordinator.async_refresh_deferred_discovery()
    await coordinator.async_refresh_deferred_discovery()

    refresh.assert_awaited_once()
    assert coordinator._optional_retry_not_before >= before + 120


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
