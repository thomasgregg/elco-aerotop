"""Asynchronous client for the ELCO Remocon R2 JSON endpoints."""

from __future__ import annotations

import asyncio
import logging
import re
from copy import deepcopy
from html import unescape
from typing import Any

from aiohttp import ClientError, ClientResponse, ClientSession, ClientTimeout
from yarl import URL

from .const import (
    BSB_DISCOVERY_ADDRESSES,
    BSB_PLANT_DATA_PATH,
    BSB_READ_PATH,
    BUS_ERRORS_PATH,
    DATA_ITEMS_PATH,
    FEATURES_PATH,
    GET_DATA_PATH,
    GLOBAL_DATA_ITEM_IDS,
    LOGIN_PATH,
    MAINTENANCE_PATH,
    MENU_ITEM_BASE_IDS,
    MENU_ITEMS_PATH,
    METERING_PATH,
    MOBILE_LOGIN_PATH,
    PLANTS_LITE_PATH,
    REQUEST_TIMEOUT,
    SAVE_DHW_PATH,
    SET_DATA_PATH,
    SET_TEMPERATURE_PATH,
    TIME_PROGRAM_PATH,
    USER_AGENT,
    ZONE_DATA_ITEM_IDS,
)
from .models import ElcoData, PlantState, ZoneState

_LOGGER = logging.getLogger(__name__)

_TOKEN_RE = re.compile(
    r'name=["\']__RequestVerificationToken["\'][^>]*value=["\']([^"\']+)',
    re.IGNORECASE,
)


class ElcoApiError(Exception):
    """Base error raised by the Remocon client."""


class ElcoAuthenticationError(ElcoApiError):
    """Authentication failed or expired."""


class ElcoConnectionError(ElcoApiError):
    """The Remocon service could not be reached."""


class ElcoResponseError(ElcoApiError):
    """The Remocon service returned an unexpected response."""


