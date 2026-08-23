"""Tests for Remocon login, reads, and safe write payloads."""

from __future__ import annotations

from collections import deque
from importlib import import_module
from typing import Any
from unittest.mock import AsyncMock

import pytest
from aiohttp import ClientConnectionError
from yarl import URL

from custom_components.elco_aerotop.api import (
    ElcoApiClient,
    ElcoAuthenticationError,
    ElcoConnectionError,
    ElcoResponseError,
    _retry_after_seconds,
)
from custom_components.elco_aerotop.const import (
    GET_DATA_PATH,
    REQUEST_TIMEOUT,
    SET_TEMPERATURE_PATH,
)

api_module = import_module("custom_components.elco_aerotop.api")


class FakeCookieJar:
    def __init__(self) -> None:
        self.cookies: dict[str, str] = {}

    def update_cookies(self, cookies: dict[str, str], response_url: URL) -> None:
        self.cookies.update(cookies)


class FakeResponse:
    def __init__(
        self,
        *,
        status: int = 200,
        json_data: Any = None,
        text_data: str = "",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status = status
        self._json_data = json_data
        self._text_data = text_data
        self.headers = headers or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def json(self, content_type=None):
        if isinstance(self._json_data, Exception):
            raise self._json_data
        return self._json_data

    async def text(self) -> str:
        return self._text_data


class FakeSession:
    def __init__(self, responses: list[FakeResponse | Exception]) -> None:
        self.responses = deque(responses)
        self.cookie_jar = FakeCookieJar()
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def _next_response(self) -> FakeResponse:
        response = self.responses.popleft()
        if isinstance(response, Exception):
            raise response
        return response

    async def get(self, url: str, **kwargs) -> FakeResponse:
        self.calls.append(("GET", url, kwargs))
        return self._next_response()

    async def post(self, url: str, **kwargs) -> FakeResponse:
        self.calls.append(("POST", url, kwargs))
        return self._next_response()

    async def request(self, method: str, url: str, **kwargs) -> FakeResponse:
        self.calls.append((method, url, kwargs))
        return self._next_response()


@pytest.mark.parametrize(
    ("value", "expected"),
    [("-10", 1), ("999999", 86400), ("nan", None), ("invalid", None)],
)
def test_retry_after_is_validated_and_bounded(value: str, expected: float | None) -> None:
    assert _retry_after_seconds(FakeResponse(headers={"Retry-After": value})) == expected


def login_responses() -> list[FakeResponse]:
    return [
        FakeResponse(
            text_data=(
                '<form><input name="__RequestVerificationToken" type="hidden" '
                'value="token-123" /></form>'
            )
        ),
        FakeResponse(json_data={"ok": True}),
    ]


def get_data_payload() -> dict[str, Any]:
    return {
        "ok": True,
        "data": {
            "plantData": {
                "outsideTemp": 5.5,
                "heatPumpOn": True,
                "dhwStorageTemp": 49,
                "dhwComfortTemp": {"value": 52, "min": 40, "max": 60, "step": 1},
                "dhwReducedTemp": {"value": 45, "min": 8, "max": 60, "step": 1},
                "dhwMode": {
                    "value": 1,
                    "allowedOptions": [0, 1, 2],
                    "allowedOptionTexts": ["Off", "On", "Eco"],
                },
            },
            "zoneData": {
                "holidays": [
                    {
                        "index": 0,
                        "fromAsIso": "2027-08-30T00:00:00+02:00",
                        "toAsIso": "2027-09-04T00:00:00+02:00",
                        "added": False,
                        "changed": False,
                        "deleted": False,
                        "osv": False,
                    }
                ],
                "mode": {"value": 1, "allowedOptions": [0, 1, 2, 3]},
                "chComfortTemp": {"value": 21, "min": 10, "max": 30, "step": 0.5},
                "chReducedTemp": {"value": 17, "min": 10, "max": 30, "step": 0.5},
                "desiredRoomTemp": 21,
            },
        },
    }


@pytest.mark.asyncio
async def test_login_uses_antiforgery_cookie_and_json_credentials() -> None:
    session = FakeSession(login_responses())
    client = ElcoApiClient(session, "user@example.com", "secret", "aabbcc", "https://example.test")

    await client.async_login()

    assert session.cookie_jar.cookies["__formRequestVerificationToken"] == "token-123"
    assert session.calls[0][2]["headers"]["User-Agent"] == ("ELCO-Aerotop-Home-Assistant/0.3.10")
    assert session.calls[1][2]["json"] == {
        "email": "user@example.com",
        "password": "secret",
        "rememberMe": False,
        "language": "English_Gb",
    }
    assert session.calls[1][2]["headers"]["User-Agent"] == ("ELCO-Aerotop-Home-Assistant/0.3.10")
    assert session.calls[0][2]["timeout"].total == REQUEST_TIMEOUT


@pytest.mark.asyncio
async def test_get_data_parses_plant_and_zone() -> None:
    session = FakeSession([*login_responses(), FakeResponse(json_data=get_data_payload())])
    client = ElcoApiClient(session, "user", "pass", "gateway", "https://example.test")

    data = await client.async_get_data([1], use_cache=False)

    assert data.gateway_id == "GATEWAY"
    assert data.plant.outside_temperature == 5.5
    assert data.zones[1].comfort_temperature.value == 21
    assert data.get_data_responses == [get_data_payload()]
    assert session.calls[-1][2]["json"]["useCache"] is False


@pytest.mark.asyncio
async def test_multi_zone_read_applies_full_timeout_to_every_request() -> None:
    session = FakeSession(
        [
            *login_responses(),
            FakeResponse(json_data=get_data_payload()),
            FakeResponse(json_data=get_data_payload()),
        ]
    )
    client = ElcoApiClient(session, "user", "pass", "gateway", "https://example.test")

    await client.async_get_data([1, 2])

    get_data_path = GET_DATA_PATH.format(gateway_id="GATEWAY")
    get_data_calls = [call for call in session.calls if call[1].endswith(get_data_path)]
    assert [call[2]["timeout"].total for call in get_data_calls] == [
        REQUEST_TIMEOUT,
        REQUEST_TIMEOUT,
    ]
    assert [call[2]["json"]["useCache"] for call in get_data_calls] == [False, True]


@pytest.mark.asyncio
async def test_core_read_reauthenticates_and_replays_once() -> None:
    session = FakeSession(
        [
            *login_responses(),
            FakeResponse(status=401),
            *login_responses(),
            FakeResponse(json_data=get_data_payload()),
        ]
    )
    client = ElcoApiClient(session, "user", "pass", "gateway", "https://example.test")

    data = await client.async_get_data([1], use_cache=False)

    get_data_path = GET_DATA_PATH.format(gateway_id="GATEWAY")
    get_data_calls = [call for call in session.calls if call[1].endswith(get_data_path)]
    assert data.plant.outside_temperature == 5.5
    assert len(get_data_calls) == 2
    assert get_data_calls[0][2]["json"] == get_data_calls[1][2]["json"]
    assert [method for method, _url, _kwargs in session.calls] == [
        "GET",
        "POST",
        "POST",
        "GET",
        "POST",
        "POST",
    ]


@pytest.mark.asyncio
async def test_core_read_propagates_second_authentication_failure() -> None:
    session = FakeSession(
        [
            *login_responses(),
            FakeResponse(status=401),
            *login_responses(),
            FakeResponse(status=401),
        ]
    )
    client = ElcoApiClient(session, "user", "pass", "gateway", "https://example.test")

    with pytest.raises(ElcoAuthenticationError, match="session expired"):
        await client.async_get_data([1])

    get_data_path = GET_DATA_PATH.format(gateway_id="GATEWAY")
    assert sum(call[1].endswith(get_data_path) for call in session.calls) == 2
    assert client._authenticated is False


@pytest.mark.asyncio
async def test_core_read_retries_one_fast_connection_failure(monkeypatch) -> None:
    session = FakeSession(
        [
            *login_responses(),
            ClientConnectionError("connection lost"),
            FakeResponse(json_data=get_data_payload()),
        ]
    )
    client = ElcoApiClient(session, "user", "pass", "gateway", "https://example.test")
    sleep = AsyncMock()
    monkeypatch.setattr(api_module.asyncio, "sleep", sleep)

    data = await client.async_get_data([1])

    get_data_path = GET_DATA_PATH.format(gateway_id="GATEWAY")
    assert data.plant.outside_temperature == 5.5
    assert sum(call[1].endswith(get_data_path) for call in session.calls) == 2
    sleep.assert_awaited_once_with(1)


@pytest.mark.asyncio
async def test_multi_zone_retry_restarts_and_returns_only_complete_snapshot(monkeypatch) -> None:
    session = FakeSession(
        [
            *login_responses(),
            FakeResponse(json_data=get_data_payload()),
            FakeResponse(status=503),
            FakeResponse(json_data=get_data_payload()),
            FakeResponse(json_data=get_data_payload()),
        ]
    )
    client = ElcoApiClient(session, "user", "pass", "gateway", "https://example.test")
    sleep = AsyncMock()
    monkeypatch.setattr(api_module.asyncio, "sleep", sleep)

    data = await client.async_get_data([1, 2])

    get_data_path = GET_DATA_PATH.format(gateway_id="GATEWAY")
    calls = [call for call in session.calls if call[1].endswith(get_data_path)]
    assert [call[2]["json"]["zone"] for call in calls] == [1, 2, 1, 2]
    assert list(data.zones) == [1, 2]
    sleep.assert_awaited_once_with(1)


@pytest.mark.asyncio
async def test_core_read_does_not_start_retry_without_operation_budget(monkeypatch) -> None:
    client = ElcoApiClient(FakeSession([]), "user", "pass", "gateway", "https://example.test")
    first_error = ElcoConnectionError("connection lost", retryable=True)
    read_once = AsyncMock(side_effect=first_error)
    client._async_get_data_once = read_once
    monkeypatch.setattr(api_module, "REQUEST_TIMEOUT", 0.01)

    with pytest.raises(ElcoConnectionError) as raised:
        await client.async_get_data([1])

    assert raised.value is first_error
    read_once.assert_awaited_once_with([1], use_cache=True)


@pytest.mark.asyncio
async def test_core_read_does_not_retry_full_timeout(monkeypatch) -> None:
    session = FakeSession([*login_responses(), TimeoutError()])
    client = ElcoApiClient(session, "user", "pass", "gateway", "https://example.test")
    sleep = AsyncMock()
    monkeypatch.setattr(api_module.asyncio, "sleep", sleep)

    with pytest.raises(ElcoConnectionError, match="Unable to communicate with Remocon") as raised:
        await client.async_get_data([1])

    get_data_path = GET_DATA_PATH.format(gateway_id="GATEWAY")
    assert sum(call[1].endswith(get_data_path) for call in session.calls) == 1
    assert raised.value.timed_out is True
    sleep.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [408, 500, 502, 503, 504])
