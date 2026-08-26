"""Asynchronous client for the ELCO Remocon R2 JSON endpoints."""

from __future__ import annotations

import asyncio
import logging
import re
from copy import deepcopy
from datetime import UTC, date, datetime, time
from email.utils import parsedate_to_datetime
from html import unescape
from math import isfinite
from typing import Any

from aiohttp import (
    ClientConnectorCertificateError,
    ClientConnectorSSLError,
    ClientError,
    ClientResponse,
    ClientSession,
    ClientSSLError,
    ClientTimeout,
)
from yarl import URL

from .bsb_controls import BSB_WRITABLE_ADDRESSES
from .const import (
    AUTOMATED_MONITORING_PATH,
    BSB_BOILER_DATA_PATH,
    BSB_DISCOVERY_ADDRESSES,
    BSB_PLANT_DATA_PATH,
    BSB_READ_PATH,
    BSB_TIME_PROGRAM_IDS,
    BSB_TIME_PROGRAM_PATH,
    BSB_WRITE_PATH,
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
    PLANT_HEADER_PATH,
    PLANT_USER_DATA_PATH,
    PLANTS_LITE_PATH,
    REQUEST_TIMEOUT,
    SAVE_DHW_PATH,
    SET_DATA_PATH,
    SET_TEMPERATURE_PATH,
    USER_AGENT,
    ZONE_DATA_ITEM_IDS,
)
from .control_mapping import ZONE_MODE_AUTOMATIC
from .models import ElcoData, PlantState, ZoneState, bsb_point_available

_LOGGER = logging.getLogger(__name__)

_CONNECT_TIMEOUT_SECONDS = 15
_SOCKET_READ_TIMEOUT_SECONDS = 65
_SAFE_READ_ATTEMPTS = 2
_SAFE_READ_RETRY_DELAY_SECONDS = 1
_RETRYABLE_READ_STATUSES = frozenset({408, 500, 502, 503, 504})
_MAX_RETRY_AFTER_SECONDS = 86400

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

    def __init__(
        self,
        message: str,
        *,
        timed_out: bool = False,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.timed_out = timed_out
        self.retryable = retryable


class ElcoResponseError(ElcoApiError):
    """The Remocon service returned an unexpected response."""

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        retry_after: float | None = None,
        ambiguous: bool = False,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after
        self.ambiguous = ambiguous


def _retry_after_seconds(response: ClientResponse) -> float | None:
    """Return a bounded Retry-After delay from an HTTP response."""
    value = response.headers.get("Retry-After")
    if value is None:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
        except (TypeError, ValueError, OverflowError):
            return None
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=UTC)
        seconds = (retry_at - datetime.now(UTC)).total_seconds()
    if not isfinite(seconds):
        return None
    return min(_MAX_RETRY_AFTER_SECONDS, max(1.0, seconds))


def _is_retryable_read_error(error: ElcoApiError) -> bool:
    """Return whether a failed read can be repeated immediately."""
    if isinstance(error, ElcoConnectionError):
        return error.retryable
    if not isinstance(error, ElcoResponseError) or error.retry_after is not None:
        return False
    if error.status in _RETRYABLE_READ_STATUSES:
        return True
    return error.status is None and "communication error" in str(error).casefold()


