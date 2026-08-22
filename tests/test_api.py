"""Tests for Remocon login, reads, and safe write payloads."""

from __future__ import annotations

from collections import deque
from typing import Any

import pytest
from yarl import URL

from custom_components.elco_aerotop.api import ElcoApiClient, ElcoAuthenticationError


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
    assert session.calls[0][2]["headers"]["User-Agent"] == ("ELCO-Aerotop-Home-Assistant/0.2.16")
    assert session.calls[1][2]["json"] == {
        "email": "user@example.com",
        "password": "secret",
        "rememberMe": False,
        "language": "English_Gb",
    }
    assert session.calls[1][2]["headers"]["User-Agent"] == ("ELCO-Aerotop-Home-Assistant/0.2.16")
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