async def test_core_read_retries_transient_http_failure_once(monkeypatch, status: int) -> None:
    session = FakeSession(
        [
            *login_responses(),
            FakeResponse(status=status),
            FakeResponse(status=status),
        ]
    )
    client = ElcoApiClient(session, "user", "pass", "gateway", "https://example.test")
    sleep = AsyncMock()
    monkeypatch.setattr(api_module.asyncio, "sleep", sleep)

    with pytest.raises(ElcoResponseError, match=rf"returned HTTP {status}$") as raised:
        await client.async_get_data([1])

    assert raised.value.status == status
    assert raised.value.retry_after is None
    get_data_path = GET_DATA_PATH.format(gateway_id="GATEWAY")
    assert sum(call[1].endswith(get_data_path) for call in session.calls) == 2
    sleep.assert_awaited_once_with(1)


@pytest.mark.asyncio
async def test_core_read_preserves_retry_after_without_immediate_retry(monkeypatch) -> None:
    session = FakeSession(
        [
            *login_responses(),
            FakeResponse(status=429, headers={"Retry-After": "120"}),
        ]
    )
    client = ElcoApiClient(session, "user", "pass", "gateway", "https://example.test")
    sleep = AsyncMock()
    monkeypatch.setattr(api_module.asyncio, "sleep", sleep)

    with pytest.raises(ElcoResponseError) as raised:
        await client.async_get_data([1])

    assert str(raised.value).endswith("returned HTTP 429")
    assert raised.value.status == 429
    assert raised.value.retry_after == 120
    get_data_path = GET_DATA_PATH.format(gateway_id="GATEWAY")
    assert sum(call[1].endswith(get_data_path) for call in session.calls) == 1
    sleep.assert_not_awaited()


