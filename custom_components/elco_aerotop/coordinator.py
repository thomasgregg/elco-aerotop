"""Data coordinator for ELCO Aerotop."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable
from dataclasses import replace
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    ElcoApiClient,
    ElcoApiError,
    ElcoAuthenticationError,
    ElcoConnectionError,
)
from .capabilities import supports_cooling
from .const import BSB_DISCOVERY_GROUPS, DOMAIN
from .models import ElcoData, ReadOnlyDiscovery

_LOGGER = logging.getLogger(__name__)

_SLOW_DISCOVERY_POLL_INTERVAL = 12
_OPTIONAL_PROBE_TIMEOUT = 15
_BSB_PROBE_TIMEOUT = 30


class ElcoDataUpdateCoordinator(DataUpdateCoordinator[ElcoData]):
    """Coordinate a single poll and serialize all appliance writes."""

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
            name=f"{DOMAIN}-{api.gateway_id}",
            update_interval=timedelta(seconds=scan_interval),
            always_update=False,
        )
        self.api = api
        self._features: dict[str, Any] | None = None
        self._zone_numbers: list[int] | None = None
        self._command_lock = asyncio.Lock()
        self._discovery_lock = asyncio.Lock()
        self._slow_discovery = ReadOnlyDiscovery()
        self._polls_until_slow_discovery = 0
        self._initial_discovery_complete = False
        self._background_discovery_task: asyncio.Task[None] | None = None

    async def _async_optional_probe(
        self,
        name: str,
        awaitable: Awaitable[Any],
        status: dict[str, str],
        *,
        timeout_seconds: int = _OPTIONAL_PROBE_TIMEOUT,
    ) -> Any:
        """Run an optional read without making the main coordinator unavailable."""
        try:
            async with asyncio.timeout(timeout_seconds):
                result = await awaitable
        except Exception as err:  # An optional endpoint must never disable core polling.
            status[name] = f"unavailable:{type(err).__name__}"
            _LOGGER.debug("Optional Remocon probe %s is unavailable: %s", name, err)
            return None
        status[name] = "available"
        return result

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

    async def _async_refresh_core_discovery(
        self,
        data: ElcoData,
    ) -> ReadOnlyDiscovery:
        """Refresh entity-bearing discovery without slow optional families."""
        assert self._features is not None
        assert self._zone_numbers is not None

        status: dict[str, str] = {}
        programs, _has_cooling = self._schedule_programs(data)

        # Metadata and error history are quick, entity-bearing JSON reads. Controller-
        # bus and schedule families are intentionally deferred until setup is complete.
        plant_metadata = await self._async_optional_probe(
            "plant_metadata",
            self.api.async_get_plant_metadata(),
            status,
        )
        bus_errors = await self._async_optional_probe(
            "bus_errors",
            self.api.async_get_bus_errors(),
            status,
        )
        for group_name in BSB_DISCOVERY_GROUPS:
            status[f"bsb_points:{group_name}"] = "deferred:background"
        status["bsb_points"] = "deferred:background"
        for program in programs:
            status[f"schedule:{program}"] = "deferred:background"
        status["metering"] = (
            "deferred:background"
            if self._features.get("hasMetering", False)
            else "unsupported:feature"
        )
        status["maintenance"] = "deferred:background"
        status["bsb_plant_data"] = "deferred:background"
        status["menu_items"] = "deferred:background"

        return ReadOnlyDiscovery(
            features=self._features,
            features_response=self.api.last_features_response,
            plant_metadata=plant_metadata if isinstance(plant_metadata, dict) else {},
            bus_errors=bus_errors,
            probe_status=status,
        )

    async def async_refresh_deferred_discovery(self, *, refresh_core: bool = False) -> None:
        """Refresh slow read-only families after config-entry setup has completed."""
        async with self._discovery_lock:
            if self.data is None or self._features is None:
                return
            source_data = self.data
            if refresh_core:
                base = await self._async_refresh_core_discovery(source_data)
            else:
                base = self._slow_discovery
            status = dict(source_data.discovery.probe_status)
            status.update(base.probe_status)
            bsb_points: dict[str, Any] = {}
            available_bsb_groups = 0
            for group_name, addresses in BSB_DISCOVERY_GROUPS.items():
                group = await self._async_optional_probe(
                    f"bsb_points:{group_name}",
                    self.api.async_get_bsb_points(addresses),
                    status,
                    timeout_seconds=_BSB_PROBE_TIMEOUT,
                )
                if isinstance(group, dict):
                    bsb_points.update(group)
                    available_bsb_groups += 1
            if available_bsb_groups == len(BSB_DISCOVERY_GROUPS):
                status["bsb_points"] = "available"
            elif available_bsb_groups:
                status["bsb_points"] = "partially_available"
            else:
                status["bsb_points"] = "unavailable"

            # Remocon forwards several of these reads to the controller bus. Keep
            # every BSB and schedule request serialized to avoid gateway contention.
            programs, has_cooling = self._schedule_programs(source_data)
            schedules: dict[str, Any] = {}
            for program in programs:
                schedule = await self._async_optional_probe(
                    f"schedule:{program}",
                    self.api.async_get_schedule(program),
                    status,
                )
                if schedule is not None:
                    schedules[program] = schedule

            if self._features.get("hasMetering", False):
                metering = await self._async_optional_probe(
                    "metering",
                    self.api.async_get_metering(self._features, has_cooling=has_cooling),
                    status,
                )
            else:
                metering = None
                status["metering"] = "unsupported:feature"
            maintenance = await self._async_optional_probe(
                "maintenance",
                self.api.async_get_maintenance(),
                status,
            )
            bsb_plant_data = await self._async_optional_probe(
                "bsb_plant_data",
                self.api.async_get_bsb_plant_data(),
                status,
            )
            menu_items = await self._async_optional_probe(
                "menu_items",
                self.api.async_get_menu_items(),
                status,
            )

            self._slow_discovery = replace(
                base,
                schedules=schedules,
                metering=metering,
                maintenance=maintenance,
                bsb_points=bsb_points,
                bsb_plant_data=(bsb_plant_data if isinstance(bsb_plant_data, dict) else {}),
                menu_items=menu_items if isinstance(menu_items, dict) else {},
                probe_status=status,
            )
            current = self.data
            discovery = replace(
                self._slow_discovery,
                system_items=current.discovery.system_items,
            )
            self.async_set_updated_data(replace(current, discovery=discovery))

    def start_deferred_discovery(self, *, refresh_core: bool = False) -> None:
        """Start one managed background discovery task if none is running."""
        if (
            self._background_discovery_task is not None
            and not self._background_discovery_task.done()
        ):
            return
        self._background_discovery_task = self.hass.async_create_background_task(
            self.async_refresh_deferred_discovery(refresh_core=refresh_core),
            f"{DOMAIN} deferred discovery for {self.api.gateway_id}",
        )

    def cancel_deferred_discovery(self) -> None:
        """Cancel entry-owned discovery during unload."""
        if self._background_discovery_task is not None:
            self._background_discovery_task.cancel()

    async def _async_update_data(self) -> ElcoData:
        try:
            if self._features is None:
                self._features = await self.api.async_get_features()
            if self._zone_numbers is None:
                self._zone_numbers = await self.api.async_get_zone_numbers(self._features)
            data = await self.api.async_get_data(self._zone_numbers)

            status = dict(self._slow_discovery.probe_status)
            system_items = await self._async_optional_probe(
                "system_items",
                self.api.async_get_system_items(self._features, self._zone_numbers),
                status,
            )

            if self._polls_until_slow_discovery <= 0:
                if not self._initial_discovery_complete:
                    self._slow_discovery = await self._async_refresh_core_discovery(data)
                    self._initial_discovery_complete = True
                else:
                    self.start_deferred_discovery(refresh_core=True)
                self._polls_until_slow_discovery = _SLOW_DISCOVERY_POLL_INTERVAL - 1
                status.update(self._slow_discovery.probe_status)
            else:
                self._polls_until_slow_discovery -= 1

            discovery = replace(
                self._slow_discovery,
                features=self._features,
                features_response=self.api.last_features_response,
                system_items=system_items or {},
                probe_status=status,
            )
            return replace(data, discovery=discovery)
        except ElcoAuthenticationError as err:
            raise ConfigEntryAuthFailed from err
        except ElcoConnectionError as err:
            raise UpdateFailed(str(err)) from err
        except ElcoApiError as err:
            raise UpdateFailed(str(err)) from err

    async def _async_fresh_data(self) -> ElcoData:
        if self._zone_numbers is None:
            self._zone_numbers = await self.api.async_get_zone_numbers()
        return await self.api.async_get_data(self._zone_numbers, use_cache=False)

    async def async_set_zone_temperature(
        self,
        zone_number: int,
        kind: str,
        value: float,
    ) -> None:
        """Safely update one zone temperature while preserving its companion value."""
        async with self._command_lock:
            try:
                fresh = await self._async_fresh_data()
                zone = fresh.zones[zone_number]
                comfort = zone.comfort_temperature.value
                reduced = zone.reduced_temperature.value
                if comfort is None or reduced is None:
                    raise HomeAssistantError("Remocon did not return both zone temperatures")
                if kind == "comfort":
                    comfort = value
                elif kind == "reduced":
                    reduced = value
                else:
                    raise HomeAssistantError(f"Unsupported temperature kind: {kind}")
                await self.api.async_set_zone_temperatures(
                    zone,
                    comfort=comfort,
                    reduced=reduced,
                )
                await self.async_request_refresh()
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
        async with self._command_lock:
            try:
                fresh = await self._async_fresh_data()
                plant = fresh.plant
                new_comfort = plant.dhw_comfort_temperature.value if comfort is None else comfort
                new_reduced = plant.dhw_reduced_temperature.value if reduced is None else reduced
                new_mode = plant.dhw_mode.value if mode is None else mode
                if new_comfort is None or new_reduced is None or new_mode is None:
                    raise HomeAssistantError("Remocon did not return complete DHW settings")
                await self.api.async_set_dhw(
                    plant,
                    comfort=new_comfort,
                    reduced=new_reduced,
                    mode=new_mode,
                )
                await self.async_request_refresh()
            except ValueError as err:
                raise HomeAssistantError(str(err)) from err
            except ElcoAuthenticationError as err:
                raise ConfigEntryAuthFailed from err
            except ElcoApiError as err:
                raise HomeAssistantError(str(err)) from err

    async def async_set_zone_mode(self, zone_number: int, mode: int) -> None:
        """Safely update a heating-zone operating mode."""
        async with self._command_lock:
            try:
                fresh = await self._async_fresh_data()
                zone = fresh.zones[zone_number]
                await self.api.async_set_zone_mode(fresh.plant, zone, mode=mode)
                await self.async_request_refresh()
            except KeyError as err:
                raise HomeAssistantError(f"Heating zone {zone_number} is unavailable") from err
            except ValueError as err:
                raise HomeAssistantError(str(err)) from err
            except ElcoAuthenticationError as err:
                raise ConfigEntryAuthFailed from err
            except ElcoApiError as err:
                raise HomeAssistantError(str(err)) from err