def _is_retryable_client_error(error: ClientError | TimeoutError) -> bool:
    """Return whether an immediate repeat can plausibly recover a transport failure."""
    return not isinstance(
        error,
        (TimeoutError, ClientSSLError, ClientConnectorSSLError, ClientConnectorCertificateError),
    )


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
        self._timeout = ClientTimeout(
            total=REQUEST_TIMEOUT,
            connect=_CONNECT_TIMEOUT_SECONDS,
            sock_connect=_CONNECT_TIMEOUT_SECONDS,
            sock_read=_SOCKET_READ_TIMEOUT_SECONDS,
        )
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
                    if response.status in (401, 403):
                        raise ElcoAuthenticationError("Remocon login was rejected")
                    if response.status != 200:
                        raise ElcoResponseError(
                            f"Login page returned HTTP {response.status}",
                            status=response.status,
                            retry_after=_retry_after_seconds(response),
                        )
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
                raise ElcoConnectionError(
                    "Unable to connect to Remocon",
                    timed_out=isinstance(err, TimeoutError),
                    retryable=_is_retryable_client_error(err),
                ) from err

    async def _decode_json(self, response: ClientResponse, operation: str) -> Any:
        if response.status in (401, 403):
            raise ElcoAuthenticationError("Remocon session expired")
        if response.status >= 400:
            raise ElcoResponseError(
                f"{operation} returned HTTP {response.status}",
                status=response.status,
                retry_after=_retry_after_seconds(response),
            )
        try:
            payload = await response.json(content_type=None)
        except (ValueError, TypeError) as err:
            text = await response.text()
            if "account/login" in text.lower() or 'id="loginform"' in text.lower():
                raise ElcoAuthenticationError("Remocon session expired") from err
            raise ElcoResponseError(
                f"{operation} did not return JSON",
                ambiguous=True,
            ) from err
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
                message = str(payload.get("message") or f"{path} failed")
                raise ElcoResponseError(
                    message,
                    ambiguous="communication error" in message.casefold(),
                )
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
            raise ElcoConnectionError(
                f"Unable to communicate with Remocon: {path}",
                timed_out=isinstance(err, TimeoutError),
                retryable=_is_retryable_client_error(err),
            ) from err

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
            raise ElcoResponseError(
                f"{path} returned an unexpected payload",
                ambiguous=True,
            )
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
                raise ElcoConnectionError(
                    "Unable to connect to the Remocon mobile API",
                    timed_out=isinstance(err, TimeoutError),
                    retryable=_is_retryable_client_error(err),
                ) from err

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
            raise ElcoConnectionError(
                f"Unable to communicate with Remocon: {path}",
                timed_out=isinstance(err, TimeoutError),
                retryable=_is_retryable_client_error(err),
            ) from err

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

    async def async_get_plant_header(self) -> dict[str, Any]:
        """Fetch live connectivity, model, and fault summary metadata."""
        payload = await self._request_payload(
            "GET",
            PLANT_HEADER_PATH.format(gateway_id=self.gateway_id),
            retry_auth=False,
            invalidate_auth=False,
        )
        data = payload.get("data", payload) if isinstance(payload, dict) else payload
        if not isinstance(data, dict):
            raise ElcoResponseError("Plant header returned an unexpected payload")
        return data

    async def async_get_plant_user_data(self) -> dict[str, Any]:
        """Fetch the read-only plant owner and account-language metadata."""
        payload = await self._request_payload(
            "GET",
            PLANT_USER_DATA_PATH.format(gateway_id=self.gateway_id),
            retry_auth=False,
            invalidate_auth=False,
        )
        data = payload.get("data", payload) if isinstance(payload, dict) else payload
        if not isinstance(data, dict):
            raise ElcoResponseError("Plant user data returned an unexpected payload")
        return data

    async def async_get_schedule(self, program: str) -> Any:
        """Fetch one read-only weekly time program."""
        program_id = BSB_TIME_PROGRAM_IDS.get(program)
        if program_id is None:
            raise ElcoResponseError(f"Unsupported BSB time program: {program}")
        is_dhw = program == "Dhw"
        zone = 0 if is_dhw or program == "Extra" else int(re.search(r"\d+$", program).group())
        payload = await self._request_payload(
            "POST",
            BSB_TIME_PROGRAM_PATH.format(gateway_id=self.gateway_id),
            body={
                "zone": zone,
                "filter": {
                    "progIds": [program_id],
                    "plant": is_dhw,
                    "zone": not is_dhw,
                },
                "useCache": True,
            },
            retry_auth=False,
            invalidate_auth=False,
        )
        return payload.get("data", payload) if isinstance(payload, dict) else payload

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

    async def async_get_automated_monitoring(self) -> Any:
        """Fetch structured predictive-maintenance and appliance-health data."""
        payload = await self._request_payload(
            "GET",
            AUTOMATED_MONITORING_PATH.format(gateway_id=self.gateway_id),
            retry_auth=False,
            invalidate_auth=False,
        )
        return payload.get("data", payload) if isinstance(payload, dict) else payload

    async def async_get_bsb_boiler_data(self) -> Any:
        """Fetch structured BSB appliance identification and boiler data."""
        payload = await self._request_payload(
            "GET",
            BSB_BOILER_DATA_PATH.format(gateway_id=self.gateway_id),
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
        *,
        command: bool = False,
    ) -> dict[str, Any]:
        """Fetch BSB parameters, with bounded resilience for command reads."""
        requested_addresses = addresses or BSB_DISCOVERY_ADDRESSES
        if not command:
            return await self._async_get_bsb_points_once(
                requested_addresses,
                retry_auth=False,
            )

        loop = asyncio.get_running_loop()
        deadline = loop.time() + REQUEST_TIMEOUT
        last_error: ElcoApiError | None = None
        for attempt in range(_SAFE_READ_ATTEMPTS):
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            try:
                async with asyncio.timeout(remaining):
                    return await self._async_get_bsb_points_once(
                        requested_addresses,
                        retry_auth=True,
                    )
            except TimeoutError as err:
                raise ElcoConnectionError(
                    "BSB command read exceeded its overall time limit",
                    timed_out=True,
                ) from err
            except (ElcoConnectionError, ElcoResponseError) as err:
                last_error = err
                if (
                    attempt == _SAFE_READ_ATTEMPTS - 1
                    or not _is_retryable_read_error(err)
                    or deadline - loop.time() <= _SAFE_READ_RETRY_DELAY_SECONDS
                ):
                    raise
                _LOGGER.debug("Retrying Remocon BSB command read after: %s", err)
                await asyncio.sleep(_SAFE_READ_RETRY_DELAY_SECONDS)

        if last_error is not None:
            raise last_error
        raise ElcoConnectionError(
            "BSB command read exceeded its overall time limit",
            timed_out=True,
        )

    async def _async_get_bsb_points_once(
        self,
        requested_addresses: tuple[str, ...],
        *,
        retry_auth: bool,
    ) -> dict[str, Any]:
        """Fetch BSB parameters once with the requested authentication policy."""
        payload = await self._request_payload(
            "GET",
            BSB_READ_PATH.format(
                gateway_id=self.gateway_id,
                addresses=",".join(requested_addresses),
            ),
            retry_auth=retry_auth,
            invalidate_auth=retry_auth,
        )
        container = payload.get("data", payload) if isinstance(payload, dict) else payload
        if not isinstance(container, list):
            raise ElcoResponseError("BSB read returned invalid data")
        return {
            str(item["address"]): item
            for item in container
            if isinstance(item, dict) and item.get("address") is not None
        }

    async def async_write_bsb_point(self, point: dict[str, Any], value: float | int) -> None:
        """Write one reviewed BSB datapoint using Remocon's compare-and-set DTO."""
        address = str(point.get("address", ""))
        if address not in BSB_WRITABLE_ADDRESSES:
            raise ValueError(f"BSB address {address or '<missing>'} is not writable")
        if not bsb_point_available(point):
            raise ValueError(f"BSB address {address} is unavailable")
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ValueError("BSB value must be numeric")
        if not isfinite(value):
            raise ValueError("BSB value must be finite")

        payload = await self._request_json(
            "POST",
            BSB_WRITE_PATH.format(gateway_id=self.gateway_id),
            body=[
                {
                    "address": int(address),
                    "oldOsv": bool(point.get("osv", False)),
                    "oldValueAsString": point.get("valueAsString"),
                    "oldValueAsNumber": point.get("valueAsNumber"),
                    "newOsv": False,
                    "newValueAsString": None,
                    "newValueAsNumber": value,
                }
            ],
        )
        errors = payload.get("data")
        if isinstance(errors, list) and errors:
            error = next(
                (
                    item
                    for item in errors
                    if isinstance(item, dict) and str(item.get("address")) == address
                ),
                errors[0],
            )
            if isinstance(error, dict):
                code = error.get("bsbErrorCode") or error.get("commErrorCode")
                detail = f" (controller error {code})" if code not in (None, 0, "0") else ""
            else:
                detail = ""
            raise ElcoResponseError(f"BSB address {address} rejected the write{detail}")

    async def async_get_data(
        self,
        zone_numbers: list[int] | None = None,
        *,
        use_cache: bool = True,
    ) -> ElcoData:
        """Fetch one complete plant/zone snapshot within a bounded deadline."""
        zones_to_fetch = zone_numbers or await self.async_get_zone_numbers()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + REQUEST_TIMEOUT * max(1, len(zones_to_fetch))
        last_error: ElcoApiError | None = None

        for attempt in range(_SAFE_READ_ATTEMPTS):
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            try:
                async with asyncio.timeout(remaining):
                    return await self._async_get_data_once(zones_to_fetch, use_cache=use_cache)
            except TimeoutError as err:
                raise ElcoConnectionError(
                    "GetData operation exceeded its overall time limit",
                    timed_out=True,
                ) from err
            except (ElcoConnectionError, ElcoResponseError) as err:
                last_error = err
                if (
                    attempt == _SAFE_READ_ATTEMPTS - 1
                    or not _is_retryable_read_error(err)
                    or deadline - loop.time() <= _SAFE_READ_RETRY_DELAY_SECONDS
                ):
                    raise
                _LOGGER.debug("Retrying complete Remocon GetData after: %s", err)
                await asyncio.sleep(_SAFE_READ_RETRY_DELAY_SECONDS)

        if last_error is not None:
            raise last_error
        raise ElcoConnectionError(
            "GetData operation exceeded its overall time limit",
            timed_out=True,
        )

    async def _async_get_data_once(
        self,
        zones_to_fetch: list[int],
        *,
        use_cache: bool,
    ) -> ElcoData:
        """Fetch plant and zone state once without retrying a partial snapshot."""
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
        cooling: bool = False,
    ) -> None:
        """Write active heating or cooling temperatures as one atomic command."""
        active_cooling = zone.cooling_active is True
        if cooling != active_cooling:
            requested = "Cooling" if cooling else "Heating"
            active = "cooling" if active_cooling else "heating"
            raise ValueError(
                f"{requested} temperatures can only be changed while the zone is in {active} mode"
            )
        comfort_variable = zone.cooling_comfort_temperature if cooling else zone.comfort_temperature
        reduced_variable = zone.cooling_reduced_temperature if cooling else zone.reduced_temperature
        comfort_variable.validate(comfort)
        reduced_variable.validate(reduced)
        if cooling and comfort > reduced:
            raise ValueError("Cooling comfort temperature cannot exceed reduced temperature")
        if not cooling and comfort < reduced:
            raise ValueError("Heating comfort temperature cannot be below reduced temperature")
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

        plant_data, zone_data = self._set_data_snapshots(plant, zone)
        raw_mode = zone_data.get("mode")
        if not isinstance(raw_mode, dict):
            raw_mode = {}
            zone_data["mode"] = raw_mode
        raw_mode["value"] = mode
        await self._post_set_data(plant_data, zone_data, zone.number)

    async def async_set_zone_holiday(
        self,
        plant: PlantState,
        zone: ZoneState,
        *,
        ends_on: date,
        starts_at: datetime,
    ) -> None:
        """Create or update Remocon's current holiday and force Automatic mode."""
        if starts_at.tzinfo is None or starts_at.utcoffset() is None:
            raise ValueError("Holiday start time must include a time zone")
        if ends_on < starts_at.date():
            raise ValueError("Holiday final day cannot be in the past")
        allowed_modes = {option.value for option in zone.mode.options}
        if allowed_modes and ZONE_MODE_AUTOMATIC not in allowed_modes:
            raise ValueError("Automatic zone mode is unavailable")

        plant_data, zone_data = self._set_data_snapshots(plant, zone)
        holidays = zone_data.get("holidays")
        if not isinstance(holidays, list):
            raise ValueError("Holiday periods are unavailable")
        current = self._current_raw_holiday(holidays)
        final_at = datetime.combine(ends_on, time.min, tzinfo=starts_at.tzinfo)
        if current is None:
            if any(
                isinstance(holiday, dict)
                and holiday.get("deleted") is not True
                and holiday.get("osv") is True
                for holiday in holidays
            ):
                raise ValueError("Inactive holiday-slot reuse has not been verified")
            holidays.append(
                {
                    "index": len(holidays),
                    "fromAsEpoch": 0,
                    "toAsEpoch": 0,
                    "fromAsIso": starts_at.replace(microsecond=0).isoformat(),
                    "toAsIso": final_at.isoformat(),
                    "added": True,
                    "deleted": False,
                    "changed": False,
                    "osv": False,
                }
            )
        else:
            current["changed"] = True
            current["toAsIso"] = final_at.isoformat()

        raw_mode = zone_data.get("mode")
        if not isinstance(raw_mode, dict):
            raw_mode = {}
            zone_data["mode"] = raw_mode
        raw_mode["value"] = ZONE_MODE_AUTOMATIC
        await self._post_set_data(plant_data, zone_data, zone.number)

    async def async_cancel_zone_holiday(
        self,
        plant: PlantState,
        zone: ZoneState,
    ) -> None:
        """Mark Remocon's current holiday deleted while preserving the zone mode."""
        plant_data, zone_data = self._set_data_snapshots(plant, zone)
        holidays = zone_data.get("holidays")
        if not isinstance(holidays, list):
            raise ValueError("Holiday periods are unavailable")
        current = self._current_raw_holiday(holidays)
        if current is None:
            raise ValueError("No current holiday to cancel")
        current["deleted"] = True
        await self._post_set_data(plant_data, zone_data, zone.number)

    def _set_data_snapshots(
        self,
        plant: PlantState,
        zone: ZoneState,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Copy and complete the plant/zone snapshots required by SetData."""
        plant_data = deepcopy(plant.raw)
        zone_data = deepcopy(zone.raw)
        plant_data.setdefault("gatewayId", self.gateway_id)
        zone_data.setdefault("gatewayId", self.gateway_id)
        zone_data["zone"] = zone.number
        return plant_data, zone_data

    @staticmethod
    def _current_raw_holiday(holidays: list[Any]) -> dict[str, Any] | None:
        """Return the first usable raw period, matching the Remocon page model."""
        return next(
            (
                holiday
                for holiday in holidays
                if isinstance(holiday, dict)
                and holiday.get("deleted") is not True
                and holiday.get("osv") is not True
            ),
            None,
        )

    async def _post_set_data(
        self,
        plant_data: dict[str, Any],
        zone_data: dict[str, Any],
        zone_number: int,
    ) -> None:
        """Send one complete PlantHomeBsb SetData command."""
        await self._request_json(
            "POST",
            SET_DATA_PATH.format(gateway_id=self.gateway_id),
            body={
                "plantData": plant_data,
                "zoneData": zone_data,
                "viewModel": {"zoneNumber": zone_number},
            },
        )