@pytest.mark.asyncio
async def test_core_read_retries_controller_communication_error(monkeypatch) -> None:
    session = FakeSession(
        [
            *login_responses(),
            FakeResponse(json_data={"ok": False, "message": "Communication error"}),
            FakeResponse(json_data=get_data_payload()),
        ]
    )
    client = ElcoApiClient(session, "user", "pass", "gateway", "https://example.test")
    sleep = AsyncMock()
    monkeypatch.setattr(api_module.asyncio, "sleep", sleep)

    data = await client.async_get_data([1], use_cache=False)

    assert data.plant.outside_temperature == 5.5
    sleep.assert_awaited_once_with(1)


@pytest.mark.asyncio
async def test_features_and_system_data_capture_gateway_capabilities() -> None:
    features_payload = {
        "ok": True,
        "data": {
            "features": {
                "zones": [{"num": 1, "isHidden": False}],
                "hasMetering": True,
            },
            "contractVersion": 2,
        },
    }
    system_payload = {
        "items": [
            {"id": "HeatingCircuitPressure", "zn": 0, "value": 1.6},
            {"id": "ZoneMeasuredTemp", "zone": 1, "value": 22.4},
        ]
    }
    session = FakeSession(
        [
            *login_responses(),
            FakeResponse(json_data=features_payload),
            FakeResponse(json_data=system_payload),
        ]
    )
    client = ElcoApiClient(session, "user", "pass", "gateway", "https://example.test")

    features = await client.async_get_features()
    zones = await client.async_get_zone_numbers(features)
    items = await client.async_get_system_items(features, zones)

    assert client.last_features_response == features_payload
    assert zones == [1]
    assert items["HeatingCircuitPressure:0"]["value"] == 1.6
    assert items["ZoneMeasuredTemp:1"]["value"] == 22.4
    assert session.calls[-1][2]["json"]["useCache"] is False
    requested = session.calls[-1][2]["json"]["items"]
    assert {"id": "HeatingCircuitPressure", "zn": 0} in requested
    assert {"id": "ZoneMeasuredTemp", "zn": 1} in requested


