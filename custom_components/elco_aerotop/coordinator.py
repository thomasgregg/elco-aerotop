"""Data coordinator for ELCO Aerotop."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

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
from .const import DOMAIN
from .models import ElcoData

_LOGGER = logging.getLogger(__name__)


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
        self._zone_numbers: list[int] | None = None
        self._command_lock = asyncio.Lock()

    async def _async_update_data(self) -> ElcoData:
        try:
            if self._zone_numbers is None:
                self._zone_numbers = await self.api.async_get_zone_numbers()
            return await self.api.async_get_data(self._zone_numbers)
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
