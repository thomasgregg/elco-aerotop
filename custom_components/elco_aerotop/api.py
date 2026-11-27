"""Asynchronous client for the ELCO Remocon R2 JSON endpoints."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from copy import deepcopy
from datetime import UTC, date, datetime, time
from email.utils import parsedate_to_datetime
from functools import partial
from html import unescape
from math import isfinite
from typing import Any, TypeVar

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
    BSB_GLOBAL_DATA_ITEM_IDS,
    BSB_PLANT_DATA_PATH,
    BSB_READ_PATH,
    BSB_TIME_PROGRAM_IDS,
    BSB_TIME_PROGRAM_PATH,
    BSB_WRITE_PATH,
    BSB_ZONE_DATA_ITEM_IDS,
    BUS_ERRORS_PATH,
    DATA_ITEMS_PATH,
    FEATURES_PATH,
    GALEVO_BUS_DEVICES_PATH,
    GALEVO_CONS_SEQUENCES_PATH,
    GALEVO_DATA_ITEMS_SET_PATH,
    GALEVO_DHW_MODE_PATH,
    GALEVO_DHW_SCHEDULED_COMFORT_WRITE_ENABLED,
    GALEVO_DHW_TEMP_PATH,
    GALEVO_DHW_TIME_PROG_TEMPERATURES_PATH,
    GALEVO_GLOBAL_DATA_ITEM_IDS,
    GALEVO_HOLIDAY_PATH,
    GALEVO_HOME_DATA_PATH,
    GALEVO_PLANT_MODE_PATH,
    GALEVO_PLANT_MODE_WRITE_ENABLED,
    GALEVO_SYSTEM_STATE_PATH,
    GALEVO_TIME_PROG_PATH,
    GALEVO_WRITES_ENABLED,
    GALEVO_ZONE_DATA_ITEM_IDS,
    GALEVO_ZONE_MODE_PATH,
    GALEVO_ZONE_PATH,
    GALEVO_ZONE_TEMPERATURES_PATH,
    GET_DATA_PATH,
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
    SYSTEM_TYPE_GALEVO,
    USER_AGENT,
)
from .control_mapping import ZONE_MODE_AUTOMATIC
from .gateway import normalize_gateway_id, plant_gateway_ids
from .models import (
    GALEVO_DHW_MODE_TO_RAW,
    GALEVO_ZONE_MODE_TO_RAW,
    ElcoData,
    NumericVariable,
    PlantState,
    ZoneState,
    bsb_point_available,
    controller_system_type,
    galevo_item_available,
    galevo_item_writable,
)

_LOGGER = logging.getLogger(__name__)

_CONNECT_TIMEOUT_SECONDS = 15
_SOCKET_READ_TIMEOUT_SECONDS = 65
_SAFE_READ_ATTEMPTS = 2
_T = TypeVar("_T")
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


class ElcoGalevoPlantDataError(ElcoResponseError):
    """A Galevo plant read returned no usable data."""


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
        # The empty value is used only to list plants before a gateway is selected.
        self.gateway_id = normalize_gateway_id(gateway_id) if gateway_id else ""
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
        self.last_features: dict[str, Any] | None = None
        self.last_system_items: dict[str, dict[str, Any]] = {}

    @property
    def system_type(self) -> int | None:
        """Return the controller family reported by the last Features response."""
        return controller_system_type(self.last_features_response)

    def _require_supported_system(self) -> None:
        """Reject controller-family write paths that are not verified for this gateway."""
        if self.system_type == SYSTEM_TYPE_GALEVO:
            raise ValueError("This control is not yet supported for Galevo gateways")

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

    def _operation(self, path: str) -> str:
        """Identify an endpoint without including its gateway identifier."""
        if not self.gateway_id:
            return path
        return re.sub(
            rf"(/|[?&](?:id|gatewayId)=){re.escape(self.gateway_id)}(?=[/?&]|$)",
            r"\1<gateway>",
            path,
        )

    async def async_login(self) -> None:
        """Create an authenticated R2 cookie session."""
        async with self._auth_lock:
            if self._authenticated:
                return
            _LOGGER.debug("Remocon R2 login started")
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
                    raise ElcoAuthenticationError("Wrong username or password")
                self._authenticated = True
                _LOGGER.debug("Remocon R2 login succeeded")
            except ElcoApiError as err:
                self._authenticated = False
                _LOGGER.debug("Remocon R2 login failed: %s", type(err).__name__)
                raise
            except (ClientError, TimeoutError) as err:
                self._authenticated = False
                _LOGGER.debug("Remocon R2 login transport failure: %s", type(err).__name__)
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

    @staticmethod
    def _check_response(payload: Any, operation: str) -> None:
        if not isinstance(payload, dict):
            return
        if payload.get("ok") is False or payload.get("success") is False:
            if "communication error" in str(payload.get("message")).casefold():
                raise ElcoResponseError("Communication error", ambiguous=True)
            raise ElcoResponseError(f"Remocon rejected {operation}")

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
        operation = self._operation(path)
        _LOGGER.debug("Remocon %s %s", method, operation)
        try:
            response = await self._session.request(
                method,
                self._url(path),
                headers=self._json_headers,
                json=body,
                timeout=self._timeout,
            )
            async with response:
                payload = await self._decode_json(response, operation)
            self._check_response(payload, operation)
            _LOGGER.debug("Remocon %s %s -> HTTP %s", method, operation, response.status)
            return payload
        except ElcoAuthenticationError:
            _LOGGER.debug("Remocon %s %s -> authentication rejected", method, operation)
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
        except ElcoApiError as err:
            _LOGGER.debug("Remocon %s %s failed: %s", method, operation, type(err).__name__)
            raise
        except (ClientError, TimeoutError) as err:
            _LOGGER.debug(
                "Remocon %s %s transport failure: %s", method, operation, type(err).__name__
            )
            raise ElcoConnectionError(
                f"Unable to communicate with Remocon: {operation}",
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
                f"{self._operation(path)} returned an unexpected payload",
                ambiguous=True,
            )
        return payload

    async def _async_mobile_login(self) -> None:
        """Create the separate token session required by mobile-only endpoints."""
        async with self._mobile_auth_lock:
            if self._mobile_token is not None:
                return
            _LOGGER.debug("Remocon mobile login started")
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
                _LOGGER.debug("Remocon mobile login succeeded")
            except ElcoApiError as err:
                self._mobile_token = None
                _LOGGER.debug("Remocon mobile login failed: %s", type(err).__name__)
                raise
            except (ClientError, TimeoutError) as err:
                self._mobile_token = None
                _LOGGER.debug("Remocon mobile login transport failure: %s", type(err).__name__)
                raise ElcoConnectionError(
                    "Unable to connect to the Remocon mobile API",
                    timed_out=isinstance(err, TimeoutError),
                    retryable=_is_retryable_client_error(err),
                ) from err

    async def _request_mobile_payload(
        self,
        path: str,
        *,
        method: str = "GET",
        body: dict[str, Any] | list[Any] | None = None,
        retry_auth: bool = True,
    ) -> Any:
        """Make one token-authenticated mobile API request."""
        if self._mobile_token is None:
            await self._async_mobile_login()
        token = self._mobile_token
        operation = self._operation(path)
        _LOGGER.debug("Remocon mobile %s %s", method, operation)
        try:
            response = await self._session.request(
                method,
                self._url(path),
                headers={**self._json_headers, "ar.authToken": token or ""},
                json=body,
                timeout=self._timeout,
            )
            async with response:
                payload = await self._decode_json(response, operation)
            self._check_response(payload, operation)
            _LOGGER.debug("Remocon mobile %s %s -> HTTP %s", method, operation, response.status)
            return payload
        except ElcoAuthenticationError:
            _LOGGER.debug("Remocon mobile %s %s -> token rejected", method, operation)
            if self._mobile_token == token:
                self._mobile_token = None
            if not retry_auth:
                raise
            await self._async_mobile_login()
            return await self._request_mobile_payload(
                path,
                method=method,
                body=body,
                retry_auth=False,
            )
        except ElcoApiError as err:
            _LOGGER.debug("Remocon mobile %s %s failed: %s", method, operation, type(err).__name__)
            raise
        except (ClientError, TimeoutError) as err:
            _LOGGER.debug(
                "Remocon mobile %s %s transport failure: %s", method, operation, type(err).__name__
            )
            raise ElcoConnectionError(
                f"Unable to communicate with Remocon: {operation}",
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
        self.last_features = features
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
        culture: str = "en",
    ) -> dict[str, dict[str, Any]]:
        """Fetch the safe scalar data items exposed by the mobile API.

        Requests the Galevo data-item catalog (``GALEVO_*_DATA_ITEM_IDS``).
        This is the authoritative read surface on Galevo; on BSB gateways it
        runs as an optional discovery probe — unsupported items are omitted.

        ``culture`` controls server-side localization of text values such as
        ``ZoneName``; the APK sends the app language
        (``Locale.getDefault().getLanguage()``).
        """
        galevo = self.system_type == SYSTEM_TYPE_GALEVO
        global_ids = GALEVO_GLOBAL_DATA_ITEM_IDS if galevo else BSB_GLOBAL_DATA_ITEM_IDS
        zone_ids = GALEVO_ZONE_DATA_ITEM_IDS if galevo else BSB_ZONE_DATA_ITEM_IDS
        items = [{"id": item_id, "zn": 0} for item_id in global_ids]
        items.extend(
            {"id": item_id, "zn": zone_number}
            for zone_number in zone_numbers
            for item_id in zone_ids
        )
        payload = await self._request_payload(
            "POST",
            DATA_ITEMS_PATH.format(gateway_id=self.gateway_id),
            body={
                "useCache": False,
                "items": items,
                "features": features,
                "culture": culture if galevo else "en",
            },
            retry_auth=galevo,
            invalidate_auth=galevo,
        )
        container = payload.get("data", payload) if isinstance(payload, dict) else payload
        if isinstance(container, list):
            returned_items = container
        elif isinstance(container, dict):
            returned_items = container.get("items", container.get("dataItems", []))
        else:
            returned_items = []
        if not isinstance(returned_items, list):
            if galevo:
                raise ElcoGalevoPlantDataError("Galevo system data returned invalid items")
            raise ElcoResponseError("System data returned invalid items")

        result: dict[str, dict[str, Any]] = {}
        for item in returned_items:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                continue
            zone = item.get("zone", item.get("zn", 0))
            try:
                zone_number = int(zone)
            except (TypeError, ValueError, OverflowError):
                continue
            if item["id"] not in (global_ids if zone_number == 0 else zone_ids):
                continue
            if zone_number != 0 and zone_number not in zone_numbers:
                continue
            result[f"{item['id']}:{zone_number}"] = item
        if galevo and not result:
            raise ElcoGalevoPlantDataError("Galevo system data contained no requested items")
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

    async def async_get_galevo_zone(self, zone_number: int) -> dict[str, Any]:
        """Fetch one Galevo zone state from the mobile zones endpoint."""
        payload = await self._request_mobile_payload(
            GALEVO_ZONE_PATH.format(gateway_id=self.gateway_id, zone=zone_number),
        )
        data = payload.get("data", payload) if isinstance(payload, dict) else payload
        if not isinstance(data, dict) or not any(
            field in data for field in ("mod", "comf", "econ", "room", "desired", "heat")
        ):
            raise ElcoResponseError("Galevo zone data returned an unexpected payload")
        return data

    async def async_get_galevo_system_state(self) -> dict[str, Any]:
        """Fetch the Galevo plant system state (pilot zones)."""
        payload = await self._request_mobile_payload(
            GALEVO_SYSTEM_STATE_PATH.format(gateway_id=self.gateway_id),
        )
        data = payload.get("data", payload) if isinstance(payload, dict) else payload
        if not isinstance(data, dict):
            raise ElcoResponseError("Galevo system state returned an unexpected payload")
        return data

    async def async_get_galevo_time_prog(self, program: str) -> Any:
        """Fetch one Galevo weekly time program (same eProgId values as BSB)."""
        program_id = BSB_TIME_PROGRAM_IDS.get(program)
        if program_id is None:
            raise ElcoResponseError(f"Unsupported time program: {program}")
        payload = await self._request_mobile_payload(
            GALEVO_TIME_PROG_PATH.format(
                gateway_id=self.gateway_id,
                program_id=program_id,
            ),
        )
        return payload.get("data", payload) if isinstance(payload, dict) else payload

    async def async_get_galevo_home_items(
        self,
        features: dict[str, Any],
        zone_number: int,
    ) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
        """Fetch the Galevo home-screen item set for one zone.

        ``POST /R2/PlantHome/GetData`` is the R2 counterpart of the BSB
        snapshot: it returns the same data-item shape as the mobile
        ``dataItems`` endpoint but with localized ``optTexts``, real
        min/max bounds and ``readOnly`` flags, plus a few items the mobile
        catalog omits (IsQuite, IsDhwBoost, IsResistorOn, cooling setpoints).
        Returns the items keyed ``<id>:<zone>`` and the raw data block.
        """
        payload = await self._request_payload(
            "POST",
            GALEVO_HOME_DATA_PATH.format(gateway_id=self.gateway_id),
            body={
                "features": {**features, "gatewayId": self.gateway_id},
                "filter": {
                    "notEssentials": True,
                    "plant": True,
                    "zone": True,
                    "dhw": True,
                },
                "useCache": True,
                "zone": zone_number,
            },
            retry_auth=False,
            invalidate_auth=False,
        )
        data = payload.get("data", payload) if isinstance(payload, dict) else payload
        items_list = data.get("items") if isinstance(data, dict) else None
        if not isinstance(items_list, list):
            raise ElcoResponseError("Galevo home data returned an unexpected payload")
        items = {
            f"{item['id']}:{item.get('zone', 0)}": item
            for item in items_list
            if isinstance(item, dict) and item.get("id") is not None
        }
        return items, data

    async def async_get_galevo_bus_devices(self) -> list[dict[str, Any]]:
        """Fetch the Galevo bus-device inventory (names, serials, categories)."""
        payload = await self._request_mobile_payload(
            GALEVO_BUS_DEVICES_PATH.format(gateway_id=self.gateway_id),
        )
        data = payload.get("data", payload) if isinstance(payload, dict) else payload
        if not isinstance(data, list):
            raise ElcoResponseError("Galevo bus devices returned an unexpected payload")
        return [item for item in data if isinstance(item, dict)]

    async def _async_read_with_retry(
        self,
        what: str,
        fetch: Callable[[], Awaitable[_T]],
        *,
        deadline_seconds: float = REQUEST_TIMEOUT,
    ) -> _T:
        """Run a read with the same bounded retry policy as BSB GetData."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + deadline_seconds
        last_error: ElcoApiError | None = None
        for attempt in range(_SAFE_READ_ATTEMPTS):
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            try:
                async with asyncio.timeout(remaining):
                    return await fetch()
            except TimeoutError as err:
                raise ElcoConnectionError(
                    f"{what} exceeded its overall time limit",
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
                _LOGGER.debug("Retrying %s after: %s", what, err)
                await asyncio.sleep(_SAFE_READ_RETRY_DELAY_SECONDS)
        if last_error is not None:
            raise last_error
        raise ElcoConnectionError(
            f"{what} exceeded its overall time limit",
            timed_out=True,
        )

    @staticmethod
    def _require_galevo_writes(*, enabled: bool | None = None) -> None:
        """Reject a Galevo operation unless its write gate is enabled."""
        if not (GALEVO_WRITES_ENABLED if enabled is None else enabled):
            raise ValueError("Galevo writes are disabled for this control")

    async def _async_galevo_change_value(
        self,
        path: str,
        new_value: Any,
        old_value: Any,
        *,
        write_enabled: bool | None = None,
    ) -> Any:
        """POST a Galevo ``ChangeValueDto`` ({new, old}) request.

        Contract verified from the APK proxies (ZoneProxy, PlantDataProxy):
        every scalar/enum write serializes both values so the gateway can
        detect concurrent changes. The DTO's wire names are ``new``/``old``
        (kotlinx @SerialName in ChangeValueDtoSerializer), not the Kotlin
        property names ``newValue``/``oldValue`` — sending the long names
        makes the server deserialize nulls (HTTP 500, or an acknowledged
        no-op as observed for plantData/mode).
        """
        self._require_galevo_writes(enabled=write_enabled)
        payload = await self._request_mobile_payload(
            path,
            method="POST",
            body={"new": new_value, "old": old_value},
        )
        return payload.get("data", payload) if isinstance(payload, dict) else payload

    async def async_set_galevo_plant_mode(
        self,
        mode: int,
        *,
        old_mode: int,
    ) -> Any:
        """Set the plant mode via ``plantData/{gw}/mode`` (raw ePlantMode).

        Live evidence 2026-11-19: with the correct ``new``/``old`` wire
        names this route applies the mode — verified Winter→Summer→Winter
        on a Galevo gateway. An earlier no-op observation was caused by
        sending the Kotlin property names ``newValue``/``oldValue``, which
        the server deserialized as an empty change.
        """
        return await self._async_galevo_change_value(
            GALEVO_PLANT_MODE_PATH.format(gateway_id=self.gateway_id),
            mode,
            old_mode,
            write_enabled=GALEVO_PLANT_MODE_WRITE_ENABLED,
        )

    async def async_set_galevo_zone_mode(
        self,
        zone_number: int,
        mode: int,
        *,
        old_mode: int,
    ) -> Any:
        """Set a zone mode via ``POST zones/{gw}/{zn}/mode`` (raw eZoneMode)."""
        return await self._async_galevo_change_value(
            GALEVO_ZONE_MODE_PATH.format(
                gateway_id=self.gateway_id,
                zone=zone_number,
            ),
            mode,
            old_mode,
        )

    async def async_set_galevo_zone_temperatures(
        self,
        zone_number: int,
        *,
        comfort: float,
        economy: float,
        old_comfort: float,
        old_economy: float,
    ) -> Any:
        """Set zone heating comfort/economy via ``zones/{gw}/{zn}/temperatures``.

        APK-verified contract (ZoneProxy.setTemperatures): both values go in
        one ``TemperaturesDto`` pair wrapped in ``ChangeValueDto``. The DTO
        serializes its fields as ``comf``/``econ`` (kotlinx @SerialName), not
        the full field names.
        """
        return await self._async_galevo_change_value(
            GALEVO_ZONE_TEMPERATURES_PATH.format(
                gateway_id=self.gateway_id,
                zone=zone_number,
            ),
            {"comf": comfort, "econ": economy},
            {"comf": old_comfort, "econ": old_economy},
        )

    async def async_set_galevo_dhw_temp(
        self,
        temperature: float,
        *,
        old_temperature: float,
    ) -> Any:
        """Set the always-on DHW setpoint via ``plantData/{gw}/dhwTemp``."""
        return await self._async_galevo_change_value(
            GALEVO_DHW_TEMP_PATH.format(gateway_id=self.gateway_id),
            temperature,
            old_temperature,
        )

    async def async_set_galevo_dhw_mode(
        self,
        mode: int,
        *,
        old_mode: int,
    ) -> Any:
        """Set the DHW mode via ``plantData/{gw}/dhwMode`` (raw eDhwMode)."""
        return await self._async_galevo_change_value(
            GALEVO_DHW_MODE_PATH.format(gateway_id=self.gateway_id),
            mode,
            old_mode,
        )

    async def async_set_galevo_dhw_timeprog_temperatures(
        self,
        *,
        comfort: float,
        economy: float,
        old_comfort: float,
        old_economy: float,
    ) -> Any:
        """Set the scheduled DHW comfort/economy pair via
        ``plantData/{gw}/dhwTimeProgTemperatures``.

        APK-verified contract (PlantDataProxy.setDhwTimeProgTemperatures):
        ``ChangeValueDto<TemperaturesDto>`` where the DTO serializes as
        ``comf``/``econ`` (kotlinx @SerialName), not the full field names.
        """
        return await self._async_galevo_change_value(
            GALEVO_DHW_TIME_PROG_TEMPERATURES_PATH.format(gateway_id=self.gateway_id),
            {"comf": comfort, "econ": economy},
            {"comf": old_comfort, "econ": old_economy},
            write_enabled=GALEVO_WRITES_ENABLED
            or (
                GALEVO_DHW_SCHEDULED_COMFORT_WRITE_ENABLED
                and economy == old_economy
                and comfort >= economy
                and all(isfinite(value) for value in (comfort, economy, old_comfort))
            ),
        )

    async def async_set_galevo_holiday(
        self,
        *,
        start: str,
        end: str,
    ) -> Any:
        """Schedule the plant holiday via ``plantData/{gw}/holiday``.

        APK-verified contract (PlantDataProxy.setHoliday): the body is a
        ``ChangeValueDto<LocalDateTime>`` where ``new`` carries the
        start and ``old`` the end, both ISO local date-times.
        """
        return await self._async_galevo_change_value(
            GALEVO_HOLIDAY_PATH.format(gateway_id=self.gateway_id),
            start,
            end,
        )

    async def async_reset_galevo_holiday(self, *, now: str) -> Any:
        """Cancel the plant holiday (``PlantDataProxy.resetHoliday``).

        The APK serializes a ``ChangeValueDto<LocalDateTime>`` carrying only
        ``old`` (the cancellation instant); ``new`` stays absent. Wire names
        verified in ChangeValueDtoSerializer: ``new``/``old``.
        """
        self._require_galevo_writes()
        payload = await self._request_mobile_payload(
            GALEVO_HOLIDAY_PATH.format(gateway_id=self.gateway_id),
            method="POST",
            body={"old": now},
        )
        return payload.get("data", payload) if isinstance(payload, dict) else payload

    async def async_set_galevo_data_item(
        self,
        item: dict[str, Any],
        value: float,
        *,
        features: dict[str, Any] | None = None,
    ) -> Any:
        """Set one writable data item via ``POST dataItems/{gw}/set``.

        APK-verified contract (DataItemProxy.setDataItems): the body carries
        the plant's ``features`` map and ``items`` with each changed item
        echoed back in full plus ``prevValue``/``prevExpiresOn`` (optimistic
        concurrency). The request includes ``prevExpiresOn`` only when the
        read returned an ``expiresOn`` field.
        """
        self._require_galevo_writes()
        if not galevo_item_writable(item):
            raise ValueError("Remocon did not confirm that this data item is writable")
        old_value = item.get("value")
        if isinstance(old_value, bool) and value not in (0, 1):
            raise ValueError("A switch value must be on or off")
        options = item.get("options")
        if isinstance(options, list) and options and value not in options:
            raise ValueError("Value is outside the controller's allowed options")
        NumericVariable.parse(item).validate(value)
        set_item = {
            **item,
            "value": bool(value) if isinstance(old_value, bool) else value,
            "prevValue": old_value,
        }
        if "expiresOn" in item:
            set_item["prevExpiresOn"] = item.get("expiresOn")
        payload = await self._request_mobile_payload(
            GALEVO_DATA_ITEMS_SET_PATH.format(gateway_id=self.gateway_id),
            method="POST",
            body={
                "features": features if features is not None else self.last_features or {},
                "items": [set_item],
            },
        )
        return payload.get("data", payload) if isinstance(payload, dict) else payload

    async def _async_set_galevo_zone_temperatures(
        self,
        zone: ZoneState,
        *,
        comfort: float,
        reduced: float,
        cooling: bool,
    ) -> None:
        """Galevo zone temperatures: dedicated route for heating, item set for cooling."""
        comfort_variable, reduced_variable = zone.temperature_variables(cooling=cooling)
        comfort_variable.validate_change(comfort)
        reduced_variable.validate_change(reduced)
        if cooling:
            if comfort > reduced:
                raise ValueError("Cooling comfort temperature cannot exceed reduced temperature")
            items = {
                key: value
                for key, value, old in (
                    ("ZoneComfortCoolingTemp", comfort, comfort_variable.value),
                    ("ZoneEconomyCoolingTemp", reduced, reduced_variable.value),
                )
                if value != old
            }
            if len(items) > 1:
                raise ValueError("Change Galevo cooling setpoints one at a time")
            for item_id, new_value in items.items():
                item = self.last_system_items.get(f"{item_id}:{zone.number}")
                if item is None:
                    raise ValueError(f"{item_id} is not available on this plant")
                await self.async_set_galevo_data_item(item, new_value)
            return
        if comfort < reduced:
            raise ValueError("Heating comfort temperature cannot be below reduced temperature")
        assert comfort_variable.value is not None and reduced_variable.value is not None
        await self.async_set_galevo_zone_temperatures(
            zone.number,
            comfort=comfort,
            economy=reduced,
            old_comfort=comfort_variable.value,
            old_economy=reduced_variable.value,
        )

    async def async_set_galevo_dhw_settings(
        self,
        plant: PlantState,
        *,
        comfort: float | None,
        reduced: float | None,
        mode: int | None,
    ) -> None:
        """Apply one DHW operation or setpoint command without partial updates."""
        self._require_galevo_writes()
        if mode is not None and mode != plant.dhw_mode.value:
            if comfort is not None or reduced is not None:
                raise ValueError("Change Galevo DHW mode and temperature separately")
            if mode not in {option.value for option in plant.dhw_mode.options}:
                raise ValueError(f"Unsupported DHW mode: {mode}")
            raw_mode = GALEVO_DHW_MODE_TO_RAW.get(mode)
            raw_current = GALEVO_DHW_MODE_TO_RAW.get(plant.dhw_mode.value)
            if raw_mode is None or raw_current is None:
                raise ValueError(f"DHW mode {mode} has no Galevo equivalent")
            await self.async_set_galevo_dhw_mode(raw_mode, old_mode=raw_current)
            return
        if comfort is None and reduced is None:
            return

        old, new = plant.galevo_dhw_temperature_change(comfort, reduced)
        if old == new:
            return
        old_comfort, old_reduced = old
        comfort, reduced = new
        if reduced is None:
            await self.async_set_galevo_dhw_temp(comfort, old_temperature=old_comfort)
            return
        assert old_reduced is not None
        await self.async_set_galevo_dhw_timeprog_temperatures(
            comfort=comfort, economy=reduced, old_comfort=old_comfort, old_economy=old_reduced
        )

    async def async_get_galevo_consumption_sequences(self) -> list[dict[str, Any]]:
        """Fetch consumption time series (kWh) from ``consSequencesApi8``.

        Verified live: ``?usages=Ch,Dhw,Cooling&hasSlp=false`` returns
        ``[{k, p, v}]`` where ``k`` is the eEnergyUsageDto value (1=heating,
        2=DHW, 3=cooling; the gateway may add aggregate kinds like
        24=ChHpResAggr), ``p`` the eSequencePeriod (1=24h, 2=7d, 3=daily,
        4=completed months) and ``v`` the kWh buckets, oldest first.
        """
        payload = await self._request_mobile_payload(
            GALEVO_CONS_SEQUENCES_PATH.format(gateway_id=self.gateway_id),
        )
        data = payload.get("data", payload) if isinstance(payload, dict) else payload
        if not isinstance(data, list):
            raise ElcoResponseError("Galevo consumption reports returned an unexpected payload")
        return [sequence for sequence in data if isinstance(sequence, dict)]

    async def async_get_galevo_data(
        self,
        features: dict[str, Any],
        zone_numbers: list[int],
        culture: str = "en",
        *,
        allow_partial_zones: bool = False,
    ) -> tuple[ElcoData, dict[str, dict[str, Any]]]:
        """Read Galevo plant and zone data.

        Polling can isolate a failed zone. Command reads use a complete
        snapshot with a shared deadline and retry policy.
        """
        if allow_partial_zones:
            return await self._async_get_galevo_data_once(
                features, zone_numbers, culture, allow_partial_zones=True
            )
        return await self._async_read_with_retry(
            "Galevo snapshot",
            lambda: self._async_get_galevo_data_once(features, zone_numbers, culture),
            deadline_seconds=REQUEST_TIMEOUT * max(1, len(zone_numbers)),
        )

    async def _async_get_galevo_data_once(
        self,
        features: dict[str, Any],
        zone_numbers: list[int],
        culture: str = "en",
        *,
        allow_partial_zones: bool = False,
    ) -> tuple[ElcoData, dict[str, dict[str, Any]]]:
        """Fetch one Galevo snapshot attempt: dataItems plus per-zone state.

        Live-verified 2026-11-19 on a Galevo gateway: the R2
        ``PlantHome/GetData`` home items are a strict subset of the mobile
        ``dataItems`` catalog (r2-only: 0 items; the mobile response adds
        19 more, e.g. ZoneName, flow temps, buffer setpoints). The
        per-zone R2 call was therefore dropped from the poll; R2 is only
        used for Features (cached) and ``SetData`` writes.
        """
        read_items = partial(self.async_get_system_items, features, zone_numbers, culture=culture)
        if allow_partial_zones:
            try:
                system_items = await self._async_read_with_retry("Galevo plant data", read_items)
            except ElcoResponseError as err:
                if err.ambiguous and err.status is None and err.retry_after is None:
                    raise ElcoGalevoPlantDataError(str(err)) from err
                raise
            if not any(
                key.endswith(":0") and galevo_item_available(item) and item.get("value") is not None
                for key, item in system_items.items()
            ):
                raise ElcoGalevoPlantDataError("Galevo system data contained no usable plant items")
        else:
            system_items = await read_items()
        feature_zones: dict[int, dict[str, Any]] = {}
        for zone in features.get("zones", []):
            if not isinstance(zone, dict):
                continue
            try:
                feature_zones[int(zone["num"])] = zone
            except (KeyError, TypeError, ValueError):
                continue

        zones: dict[int, ZoneState] = {}
        raw_responses: list[Any] = []
        unavailable_zones = []
        for zone_number in zone_numbers:
            raw = (
                await self._async_poll_galevo_zone(zone_number)
                if allow_partial_zones
                else await self.async_get_galevo_zone(zone_number)
            )
            if raw is None:
                unavailable_zones.append(zone_number)
                zones[zone_number] = ZoneState.from_galevo(zone_number, {}, {})
                continue
            raw_responses.append(raw)
            zones[zone_number] = ZoneState.from_galevo(
                zone_number,
                raw,
                system_items,
                feature_zones.get(zone_number),
            )
        if unavailable_zones:
            unavailable_suffixes = tuple(f":{zone}" for zone in unavailable_zones)
            system_items = {
                key: item
                for key, item in system_items.items()
                if not key.endswith(unavailable_suffixes)
            }
        data = ElcoData(
            self.gateway_id,
            PlantState.from_galevo(system_items),
            zones,
            get_data_responses=raw_responses,
            unavailable_zones=tuple(unavailable_zones),
        )
        # Keep the merged item map for Galevo data-item writes: the set
        # request echoes the full item plus prevValue.
        self.last_system_items = system_items
        return data, system_items

    async def _async_poll_galevo_zone(self, zone_number: int) -> dict[str, Any] | None:
        """Read one zone with its own deadline and isolate zone-level failures."""
        try:
            return await self._async_read_with_retry(
                f"Galevo zone {zone_number}", partial(self.async_get_galevo_zone, zone_number)
            )
        except (ElcoConnectionError, ElcoResponseError) as err:
            if isinstance(err, ElcoResponseError) and (
                err.status == 429 or err.retry_after is not None
            ):
                raise
            _LOGGER.debug("Galevo zone %s data unavailable: %s", zone_number, err)
            return None

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
        if isinstance(items, dict):
            # Galevo single-item responses are keyed by menu id.
            items = list(items.values())
        if not isinstance(items, list):
            raise ElcoResponseError("Menu items returned an unexpected payload")
        return {
            str(item["id"]): item
            for item in items
            if isinstance(item, dict) and item.get("id") is not None
        }

    async def async_list_plants(self) -> list[dict[str, Any]]:
        """Fetch every gateway/plant registered on the authenticated account."""
        payload = await self._request_payload(
            "GET",
            PLANTS_LITE_PATH,
            retry_auth=False,
            invalidate_auth=False,
        )
        plants = payload.get("data", payload) if isinstance(payload, dict) else payload
        if not isinstance(plants, list):
            raise ElcoResponseError("Plant list returned an unexpected payload")
        return [plant for plant in plants if isinstance(plant, dict)]

    async def async_get_plant_metadata(self) -> dict[str, Any]:
        """Fetch metadata, serial, and location from the native plant list."""
        for plant in await self.async_list_plants():
            if self.gateway_id in plant_gateway_ids(plant):
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
        zone_match = re.search(r"\d+$", program)
        zone = 0 if is_dhw or program == "Extra" or zone_match is None else int(zone_match.group())
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
        retry_auth: bool = False,
    ) -> Any:
        """Fetch read-only metering data when the plant supports it."""
        payload = await self._request_payload(
            "POST",
            METERING_PATH.format(gateway_id=self.gateway_id),
            body={"features": features, "hasCooling": has_cooling},
            retry_auth=retry_auth,
            invalidate_auth=retry_auth,
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
        self._require_supported_system()
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
        """Write heating or cooling setpoints for the active season only."""
        if error := zone.temperature_write_error(cooling=cooling):
            raise ValueError(error)
        if self.system_type == SYSTEM_TYPE_GALEVO:
            await self._async_set_galevo_zone_temperatures(
                zone,
                comfort=comfort,
                reduced=reduced,
                cooling=cooling,
            )
            return
        self._require_supported_system()
        comfort_variable, reduced_variable = zone.temperature_variables(cooling=cooling)
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
        if self.system_type == SYSTEM_TYPE_GALEVO:
            await self.async_set_galevo_dhw_settings(
                plant,
                comfort=comfort if comfort != plant.dhw_target_temperature.value else None,
                reduced=reduced if reduced != plant.dhw_reduced_temperature.value else None,
                mode=mode,
            )
            return
        self._require_supported_system()
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
        if self.system_type == SYSTEM_TYPE_GALEVO:
            allowed_modes = {option.value for option in zone.mode.options}
            if mode not in allowed_modes:
                raise ValueError(f"Unsupported zone mode: {mode}")
            raw_mode = GALEVO_ZONE_MODE_TO_RAW.get(mode)
            if raw_mode is None:
                raise ValueError(f"Zone mode {mode} has no Galevo equivalent")
            old_mode = GALEVO_ZONE_MODE_TO_RAW.get(zone.mode.value)
            if old_mode is None:
                raise ValueError("Remocon did not return the current zone mode")
            await self.async_set_galevo_zone_mode(
                zone.number,
                raw_mode,
                old_mode=old_mode,
            )
            return
        self._require_supported_system()
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
        self._require_supported_system()
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
        self._require_supported_system()
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