@pytest.mark.asyncio
async def test_read_only_endpoint_families_accept_their_payload_shapes() -> None:
    session = FakeSession(
        [
            *login_responses(),
            FakeResponse(json_data={"ok": True, "data": {"timeProgs": []}}),
            FakeResponse(json_data={"ok": True, "data": {"asKwhRaw": []}}),
            FakeResponse(json_data={"ok": True, "data": {"nextMaintenance": "2027-01-01"}}),
            FakeResponse(json_data=[]),
            FakeResponse(
                json_data={
                    "ok": True,
                    "data": [
                        {"address": 700, "textualValue": "Automatic"},
                        {"address": "710", "value": 23.0},
                    ],
                }
            ),
        ]
    )
    client = ElcoApiClient(session, "user", "pass", "gateway", "https://example.test")

    assert await client.async_get_schedule("ChZn1") == {"timeProgs": []}
    assert await client.async_get_metering({}, has_cooling=False) == {"asKwhRaw": []}
    assert await client.async_get_maintenance() == {"nextMaintenance": "2027-01-01"}
    assert await client.async_get_bus_errors() == []
    assert (await client.async_get_bsb_points())["700"]["textualValue"] == "Automatic"

    assert session.calls[2][1].endswith("/R2/PlantTimeProgBsb/GetData/GATEWAY")
    assert session.calls[2][2]["json"] == {
        "zone": 1,
        "filter": {"progIds": [1], "plant": False, "zone": True},
        "useCache": True,
    }
    assert session.calls[3][2]["json"] == {"features": {}, "hasCooling": False}
    assert "addresses=327836,2950516,2950542,2950544,2950546" in session.calls[-1][1]
    assert "327836" in session.calls[-1][1]
    assert "340067" in session.calls[-1][1]
    assert "5834029" in session.calls[-1][1]


