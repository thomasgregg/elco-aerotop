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
        self._slow_discovery = ReadOnlyDiscovery()
        self._polls_until_slow_discovery = 0

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

    async def _async_refresh_slow_discovery(
        self,
        data: ElcoData,
    ) -> ReadOnlyDiscovery:
        """Refresh low-frequency, read-only endpoint families."""
        assert self._features is not None
        assert self._zone_numbers is not None

        status: dict[str, str] = {}
        schedules: dict[str, Any] = {}
        programs = [f"ChZn{zone}" for zone in self._zone_numbers]
        has_cooling = any(supports_cooling(self._features, zone) for zone in data.zones.values())
        if has_cooling:
            programs.extend(f"CoolZn{zone}" for zone in self._zone_numbers)
        if self._features.get("dhwProgSupported", True) and not self._features.get(
            "dhwHidden", False
        ):
            programs.append("Dhw")

        # Remocon forwards several of these reads to the controller bus. Keep them
        # serialized: some gateways time out when schedule and BSB reads overlap.
        plant_metadata = await self._async_optional_probe(
            "plant_metadata",
            self.api.async_get_plant_metadata(),
            status,
        )
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
        bus_errors = await self._async_optional_probe(
            "bus_errors",
            self.api.async_get_bus_errors(),
            status,
        )

        return ReadOnlyDiscovery(
            features=self._features,
            features_response=self.api.last_features_response,
            plant_metadata=plant_metadata if isinstance(plant_metadata, dict) else {},
            schedules=schedules,
            metering=metering,
            maintenance=maintenance,
            bus_errors=bus_errors,
            bsb_points=bsb_points,
            probe_status=status,
        )

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
                self._slow_discovery = await self._async_refresh_slow_discovery(data)
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