class ElcoApiClient:
    """Client for the browser-facing Remocon R2 API."""

    def __init__(
        self,
        session: ClientSession,
        username: str,
        password: str,
        gateway_id: str,
        base_url: str,
    ) -> None:
        self._session = session
        self._username = username
        self._password = password
        self.gateway_id = gateway_id.upper()
        self.base_url = base_url.rstrip("/")
        self._authenticated = False
        self._auth_lock = asyncio.Lock()
        self._mobile_token: str | None = None
        self._mobile_auth_lock = asyncio.Lock()
        self._timeout = ClientTimeout(total=REQUEST_TIMEOUT)
        self.last_features_response: Any = None

    @property
    def _json_headers(self) -> dict[str, str]:
        return {
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Ajax-Request": "json",
            "Content-Type": "application/json; charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest",
        }

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    async def async_login(self) -> None:
        """Create an authenticated R2 cookie session."""
        async with self._auth_lock:
            if self._authenticated:
                return
            try:
                response = await self._session.get(
                    self._url(LOGIN_PATH),
                    headers={"User-Agent": USER_AGENT},
                    timeout=self._timeout,
                )
                async with response:
                    if response.status != 200:
                        raise ElcoConnectionError(f"Login page returned HTTP {response.status}")
                    html = await response.text()

                token_match = _TOKEN_RE.search(html)
                if token_match is None:
                    raise ElcoResponseError("Login page did not contain an anti-forgery token")
                token = unescape(token_match.group(1))
                self._session.cookie_jar.update_cookies(
                    {"__formRequestVerificationToken": token},
                    response_url=URL(self.base_url),
                )

                response = await self._session.post(
                    self._url(LOGIN_PATH),
                    headers=self._json_headers,
                    json={
                        "email": self._username,
                        "password": self._password,
                        "rememberMe": False,
                        "language": "English_Gb",
                    },
                    timeout=self._timeout,
                )
                async with response:
                    payload = await self._decode_json(response, "login")
                if not isinstance(payload, dict):
                    raise ElcoResponseError("Login returned an unexpected payload")
                if not payload.get("ok"):
                    raise ElcoAuthenticationError(
                        str(payload.get("message") or "Wrong username or password")
                    )
                self._authenticated = True
            except ElcoApiError:
                self._authenticated = False
                raise
            except (ClientError, TimeoutError) as err:
                self._authenticated = False
                raise ElcoConnectionError("Unable to connect to Remocon") from err

    async def _decode_json(self, response: ClientResponse, operation: str) -> Any:
        if response.status in (401, 403):
            raise ElcoAuthenticationError("Remocon session expired")
        if response.status >= 400:
            raise ElcoResponseError(f"{operation} returned HTTP {response.status}")
        try:
            payload = await response.json(content_type=None)
        except (ValueError, TypeError) as err:
            text = await response.text()
            if "account/login" in text.lower() or 'id="loginform"' in text.lower():
                raise ElcoAuthenticationError("Remocon session expired") from err
            raise ElcoResponseError(f"{operation} did not return JSON") from err
        return payload

    async def _request_payload(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | list[Any] | None = None,
        retry_auth: bool = True,
        invalidate_auth: bool = True,
    ) -> Any:
        if not self._authenticated:
            await self.async_login()
        try:
            response = await self._session.request(
                method,
                self._url(path),
                headers=self._json_headers,
                json=body,
                timeout=self._timeout,
            )
            async with response:
                payload = await self._decode_json(response, path)
            if isinstance(payload, dict) and payload.get("ok") is False:
                raise ElcoResponseError(str(payload.get("message") or f"{path} failed"))
            return payload
        except ElcoAuthenticationError:
            if invalidate_auth:
                self._authenticated = False
            if not retry_auth:
                raise
            await self.async_login()
            return await self._request_payload(
                method,
                path,
                body=body,
                retry_auth=False,
                invalidate_auth=invalidate_auth,
            )
        except ElcoApiError:
            raise
        except (ClientError, TimeoutError) as err:
            raise ElcoConnectionError(f"Unable to communicate with Remocon: {path}") from err

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | list[Any] | None = None,
        retry_auth: bool = True,
    ) -> dict[str, Any]:
        payload = await self._request_payload(
            method,
            path,
            body=body,
            retry_auth=retry_auth,
        )
        if not isinstance(payload, dict):
            raise ElcoResponseError(f"{path} returned an unexpected payload")
        return payload

    async def _async_mobile_login(self) -> None:
        """Create the separate token session required by mobile-only endpoints."""
        async with self._mobile_auth_lock:
            if self._mobile_token is not None:
                return
            try:
                response = await self._session.post(
                    self._url(MOBILE_LOGIN_PATH),
                    headers=self._json_headers,
                    json={"usr": self._username, "pwd": self._password},
                    timeout=self._timeout,
                )
                async with response:
                    payload = await self._decode_json(response, "mobile login")
                token = payload.get("token") if isinstance(payload, dict) else None
                if not isinstance(token, str) or not token:
                    raise ElcoAuthenticationError("Mobile login did not return a token")
                self._mobile_token = token
            except ElcoApiError:
                self._mobile_token = None
                raise
            except (ClientError, TimeoutError) as err:
                self._mobile_token = None
                raise ElcoConnectionError("Unable to connect to the Remocon mobile API") from err

    async def _request_mobile_payload(
        self,
        path: str,
        *,
        retry_auth: bool = True,
    ) -> Any:
        """Make one token-authenticated, read-only mobile API request."""
        if self._mobile_token is None:
            await self._async_mobile_login()
        try:
            response = await self._session.get(
                self._url(path),
                headers={**self._json_headers, "ar.authToken": self._mobile_token or ""},
                timeout=self._timeout,
            )
            async with response:
                return await self._decode_json(response, path)
        except ElcoAuthenticationError:
            self._mobile_token = None
            if not retry_auth:
                raise
            await self._async_mobile_login()
            return await self._request_mobile_payload(path, retry_auth=False)
        except ElcoApiError:
            raise
        except (ClientError, TimeoutError) as err:
            raise ElcoConnectionError(f"Unable to communicate with Remocon: {path}") from err

    async def async_get_features(self) -> dict[str, Any]:
        """Fetch the complete capability map returned for this gateway."""
        payload = await self._request_json(
            "GET",
            FEATURES_PATH.format(gateway_id=self.gateway_id),
        )
        self.last_features_response = payload
        data = payload.get("data", payload)
        if not isinstance(data, dict):
            raise ElcoResponseError("Features returned invalid data")
        features = data.get("features", data)
        if not isinstance(features, dict):
            raise ElcoResponseError("Features did not contain a capability map")
        return features

    async def async_get_zone_numbers(
        self,
        features: dict[str, Any] | None = None,
    ) -> list[int]:
        """Fetch configured heating zone numbers, falling back to zone 1."""
        capability_map = features if features is not None else await self.async_get_features()
        zones = capability_map.get("zones", [])
        numbers: set[int] = set()
        for zone in zones:
            if not isinstance(zone, dict) or zone.get("isHidden", False):
                continue
            try:
                numbers.add(int(zone["num"]))
            except (KeyError, TypeError, ValueError):
                continue
        return sorted(numbers) or [1]

    async def async_get_system_items(
        self,
        features: dict[str, Any],
        zone_numbers: list[int],
    ) -> dict[str, dict[str, Any]]:
        """Fetch the safe scalar data items exposed by the mobile API."""
        items = [{"id": item_id, "zn": 0} for item_id in GLOBAL_DATA_ITEM_IDS]
        items.extend(
            {"id": item_id, "zn": zone_number}
            for zone_number in zone_numbers
            for item_id in ZONE_DATA_ITEM_IDS
        )
        payload = await self._request_payload(
            "POST",
            DATA_ITEMS_PATH.format(gateway_id=self.gateway_id),
            body={
                "useCache": False,
                "items": items,
                "features": features,
                "culture": "en",
            },
            retry_auth=False,
            invalidate_auth=False,
        )
        container = payload.get("data", payload) if isinstance(payload, dict) else payload
        if isinstance(container, list):
            returned_items = container
        elif isinstance(container, dict):
            returned_items = container.get("items", container.get("dataItems", []))
        else:
            returned_items = []
        if not isinstance(returned_items, list):
            raise ElcoResponseError("System data returned invalid items")

        result: dict[str, dict[str, Any]] = {}
        for item in returned_items:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                continue
            zone = item.get("zone", item.get("zn", 0))
            try:
                zone_number = int(zone)
            except (TypeError, ValueError):
                zone_number = 0
            result[f"{item['id']}:{zone_number}"] = item
        return result

    async def async_get_bsb_plant_data(self) -> dict[str, Any]:
        """Fetch the complete read-only BSB plant snapshot used by mobile clients."""
        payload = await self._request_payload(
            "GET",
            BSB_PLANT_DATA_PATH.format(gateway_id=self.gateway_id),
            retry_auth=False,
            invalidate_auth=False,
        )
        data = payload.get("data", payload) if isinstance(payload, dict) else payload
        if not isinstance(data, dict):
            raise ElcoResponseError("BSB plant data returned an unexpected payload")
        return data

    async def async_get_menu_items(
        self,
        item_ids: tuple[int, ...] = MENU_ITEM_BASE_IDS,
    ) -> dict[str, dict[str, Any]]:
        """Fetch all supported values from the bounded mobile menu-item catalog."""
        payload = await self._request_mobile_payload(
            MENU_ITEMS_PATH.format(
                gateway_id=self.gateway_id,
                item_ids=",".join(str(item_id) for item_id in item_ids),
            ),
        )
        items = payload.get("data", payload) if isinstance(payload, dict) else payload
        if not isinstance(items, list):
            raise ElcoResponseError("Menu items returned an unexpected payload")
        return {
            str(item["id"]): item
            for item in items
            if isinstance(item, dict) and item.get("id") is not None
        }

    async def async_get_plant_metadata(self) -> dict[str, Any]:
        """Fetch metadata, serial, and location from the native plant list."""
        payload = await self._request_payload(
            "GET",
            PLANTS_LITE_PATH,
            retry_auth=False,
            invalidate_auth=False,
        )
        plants = payload.get("data", payload) if isinstance(payload, dict) else payload
        if not isinstance(plants, list):
            raise ElcoResponseError("Plant metadata returned an unexpected payload")
        for plant in plants:
            if not isinstance(plant, dict):
                continue
            identifiers = {str(plant.get(key, "")).upper() for key in ("gwId", "gwSerial")}
            if self.gateway_id in identifiers:
                return plant
        raise ElcoResponseError("Configured gateway was not present in the plant list")

    async def async_get_schedule(self, program: str) -> Any:
        """Fetch one read-only weekly time program."""
        return await self._request_payload(
            "GET",
            TIME_PROGRAM_PATH.format(gateway_id=self.gateway_id, program=program),
            retry_auth=False,
            invalidate_auth=False,
        )

    async def async_get_metering(
        self,
        features: dict[str, Any],
        *,
        has_cooling: bool,
    ) -> Any:
        """Fetch read-only metering data when the plant supports it."""
        payload = await self._request_payload(
            "POST",
            METERING_PATH.format(gateway_id=self.gateway_id),
            body={"features": features, "hasCooling": has_cooling},
            retry_auth=False,
            invalidate_auth=False,
        )
        return payload.get("data", payload) if isinstance(payload, dict) else payload

    async def async_get_maintenance(self) -> Any:
        """Fetch read-only maintenance information."""
        payload = await self._request_payload(
            "GET",
            MAINTENANCE_PATH.format(gateway_id=self.gateway_id),
            retry_auth=False,
            invalidate_auth=False,
        )
        return payload.get("data", payload) if isinstance(payload, dict) else payload

    async def async_get_bus_errors(self) -> Any:
        """Fetch read-only controller error history."""
        return await self._request_payload(
            "GET",
            BUS_ERRORS_PATH.format(gateway_id=self.gateway_id),
            retry_auth=False,
            invalidate_auth=False,
        )

    async def async_get_bsb_points(
        self,
        addresses: tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        """Fetch the allowlisted read-only BSB parameters."""
        requested_addresses = addresses or BSB_DISCOVERY_ADDRESSES
        payload = await self._request_payload(
            "GET",
            BSB_READ_PATH.format(
                gateway_id=self.gateway_id,
                addresses=",".join(requested_addresses),
            ),
            retry_auth=False,
            invalidate_auth=False,
        )
        container = payload.get("data", payload) if isinstance(payload, dict) else payload
        if not isinstance(container, list):
            raise ElcoResponseError("BSB read returned invalid data")
        return {
            str(item["address"]): item
            for item in container
            if isinstance(item, dict) and item.get("address") is not None
        }

    async def async_get_data(
        self,
        zone_numbers: list[int] | None = None,
        *,
        use_cache: bool = True,
    ) -> ElcoData:
        """Fetch plant and zone state."""
        zones_to_fetch = zone_numbers or await self.async_get_zone_numbers()
        plant_raw: dict[str, Any] | None = None
        zones: dict[int, ZoneState] = {}
        raw_responses: list[dict[str, Any]] = []

        for index, zone_number in enumerate(zones_to_fetch):
            payload = await self._request_json(
                "POST",
                GET_DATA_PATH.format(gateway_id=self.gateway_id),
                body={
                    "useCache": use_cache and index > 0,
                    "zone": zone_number,
                    "filter": {"progIds": None, "plant": index == 0, "zone": True},
                },
            )
            raw_responses.append(payload)
            data = payload.get("data") or {}
            if not isinstance(data, dict):
                raise ElcoResponseError("GetData returned invalid data")
            if isinstance(data.get("plantData"), dict):
                plant_raw = data["plantData"]
            if isinstance(data.get("zoneData"), dict):
                zones[zone_number] = ZoneState.parse(zone_number, data["zoneData"])

        if plant_raw is None:
            raise ElcoResponseError("GetData did not return plant data")
        return ElcoData(
            self.gateway_id,
            PlantState.parse(plant_raw),
            zones,
            get_data_responses=raw_responses,
        )

    async def async_set_zone_temperatures(
        self,
        zone: ZoneState,
        *,
        comfort: float,
        reduced: float,
    ) -> None:
        """Write heating comfort and reduced temperatures as one atomic command."""
        zone.comfort_temperature.validate(comfort)
        zone.reduced_temperature.validate(reduced)
        if comfort < reduced:
            raise ValueError("Comfort temperature cannot be below reduced temperature")
        await self._request_json(
            "POST",
            SET_TEMPERATURE_PATH.format(gateway_id=self.gateway_id),
            body={
                "zoneNum": zone.number,
                "comfort": comfort,
                "reduced": reduced,
                "plantData": None,
                "zoneData": zone.raw,
            },
        )

    async def async_set_dhw(
        self,
        plant: PlantState,
        *,
        comfort: float,
        reduced: float,
        mode: int,
    ) -> None:
        """Write DHW temperatures and mode as one atomic command."""
        plant.dhw_comfort_temperature.validate(comfort)
        plant.dhw_reduced_temperature.validate(reduced)
        allowed_modes = {option.value for option in plant.dhw_mode.options}
        if allowed_modes and mode not in allowed_modes:
            raise ValueError(f"Unsupported DHW mode: {mode}")
        if comfort < reduced:
            raise ValueError("DHW comfort temperature cannot be below reduced temperature")
        await self._request_json(
            "POST",
            SAVE_DHW_PATH.format(gateway_id=self.gateway_id),
            body={
                "plantData": plant.raw,
                "comfortTemp": comfort,
                "reducedTemp": reduced,
                "dhwMode": mode,
            },
        )

    async def async_set_zone_mode(
        self,
        plant: PlantState,
        zone: ZoneState,
        *,
        mode: int,
    ) -> None:
        """Write a heating-zone mode while preserving required plant values."""
        allowed_modes = {option.value for option in zone.mode.options}
        if allowed_modes and mode not in allowed_modes:
            raise ValueError(f"Unsupported zone mode: {mode}")

        plant_data = deepcopy(plant.raw)
        zone_data = deepcopy(zone.raw)
        plant_data.setdefault("gatewayId", self.gateway_id)
        zone_data.setdefault("gatewayId", self.gateway_id)
        zone_data["zone"] = zone.number
        raw_mode = zone_data.get("mode")
        if not isinstance(raw_mode, dict):
            raw_mode = {}
            zone_data["mode"] = raw_mode
        raw_mode["value"] = mode

        await self._request_json(
            "POST",
            SET_DATA_PATH.format(gateway_id=self.gateway_id),
            body={
                "plantData": plant_data,
                "zoneData": zone_data,
                "viewModel": {"zoneNumber": zone.number},
            },
        )