@pytest.mark.asyncio
async def test_broad_read_only_mobile_discovery_preserves_complete_payloads() -> None:
    session = FakeSession(
        [
            *login_responses(),
            FakeResponse(
                json_data={
                    "gw": "GATEWAY",
                    "zones": {"1": {"holidays": [{"index": 0, "osv": True}]}},
                }
            ),
            FakeResponse(json_data={"token": "mobile-token"}),
            FakeResponse(
                json_data=[
                    {"id": 119, "value": -64, "unit": "dBm"},
                    {"id": 253, "value": 3210, "unit": "h"},
                ]
            ),
        ]
    )
    client = ElcoApiClient(session, "user", "pass", "gateway", "https://example.test")

    bsb_data = await client.async_get_bsb_plant_data()
    menu_items = await client.async_get_menu_items((119, 253))

    assert bsb_data["zones"]["1"]["holidays"][0]["osv"] is True
    assert menu_items["119"]["unit"] == "dBm"
    assert menu_items["253"]["value"] == 3210
    assert session.calls[2][1].endswith("/api/v2/remote/bsbPlantData/GATEWAY")
    assert session.calls[3][1].endswith("/api/v2/accounts/login")
    assert session.calls[3][2]["json"] == {"usr": "user", "pwd": "pass"}
    assert session.calls[4][1].endswith("/api/v2/menuItems/GATEWAY?menuItems=119,253")
    assert session.calls[4][2]["headers"]["ar.authToken"] == "mobile-token"


@pytest.mark.asyncio
async def test_bsb_read_accepts_an_isolated_address_group() -> None:
    session = FakeSession(
        [
            *login_responses(),
            FakeResponse(
                json_data={
                    "ok": True,
                    "data": [{"address": "2950516", "textualValue": "Automatic"}],
                }
            ),
        ]
    )
    client = ElcoApiClient(session, "user", "pass", "gateway", "https://example.test")

    points = await client.async_get_bsb_points(("2950516", "2950544"))

    assert points["2950516"]["textualValue"] == "Automatic"
    assert "addresses=2950516,2950544" in session.calls[-1][1]
    assert "5834029" not in session.calls[-1][1]


@pytest.mark.asyncio
async def test_plant_metadata_matches_gateway_id_or_serial() -> None:
    plants = [
        {
            "gwId": "internal-id",
            "gwSerial": "GATEWAY",
            "plantName": "Home",
            "location": {"cityName": "Example City"},
            "gwFwVer": "1.2.3",
        }
    ]
    session = FakeSession([*login_responses(), FakeResponse(json_data=plants)])
    client = ElcoApiClient(session, "user", "pass", "gateway", "https://example.test")

    metadata = await client.async_get_plant_metadata()

    assert metadata["plantName"] == "Home"
    assert metadata["location"]["cityName"] == "Example City"
    assert session.calls[-1][1].endswith("/api/v2/remote/plants/lite")


