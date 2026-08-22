"""Tests for Remocon login, reads, and safe write payloads."""

from __future__ import annotations

from collections import deque
from typing import Any

import pytest
from yarl import URL

from custom_components.elco_aerotop.api import ElcoApiClient


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
    ) -> None:
        self.status = status
        self._json_data = json_data
        self._text_data = text_data

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
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = deque(responses)
        self.cookie_jar = FakeCookieJar()
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    async def get(self, url: str, **kwargs) -> FakeResponse:
        self.calls.append(("GET", url, kwargs))
        return self.responses.popleft()

    async def post(self, url: str, **kwargs) -> FakeResponse:
        self.calls.append(("POST", url, kwargs))
        return self.responses.popleft()

    async def request(self, method: str, url: str, **kwargs) -> FakeResponse:
        self.calls.append((method, url, kwargs))
        return self.responses.popleft()


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
    assert session.calls[0][2]["headers"]["User-Agent"] == ("ELCO-Aerotop-Home-Assistant/0.2.0")
    assert session.calls[1][2]["json"] == {
        "email": "user@example.com",
        "password": "secret",
        "rememberMe": False,
        "language": "English_Gb",
    }
    assert session.calls[1][2]["headers"]["User-Agent"] == ("ELCO-Aerotop-Home-Assistant/0.2.0")
    assert session.calls[0][2]["timeout"].total == 30


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
            FakeResponse(json_data={"plans": []}),
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

    assert await client.async_get_schedule("ChZn1") == {"plans": []}
    assert await client.async_get_metering({}, has_cooling=False) == {"asKwhRaw": []}
    assert await client.async_get_maintenance() == {"nextMaintenance": "2027-01-01"}
    assert await client.async_get_bus_errors() == []
    assert (await client.async_get_bsb_points())["700"]["textualValue"] == "Automatic"

    assert "/timeProgs/GATEWAY/ChZn1?umsys=si" in session.calls[2][1]
    assert session.calls[3][2]["json"] == {"features": {}, "hasCooling": False}
    assert "addresses=700,710,712,714,720,730" in session.calls[-1][1]


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
async def test_zone_mode_write_uses_minimal_set_data_payload() -> None:
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
    assert request_body == {
        "plantData": {
            "dhwComfortTemp": {"value": 52},
            "dhwReducedTemp": {"value": 45},
            "dhwMode": {"value": 1},
        },
        "zoneData": {"zone": 1, "mode": {"value": 2}},
        "viewModel": {"zoneNumber": 1},
    }
