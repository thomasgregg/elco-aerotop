"""Data coordinator for ELCO Aerotop."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Coroutine
from contextlib import suppress
from dataclasses import replace
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    ElcoApiClient,
    ElcoApiError,
    ElcoAuthenticationError,
    ElcoConnectionError,
    ElcoGalevoPlantDataError,
    ElcoResponseError,
)
from .bsb_controls import (
    BSB_NUMBER_CONTROL_SPECS,
    HOLIDAY_OPERATING_LEVEL_ADDRESS,
    bsb_point_number,
    holiday_level_value,
)
from .capabilities import supports_cooling, supports_scheduled_dhw_comfort_write
from .const import (
    BSB_DISCOVERY_GROUPS,
    DOMAIN,
    GALEVO_DHW_SCHEDULED_COMFORT_WRITE_ENABLED,
    GALEVO_WRITES_ENABLED,
    MENU_ITEM_BASE_IDS,
    MENU_ITEM_CASCADE_IDS,
    MENU_ITEM_HP_IDS,
    MENU_ITEM_HP_STATUS_IDS,
    MENU_ITEM_HYBRID_IDS,
    MENU_ITEM_SLP_IDS,
    MENU_ITEM_VMC_IDS,
    SYSTEM_TYPE_BSB,
    SYSTEM_TYPE_GALEVO,
)
from .control_mapping import ZONE_MODE_AUTOMATIC
from .holiday import current_holiday
from .metering import GalevoAnnualMetering, galevo_annual_metering
from .models import (
    ElcoData,
    NumericVariable,
    ReadOnlyDiscovery,
    SelectVariable,
    controller_system_type,
    galevo_item_available,
    galevo_item_switch_value,
    galevo_item_writable,
)

_LOGGER = logging.getLogger(__name__)

_SLOW_DISCOVERY_INTERVAL_SECONDS = 3600
_OPTIONAL_PROBE_TIMEOUT = 65
_BSB_PROBE_TIMEOUT = 30
_GALEVO_METERING_TIMEOUT = 30
_HP_STATUS_TIMEOUT = 5
_TRANSIENT_RECOVERY_DELAYS = (60, 300, 900)
_DEFAULT_RATE_LIMIT_DELAY = 300
_TRANSIENT_RESPONSE_STATUSES = frozenset({408, 500, 502, 503, 504})
_OPTIONAL_CIRCUIT_STATUSES = frozenset({502, 503, 504})


async def _null_probe() -> None:
    """Return nothing; keeps the discovery gather positional for skipped probes."""
    return None


class _OptionalDiscoveryAborted(Exception):
    """Raised internally to stop optional requests after a global failure."""

    def __init__(self, error: Exception) -> None:
        super().__init__(str(error))
        self.error = error


class ElcoDataUpdateCoordinator(DataUpdateCoordinator[ElcoData]):
    """Coordinate polling and serialize controller-facing gateway traffic."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        api: ElcoApiClient,
        scan_interval: int,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=scan_interval),
            always_update=False,
        )
        self.api = api
        self._scan_interval = scan_interval
        # Server-localized text (e.g. ZoneName) follows the UI language —
        # the APK sends Locale.getDefault().getLanguage() (language part
        # only; the API rejects region tags such as "de-DE").
        language = getattr(getattr(hass, "config", None), "language", None)
        self._culture = (language or "en").split("-")[0].split("_")[0] or "en"
        self._features: dict[str, Any] | None = None
        self._zone_numbers: list[int] | None = None
        self._command_lock = asyncio.Lock()
        self._gateway_lock = asyncio.Lock()
        self._discovery_lock = asyncio.Lock()
        # Pending holiday window edited by the date entities; only sent to
        # the gateway when the holiday switch is turned on.
        self._pending_holiday_start: date | None = None
        self._pending_holiday_end: date | None = None
        self._slow_discovery = ReadOnlyDiscovery()
        self._slow_discovery_poll_count = max(
            1,
            (_SLOW_DISCOVERY_INTERVAL_SECONDS + scan_interval - 1) // scan_interval,
        )
        self._polls_until_slow_discovery = 0
        self._initial_discovery_complete = False
        self._background_discovery_task: asyncio.Task[None] | None = None
        self._hp_status_task: asyncio.Task[None] | None = None
        self._hp_status_items: dict[str, dict[str, Any]] = {}
        self.initial_refresh_task: asyncio.Task[None] | None = None
        self.unloading = False
        self._write_refresh_task: asyncio.Task[Any] | None = None
        self._transient_failure_count = 0
        self._optional_transient_failures = 0
        self._optional_retry_not_before = 0.0
        self._gateway_retry_not_before = 0.0
        self.last_successful_update: datetime | None = None
        self._successful_update_listeners: set[Callable[[], None]] = set()

    @callback
    def async_add_successful_update_listener(
        self, update_callback: Callable[[], None]
    ) -> Callable[[], None]:
        """Register a callback for every successful core data capture."""
        self._successful_update_listeners.add(update_callback)

        def remove_listener() -> None:
            self._successful_update_listeners.discard(update_callback)

        return remove_listener

    @callback
    def _notify_successful_update_listeners(self) -> None:
        """Notify timestamp consumers even when the returned data is unchanged."""
        for update_callback in tuple(self._successful_update_listeners):
            update_callback()

    async def _async_optional_probe(
        self,
        name: str,
        awaitable: Awaitable[Any],
        status: dict[str, str],
        *,
        timeout_seconds: int = _OPTIONAL_PROBE_TIMEOUT,
        circuit_breaker: bool = False,
        abort_on_timeout: bool = True,
    ) -> Any:
        """Run an optional read without making the main coordinator unavailable."""
        try:
            async with asyncio.timeout(timeout_seconds):
                result = await awaitable
        except Exception as err:  # An optional endpoint must never disable core polling.
            status[name] = f"unavailable:{type(err).__name__}"
            _LOGGER.debug("Optional Remocon probe %s is unavailable: %s", name, err)
            if isinstance(err, ElcoResponseError) and (
                err.status == 429 or err.retry_after is not None
            ):
                self._record_gateway_backoff(err.retry_after or _DEFAULT_RATE_LIMIT_DELAY)
            if (
                circuit_breaker
                and (abort_on_timeout or not isinstance(err, TimeoutError))
                and self._optional_failure_should_abort(err)
            ):
                raise _OptionalDiscoveryAborted(err) from err
            return None
        if circuit_breaker:
            self._optional_transient_failures = 0
        status[name] = "available"
        return result

    def _optional_failure_should_abort(self, error: Exception) -> bool:
        """Return whether a global failure should stop deferred discovery."""
        if isinstance(error, (ElcoConnectionError, TimeoutError)):
            return True
        if isinstance(error, ElcoResponseError) and error.status == 429:
            return True
        if isinstance(error, ElcoResponseError) and error.status in _OPTIONAL_CIRCUIT_STATUSES:
            self._optional_transient_failures += 1
            return self._optional_transient_failures >= 2
        self._optional_transient_failures = 0
        return False

    async def _async_serialized_optional_probe(
        self,
        name: str,
        request: Callable[[], Awaitable[Any]],
        status: dict[str, str],
        *,
        timeout_seconds: int = _OPTIONAL_PROBE_TIMEOUT,
        abort_on_timeout: bool = True,
    ) -> Any:
        """Run one optional controller request without overlapping core traffic."""
        async with self._gateway_lock:
            if remaining := self._gateway_backoff():
                raise _OptionalDiscoveryAborted(
                    ElcoResponseError("Remocon requested a delay", retry_after=remaining)
                )
            return await self._async_optional_probe(
                name,
                request(),
                status,
                timeout_seconds=timeout_seconds,
                circuit_breaker=True,
                abort_on_timeout=abort_on_timeout,
            )

    def _schedule_programs(self, data: ElcoData) -> tuple[list[str], bool]:
        """Return applicable schedule names and the cooling capability."""
        assert self._features is not None
        assert self._zone_numbers is not None
        programs = [f"ChZn{zone}" for zone in self._zone_numbers]
        has_cooling = any(supports_cooling(self._features, zone) for zone in data.zones.values())
        if has_cooling:
            programs.extend(f"CoolZn{zone}" for zone in self._zone_numbers)
        if self._features.get("dhwProgSupported", True) and not self._features.get(
            "dhwHidden", False
        ):
            programs.append("Dhw")
        return programs, has_cooling

    def _menu_item_ids(self) -> tuple[int, ...]:
        """Return documented diagnostic IDs relevant to advertised capabilities."""
        assert self._features is not None
        item_ids = list(MENU_ITEM_BASE_IDS)
        if self._features.get("hasVmc", False):
            item_ids.extend(MENU_ITEM_VMC_IDS)
        if self._features.get("hasSlp", False):
            item_ids.extend(MENU_ITEM_SLP_IDS)
        if self._features.get("hybridSys", False):
            item_ids.extend(MENU_ITEM_HYBRID_IDS)
        if self._features.get("hpSys", False):
            item_ids.extend(MENU_ITEM_HP_IDS)
        if any(
            self._features.get(key, False)
            for key in ("bmsActive", "cascadeSys", "hpCascadeSys", "hpCascadeSysPcm5")
        ):
            item_ids.extend(MENU_ITEM_CASCADE_IDS)
        return tuple(item_ids)

    def _system_type(self) -> int | None:
        """Return the controller family reported by the Features response."""
        return controller_system_type(self.api.last_features_response)

    @property
    def galevo_annual_metering(self) -> GalevoAnnualMetering | None:
        """Return the latest complete report while it still represents last year."""
        if self.data is None:
            return None
        report = self.data.discovery.galevo_annual_metering
        return report if report is not None and report.year == self._local_now().year - 1 else None

    async def _async_refresh_core_discovery(
        self,
        data: ElcoData,
    ) -> ReadOnlyDiscovery:
        """Refresh entity-bearing discovery without slow optional families."""
        assert self._features is not None
        assert self._zone_numbers is not None

        status: dict[str, str] = {}
        programs, _has_cooling = self._schedule_programs(data)
        # Match the core read path and entity registration: every family that
        # is not Galevo follows the BSB protocol, so a missing or unparsable
        # systemType keeps BSB discovery running instead of dropping it.
        is_bsb = self._system_type() != SYSTEM_TYPE_GALEVO

        # These cloud JSON reads populate entities during setup. Run them together so
        # a Remocon response that legitimately takes close to a minute does not make
        # the worst-case setup time grow by one timeout per endpoint.
        (
            plant_metadata,
            plant_header,
            plant_user_data,
            bsb_boiler_data,
            bus_errors,
        ) = await asyncio.gather(
            self._async_optional_probe(
                "plant_metadata",
                self.api.async_get_plant_metadata(),
                status,
            ),
            self._async_optional_probe(
                "plant_header",
                self.api.async_get_plant_header(),
                status,
            ),
            # The GetUserData endpoint answers 200 with an empty payload on
            # the live Galevo gateway (verified 2026-11-19) — BSB-only probe.
            self._async_optional_probe(
                "plant_user_data",
                (self.api.async_get_plant_user_data() if is_bsb else _null_probe()),
                status,
            ),
            self._async_optional_probe(
                "bsb_boiler_data",
                (self.api.async_get_bsb_boiler_data() if is_bsb else _null_probe()),
                status,
            ),
            self._async_optional_probe(
                "bus_errors",
                self.api.async_get_bus_errors(),
                status,
            ),
        )
        if not is_bsb:
            status["bsb_boiler_data"] = "unsupported:system_family"
            status["plant_user_data"] = "unsupported:system_family"
        for group_name in BSB_DISCOVERY_GROUPS:
            status[f"bsb_points:{group_name}"] = (
                "deferred:background" if is_bsb else "unsupported:system_family"
            )
        status["bsb_points"] = "deferred:background" if is_bsb else "unsupported:system_family"
        for program in programs:
            status[f"schedule:{program}"] = "deferred:background"
        has_metering = self._features.get("hasMetering", False)
        status["metering"] = (
            "deferred:background"
            if has_metering is True or (is_bsb and has_metering)
            else "unsupported:feature"
        )
        status["maintenance"] = "deferred:background"
        status["automated_monitoring"] = "deferred:background"
        status["bsb_plant_data"] = "deferred:background" if is_bsb else "unsupported:system_family"
        status["bus_devices"] = "unsupported:bsb_system" if is_bsb else "deferred:background"
        status["energy_sequences"] = "unsupported:bsb_system" if is_bsb else "deferred:background"
        status["menu_items"] = "unsupported:bsb_system" if is_bsb else "deferred:background"

        return ReadOnlyDiscovery(
            features=self._features,
            features_response=self.api.last_features_response,
            plant_metadata=plant_metadata if isinstance(plant_metadata, dict) else {},
            plant_header=plant_header if isinstance(plant_header, dict) else {},
            plant_user_data=plant_user_data if isinstance(plant_user_data, dict) else {},
            bsb_boiler_data=bsb_boiler_data,
            bus_errors=bus_errors,
            probe_status=status,
            system_type=self._system_type(),
        )

    async def async_refresh_deferred_discovery(self, *, refresh_core: bool = False) -> None:
        """Refresh optional data unless a server backoff is still active."""
        loop = asyncio.get_running_loop()
        if (
            self.unloading
            or loop.time() < self._optional_retry_not_before
            or self._gateway_backoff()
        ):
            return
        self._optional_transient_failures = 0
        try:
            await self._async_refresh_deferred_discovery(refresh_core=refresh_core)
        except _OptionalDiscoveryAborted as err:
            source = err.error
            if isinstance(source, ElcoResponseError) and (
                source.status == 429 or source.retry_after is not None
            ):
                delay = source.retry_after or _DEFAULT_RATE_LIMIT_DELAY
                self._optional_retry_not_before = loop.time() + delay
                self._record_gateway_backoff(delay)
            _LOGGER.debug("Deferred Remocon discovery stopped after: %s", source)

    async def _async_refresh_deferred_discovery(self, *, refresh_core: bool = False) -> None:
        """Refresh slow read-only families after config-entry setup has completed."""
        async with self._discovery_lock:
            if self.data is None or self._features is None:
                return
            source_data = self.data
            if refresh_core:
                async with self._gateway_lock:
                    base = await self._async_refresh_core_discovery(source_data)
            else:
                base = self._slow_discovery
            status = dict(source_data.discovery.probe_status)
            status.update(base.probe_status)
            bsb_points: dict[str, Any] = {}
            available_bsb_groups = 0
            programs, has_cooling = self._schedule_programs(source_data)
            supported_bsb_groups = len(BSB_DISCOVERY_GROUPS)
            is_bsb = self._system_type() != SYSTEM_TYPE_GALEVO
            for group_name, addresses in BSB_DISCOVERY_GROUPS.items():
                if group_name.startswith("energy_history_"):
                    continue
                if not is_bsb:
                    status[f"bsb_points:{group_name}"] = "unsupported:system_family"
                    supported_bsb_groups -= 1
                    continue
                source_outlet_minimum = group_name == "source_outlet_minimum"
                if (group_name == "cooling_2" and not has_cooling) or (
                    source_outlet_minimum and not self._features.get("hpSys", False)
                ):
                    status[f"bsb_points:{group_name}"] = "unsupported:feature"
                    supported_bsb_groups -= 1
                    continue
                group = await self._async_serialized_optional_probe(
                    f"bsb_points:{group_name}",
                    lambda addresses=addresses: self.api.async_get_bsb_points(addresses),
                    status,
                    timeout_seconds=_BSB_PROBE_TIMEOUT,
                    abort_on_timeout=not source_outlet_minimum,
                )
                if isinstance(group, dict):
                    bsb_points.update(group)
                    available_bsb_groups += 1

            # Remocon forwards several of these reads to the controller bus. Keep
            # every BSB and schedule request serialized to avoid gateway contention.
            schedules: dict[str, Any] = {}
            get_schedule = (
                self.api.async_get_schedule if is_bsb else self.api.async_get_galevo_time_prog
            )
            for program in programs:
                schedule = await self._async_serialized_optional_probe(
                    f"schedule:{program}",
                    lambda program=program: get_schedule(program),
                    status,
                )
                if schedule is not None:
                    schedules[program] = schedule

            features = self._features
            has_galevo_metering = not is_bsb and features.get("hasMetering") is True
            if is_bsb and features.get("hasMetering", False):
                metering = await self._async_serialized_optional_probe(
                    "metering",
                    lambda: self.api.async_get_metering(
                        features,
                        has_cooling=has_cooling,
                    ),
                    status,
                )
            else:
                metering = None
                if not has_galevo_metering:
                    status["metering"] = "unsupported:feature"
            if is_bsb:
                maintenance = await self._async_serialized_optional_probe(
                    "maintenance",
                    self.api.async_get_maintenance,
                    status,
                )
                automated_monitoring = await self._async_serialized_optional_probe(
                    "automated_monitoring",
                    self.api.async_get_automated_monitoring,
                    status,
                )
            else:
                maintenance = None
                automated_monitoring = None
                status["maintenance"] = "unsupported:system_family"
                status["automated_monitoring"] = "unsupported:system_family"
            if is_bsb:
                bsb_boiler_data = await self._async_serialized_optional_probe(
                    "bsb_boiler_data",
                    self.api.async_get_bsb_boiler_data,
                    status,
                )
                bsb_plant_data = await self._async_serialized_optional_probe(
                    "bsb_plant_data",
                    self.api.async_get_bsb_plant_data,
                    status,
                )
            else:
                bsb_boiler_data = None
                bsb_plant_data = None
                status["bsb_boiler_data"] = "unsupported:system_family"
                status["bsb_plant_data"] = "unsupported:system_family"
            if is_bsb:
                bus_devices: list[dict[str, Any]] = []
                status["bus_devices"] = "unsupported:bsb_system"
            else:
                devices = await self._async_serialized_optional_probe(
                    "bus_devices",
                    self.api.async_get_galevo_bus_devices,
                    status,
                )
                bus_devices = devices if isinstance(devices, list) else []
            energy_sequences: list[dict[str, Any]] = []
            if is_bsb:
                status["energy_sequences"] = "unsupported:bsb_system"
            else:
                sequences = await self._async_serialized_optional_probe(
                    "energy_sequences",
                    self.api.async_get_galevo_consumption_sequences,
                    status,
                )
                if isinstance(sequences, list):
                    energy_sequences = sequences
            menu_items: dict[str, dict[str, Any]] = {}
            if is_bsb:
                status["menu_items"] = "unsupported:bsb_system"
            else:
                menu_item_ids = self._menu_item_ids()
                # Galevo omits unsupported IDs from a batched response, so a
                # single request covers the whole catalog. Fall back to
                # per-item reads for families that reject the batch.
                batched = await self._async_serialized_optional_probe(
                    "menu_items",
                    lambda: self.api.async_get_menu_items(menu_item_ids),
                    status,
                )
                if isinstance(batched, dict) and batched:
                    menu_items.update(batched)
                    status["menu_items"] = "available"
                else:
                    available_menu_items = 0
                    for item_id in menu_item_ids:
                        returned_items = await self._async_serialized_optional_probe(
                            f"menu_item:{item_id}",
                            lambda item_id=item_id: self.api.async_get_menu_items((item_id,)),
                            status,
                        )
                        if isinstance(returned_items, dict):
                            menu_items.update(returned_items)
                            available_menu_items += 1
                    if available_menu_items == len(menu_item_ids):
                        status["menu_items"] = "available"
                    elif available_menu_items:
                        status["menu_items"] = "partially_available"
                    else:
                        status["menu_items"] = "unavailable"

            # Annual history is intentionally last. Some controllers reject an
            # oversized energy request slowly; this ordering prevents that
            # optional family from delaying schedules, maintenance, or native
            # BSB plant data. Each eight-address slot is isolated as well.
            for group_name, addresses in BSB_DISCOVERY_GROUPS.items():
                if not group_name.startswith("energy_history_"):
                    continue
                if not is_bsb:
                    status[f"bsb_points:{group_name}"] = "unsupported:system_family"
                    continue
                group = await self._async_serialized_optional_probe(
                    f"bsb_points:{group_name}",
                    lambda addresses=addresses: self.api.async_get_bsb_points(addresses),
                    status,
                    timeout_seconds=_BSB_PROBE_TIMEOUT,
                )
                if isinstance(group, dict):
                    bsb_points.update(group)
                    available_bsb_groups += 1

            if not is_bsb:
                status["bsb_points"] = "unsupported:system_family"
            elif available_bsb_groups == supported_bsb_groups:
                status["bsb_points"] = "available"
            elif available_bsb_groups:
                status["bsb_points"] = "partially_available"
            else:
                status["bsb_points"] = "unavailable"

            discovery = replace(
                base,
                schedules=schedules,
                metering=metering,
                galevo_annual_metering=(
                    self._slow_discovery.galevo_annual_metering if has_galevo_metering else None
                ),
                maintenance=maintenance,
                automated_monitoring=automated_monitoring,
                bsb_boiler_data=bsb_boiler_data,
                bsb_points=bsb_points,
                bsb_plant_data=(bsb_plant_data if isinstance(bsb_plant_data, dict) else {}),
                menu_items=menu_items,
                bus_devices=bus_devices,
                energy_sequences=energy_sequences,
                probe_status=status,
            )
            self._publish_deferred_discovery(discovery)
            if has_galevo_metering:
                await self._async_refresh_galevo_metering(features, has_cooling)

    async def _async_refresh_galevo_metering(
        self, features: dict[str, Any], has_cooling: bool
    ) -> None:
        """Fetch the web report after the regular discovery data has been published."""
        status = dict(self._slow_discovery.probe_status)
        async with self._gateway_lock:
            if self.unloading or self._gateway_backoff():
                return
            year = self._local_now().year - 1
            payload = await self._async_optional_probe(
                "metering",
                self.api.async_get_metering(features, has_cooling=has_cooling, retry_auth=True),
                status,
                timeout_seconds=_GALEVO_METERING_TIMEOUT,
            )
        report = (
            galevo_annual_metering(payload, year=year)
            if year == self._local_now().year - 1
            else None
        )
        if status["metering"] == "available" and report is None:
            status["metering"] = "unavailable:incomplete_previous_year"
        self._publish_deferred_discovery(
            replace(self._slow_discovery, galevo_annual_metering=report, probe_status=status)
        )

    @callback
    def _menu_items_with_hp_status(
        self, menu_items: dict[str, dict[str, Any]]
    ) -> dict[str, dict[str, Any]]:
        """Combine live status with the slower settings catalog."""
        return {**menu_items, **self._hp_status_items}

    async def _async_refresh_hp_status(self) -> None:
        """Read optional EM20 status after publishing core data."""
        items: dict[str, dict[str, Any]] = {}
        try:
            async with asyncio.timeout(_HP_STATUS_TIMEOUT), self._gateway_lock:
                if (
                    not self.unloading
                    and not self._command_lock.locked()
                    and not self._gateway_backoff()
                ):
                    response = await self.api.async_get_menu_items(MENU_ITEM_HP_STATUS_IDS)
                    items = {
                        key: response[key]
                        for item_id in MENU_ITEM_HP_STATUS_IDS
                        if (key := str(item_id)) in response
                    }
        except (ElcoApiError, TimeoutError) as err:
            if isinstance(err, ElcoApiError):
                self._record_response_backoff(err)
            _LOGGER.debug("Heat-pump status menu items unreadable: %s", err)

        self._publish_hp_status(items)

    @callback
    def _publish_hp_status(self, items: dict[str, dict[str, Any]]) -> None:
        """Update the optional EM20 readings on the latest core snapshot."""
        if self.unloading or items == self._hp_status_items:
            return
        self._hp_status_items = items
        if self.data is None or not self.last_update_success:
            return
        discovery = replace(
            self.data.discovery,
            menu_items=self._menu_items_with_hp_status(self._slow_discovery.menu_items),
        )
        self.data = replace(self.data, discovery=discovery)
        self.async_update_listeners()

    @callback
    def _publish_deferred_discovery(self, discovery: ReadOnlyDiscovery) -> None:
        """Merge optional data into the latest core snapshot."""
        if self.unloading:
            return
        self._slow_discovery = discovery
        current = self.data
        discovery = replace(
            discovery,
            system_items=current.discovery.system_items,
            menu_items=self._menu_items_with_hp_status(discovery.menu_items),
        )
        self.data = replace(current, discovery=discovery)
        self.async_update_listeners()

    def start_deferred_discovery(self, *, refresh_core: bool = False) -> None:
        """Start one managed background discovery task if none is running."""
        self._start_background_discovery(
            lambda: self.async_refresh_deferred_discovery(refresh_core=refresh_core)
        )

    def _start_background_discovery(self, run: Callable[[], Coroutine[Any, Any, None]]) -> None:
        """Track optional gateway work so a new command or unload can cancel it."""
        if self.unloading or (
            self._background_discovery_task is not None
            and not self._background_discovery_task.done()
        ):
            return
        self._background_discovery_task = self.hass.async_create_background_task(
            run(),
            f"{DOMAIN} deferred discovery",
            eager_start=False,
        )

    async def _async_refresh_bsb_system_items(self, snapshot: ElcoData) -> None:
        """Refresh optional BSB items after publishing the core command readback."""
        assert self._features is not None and self._zone_numbers is not None
        async with self._gateway_lock:
            if self.unloading or self.data is not snapshot or self._gateway_backoff():
                return
            status = dict(snapshot.discovery.probe_status)
            system_items = await self._async_optional_probe(
                "system_items",
                self.api.async_get_system_items(
                    self._features, self._zone_numbers, culture=self._culture
                ),
                status,
            )
            if self.unloading or self.data is not snapshot:
                return
            discovery = replace(
                snapshot.discovery, system_items=system_items or {}, probe_status=status
            )
            self.data = replace(snapshot, discovery=discovery)
            self.async_update_listeners()

    async def _async_cancel_deferred_discovery(self) -> None:
        """Stop optional gateway reads before a user command or unload."""
        current_task = asyncio.current_task()
        tasks = [
            task
            for task in (self._background_discovery_task, self._hp_status_task)
            if task is not None and not task.done() and task is not current_task
        ]
        for task in tasks:
            task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError):
                await task
        if self._background_discovery_task in tasks:
            self._background_discovery_task = None
        if self._hp_status_task in tasks:
            self._hp_status_task = None
            self._publish_hp_status({})

    def cancel_deferred_discovery(self) -> None:
        """Cancel entry-owned discovery during unload."""
        for task in (self._background_discovery_task, self._hp_status_task):
            if task is not None:
                task.cancel()

    async def async_stop_background_discovery(self) -> None:
        """Cancel and drain entry-owned work before platform unload."""
        task = self.initial_refresh_task
        if task is not None and not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        await self._async_cancel_deferred_discovery()

    def _record_gateway_backoff(self, delay: float) -> None:
        self._gateway_retry_not_before = max(
            self._gateway_retry_not_before, asyncio.get_running_loop().time() + delay
        )

    def _record_response_backoff(self, error: ElcoApiError) -> None:
        if isinstance(error, ElcoResponseError) and (
            error.status == 429 or error.retry_after is not None
        ):
            self._record_gateway_backoff(error.retry_after or _DEFAULT_RATE_LIMIT_DELAY)

    def _gateway_backoff(self) -> float:
        return max(0.0, self._gateway_retry_not_before - asyncio.get_running_loop().time())

    def _next_transient_recovery_delay(self) -> float:
        """Return a bounded recovery delay and advance the failure count."""
        index = self._transient_failure_count
        self._transient_failure_count += 1
        if index < len(_TRANSIENT_RECOVERY_DELAYS):
            delay = _TRANSIENT_RECOVERY_DELAYS[index]
        else:
            delay = self._scan_interval
        return float(min(self._scan_interval, delay))

    @staticmethod
    def _is_transient_response_error(error: ElcoResponseError) -> bool:
        """Return whether a response failure should receive an earlier poll."""
        return error.status in _TRANSIENT_RESPONSE_STATUSES or (
            error.status is None and "communication error" in str(error).casefold()
        )

    def _core_snapshot(
        self,
        data: ElcoData,
        system_items: dict[str, dict[str, Any]],
        status: dict[str, str],
        system_type: int | None,
    ) -> ElcoData:
        """Attach optional discovery to a fresh core capture."""
        discovery = replace(
            self._slow_discovery,
            features=self._features or {},
            features_response=self.api.last_features_response,
            system_items=system_items,
            probe_status=status,
            system_type=system_type,
            menu_items=self._menu_items_with_hp_status(self._slow_discovery.menu_items),
        )
        return replace(data, discovery=discovery)

    def _record_snapshot(self, data: ElcoData) -> None:
        """Track the last complete snapshot or shorten polling during zone recovery."""
        previously_unavailable = self.data.unavailable_zones if self.data is not None else ()
        if data.unavailable_zones:
            delay = self._next_transient_recovery_delay()
            self.update_interval = timedelta(seconds=delay)
            if data.unavailable_zones != previously_unavailable:
                _LOGGER.warning(
                    "Galevo zone data unavailable for zones %s",
                    ", ".join(map(str, data.unavailable_zones)),
                )
            return
        if previously_unavailable:
            _LOGGER.info("Galevo zone reads recovered")
        self.update_interval = timedelta(seconds=self._scan_interval)
        self._transient_failure_count = 0
        self.last_successful_update = data.captured_at
        self._notify_successful_update_listeners()

    async def _async_update_data(self) -> ElcoData:
        self.update_interval = timedelta(seconds=self._scan_interval)
        try:
            start_slow_discovery = False
            refresh_core = False
            write_refresh = self._write_refresh_task is asyncio.current_task()
            async with self._gateway_lock:
                if self.unloading:
                    raise UpdateFailed("The integration is unloading")
                if remaining := self._gateway_backoff():
                    raise UpdateFailed("Remocon requested a polling delay", retry_after=remaining)
                if self._features is None:
                    self._features = await self.api.async_get_features()
                system_type = self._system_type()
                if system_type not in (None, SYSTEM_TYPE_BSB, SYSTEM_TYPE_GALEVO):
                    raise UpdateFailed(f"Unsupported Remocon controller family ({system_type})")
                if self._zone_numbers is None:
                    self._zone_numbers = await self.api.async_get_zone_numbers(self._features)

                status = dict(self._slow_discovery.probe_status)
                if system_type == SYSTEM_TYPE_GALEVO:
                    data, system_items = await self.api.async_get_galevo_data(
                        self._features,
                        self._zone_numbers,
                        culture=self._culture,
                        allow_partial_zones=True,
                    )
                    status["system_items"] = "available"
                else:
                    data = await self.api.async_get_data(self._zone_numbers, use_cache=True)
                    if write_refresh:
                        previous = self.data.discovery if self.data is not None else None
                        system_items = previous.system_items if previous is not None else {}
                        if previous is not None and "system_items" in previous.probe_status:
                            status["system_items"] = previous.probe_status["system_items"]
                    else:
                        system_items = await self._async_optional_probe(
                            "system_items",
                            self.api.async_get_system_items(
                                self._features, self._zone_numbers, culture=self._culture
                            ),
                            status,
                        )

                if write_refresh:
                    pass
                elif self._polls_until_slow_discovery <= 0:
                    if not self._initial_discovery_complete:
                        self._slow_discovery = await self._async_refresh_core_discovery(data)
                        self._initial_discovery_complete = True
                        start_slow_discovery = True
                    else:
                        start_slow_discovery = True
                        refresh_core = True
                    self._polls_until_slow_discovery = self._slow_discovery_poll_count - 1
                    status.update(self._slow_discovery.probe_status)
                else:
                    self._polls_until_slow_discovery -= 1

            if self.unloading:
                raise UpdateFailed("The integration is unloading")
            if self._hp_status_items and not self.last_update_success:
                self._hp_status_items.clear()
            updated_data = self._core_snapshot(data, system_items or {}, status, system_type)
            self._record_snapshot(updated_data)
            if (
                not write_refresh
                and system_type == SYSTEM_TYPE_GALEVO
                and self._features.get("hasEm20") is True
                and (self._hp_status_task is None or self._hp_status_task.done())
            ):
                self._hp_status_task = self.hass.async_create_background_task(
                    self._async_refresh_hp_status(),
                    f"{DOMAIN} EM20 status",
                    eager_start=False,
                )
            if write_refresh and system_type != SYSTEM_TYPE_GALEVO:
                self._start_background_discovery(
                    lambda: self._async_refresh_bsb_system_items(updated_data)
                )
            elif start_slow_discovery:
                self.start_deferred_discovery(refresh_core=refresh_core)
            return updated_data
        except ElcoAuthenticationError as err:
            raise ConfigEntryAuthFailed from err
        except ElcoConnectionError as err:
            raise UpdateFailed(
                str(err),
                retry_after=self._next_transient_recovery_delay(),
            ) from err
        except ElcoResponseError as err:
            if err.status == 429 or err.retry_after is not None:
                delay = err.retry_after or _DEFAULT_RATE_LIMIT_DELAY
                self._record_gateway_backoff(delay)
                raise UpdateFailed(
                    str(err),
                    retry_after=delay,
                ) from err
            if isinstance(err, ElcoGalevoPlantDataError) or self._is_transient_response_error(err):
                raise UpdateFailed(
                    str(err),
                    retry_after=self._next_transient_recovery_delay(),
                ) from err
            raise UpdateFailed(str(err)) from err
        except ElcoApiError as err:
            raise UpdateFailed(str(err)) from err

    async def _async_fresh_data(self, zone_numbers: list[int] | None = None) -> ElcoData:
        """Read uncached state for the requested zones."""
        if self.unloading:
            raise ElcoResponseError("The integration is unloading")
        if remaining := self._gateway_backoff():
            raise ElcoResponseError("Remocon requested a polling delay", retry_after=remaining)
        if self._zone_numbers is None:
            self._zone_numbers = await self.api.async_get_zone_numbers()
        requested_zones = zone_numbers or self._zone_numbers
        if self._system_type() == SYSTEM_TYPE_GALEVO:
            data, system_items = await self.api.async_get_galevo_data(
                self._features or {},
                requested_zones,
                culture=self._culture,
            )
            return replace(
                data,
                discovery=replace(
                    data.discovery, system_items=system_items, system_type=SYSTEM_TYPE_GALEVO
                ),
            )
        return await self.api.async_get_data(requested_zones, use_cache=False)

    async def _async_fresh_plant_data(self) -> ElcoData:
        """Read fresh plant state using one known zone request."""
        if self._zone_numbers is None:
            self._zone_numbers = await self.api.async_get_zone_numbers()
        return await self._async_fresh_data([self._zone_numbers[0]])

    @staticmethod
    def _is_ambiguous_write_error(error: ElcoApiError) -> bool:
        """Return whether a failed write may still have reached the controller."""
        if isinstance(error, ElcoConnectionError):
            return True
        return isinstance(error, ElcoResponseError) and (
            error.ambiguous
            or error.status == 408
            or (error.status is not None and error.status >= 500)
        )

    @staticmethod
    def _zone_temperatures_match(
        data: ElcoData,
        zone_number: int,
        comfort: float,
        reduced: float,
        *,
        cooling: bool = False,
    ) -> bool:
        zone = data.zones.get(zone_number)
        if zone is None:
            return False
        comfort_variable, reduced_variable = zone.temperature_variables(cooling=cooling)
        return comfort_variable.value == comfort and reduced_variable.value == reduced

    @staticmethod
    def _bsb_value_matches(points: dict[str, Any], address: str, value: float | int) -> bool:
        current = bsb_point_number(points.get(address))
        return current is not None and abs(current - float(value)) <= 1e-6

    def _store_bsb_points(self, points: dict[str, Any]) -> None:
        """Merge a verified control read into deferred discovery state."""
        merged = {**self._slow_discovery.bsb_points, **points}
        self._slow_discovery = replace(self._slow_discovery, bsb_points=merged)

    @staticmethod
    def _dhw_settings_match(
        data: ElcoData,
        comfort: float,
        reduced: float,
        mode: int,
    ) -> bool:
        plant = data.plant
        return (
            plant.dhw_comfort_temperature.value == comfort
            and plant.dhw_reduced_temperature.value == reduced
            and plant.dhw_mode.value == mode
        )

    @staticmethod
    def _zone_mode_matches(data: ElcoData, zone_number: int, mode: int) -> bool:
        zone = data.zones.get(zone_number)
        return zone is not None and zone.mode.value == mode

    @staticmethod
    def _zone_holiday_matches(
        data: ElcoData,
        zone_number: int,
        ends_on: date,
        target_index: int | None,
    ) -> bool:
        """Return whether a fresh read confirms the requested current holiday."""
        zone = data.zones.get(zone_number)
        if zone is None or zone.mode.value != ZONE_MODE_AUTOMATIC:
            return False
        return any(
            holiday.end == ends_on
            and (target_index is None or holiday.index == target_index)
            and not holiday.deleted
            and not holiday.out_of_service
            for holiday in zone.holidays
        )

    @staticmethod
    def _zone_holiday_cancelled(
        data: ElcoData,
        zone_number: int,
        target_index: int | None,
        target_start: date,
        target_end: date,
    ) -> bool:
        """Return whether a fresh read confirms that the targeted holiday is inactive."""
        zone = data.zones.get(zone_number)
        if zone is None:
            return False
        for holiday in zone.holidays:
            same_period = (
                holiday.index == target_index
                if target_index is not None
                else holiday.start == target_start and holiday.end == target_end
            )
            if same_period:
                return holiday.deleted or holiday.out_of_service
        return True

    async def _async_write_with_reconciliation(
        self,
        *,
        write: Callable[[], Awaitable[Any]],
        reconcile: Callable[[], Awaitable[ElcoData]],
        matches: Callable[[ElcoData], bool],
        description: str,
        verify_success: bool = False,
    ) -> HomeAssistantError | None:
        """Run one write and verify state instead of replaying an ambiguous failure."""
        _LOGGER.debug("Remocon write started: %s", description)
        try:
            await write()
        except (ElcoConnectionError, ElcoResponseError) as err:
            self._record_response_backoff(err)
            if not self._is_ambiguous_write_error(err):
                raise
            _LOGGER.debug(
                "Remocon write %s returned an ambiguous error; reconciling: %s",
                description,
                err,
            )
            if isinstance(err, ElcoResponseError) and err.retry_after is not None:
                raise HomeAssistantError(
                    f"{description} may have been applied, but Remocon requested a delay before "
                    "another request; check the controller or official application"
                ) from err
            try:
                reconciled = await reconcile()
            except ElcoApiError as read_error:
                self._record_response_backoff(read_error)
                raise HomeAssistantError(
                    f"{description} may have been applied, but its result could not be verified; "
                    "check the controller or official application"
                ) from err
            if matches(reconciled):
                _LOGGER.debug("Confirmed %s after an ambiguous Remocon response", description)
                return None
            return HomeAssistantError(f"{description} was not confirmed by a fresh controller read")
        if verify_success:
            try:
                verified = await reconcile()
            except ElcoApiError as err:
                self._record_response_backoff(err)
                raise HomeAssistantError(
                    f"{description} was acknowledged, but the outcome could not be verified"
                ) from err
            if not matches(verified):
                return HomeAssistantError(
                    f"{description} was not confirmed by a fresh controller read"
                )
        return None

    async def _async_refresh_after_write(self) -> None:
        """Refresh core state without waiting for BSB diagnostics after a write."""
        if self.unloading or self._gateway_backoff():
            return
        self._write_refresh_task = asyncio.current_task()
        try:
            await self.async_refresh()
        finally:
            self._write_refresh_task = None

    async def _async_set_bsb_value(
        self,
        *,
        address: str,
        read_addresses: tuple[str, ...],
        resolve_value: Callable[[dict[str, Any]], float | int],
        description: str,
    ) -> None:
        """Write and read back one allowlisted BSB value without replaying it."""
        async with self._command_lock:
            try:
                await self._async_cancel_deferred_discovery()
                async with self._gateway_lock:
                    before = await self.api.async_get_bsb_points(read_addresses, command=True)
                    value = resolve_value(before)
                    point = before.get(address)
                    if not isinstance(point, dict):
                        raise HomeAssistantError(f"{description} is unavailable")

                    ambiguous_error: ElcoApiError | None = None
                    try:
                        await self.api.async_write_bsb_point(point, value)
                    except (ElcoConnectionError, ElcoResponseError) as err:
                        if not self._is_ambiguous_write_error(err):
                            raise
                        if isinstance(err, ElcoResponseError) and err.retry_after is not None:
                            raise HomeAssistantError(
                                f"{description} may have been applied, but Remocon requested a "
                                "delay before verification; check the controller or official "
                                "application"
                            ) from err
                        ambiguous_error = err

                    try:
                        after = await self.api.async_get_bsb_points(read_addresses, command=True)
                    except ElcoApiError as err:
                        raise HomeAssistantError(
                            f"{description} may have been applied, but its result could not be "
                            "verified; check the controller or official application"
                        ) from (ambiguous_error or err)
                    if not self._bsb_value_matches(after, address, value):
                        raise HomeAssistantError(
                            f"{description} was not confirmed by a fresh controller read"
                        ) from ambiguous_error
                    self._store_bsb_points(after)

                await self._async_refresh_after_write()
            except ValueError as err:
                raise HomeAssistantError(str(err)) from err
            except ElcoAuthenticationError as err:
                raise ConfigEntryAuthFailed from err
            except ElcoApiError as err:
                raise HomeAssistantError(str(err)) from err

    async def async_set_bsb_number(self, key: str, value: float) -> None:
        """Write one reviewed numeric BSB controller setting."""
        spec = BSB_NUMBER_CONTROL_SPECS.get(key)
        if spec is None:
            raise HomeAssistantError(f"Unsupported BSB number control: {key}")
        addresses = (spec.address,) + (
            (spec.maximum_address,) if spec.maximum_address is not None else ()
        )

        def resolve_value(points: dict[str, Any]) -> float:
            if bsb_point_number(points.get(spec.address)) is None:
                raise ValueError(f"{spec.name} is unavailable")
            spec.validate(value, points)
            return value

        await self._async_set_bsb_value(
            address=spec.address,
            read_addresses=addresses,
            resolve_value=resolve_value,
            description=spec.name,
        )

    async def async_set_holiday_operating_level(self, option: str) -> None:
        """Write heating-circuit 1's Reduced/Frost-protection holiday level."""

        def resolve_value(points: dict[str, Any]) -> int:
            return holiday_level_value(points.get(HOLIDAY_OPERATING_LEVEL_ADDRESS), option)

        # Resolve the numeric enum from the same fresh read used for compare-and-set.
        await self._async_set_bsb_value(
            address=HOLIDAY_OPERATING_LEVEL_ADDRESS,
            read_addresses=(HOLIDAY_OPERATING_LEVEL_ADDRESS,),
            resolve_value=resolve_value,
            description="Holiday operating level",
        )

    async def async_set_zone_temperature(
        self,
        zone_number: int,
        kind: str,
        value: float,
    ) -> None:
        """Safely update one zone temperature while preserving its companion value."""
        galevo = self._system_type() == SYSTEM_TYPE_GALEVO
        if galevo and not GALEVO_WRITES_ENABLED:
            raise HomeAssistantError("Galevo zone temperature writes are disabled")
        async with self._command_lock:
            try:
                await self._async_cancel_deferred_discovery()
                async with self._gateway_lock:
                    fresh = await self._async_fresh_data([zone_number])
                    zone = fresh.zones[zone_number]
                    cooling = kind.startswith("cooling_")
                    if error := zone.temperature_write_error(cooling=cooling):
                        raise ValueError(error)
                    comfort_variable, reduced_variable = zone.temperature_variables(cooling=cooling)
                    comfort = comfort_variable.value
                    reduced = reduced_variable.value
                    assert comfort is not None and reduced is not None
                    if kind in ("comfort", "cooling_comfort"):
                        comfort_variable.validate_change(value)
                        comfort = value
                    elif kind in ("reduced", "cooling_reduced"):
                        reduced_variable.validate_change(value)
                        reduced = value
                    else:
                        raise HomeAssistantError(f"Unsupported temperature kind: {kind}")
                    write_failure = await self._async_write_with_reconciliation(
                        write=lambda: self.api.async_set_zone_temperatures(
                            zone,
                            comfort=comfort,
                            reduced=reduced,
                            cooling=cooling,
                        ),
                        reconcile=lambda: self._async_fresh_data([zone_number]),
                        matches=lambda state: self._zone_temperatures_match(
                            state,
                            zone_number,
                            comfort,
                            reduced,
                            cooling=cooling,
                        ),
                        description=(
                            f"{'Cooling' if cooling else 'Heating'} zone {zone_number} "
                            "temperature change"
                        ),
                        verify_success=galevo,
                    )
                await self._async_refresh_after_write()
                if write_failure is not None:
                    raise write_failure
            except KeyError as err:
                raise HomeAssistantError(f"Heating zone {zone_number} is unavailable") from err
            except ValueError as err:
                raise HomeAssistantError(str(err)) from err
            except ElcoAuthenticationError as err:
                raise ConfigEntryAuthFailed from err
            except ElcoApiError as err:
                raise HomeAssistantError(str(err)) from err

    async def async_set_dhw(
        self,
        *,
        comfort: float | None = None,
        reduced: float | None = None,
        mode: int | None = None,
    ) -> None:
        """Safely update DHW settings while preserving unchanged values."""
        if self._system_type() == SYSTEM_TYPE_GALEVO:
            if not GALEVO_WRITES_ENABLED:
                raise HomeAssistantError("Galevo hot-water writes are disabled")
            await self._async_set_galevo_dhw(comfort=comfort, reduced=reduced, mode=mode)
            return
        async with self._command_lock:
            try:
                await self._async_cancel_deferred_discovery()
                async with self._gateway_lock:
                    fresh = await self._async_fresh_plant_data()
                    plant = fresh.plant
                    new_comfort = (
                        plant.dhw_comfort_temperature.value if comfort is None else comfort
                    )
                    new_reduced = (
                        plant.dhw_reduced_temperature.value if reduced is None else reduced
                    )
                    new_mode = plant.dhw_mode.value if mode is None else mode
                    if new_comfort is None or new_reduced is None or new_mode is None:
                        raise HomeAssistantError("Remocon did not return complete DHW settings")
                    write_failure = await self._async_write_with_reconciliation(
                        write=lambda: self.api.async_set_dhw(
                            plant,
                            comfort=new_comfort,
                            reduced=new_reduced,
                            mode=new_mode,
                        ),
                        reconcile=self._async_fresh_plant_data,
                        matches=lambda state: self._dhw_settings_match(
                            state,
                            new_comfort,
                            new_reduced,
                            new_mode,
                        ),
                        description="Domestic hot water change",
                    )
                await self._async_refresh_after_write()
                if write_failure is not None:
                    raise write_failure
            except ValueError as err:
                raise HomeAssistantError(str(err)) from err
            except ElcoAuthenticationError as err:
                raise ConfigEntryAuthFailed from err
            except ElcoApiError as err:
                raise HomeAssistantError(str(err)) from err

    async def _async_set_galevo_dhw(
        self, *, comfort: float | None, reduced: float | None, mode: int | None
    ) -> None:
        expected: tuple[float, float | None] | None = None
        changed_mode = False

        async def write(data: ElcoData) -> None:
            nonlocal expected, changed_mode
            plant = data.plant
            if comfort is not None or reduced is not None:
                if mode is not None and mode != plant.dhw_mode.value:
                    # Reject invalid targets before changing the operating mode.
                    replace(
                        plant, dhw_mode=replace(plant.dhw_mode, value=mode)
                    ).galevo_dhw_temperature_change(comfort, reduced)
                    observed, failure = await self._async_galevo_write_and_read(
                        write=lambda: self.api.async_set_galevo_dhw_settings(
                            plant, comfort=None, reduced=None, mode=mode
                        ),
                        matches=lambda state: state.plant.dhw_mode.value == mode,
                        description="Domestic hot water mode change",
                    )
                    self._publish_galevo_snapshot(observed)
                    if failure is not None:
                        raise failure
                    plant = observed.plant
                    changed_mode = True
                _, expected = plant.galevo_dhw_temperature_change(comfort, reduced)

            await self.api.async_set_galevo_dhw_settings(
                plant, comfort=comfort, reduced=reduced, mode=mode
            )

        def matches(data: ElcoData) -> bool:
            plant = data.plant
            if mode is not None and plant.dhw_mode.value != mode:
                return False
            if expected is None:
                return True
            if expected[1] is None:
                return plant.dhw_always_temperature.value == expected[0]
            return (
                plant.dhw_comfort_temperature.value,
                plant.dhw_reduced_temperature.value,
            ) == expected

        try:
            await self._async_galevo_command("Domestic hot water change", write, matches)
        except HomeAssistantError as err:
            if changed_mode:
                raise HomeAssistantError(
                    f"Hot-water mode changed, but the temperature change failed or was "
                    f"unconfirmed: {err}"
                ) from err
            raise

    async def _async_galevo_write_and_read(
        self,
        *,
        write: Callable[[], Awaitable[Any]],
        matches: Callable[[ElcoData], bool],
        description: str,
    ) -> tuple[ElcoData, HomeAssistantError | None]:
        """Verify once and allow one more read if Remocon still shows the old value."""
        observed: ElcoData | None = None

        async def reconcile() -> ElcoData:
            nonlocal observed
            try:
                observed = await self._async_fresh_data()
                if not matches(observed):
                    observed = await self._async_fresh_data()
                return observed
            except ElcoAuthenticationError:
                if self.config_entry is not None and not self.unloading:
                    self.config_entry.async_start_reauth(self.hass)
                raise

        failure = await self._async_write_with_reconciliation(
            write=write,
            reconcile=reconcile,
            matches=matches,
            description=description,
            verify_success=True,
        )
        assert observed is not None
        return observed, failure

    def _publish_galevo_snapshot(self, data: ElcoData) -> None:
        """Publish the complete command readback while still holding the gateway lock."""
        if self.unloading:
            return
        snapshot = self._core_snapshot(
            data,
            data.discovery.system_items,
            {**self._slow_discovery.probe_status, "system_items": "available"},
            SYSTEM_TYPE_GALEVO,
        )
        self._record_snapshot(snapshot)
        self.async_set_updated_data(snapshot)

    async def _async_galevo_command(
        self,
        description: str,
        write: Callable[[ElcoData], Awaitable[Any]],
        matches: Callable[[ElcoData], bool],
    ) -> None:
        """Validate, send and verify a Galevo command under the gateway lock."""
        if self._system_type() != SYSTEM_TYPE_GALEVO:
            raise HomeAssistantError("This control requires a Galevo gateway")
        async with self._command_lock:
            try:
                await self._async_cancel_deferred_discovery()
                async with self._gateway_lock:
                    fresh = await self._async_fresh_data()
                    observed, failure = await self._async_galevo_write_and_read(
                        write=lambda: write(fresh),
                        matches=matches,
                        description=description,
                    )
                    self._publish_galevo_snapshot(observed)
                if failure is not None:
                    raise failure
            except ValueError as err:
                raise HomeAssistantError(str(err)) from err
            except ElcoAuthenticationError as err:
                if self.config_entry is not None:
                    self.config_entry.async_start_reauth(self.hass)
                raise HomeAssistantError("Remocon authentication failed") from err
            except ElcoApiError as err:
                self._record_response_backoff(err)
                raise HomeAssistantError(str(err)) from err

    async def async_set_plant_mode(self, mode: int) -> None:
        """Set and verify the hardware-tested mobile plant mode command."""

        async def write(data: ElcoData) -> None:
            item = data.discovery.system_item("PlantMode")
            if item is None or not galevo_item_writable(item):
                raise ValueError("Remocon did not confirm that plant mode is writable")
            current = SelectVariable.parse(item)
            if mode not in {option.value for option in current.options}:
                raise ValueError(f"Unsupported plant mode: {mode}")
            if current.value is None:
                raise ValueError("Remocon did not return the current plant mode")
            if mode != current.value:
                await self.api.async_set_galevo_plant_mode(mode, old_mode=current.value)

        def confirmed(data: ElcoData) -> bool:
            item = data.discovery.system_item("PlantMode")
            return bool(
                item and galevo_item_available(item) and SelectVariable.parse(item).value == mode
            )

        await self._async_galevo_command("Plant mode change", write, confirmed)

    async def async_set_scheduled_dhw_comfort_temperature(self, value: float) -> None:
        """Change the scheduled comfort setpoint and verify both temperatures."""
        if not GALEVO_DHW_SCHEDULED_COMFORT_WRITE_ENABLED:
            raise HomeAssistantError("Galevo scheduled hot-water comfort writes are disabled")
        economy: float | None = None

        async def write(data: ElcoData) -> None:
            nonlocal economy
            if not supports_scheduled_dhw_comfort_write(data):
                raise ValueError("Remocon did not confirm writable Time Based hot-water settings")
            comfort = data.plant.dhw_comfort_temperature
            economy = data.plant.dhw_reduced_temperature.value
            assert comfort.value is not None and economy is not None
            comfort.validate(value)
            if value < economy:
                raise ValueError("Scheduled comfort temperature cannot be below economy")
            if value != comfort.value:
                await self.api.async_set_galevo_dhw_timeprog_temperatures(
                    comfort=value, economy=economy, old_comfort=comfort.value, old_economy=economy
                )

        def confirmed(data: ElcoData) -> bool:
            return (
                data.plant.dhw_comfort_temperature.value == value
                and data.plant.dhw_reduced_temperature.value == economy
            )

        await self._async_galevo_command("Scheduled hot-water comfort change", write, confirmed)

    def set_pending_holiday(self, *, start: date | None, end: date | None) -> None:
        """Store the local draft without changing the gateway."""
        if start is not None:
            self._pending_holiday_start = start
        if end is not None:
            self._pending_holiday_end = end
        if self.data is not None:
            self.async_update_listeners()

    @property
    def pending_holiday_start(self) -> date | None:
        return self._pending_holiday_start

    @property
    def pending_holiday_end(self) -> date | None:
        return self._pending_holiday_end

    def _local_now(self) -> datetime:
        return datetime.now(ZoneInfo(self.hass.config.time_zone))

    async def async_set_galevo_holiday(self) -> None:
        """Send a local holiday draft and confirm the controller's reported state."""
        if not GALEVO_WRITES_ENABLED:
            raise HomeAssistantError("Galevo holiday writes are disabled")
        start, end = self.pending_holiday_start, self.pending_holiday_end
        if start is None or end is None:
            raise HomeAssistantError("Set both holiday draft dates before activating")
        today = self._local_now().date()
        if end < start or start < today:
            raise HomeAssistantError("Select an ordered holiday window starting today or later")
        start_iso = f"{start.isoformat()}T00:00:00"
        end_iso = f"{end.isoformat()}T23:59:59"
        previously_active: bool | None = None

        async def write(data: ElcoData) -> None:
            nonlocal previously_active
            previously_active = galevo_item_switch_value(data.discovery.system_item("Holiday"))
            if previously_active is None:
                raise ValueError("Remocon did not report the current holiday state")
            await self.api.async_set_galevo_holiday(start=start_iso, end=end_iso)

        def matches(data: ElcoData) -> bool:
            item = data.discovery.system_item("Holiday")
            if item is None or galevo_item_switch_value(item) is not True:
                return False
            expiry = item.get("expiresOn")
            if isinstance(expiry, str):
                try:
                    reported_end = datetime.fromisoformat(expiry)
                except ValueError:
                    return False
                if reported_end.tzinfo is not None:
                    reported_end = reported_end.astimezone(self._local_now().tzinfo)
                return reported_end.date() == end
            return previously_active is False and start == today

        await self._async_galevo_command(
            "Holiday activation",
            write,
            matches,
        )

    async def async_reset_galevo_holiday(self) -> None:
        """Cancel a controller holiday and confirm the result."""
        if not GALEVO_WRITES_ENABLED:
            raise HomeAssistantError("Galevo holiday writes are disabled")
        await self._async_galevo_command(
            "Holiday cancellation",
            lambda _: self.api.async_reset_galevo_holiday(
                now=self._local_now().replace(tzinfo=None, microsecond=0).isoformat(),
            ),
            lambda data: galevo_item_switch_value(data.discovery.system_item("Holiday")) is False,
        )

    async def async_set_galevo_data_item(self, item_key: str, value: float) -> None:
        """Update a writable data item using the last observed gateway value."""
        if not GALEVO_WRITES_ENABLED:
            raise HomeAssistantError("Galevo data-item writes are disabled")

        def matches(data: ElcoData) -> bool:
            item = data.discovery.system_items.get(item_key)
            return bool(
                item
                and galevo_item_available(item)
                and (item.get("value") == value or NumericVariable.parse(item).value == value)
            )

        async def write(data: ElcoData) -> None:
            item = data.discovery.system_items.get(item_key)
            if item is None or not galevo_item_writable(item):
                raise ValueError("Remocon did not return a writable data item")
            if not matches(data):
                await self.api.async_set_galevo_data_item(item, value)

        await self._async_galevo_command("Data item change", write, matches)

    async def async_set_zone_mode(self, zone_number: int, mode: int) -> None:
        """Safely update a heating-zone operating mode."""
        galevo = self._system_type() == SYSTEM_TYPE_GALEVO
        if galevo and not GALEVO_WRITES_ENABLED:
            raise HomeAssistantError("Galevo zone mode writes are disabled")
        async with self._command_lock:
            try:
                await self._async_cancel_deferred_discovery()
                async with self._gateway_lock:
                    fresh = await self._async_fresh_data([zone_number])
                    zone = fresh.zones[zone_number]
                    write_failure = await self._async_write_with_reconciliation(
                        write=lambda: self.api.async_set_zone_mode(
                            fresh.plant,
                            zone,
                            mode=mode,
                        ),
                        reconcile=lambda: self._async_fresh_data([zone_number]),
                        matches=lambda state: self._zone_mode_matches(
                            state,
                            zone_number,
                            mode,
                        ),
                        description=f"Heating zone {zone_number} mode change",
                        verify_success=galevo,
                    )
                await self._async_refresh_after_write()
                if write_failure is not None:
                    raise write_failure
            except KeyError as err:
                raise HomeAssistantError(f"Heating zone {zone_number} is unavailable") from err
            except ValueError as err:
                raise HomeAssistantError(str(err)) from err
            except ElcoAuthenticationError as err:
                raise ConfigEntryAuthFailed from err
            except ElcoApiError as err:
                raise HomeAssistantError(str(err)) from err

    async def async_set_zone_holiday(
        self,
        zone_number: int,
        ends_on: date,
        *,
        starts_at: datetime,
    ) -> None:
        """Safely create or update Remocon's current holiday for one zone."""
        async with self._command_lock:
            try:
                await self._async_cancel_deferred_discovery()
                async with self._gateway_lock:
                    fresh = await self._async_fresh_data([zone_number])
                    zone = fresh.zones[zone_number]
                    current = current_holiday(zone)
                    raw_holidays = zone.raw.get("holidays")
                    if not isinstance(raw_holidays, list):
                        raise HomeAssistantError("Holiday periods are unavailable")
                    target_index = current.index if current is not None else len(raw_holidays)
                    write_failure = await self._async_write_with_reconciliation(
                        write=lambda: self.api.async_set_zone_holiday(
                            fresh.plant,
                            zone,
                            ends_on=ends_on,
                            starts_at=starts_at,
                        ),
                        reconcile=lambda: self._async_fresh_data([zone_number]),
                        matches=lambda state: self._zone_holiday_matches(
                            state,
                            zone_number,
                            ends_on,
                            target_index,
                        ),
                        description=f"Heating zone {zone_number} holiday change",
                    )
                await self._async_refresh_after_write()
                if write_failure is not None:
                    raise write_failure
            except KeyError as err:
                raise HomeAssistantError(f"Heating zone {zone_number} is unavailable") from err
            except ValueError as err:
                raise HomeAssistantError(str(err)) from err
            except ElcoAuthenticationError as err:
                raise ConfigEntryAuthFailed from err
            except ElcoApiError as err:
                raise HomeAssistantError(str(err)) from err

    async def async_cancel_zone_holiday(self, zone_number: int) -> None:
        """Safely cancel Remocon's current holiday for one zone."""
        async with self._command_lock:
            try:
                await self._async_cancel_deferred_discovery()
                async with self._gateway_lock:
                    fresh = await self._async_fresh_data([zone_number])
                    zone = fresh.zones[zone_number]
                    holiday = current_holiday(zone)
                    if holiday is None:
                        raise HomeAssistantError("No current holiday to cancel")
                    write_failure = await self._async_write_with_reconciliation(
                        write=lambda: self.api.async_cancel_zone_holiday(fresh.plant, zone),
                        reconcile=lambda: self._async_fresh_data([zone_number]),
                        matches=lambda state: self._zone_holiday_cancelled(
                            state,
                            zone_number,
                            holiday.index,
                            holiday.start,
                            holiday.end,
                        ),
                        description=f"Heating zone {zone_number} holiday cancellation",
                    )
                await self._async_refresh_after_write()
                if write_failure is not None:
                    raise write_failure
            except KeyError as err:
                raise HomeAssistantError(f"Heating zone {zone_number} is unavailable") from err
            except ValueError as err:
                raise HomeAssistantError(str(err)) from err
            except ElcoAuthenticationError as err:
                raise ConfigEntryAuthFailed from err
            except ElcoApiError as err:
                raise HomeAssistantError(str(err)) from err