@pytest.mark.asyncio
async def test_plant_header_returns_live_status_metadata() -> None:
    header = {
        "plantAddress": "Example address",
        "applianceModel": "RVS 61",
        "gwOnline": True,
        "errorType": 0,
        "errorText": "Status: OK",
    }
    session = FakeSession([*login_responses(), FakeResponse(json_data=header)])
    client = ElcoApiClient(session, "user", "pass", "gateway", "https://example.test")

    result = await client.async_get_plant_header()

    assert result == header
    assert session.calls[-1][1].endswith("/R2/Plant/PlantHeader/GATEWAY")


@pytest.mark.asyncio
async def test_plant_user_data_returns_owner_metadata() -> None:
    user_data = {
        "firstName": "Thomas",
        "lastName": "Gregg",
        "emailLanguage": "English",
        "phone": "",
        "mobilePhone": "",
    }
    session = FakeSession([*login_responses(), FakeResponse(json_data={"data": user_data})])
    client = ElcoApiClient(session, "user", "pass", "gateway", "https://example.test")

    result = await client.async_get_plant_user_data()

    assert result == user_data
    assert session.calls[-1][1].endswith("/R2/PlantData/GetUserData?id=GATEWAY")


@pytest.mark.asyncio
async def test_optional_forbidden_response_does_not_invalidate_core_session() -> None:
    session = FakeSession(
        [
            *login_responses(),
            FakeResponse(status=403),
            FakeResponse(json_data=get_data_payload()),
        ]
    )
    client = ElcoApiClient(session, "user", "pass", "gateway", "https://example.test")

    with pytest.raises(ElcoAuthenticationError):
        await client.async_get_schedule("ChZn1")
    data = await client.async_get_data([1])

    assert data.plant.outside_temperature == 5.5
    assert [method for method, _url, _kwargs in session.calls] == ["GET", "POST", "POST", "POST"]


@pytest.mark.asyncio
async def test_bsb_dhw_schedule_uses_verified_program_filter() -> None:
    session = FakeSession(
        [*login_responses(), FakeResponse(json_data={"ok": True, "data": {"timeProgs": []}})]
    )
    client = ElcoApiClient(session, "user", "pass", "gateway", "https://example.test")

    await client.async_get_schedule("Dhw")

    assert session.calls[-1][2]["json"] == {
        "zone": 0,
        "filter": {"progIds": [7], "plant": True, "zone": False},
        "useCache": True,
    }


@pytest.mark.asyncio
async def test_monitoring_and_bsb_boiler_reads_preserve_structured_payloads() -> None:
    session = FakeSession(
        [
            *login_responses(),
            FakeResponse(
                json_data={
                    "ok": True,
                    "data": {
                        "automatedMonitoring": {"hydraulicPressure": 3},
                        "predictiveMaintenances": [],
                    },
                }
            ),
            FakeResponse(json_data={"ok": True, "data": {"model": "RVS61"}}),
        ]
    )
    client = ElcoApiClient(session, "user", "pass", "gateway", "https://example.test")

    monitoring = await client.async_get_automated_monitoring()
    boiler = await client.async_get_bsb_boiler_data()

    assert monitoring["automatedMonitoring"]["hydraulicPressure"] == 3
    assert monitoring["predictiveMaintenances"] == []
    assert boiler["model"] == "RVS61"
    assert session.calls[2][1].endswith("/R2/AutomatedMonitoring/GetDrawerData/GATEWAY")
    assert session.calls[3][1].endswith("/R2/PlantData/GetBsbBoilerData?id=GATEWAY")


@pytest.mark.asyncio
async def test_zone_write_preserves_full_zone_state() -> None:
    session = FakeSession(
        [
            *login_responses(),
            FakeResponse(json_data=get_data_payload()),
            FakeResponse(json_data={"ok": True, "data": {}}),
        ]
    )
    client = ElcoApiClient(session, "user", "pass", "gateway", "https://example.test")
    data = await client.async_get_data([1], use_cache=False)

    await client.async_set_zone_temperatures(data.zones[1], comfort=22, reduced=17)

    request_body = session.calls[-1][2]["json"]
    assert request_body["zoneNum"] == 1
    assert request_body["comfort"] == 22
    assert request_body["reduced"] == 17
    assert request_body["zoneData"] == data.zones[1].raw


@pytest.mark.asyncio
async def test_zone_write_reauthenticates_and_replays_once_after_unauthorized() -> None:
    session = FakeSession(
        [
            *login_responses(),
            FakeResponse(json_data=get_data_payload()),
            FakeResponse(status=401),
            *login_responses(),
            FakeResponse(json_data={"ok": True, "data": {}}),
        ]
    )
    client = ElcoApiClient(session, "user", "pass", "gateway", "https://example.test")
    data = await client.async_get_data([1], use_cache=False)

    await client.async_set_zone_temperatures(data.zones[1], comfort=22, reduced=17)

    write_path = SET_TEMPERATURE_PATH.format(gateway_id="GATEWAY")
    write_calls = [call for call in session.calls if call[1].endswith(write_path)]
    assert len(write_calls) == 2
    assert write_calls[0][2]["json"] == write_calls[1][2]["json"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [ClientConnectionError("connection lost"), TimeoutError()],
    ids=["client-connection-error", "timeout"],
)
async def test_zone_write_transport_failure_is_not_replayed(error: Exception) -> None:
    session = FakeSession(
        [
            *login_responses(),
            FakeResponse(json_data=get_data_payload()),
            error,
            FakeResponse(json_data={"ok": True, "data": {}}),
        ]
    )
    client = ElcoApiClient(session, "user", "pass", "gateway", "https://example.test")
    data = await client.async_get_data([1], use_cache=False)

    with pytest.raises(ElcoConnectionError, match="Unable to communicate with Remocon"):
        await client.async_set_zone_temperatures(data.zones[1], comfort=22, reduced=17)

    write_path = SET_TEMPERATURE_PATH.format(gateway_id="GATEWAY")
    assert sum(call[1].endswith(write_path) for call in session.calls) == 1


@pytest.mark.asyncio
async def test_dhw_write_preserves_full_plant_state() -> None:
    session = FakeSession(
        [
            *login_responses(),
            FakeResponse(json_data=get_data_payload()),
            FakeResponse(json_data={"ok": True, "data": {}}),
        ]
    )
    client = ElcoApiClient(session, "user", "pass", "gateway", "https://example.test")
    data = await client.async_get_data([1], use_cache=False)

    await client.async_set_dhw(data.plant, comfort=53, reduced=45, mode=2)

    request_body = session.calls[-1][2]["json"]
    assert request_body["plantData"] == data.plant.raw
    assert request_body["comfortTemp"] == 53
    assert request_body["reducedTemp"] == 45
    assert request_body["dhwMode"] == 2


@pytest.mark.asyncio
async def test_zone_mode_write_preserves_complete_set_data_payload() -> None:
    session = FakeSession(
        [
            *login_responses(),
            FakeResponse(json_data=get_data_payload()),
            FakeResponse(json_data={"ok": True, "data": {}}),
        ]
    )
    client = ElcoApiClient(session, "user", "pass", "gateway", "https://example.test")
    data = await client.async_get_data([1], use_cache=False)

    await client.async_set_zone_mode(data.plant, data.zones[1], mode=2)

    request_body = session.calls[-1][2]["json"]
    assert request_body["plantData"] == {
        **data.plant.raw,
        "gatewayId": "GATEWAY",
    }
    assert request_body["zoneData"] == {
        **data.zones[1].raw,
        "gatewayId": "GATEWAY",
        "zone": 1,
        "mode": {"value": 2, "allowedOptions": [0, 1, 2, 3]},
    }
    assert request_body["zoneData"]["holidays"] == data.zones[1].raw["holidays"]
    assert request_body["viewModel"] == {"zoneNumber": 1}
