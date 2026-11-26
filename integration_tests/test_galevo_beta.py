"""Verify Galevo beta behavior through the real Home Assistant lifecycle."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.components.climate.const import ClimateEntityFeature
from homeassistant.components.water_heater import WaterHeaterEntityFeature
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from test_lifecycle import DOMAIN, GATEWAY_ID, existing_entry, fake_gateway, make_entry

from custom_components.elco_aerotop import config_flow
from custom_components.elco_aerotop.api import (
    ElcoApiClient,
    ElcoAuthenticationError,
    ElcoConnectionError,
    ElcoResponseError,
)
from custom_components.elco_aerotop.diagnostics import async_get_config_entry_diagnostics
from custom_components.elco_aerotop.models import (
    GALEVO_DHW_MODE_TO_RAW,
    GALEVO_ZONE_MODE_TO_RAW,
    ElcoData,
    PlantState,
    ReadOnlyDiscovery,
    ZoneState,
)
from custom_components.elco_aerotop.sensor import _energy_value


@pytest.fixture
def galevo_fixture(hass, monkeypatch):
    api, _ = fake_gateway(monkeypatch)
    api.last_features_response = {"data": {"plant": {"systemType": 3}}}
    api.async_get_galevo_time_prog.return_value = {}
    api.async_get_galevo_bus_devices.return_value = [{"name": "Modem", "sn": "SYNTHETIC-MODEM-ID"}]
    api.async_get_galevo_consumption_sequences.return_value = [
        {"k": 1, "p": 4, "v": [1] * 12},
        {"k": 2, "p": 4, "v": [2] * 11 + [None]},
    ]
    api.async_get_menu_items.return_value = {"119": {"value": 4}}
    items = {}
    zone = ZoneState.from_galevo(1, {"mod": {"value": 3, "allowedOptions": [2, 3]}}, {})

    def read(*args, **kwargs):
        snapshot_items = {key: dict(value) for key, value in items.items()}
        return ElcoData(
            GATEWAY_ID, PlantState.from_galevo(snapshot_items), {1: zone}
        ), snapshot_items

    api.async_get_galevo_data.side_effect = read
    return api, make_entry(hass), items


@pytest.fixture
def galevo_metering(galevo_fixture):
    api, entry, _ = galevo_fixture
    api.async_get_features.return_value = {
        **api.async_get_features.return_value,
        "hasMetering": True,
    }
    path = Path(__file__).parents[1] / "tests/fixtures/galevo_metering.json"
    report = json.loads(path.read_text(encoding="utf-8"))
    api.async_get_metering.return_value = report
    return api, entry, report


def _estimated_jaz_entity(hass):
    return er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"{GATEWAY_ID}_galevo_estimated_jaz"
    )


@pytest.mark.freeze_time("2026-07-01T12:00:00Z", real_asyncio=True)
async def test_jaz_waits_for_complete_data_without_delaying_existing_sensors(hass, galevo_metering):
    api, entry, report = galevo_metering
    started = asyncio.Event()
    release = asyncio.Event()

    async def read(*args, **kwargs):
        started.set()
        await release.wait()
        return {**report, "oldestMonth": "February 2025"}

    api.async_get_metering.side_effect = read
    assert await hass.config_entries.async_setup(entry.entry_id)
    try:
        await asyncio.wait_for(started.wait(), 5)
        await hass.async_block_till_done()
        energy = er.async_get(hass).async_get_entity_id(
            "sensor", DOMAIN, f"{GATEWAY_ID}_energy_heating_last_12_complete_months"
        )
        assert float(hass.states.get(energy).state) == 12
        assert _estimated_jaz_entity(hass) is None
    finally:
        release.set()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert _estimated_jaz_entity(hass) is None

    api.async_get_metering.side_effect = None
    await entry.runtime_data.async_refresh_deferred_discovery()
    await hass.async_block_till_done()
    entity = _estimated_jaz_entity(hass)
    state = hass.states.get(entity)
    assert er.async_get(hass).async_get(entity).original_name == (
        "Estimated SPF – previous calendar year"
    )
    assert float(state.state) == pytest.approx(1320 / 420, abs=0.005)
    assert state.attributes["year"] == 2025
    assert api.async_get_metering.await_args.kwargs["retry_auth"] is True


@pytest.mark.freeze_time("2026-07-01T12:00:00Z", real_asyncio=True)
async def test_jaz_id_survives_optional_errors_reload_and_language_change(hass, galevo_metering):
    api, entry, _ = galevo_metering
    registry = er.async_get(hass)
    entity = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{GATEWAY_ID}_galevo_estimated_jaz",
        config_entry=entry,
        has_entity_name=True,
        original_name="Estimated JAZ – previous calendar year",
        suggested_object_id="elco_example_estimated_jaz_previous_calendar_year",
    ).entity_id
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert _estimated_jaz_entity(hass) == entity
    assert registry.async_get(entity).original_name == "Estimated SPF – previous calendar year"

    api.async_get_metering.side_effect = TimeoutError("metering timed out")
    coordinator = entry.runtime_data
    await coordinator.async_refresh_deferred_discovery()
    await hass.async_block_till_done()
    assert hass.states.get(entity).state == "unavailable"
    assert coordinator.last_update_success
    assert coordinator.data.discovery.probe_status["metering"] == "unavailable:TimeoutError"

    hass.config.language = "de"
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert _estimated_jaz_entity(hass) == entity
    assert registry.async_get(entity).original_name == "Geschätzte Jahresarbeitszahl – Vorjahr"
    assert hass.states.get(entity).state == "unavailable"

    api.async_get_metering.side_effect = None
    await entry.runtime_data.async_refresh_deferred_discovery()
    await hass.async_block_till_done()
    assert _estimated_jaz_entity(hass) == entity
    assert float(hass.states.get(entity).state) == pytest.approx(1320 / 420, abs=0.005)


async def test_galevo_without_metering_does_not_request_it(hass, galevo_fixture):
    api, entry, _ = galevo_fixture
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    api.async_get_metering.assert_not_awaited()
    assert _estimated_jaz_entity(hass) is None


async def test_unload_cancels_metering(hass, galevo_metering):
    api, entry, _ = galevo_metering
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def read(*args, **kwargs):
        started.set()
        try:
            await asyncio.Future()
        finally:
            cancelled.set()

    api.async_get_metering.side_effect = read
    assert await hass.config_entries.async_setup(entry.entry_id)
    await asyncio.wait_for(started.wait(), 5)
    assert await asyncio.wait_for(hass.config_entries.async_unload(entry.entry_id), 5)
    assert cancelled.is_set()
    assert _estimated_jaz_entity(hass) is None


@pytest.mark.freeze_time("2026-07-01T12:00:00Z", real_asyncio=True)
async def test_metering_honors_server_backoff(hass, galevo_metering):
    api, entry, _ = galevo_metering
    api.async_get_metering.side_effect = ElcoResponseError(
        "Remocon requested a delay", status=429, retry_after=120
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    coordinator = entry.runtime_data
    assert coordinator.last_update_success
    assert 0 < coordinator._gateway_backoff() <= 120
    assert _estimated_jaz_entity(hass) is None
    await coordinator.async_refresh_deferred_discovery()
    await coordinator.async_refresh()
    api.async_get_metering.assert_awaited_once()
    api.async_get_galevo_data.assert_awaited_once()


@pytest.mark.freeze_time("2026-12-31T22:59:59Z", real_asyncio=True)
async def test_discards_metering_request_spanning_local_new_year(hass, galevo_metering, freezer):
    api, entry, report = galevo_metering
    await hass.config.async_set_time_zone("Europe/Berlin")
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    entity = _estimated_jaz_entity(hass)
    assert hass.states.get(entity).attributes["year"] == 2025

    async def cross_year(*args, **kwargs):
        freezer.move_to("2026-12-31T23:00:01Z")
        return report

    api.async_get_metering.side_effect = cross_year
    coordinator = entry.runtime_data
    await coordinator.async_refresh_deferred_discovery()
    await hass.async_block_till_done()
    assert coordinator.data.discovery.galevo_annual_metering is None
    assert hass.states.get(entity).state == "unavailable"

    api.async_get_metering.side_effect = None
    await coordinator.async_refresh_deferred_discovery()
    await hass.async_block_till_done()
    assert hass.states.get(entity).attributes["year"] == 2026


def _plant_mode(value=1, *, read_only=False):
    return {
        "value": value,
        "options": [0, 1],
        "optTexts": ["Summer", "Winter"],
        "readOnly": read_only,
    }


def _scheduled_dhw_items(mode=1):
    return {
        "DhwMode:0": {"value": mode, "options": [0, 1, 2]},
        "DhwTimeProgComfortTemp:0": {
            "value": 53,
            "min": 35,
            "max": 60,
            "step": 1,
            "readOnly": False,
        },
        "DhwTimeProgEconomyTemp:0": {"value": 40},
    }


def _existing_scheduled_comfort(hass, entry):
    return (
        er.async_get(hass)
        .async_get_or_create(
            "number",
            DOMAIN,
            f"{GATEWAY_ID}_dhw_scheduled_comfort_temperature",
            suggested_object_id="existing_scheduled_comfort",
            config_entry=entry,
        )
        .entity_id
    )


@pytest.mark.parametrize("readback", ["confirmed", "unchanged", "economy_changed"])
async def test_beta_scheduled_hot_water_number_verifies_both_setpoints(
    hass, galevo_fixture, readback
):
    api, entry, items = galevo_fixture
    items.update(_scheduled_dhw_items())
    number = _existing_scheduled_comfort(hass, entry)

    def write(*, comfort, economy, old_comfort, old_economy):
        if readback != "unchanged":
            items["DhwTimeProgComfortTemp:0"]["value"] = comfort
        if readback == "economy_changed":
            items["DhwTimeProgEconomyTemp:0"]["value"] = economy + 1

    api.async_set_galevo_dhw_timeprog_temperatures.side_effect = write
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    registry = er.async_get(hass)
    assert hass.states.get(number).attributes["mode"] == "box"
    assert registry.async_get_entity_id("select", DOMAIN, f"{GATEWAY_ID}_dhw_mode") is None
    assert (
        registry.async_get_entity_id("number", DOMAIN, f"{GATEWAY_ID}_dhw_reduced_temperature")
        is not None
    )
    api.async_get_galevo_data.reset_mock()
    call = hass.services.async_call(
        "number", "set_value", {"entity_id": number, "value": 52}, blocking=True
    )
    if readback == "confirmed":
        await call
        assert float(hass.states.get(number).state) == 52
    else:
        with pytest.raises(HomeAssistantError, match="not confirmed"):
            await call
    api.async_set_galevo_dhw_timeprog_temperatures.assert_awaited_once_with(
        comfort=52, economy=40, old_comfort=53, old_economy=40
    )
    api.async_set_galevo_dhw_temp.assert_not_awaited()
    api.async_set_galevo_dhw_mode.assert_not_awaited()
    assert api.async_get_galevo_data.await_count == (2 if readback == "confirmed" else 3)


async def test_scheduled_comfort_checks_fresh_mode_availability_and_bounds(hass, galevo_fixture):
    api, entry, items = galevo_fixture
    items.update(_scheduled_dhw_items(mode=2))
    number = _existing_scheduled_comfort(hass, entry)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    coordinator = entry.runtime_data
    items.update(_scheduled_dhw_items())
    await coordinator.async_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert float(hass.states.get(number).state) == 53

    for value, error in [(39, "below economy"), (61, "above the maximum"), (52.5, "step")]:
        with pytest.raises(HomeAssistantError, match=error):
            await coordinator.async_set_scheduled_dhw_comfort_temperature(value)
    await coordinator.async_set_scheduled_dhw_comfort_temperature(53)
    items["DhwMode:0"]["value"] = 2
    with pytest.raises(HomeAssistantError, match="Time Based"):
        await coordinator.async_set_scheduled_dhw_comfort_temperature(52)
    await coordinator.async_refresh()
    assert hass.states.get(number).state == "unavailable"
    api.async_set_galevo_dhw_timeprog_temperatures.assert_not_awaited()


@pytest.mark.parametrize("missing", ["target", "options"])
async def test_native_controls_recover_from_partial_discovery(hass, galevo_fixture, missing):
    api, entry, items = galevo_fixture
    full = _scheduled_dhw_items() | {
        "PlantMode:0": {"value": 1},
        "ZoneMode:1": {"value": 3, "options": [0, 1, 2, 3]},
        "ZoneComfortTemp:1": {"value": 21, "min": 10, "max": 30},
        "ZoneEconomyTemp:1": {"value": 17},
    }
    items.update(full)
    if missing == "target":
        del items["ZoneComfortTemp:1"], items["DhwTimeProgComfortTemp:0"]
    else:
        items["ZoneMode:1"] = {"value": 3}
        items["DhwMode:0"] = {"value": 1}

    def read(*args, **kwargs):
        snapshot = {key: dict(item) for key, item in items.items()}
        return ElcoData(
            GATEWAY_ID,
            PlantState.from_galevo(snapshot),
            {1: ZoneState.from_galevo(1, {}, snapshot)},
        ), snapshot

    api.async_get_galevo_data.side_effect = read
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    registry = er.async_get(hass)
    thermostat = registry.async_get_entity_id("climate", DOMAIN, f"{GATEWAY_ID}_zone_1_thermostat")
    heater = registry.async_get_entity_id(
        "water_heater", DOMAIN, f"{GATEWAY_ID}_domestic_hot_water"
    )
    climate_features = hass.states.get(thermostat).attributes["supported_features"]
    heater_features = hass.states.get(heater).attributes["supported_features"]
    assert bool(climate_features & ClimateEntityFeature.TARGET_TEMPERATURE) == (
        missing == "options"
    )
    assert bool(heater_features & WaterHeaterEntityFeature.TARGET_TEMPERATURE) == (
        missing == "options"
    )
    assert bool(climate_features & ClimateEntityFeature.PRESET_MODE) == (missing == "target")
    assert bool(heater_features & WaterHeaterEntityFeature.OPERATION_MODE) == (missing == "target")

    if missing == "target":

        def set_operation(plant, *, comfort, reduced, mode):
            items["DhwMode:0"] = {**items["DhwMode:0"], "value": GALEVO_DHW_MODE_TO_RAW[mode]}

        api.async_set_galevo_dhw_settings.side_effect = set_operation
        await hass.services.async_call(
            "water_heater",
            "set_operation_mode",
            {"entity_id": heater, "operation_mode": "off"},
            blocking=True,
        )
        assert hass.states.get(heater).state == "off"

    items.update(full)
    await entry.runtime_data.async_refresh()
    assert hass.states.get(thermostat).attributes["temperature"] == 21
    assert hass.states.get(thermostat).attributes["preset_modes"] == ["eco", "comfort"]
    assert hass.states.get(heater).attributes["temperature"] == 53
    assert hass.states.get(heater).attributes["operation_list"] == [
        "off",
        "heat_pump",
        "performance",
    ]

    items.clear()
    await entry.runtime_data.async_refresh()
    assert hass.states.get(thermostat).state == "unavailable"
    assert hass.states.get(heater).state == "unavailable"
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get(thermostat).state == "unavailable"
    assert hass.states.get(heater).state == "unavailable"
    items.update(full)
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get(thermostat).attributes["temperature"] == 21
    assert hass.states.get(heater).attributes["temperature"] == 53
    for domain, key in (
        ("select", "dhw_mode"),
        ("select", "zone_1_mode"),
        ("number", "dhw_comfort_temperature"),
        ("number", "dhw_scheduled_comfort_temperature"),
        ("number", "zone_1_comfort_temperature"),
    ):
        assert registry.async_get_entity_id(domain, DOMAIN, f"{GATEWAY_ID}_{key}") is None


async def test_late_capability_energy_and_privacy(hass, galevo_fixture):
    api, entry, items = galevo_fixture
    items["DhwTemp:0"] = {"value": 55}
    items["DhwMode:0"] = {"value": 2, "options": [0, 2]}
    items["Holiday:0"] = {"value": 1, "readOnly": False}
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    registry = er.async_get(hass)
    coordinator = entry.runtime_data
    assert coordinator.data.discovery.system_type == 3
    assert registry.async_get_entity_id("select", DOMAIN, f"{GATEWAY_ID}_plant_mode") is None
    assert registry.async_get_entity_id("switch", DOMAIN, f"{GATEWAY_ID}_holiday") is not None
    assert (
        registry.async_get_entity_id("water_heater", DOMAIN, f"{GATEWAY_ID}_domestic_hot_water")
        is not None
    )
    assert (
        registry.async_get_entity_id(
            "sensor", DOMAIN, f"{GATEWAY_ID}_annual_energy_input_heating_1"
        )
        is None
    )
    energy = registry.async_get_entity_id(
        "sensor", DOMAIN, f"{GATEWAY_ID}_energy_heating_last_12_complete_months"
    )
    assert float(hass.states.get(energy).state) == 12
    data = coordinator.data
    assert _energy_value(data, 2, 4, "last_month") is None
    assert _energy_value(data, 2, 4, "last_12_complete_months") is None
    ready = await async_get_config_entry_diagnostics(hass, entry)
    assert ready["data"]["discovery"]["bus_devices"][0]["sn"] == "<redacted>"

    items["PlantMode:0"] = _plant_mode(read_only=True)
    await coordinator.async_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert registry.async_get_entity_id("select", DOMAIN, f"{GATEWAY_ID}_plant_mode") is None
    items["PlantMode:0"] = _plant_mode()
    await coordinator.async_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)
    select_id = registry.async_get_entity_id("select", DOMAIN, f"{GATEWAY_ID}_plant_mode")
    assert hass.states.get(select_id).state == "Winter"
    api.async_set_galevo_holiday.assert_not_awaited()


@pytest.mark.parametrize(
    ("months", "last_month", "last_year"),
    [
        pytest.param([], None, None, id="no-history"),
        pytest.param("invalid", None, None, id="malformed-series"),
        pytest.param([8.25], 8.25, None, id="first-completed-month"),
        pytest.param([1] * 11, 1, None, id="fewer-than-twelve-months"),
        pytest.param([1] * 11 + [2.5], 2.5, 13.5, id="exactly-twelve-months"),
        pytest.param([200] + [1] * 11 + [2.5], 2.5, 13.5, id="exclude-older-months"),
        pytest.param([None] + [1] * 11 + [2.5], 2.5, 13.5, id="ignore-older-gap"),
        pytest.param([1] * 11 + [0], 0, 11, id="zero-latest-month"),
        pytest.param([0] * 12, 0, 0, id="zero-throughout"),
        pytest.param([1] * 11 + [None], None, None, id="missing-latest-month"),
        pytest.param([None] + [1] * 11, 1, None, id="gap-within-year"),
        pytest.param([1] * 11 + [False], None, None, id="boolean-latest-month"),
        pytest.param([1] * 11 + ["2.5"], None, None, id="string-latest-month"),
        pytest.param([1] * 11 + [float("nan")], None, None, id="nan-latest-month"),
        pytest.param([1] * 11 + [float("inf")], None, None, id="infinite-latest-month"),
    ],
)
def test_galevo_completed_month_windows(months, last_month, last_year):
    data = ElcoData(
        GATEWAY_ID,
        PlantState.from_galevo({}),
        discovery=ReadOnlyDiscovery(energy_sequences=[{"k": 1, "p": 4, "v": months}]),
    )
    assert _energy_value(data, 1, 4, "last_month") == last_month
    assert _energy_value(data, 1, 4, "last_12_complete_months") == last_year


async def test_galevo_monthly_entities_publish_latest_completed_month(hass, galevo_fixture):
    api, entry, _ = galevo_fixture
    # Issue #1 had 22 monthly buckets. The newest was the first nonzero heating
    # month, hot water had a different previous total, and cooling ended at zero.
    # Use synthetic readings with the same layout.
    reports = [
        {"k": 1, "p": 4, "v": [0] * 21 + [37.125]},
        {"k": 2, "p": 4, "v": [0] * 18 + [1.25, 70.5, 78.875, 105.625]},
        {"k": 3, "p": 4, "v": [0] * 19 + [40.5, 19.75, 0]},
        {"k": 1, "p": 1, "v": [0.5] * 24},
        {"k": 1, "p": 2, "v": [2] * 7},
    ]
    api.async_get_galevo_consumption_sequences.return_value = reports
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    registry = er.async_get(hass)

    def entity_id(key):
        return registry.async_get_entity_id("sensor", DOMAIN, f"{GATEWAY_ID}_energy_{key}")

    expected = {
        "heating_last_month": 37.125,
        "heating_last_12_complete_months": 37.125,
        "dhw_last_month": 105.625,
        "dhw_last_12_complete_months": 256.25,
        "cooling_last_month": 0,
        "cooling_last_12_complete_months": 60.25,
        "heating_last_24_hours": 12,
        "heating_last_7_days": 14,
    }
    ids = {key: entity_id(key) for key in expected}
    for key, value in expected.items():
        assert float(hass.states.get(ids[key]).state) == pytest.approx(value)

    # A new completed month refreshes both windows under the same registry IDs.
    api.async_get_galevo_consumption_sequences.return_value = [
        {**reports[0], "v": reports[0]["v"] + [22.5]},
        *reports[1:],
    ]
    await entry.runtime_data.async_refresh_deferred_discovery()
    await hass.async_block_till_done()
    assert {key: entity_id(key) for key in expected} == ids
    assert float(hass.states.get(ids["heating_last_month"]).state) == pytest.approx(22.5)
    assert float(hass.states.get(ids["heating_last_12_complete_months"]).state) == pytest.approx(
        59.625
    )


@pytest.mark.parametrize("error", [None, ElcoResponseError("Rejected")])
async def test_plant_mode_locks_and_reports_unconfirmed_changes(hass, galevo_fixture, error):
    api, entry, items = galevo_fixture
    items["PlantMode:0"] = _plant_mode()
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    coordinator = entry.runtime_data
    api.async_get_galevo_data.reset_mock()
    api.async_set_galevo_plant_mode.side_effect = error
    async with coordinator._gateway_lock:
        command = asyncio.create_task(coordinator.async_set_plant_mode(0))
        await asyncio.sleep(0)
        api.async_set_galevo_plant_mode.assert_not_awaited()
    with pytest.raises(HomeAssistantError, match="not confirmed" if error is None else "Rejected"):
        await command
    api.async_set_galevo_plant_mode.assert_awaited_once_with(0, old_mode=1)
    assert coordinator.data.discovery.system_value("PlantMode") == 1
    assert api.async_get_galevo_data.await_count == (3 if error is None else 1)


@pytest.mark.parametrize("confirm_on_read", [2, 3], ids=["immediate", "late"])
async def test_plant_mode_publishes_the_confirming_snapshot(hass, galevo_fixture, confirm_on_read):
    api, entry, items = galevo_fixture
    items["PlantMode:0"] = _plant_mode()
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    coordinator = entry.runtime_data
    previous = coordinator.data
    api.async_get_galevo_data.reset_mock()
    read_snapshot = api.async_get_galevo_data.side_effect

    def read(*args, **kwargs):
        if api.async_get_galevo_data.await_count == confirm_on_read:
            items["PlantMode:0"] = _plant_mode(value=0)
        return read_snapshot(*args, **kwargs)

    api.async_get_galevo_data.side_effect = read
    select_id = er.async_get(hass).async_get_entity_id("select", DOMAIN, f"{GATEWAY_ID}_plant_mode")
    await hass.services.async_call(
        "select", "select_option", {"entity_id": select_id, "option": "Summer"}, blocking=True
    )

    api.async_set_galevo_plant_mode.assert_awaited_once_with(0, old_mode=1)
    assert api.async_get_galevo_data.await_count == confirm_on_read
    assert hass.states.get(select_id).state == "Summer"
    assert coordinator.data.discovery.probe_status["system_items"] == "available"
    assert coordinator.data.discovery.bus_devices == previous.discovery.bus_devices
    assert coordinator.last_successful_update == coordinator.data.captured_at
    assert coordinator.last_successful_update > previous.captured_at


async def test_consecutive_plant_mode_commands_publish_complete_multizone_snapshots(
    hass, galevo_fixture
):
    api, entry, items = galevo_fixture
    items["PlantMode:0"] = _plant_mode()
    api.async_get_zone_numbers.return_value = [1, 2]
    before_first_confirmation = asyncio.Event()
    release_first_confirmation = asyncio.Event()
    read_snapshot = api.async_get_galevo_data.side_effect

    async def read(features, zones, *, culture, allow_partial_zones=False):
        assert zones == [1, 2]
        if api.async_get_galevo_data.await_count == 3:
            before_first_confirmation.set()
            await release_first_confirmation.wait()
        data, snapshot_items = read_snapshot(features, zones, culture=culture)
        zone_2 = ZoneState.from_galevo(2, {"mod": {"value": 3}}, snapshot_items)
        return replace(data, zones={**data.zones, 2: zone_2}), snapshot_items

    def write(mode, *, old_mode):
        assert old_mode == items["PlantMode:0"]["value"]
        items["PlantMode:0"] = _plant_mode(value=mode)

    api.async_get_galevo_data.side_effect = read
    api.async_set_galevo_plant_mode.side_effect = write
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    coordinator = entry.runtime_data
    published = []
    coordinator.async_add_listener(
        lambda: published.append(
            (coordinator.data.discovery.system_value("PlantMode"), set(coordinator.data.zones))
        )
    )

    async with asyncio.timeout(2), asyncio.TaskGroup() as commands:
        commands.create_task(coordinator.async_set_plant_mode(0))
        await before_first_confirmation.wait()
        commands.create_task(coordinator.async_set_plant_mode(1))
        await asyncio.sleep(0)
        api.async_set_galevo_plant_mode.assert_awaited_once_with(0, old_mode=1)
        release_first_confirmation.set()

    assert api.async_set_galevo_plant_mode.await_count == 2
    assert api.async_get_galevo_data.await_count == 5
    assert published == [(0, {1, 2}), (1, {1, 2})]


@pytest.mark.parametrize(
    ("failure_on_read", "error", "ambiguous"),
    [
        (2, ElcoConnectionError("readback timed out"), False),
        (2, ElcoAuthenticationError("session expired"), False),
        (2, ElcoResponseError("rate limited", status=429, retry_after=120), False),
        (3, ElcoResponseError("rate limited", status=429, retry_after=120), False),
        (3, ElcoResponseError("rate limited", status=429, retry_after=120), True),
    ],
    ids=[
        "timeout",
        "session-expired",
        "rate-limited",
        "follow-up-rate-limited",
        "lost-response-rate-limited",
    ],
)
async def test_plant_mode_readback_failure_never_replays_the_write(
    hass, galevo_fixture, monkeypatch, failure_on_read, error, ambiguous
):
    api, entry, items = galevo_fixture
    items["PlantMode:0"] = _plant_mode()
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    coordinator = entry.runtime_data
    previous = coordinator.data
    start_reauth = Mock()
    monkeypatch.setattr(type(entry), "async_start_reauth", start_reauth)
    api.async_get_galevo_data.reset_mock()
    read_snapshot = api.async_get_galevo_data.side_effect

    def read(*args, **kwargs):
        if api.async_get_galevo_data.await_count == failure_on_read:
            raise error
        return read_snapshot(*args, **kwargs)

    api.async_get_galevo_data.side_effect = read
    if ambiguous:
        api.async_set_galevo_plant_mode.side_effect = ElcoConnectionError("write response lost")

    with pytest.raises(HomeAssistantError, match="could not be verified"):
        await coordinator.async_set_plant_mode(0)

    api.async_set_galevo_plant_mode.assert_awaited_once_with(0, old_mode=1)
    assert api.async_get_galevo_data.await_count == failure_on_read
    assert coordinator.data is previous
    if isinstance(error, ElcoAuthenticationError):
        start_reauth.assert_called_once_with(hass)
    else:
        start_reauth.assert_not_called()
    if isinstance(error, ElcoResponseError):
        assert coordinator._gateway_backoff() > 0
        with pytest.raises(HomeAssistantError, match="requested a polling delay"):
            await coordinator.async_set_plant_mode(0)
        api.async_set_galevo_plant_mode.assert_awaited_once()
        assert api.async_get_galevo_data.await_count == failure_on_read


async def test_ambiguous_plant_mode_write_can_be_verified_without_replay(hass, galevo_fixture):
    api, entry, items = galevo_fixture
    items["PlantMode:0"] = _plant_mode()

    def apply_then_disconnect(mode, *, old_mode):
        assert mode == 0 and old_mode == 1
        items["PlantMode:0"] = _plant_mode(value=mode)
        raise ElcoConnectionError("Connection lost after command")

    api.async_set_galevo_plant_mode.side_effect = apply_then_disconnect
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    coordinator = entry.runtime_data
    api.async_get_galevo_data.reset_mock()
    await coordinator.async_set_plant_mode(0)
    api.async_set_galevo_plant_mode.assert_awaited_once()
    assert api.async_get_galevo_data.await_count == 2
    assert coordinator.data.discovery.system_value("PlantMode") == 0


async def test_reauthentication_checks_gateway_membership(hass, monkeypatch):
    entry, _, _ = existing_entry(hass)
    client = Mock(
        gateway_id=GATEWAY_ID,
        async_login=AsyncMock(),
        async_list_plants=AsyncMock(return_value=[{"gwId": "DIFFERENT-GATEWAY"}]),
        async_get_features=AsyncMock(),
    )
    monkeypatch.setattr(config_flow, "ElcoApiClient", lambda *args: client)
    flow = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "reauth", "entry_id": entry.entry_id}, data=dict(entry.data)
    )
    result = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {"username": "different@example.invalid", "password": "synthetic"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "gateway_access_denied"}
    assert entry.data["username"] == "synthetic@example.invalid"
    client.async_get_features.assert_not_awaited()


async def test_bad_snapshot_keeps_last_good_read(hass, galevo_fixture):
    api, entry, items = galevo_fixture
    items["PlantMode:0"] = _plant_mode()
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    coordinator = entry.runtime_data
    old_data = coordinator.data
    api.async_get_galevo_data.side_effect = ElcoResponseError("Invalid response")
    await coordinator.async_refresh()
    assert not coordinator.last_update_success
    assert coordinator.data is old_data
    assert coordinator.last_successful_update == old_data.captured_at


@pytest.fixture
def galevo_zone_poll(galevo_fixture, monkeypatch):
    api, entry, items = galevo_fixture
    api.async_get_zone_numbers.return_value = [1, 2]
    items.update(
        _scheduled_dhw_items()
        | {
            "PlantMode:0": _plant_mode(),
            "OutsideTemp:0": {"value": 4},
            "HeatingFlowTemp:1": {"value": 30, "min": 15, "max": 50},
        }
    )
    client = ElcoApiClient(Mock(), "user", "pass", GATEWAY_ID, "https://example.test")
    client.last_features_response = api.last_features_response
    zone_errors: dict[int, ElcoResponseError] = {}

    def read_items(*args, **kwargs):
        return {key: dict(value) for key, value in items.items()}

    def read_zone(zone_number):
        if zone_number in zone_errors:
            raise zone_errors[zone_number]
        return {
            "mod": {"value": 3, "allowedOptions": [2, 3]},
            "comf": {"value": 21, "min": 10, "max": 30},
            "econ": {"value": 17, "min": 10, "max": 30},
            "room": 20 + zone_number,
        }

    monkeypatch.setattr(client, "async_get_system_items", AsyncMock(side_effect=read_items))
    monkeypatch.setattr(client, "async_get_galevo_zone", AsyncMock(side_effect=read_zone))
    api.async_get_galevo_data.side_effect = client.async_get_galevo_data
    return api, entry, client, items, zone_errors


@pytest.mark.parametrize("failed_zones", [(1,), (1, 2)], ids=["one-zone", "all-zones"])
async def test_partial_poll_preserves_fresh_plant_and_recovers_same_entities(
    hass, galevo_zone_poll, failed_zones
):
    api, entry, _, items, zone_errors = galevo_zone_poll
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    coordinator = entry.runtime_data
    registry = er.async_get(hass)
    thermostat = {
        zone: registry.async_get_entity_id(
            "climate", DOMAIN, f"{GATEWAY_ID}_zone_{zone}_thermostat"
        )
        for zone in (1, 2)
    }
    heater = registry.async_get_entity_id(
        "water_heater", DOMAIN, f"{GATEWAY_ID}_domestic_hot_water"
    )
    outdoor = registry.async_get_entity_id("sensor", DOMAIN, f"{GATEWAY_ID}_outside_temperature")
    flow = registry.async_get_entity_id(
        "number", DOMAIN, f"{GATEWAY_ID}_zone_1_heating_flow_temperature"
    )
    assert all(thermostat.values()) and heater and outdoor and flow
    timestamp = coordinator.last_successful_update
    items["OutsideTemp:0"]["value"] = 5
    zone_errors.update({zone: ElcoResponseError("Invalid zone response") for zone in failed_zones})

    for delay in (60, 300, 900, 3600):
        await coordinator.async_refresh()
        assert coordinator.last_update_success
        assert coordinator.update_interval == timedelta(seconds=delay)
        assert coordinator.data.unavailable_zones == failed_zones
        assert coordinator.last_successful_update == timestamp
        assert float(hass.states.get(outdoor).state) == 5
        assert hass.states.get(heater).state == "heat_pump"
        assert hass.states.get(flow).state == "unavailable"
        for zone, entity_id in thermostat.items():
            assert hass.states.get(entity_id).state == (
                "unavailable" if zone in failed_zones else "auto"
            )

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    assert diagnostics["data"]["unavailable_zones"] == list(failed_zones)
    with pytest.raises(HomeAssistantError, match="Invalid zone response"):
        await coordinator.async_set_zone_temperature(1, "comfort", 22)
    api.async_set_zone_temperatures.assert_not_awaited()

    zone_errors.clear()
    await coordinator.async_refresh()
    assert coordinator.data.unavailable_zones == ()
    assert coordinator.last_successful_update == coordinator.data.captured_at > timestamp
    assert coordinator.update_interval == timedelta(hours=1)
    assert float(hass.states.get(flow).state) == 30
    for zone, entity_id in thermostat.items():
        assert hass.states.get(entity_id).state == "auto"
        assert entity_id == registry.async_get_entity_id(
            "climate", DOMAIN, f"{GATEWAY_ID}_zone_{zone}_thermostat"
        )


async def test_healthy_zone_command_requires_fresh_target_and_survives_other_zone_failure(
    hass, galevo_zone_poll
):
    api, entry, client, _, zone_errors = galevo_zone_poll
    zone_errors[1] = ElcoResponseError("Invalid zone response")
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    coordinator = entry.runtime_data
    initial_read = client.async_get_galevo_zone.side_effect
    comfort_target = 21

    def read_zone(zone_number):
        data = initial_read(zone_number)
        data["comf"]["value"] = comfort_target
        return data

    def write(zone, *, comfort, reduced, cooling):
        nonlocal comfort_target
        assert zone.number == 2
        assert zone.comfort_temperature.value == 21
        assert reduced == 17
        assert not cooling
        comfort_target = comfort

    client.async_get_galevo_zone.side_effect = read_zone
    api.async_set_zone_temperatures.side_effect = write
    await coordinator.async_set_zone_temperature(2, "comfort", 22)

    api.async_set_zone_temperatures.assert_awaited_once()
    assert coordinator.data.unavailable_zones == (1,)
    thermostat = er.async_get(hass).async_get_entity_id(
        "climate", DOMAIN, f"{GATEWAY_ID}_zone_2_thermostat"
    )
    assert hass.states.get(thermostat).attributes["temperature"] == 22


async def test_partial_first_poll_discovers_failed_zone_only_after_recovery(hass, galevo_zone_poll):
    _, entry, _, _, zone_errors = galevo_zone_poll
    zone_errors[1] = ElcoResponseError("Invalid zone response")
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    coordinator = entry.runtime_data
    registry = er.async_get(hass)
    heater = registry.async_get_entity_id(
        "water_heater", DOMAIN, f"{GATEWAY_ID}_domestic_hot_water"
    )

    assert hass.states.get(heater).state == "heat_pump"
    assert coordinator.last_successful_update is None
    assert not registry.async_get_entity_id("climate", DOMAIN, f"{GATEWAY_ID}_zone_1_thermostat")
    assert registry.async_get_entity_id("climate", DOMAIN, f"{GATEWAY_ID}_zone_2_thermostat")

    zone_errors.clear()
    await coordinator.async_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)
    thermostat = registry.async_get_entity_id("climate", DOMAIN, f"{GATEWAY_ID}_zone_1_thermostat")
    assert hass.states.get(thermostat).state == "auto"
    assert coordinator.last_successful_update is not None


@pytest.mark.parametrize("scope", ["plant", "zone-rate-limit"])
async def test_unusable_plant_or_zone_rate_limit_still_fails_full_poll(
    hass, galevo_zone_poll, scope
):
    _, entry, client, items, zone_errors = galevo_zone_poll
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    coordinator = entry.runtime_data
    registry = er.async_get(hass)
    heater = registry.async_get_entity_id(
        "water_heater", DOMAIN, f"{GATEWAY_ID}_domestic_hot_water"
    )
    old_data = coordinator.data
    client.async_get_galevo_zone.reset_mock()
    if scope == "plant":
        items.clear()
    else:
        zone_errors[1] = ElcoResponseError("Rate limited", status=429, retry_after=120)

    await coordinator.async_refresh()

    assert not coordinator.last_update_success
    assert coordinator.data is old_data
    assert coordinator.last_successful_update == old_data.captured_at
    assert hass.states.get(heater).state == "unavailable"
    if scope == "plant":
        client.async_get_galevo_zone.assert_not_awaited()
        assert coordinator.last_exception.retry_after == 60
    else:
        client.async_get_galevo_zone.assert_awaited_once_with(1)
        assert coordinator._gateway_backoff() > 0
        assert coordinator.last_exception.retry_after == 120
        client.async_get_system_items.reset_mock()
        await coordinator.async_refresh()
        client.async_get_system_items.assert_not_awaited()


async def test_holiday_uses_explicit_draft_and_checks_readback(hass, galevo_fixture, monkeypatch):
    api, entry, items = galevo_fixture
    items["Holiday:0"] = {"value": 0, "readOnly": False}
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    coordinator = entry.runtime_data
    monkeypatch.setattr(
        coordinator, "_local_now", lambda: datetime.fromisoformat("2026-11-22T12:34:56+01:00")
    )
    with pytest.raises(HomeAssistantError, match="both holiday draft dates"):
        await coordinator.async_set_galevo_holiday()
    coordinator.set_pending_holiday(start=date(2026, 11, 24), end=date(2026, 11, 23))
    with pytest.raises(HomeAssistantError, match="ordered holiday window"):
        await coordinator.async_set_galevo_holiday()
    api.async_set_galevo_holiday.assert_not_awaited()
    registry = er.async_get(hass)
    for boundary, value in (("start", "2026-11-24"), ("end", "2026-11-26")):
        draft = registry.async_get_entity_id("date", DOMAIN, f"{GATEWAY_ID}_holiday_{boundary}")
        await hass.services.async_call(
            "date", "set_value", {"entity_id": draft, "date": value}, blocking=True
        )
    holiday = registry.async_get_entity_id("switch", DOMAIN, f"{GATEWAY_ID}_holiday")
    assert hass.states.get(holiday).attributes["pending_holiday_end"] == "2026-11-26"

    def schedule(*, start, end):
        assert start == "2026-11-24T00:00:00"
        items["Holiday:0"] = {"value": 1, "expiresOn": end}

    def cancel(*, now):
        assert now == "2026-11-22T12:34:56"
        items["Holiday:0"] = {"value": 0}

    api.async_set_galevo_holiday.side_effect = schedule
    api.async_reset_galevo_holiday.side_effect = cancel
    await hass.services.async_call("switch", "turn_on", {"entity_id": holiday}, blocking=True)
    api.async_set_galevo_holiday.assert_awaited_once_with(
        start="2026-11-24T00:00:00", end="2026-11-26T23:59:59"
    )
    await hass.services.async_call("switch", "turn_off", {"entity_id": holiday}, blocking=True)
    api.async_reset_galevo_holiday.assert_awaited_once()


@pytest.mark.parametrize("days_ahead", [0, 2])
async def test_holiday_without_expiry_only_confirms_same_day_activation(
    hass, galevo_fixture, monkeypatch, days_ahead
):
    from datetime import timedelta

    api, entry, items = galevo_fixture
    items["Holiday:0"] = {"value": False}
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    coordinator = entry.runtime_data
    today = datetime.fromisoformat("2026-11-22T12:34:56+01:00")
    monkeypatch.setattr(coordinator, "_local_now", lambda: today)
    coordinator.set_pending_holiday(
        start=today.date() + timedelta(days=days_ahead),
        end=today.date() + timedelta(days=4),
    )

    def activate(**kwargs):
        items["Holiday:0"] = {"value": True}

    api.async_set_galevo_holiday.side_effect = activate
    if days_ahead:
        with pytest.raises(HomeAssistantError, match="not confirmed"):
            await coordinator.async_set_galevo_holiday()
    else:
        await coordinator.async_set_galevo_holiday()
    api.async_set_galevo_holiday.assert_awaited_once()


@pytest.mark.parametrize(
    ("domain", "key", "item_key", "initial", "value"),
    [
        ("switch", "quiet_mode", "IsQuite:0", False, 1),
        ("switch", "domestic_hot_water_boost", "IsDhwBoost:0", 0, 1),
        ("switch", "automatic_thermoregulation", "AutomaticThermoregulation:0", 0, 1),
        ("number", "zone_1_heating_flow_temperature", "HeatingFlowTemp:1", 35, 36),
        ("number", "zone_1_heating_flow_offset", "HeatingFlowOffset:1", 0, -1),
        ("number", "zone_1_cooling_flow_temperature", "CoolingFlowTemp:1", 19, 20),
        ("number", "zone_1_cooling_flow_offset", "CoolingFlowOffset:1", 0, 1),
    ],
)
async def test_galevo_data_item_controls_register_and_write(
    hass, galevo_fixture, domain, key, item_key, initial, value
):
    api, entry, items = galevo_fixture
    item_id, zone = item_key.split(":")
    item = {"id": item_id, "zone": int(zone), "value": initial, "min": -7, "max": 60}
    items[item_key] = item

    def write(before, target):
        assert before["value"] == initial
        items[item_key]["value"] = target

    api.async_set_galevo_data_item.side_effect = write
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    entity = er.async_get(hass).async_get_entity_id(domain, DOMAIN, f"{GATEWAY_ID}_{key}")
    assert entity
    if domain == "switch":
        await hass.services.async_call(domain, "turn_on", {"entity_id": entity}, blocking=True)
        assert hass.states.get(entity).state == "on"
    else:
        assert hass.states.get(entity).attributes["mode"] == (
            "slider" if key.endswith("offset") else "box"
        )
        await hass.services.async_call(
            domain, "set_value", {"entity_id": entity, "value": value}, blocking=True
        )
        assert float(hass.states.get(entity).state) == value
    assert api.async_set_galevo_data_item.await_args.args[1] == value


@pytest.mark.parametrize("cooling", [False, True])
async def test_galevo_thermostat_and_zone_mode_follow_selected_plant_mode(
    hass, galevo_fixture, cooling
):
    api, entry, items = galevo_fixture
    items.update(
        {
            "PlantMode:0": {"value": 3 if cooling else 1, "options": [1, 3]},
            "ZoneMode:1": {"value": 3, "options": [0, 1, 2, 3]},
            "ZoneComfortTemp:1": {"value": 21, "min": 10, "max": 30},
            "ZoneEconomyTemp:1": {"value": 17},
            "ZoneComfortCoolingTemp:1": {"value": 22, "min": 16, "max": 30},
            "ZoneEconomyCoolingTemp:1": {"value": 27},
        }
    )

    def read(*args, **kwargs):
        snapshot = {key: dict(item) for key, item in items.items()}
        zone = ZoneState.from_galevo(1, {"heat": False}, snapshot)
        return ElcoData(GATEWAY_ID, PlantState.from_galevo(snapshot), {1: zone}), snapshot

    def write_zone_mode(plant, zone, *, mode):
        items["ZoneMode:1"]["value"] = GALEVO_ZONE_MODE_TO_RAW[mode]

    def write_temperature(zone, *, comfort, reduced, cooling):
        key = "ZoneComfortCoolingTemp:1" if cooling else "ZoneComfortTemp:1"
        items[key]["value"] = comfort

    api.async_get_galevo_data.side_effect = read
    api.async_set_zone_mode.side_effect = write_zone_mode
    api.async_set_zone_temperatures.side_effect = write_temperature
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    registry = er.async_get(hass)
    thermostat = registry.async_get_entity_id("climate", DOMAIN, f"{GATEWAY_ID}_zone_1_thermostat")
    select = registry.async_get_entity_id("select", DOMAIN, f"{GATEWAY_ID}_zone_1_mode")
    hvac_mode = "cool" if cooling else "heat"
    assert hvac_mode in hass.states.get(thermostat).attributes["hvac_modes"]
    assert hass.states.get(thermostat).attributes["hvac_action"] == "idle"
    await hass.services.async_call(
        "climate", "set_hvac_mode", {"entity_id": thermostat, "hvac_mode": hvac_mode}, blocking=True
    )
    assert hass.states.get(thermostat).state == hvac_mode
    assert select is None
    for key in ("comfort_temperature", "cooling_comfort_temperature"):
        assert registry.async_get_entity_id("number", DOMAIN, f"{GATEWAY_ID}_zone_1_{key}") is None
    for key in ("reduced_temperature", "cooling_reduced_temperature"):
        assert (
            registry.async_get_entity_id("number", DOMAIN, f"{GATEWAY_ID}_zone_1_{key}") is not None
        )
    await hass.services.async_call(
        "climate", "set_temperature", {"entity_id": thermostat, "temperature": 23}, blocking=True
    )
    assert api.async_set_zone_temperatures.await_args.kwargs["cooling"] is cooling
    assert hass.states.get(thermostat).attributes["temperature"] == 23

    # The other season can lack its current target while still offering modes.
    items["PlantMode:0"]["value"] = 1 if cooling else 3
    new_target = "ZoneComfortTemp:1" if cooling else "ZoneComfortCoolingTemp:1"
    saved_target = items.pop(new_target)
    await entry.runtime_data.async_refresh()
    assert ("heat" if cooling else "cool") in hass.states.get(thermostat).attributes["hvac_modes"]
    assert not hass.states.get(thermostat).attributes["supported_features"] & (
        ClimateEntityFeature.TARGET_TEMPERATURE
    )
    assert registry.async_get_entity_id("select", DOMAIN, f"{GATEWAY_ID}_zone_1_mode") is None
    items[new_target] = saved_target
    await entry.runtime_data.async_refresh()
    assert hass.states.get(thermostat).attributes["temperature"] == saved_target["value"]


@pytest.mark.parametrize(
    ("changed_item", "new_item", "error"),
    [
        ("PlantMode:0", None, "current plant season"),
        ("PlantMode:0", {"value": 1}, "only be changed when cooling is selected"),
        ("ZoneEconomyCoolingTemp:1", None, "both zone temperatures"),
    ],
)
async def test_zone_temperature_requires_fresh_season_and_both_targets(
    hass, galevo_fixture, changed_item, new_item, error
):
    api, entry, items = galevo_fixture
    items.update(
        {
            "PlantMode:0": {"value": 3, "options": [1, 3]},
            "ZoneMode:1": {"value": 2, "options": [0, 1, 2, 3]},
            "ZoneComfortTemp:1": {"value": 21},
            "ZoneEconomyTemp:1": {"value": 17},
            "ZoneComfortCoolingTemp:1": {"value": 23},
            "ZoneEconomyCoolingTemp:1": {"value": 27},
        }
    )

    def read(*args, **kwargs):
        snapshot = {key: dict(item) for key, item in items.items()}
        zone = ZoneState.from_galevo(1, {"heat": False, "isCoolingActive": False}, snapshot)
        return ElcoData(GATEWAY_ID, PlantState.from_galevo(snapshot), {1: zone}), snapshot

    def write(zone, *, comfort, reduced, cooling):
        assert cooling and reduced == 27
        items["ZoneComfortCoolingTemp:1"]["value"] = comfort

    api.async_get_galevo_data.side_effect = read
    api.async_set_zone_temperatures.side_effect = write
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    registry = er.async_get(hass)
    thermostat = registry.async_get_entity_id("climate", DOMAIN, f"{GATEWAY_ID}_zone_1_thermostat")
    service_data = {"entity_id": thermostat, "temperature": 24}
    assert hass.states.get(thermostat).attributes["temperature"] == 23

    saved_item = items.pop(changed_item)
    if new_item is not None:
        items[changed_item] = new_item
    with pytest.raises(HomeAssistantError, match=error):
        await hass.services.async_call("climate", "set_temperature", service_data, blocking=True)
    api.async_set_zone_temperatures.assert_not_awaited()

    await entry.runtime_data.async_refresh()
    state = hass.states.get(thermostat)
    if new_item is not None:
        assert state.attributes["temperature"] == 21
        assert "heat" in state.attributes["hvac_modes"]
    else:
        assert not state.attributes["supported_features"] & ClimateEntityFeature.TARGET_TEMPERATURE
    if changed_item == "PlantMode:0" and new_item is None:
        assert state.attributes["hvac_modes"] == ["off", "auto"]
        assert state.attributes.get("temperature") is None
    assert state.attributes["hvac_action"] == "idle"

    items[changed_item] = saved_item
    await entry.runtime_data.async_refresh()
    await hass.services.async_call("climate", "set_temperature", service_data, blocking=True)
    assert hass.states.get(thermostat).attributes["temperature"] == 24
    api.async_set_zone_temperatures.assert_awaited_once()


@pytest.mark.parametrize("plant_mode", [1, 3])
@pytest.mark.parametrize("upgrading", [False, True])
async def test_galevo_blocks_off_season_writes_on_mirrored_gateway(
    hass, galevo_fixture, plant_mode, upgrading
):
    api, entry, items = galevo_fixture
    cooling_active = plant_mode == 3
    reduced = 24 if cooling_active else 17
    items.update(
        {
            "PlantMode:0": {"value": plant_mode, "options": [1, 3]},
            "ZoneMode:1": {"value": 2, "options": [0, 1, 2, 3]},
            "ZoneComfortTemp:1": {"value": 20},
            "ZoneComfortCoolingTemp:1": {"value": 20},
            "ZoneDesiredTemp:1": {"value": 20},
            "ZoneEconomyTemp:1": {"value": reduced},
            "ZoneEconomyCoolingTemp:1": {"value": reduced},
        }
    )

    def read(*args, **kwargs):
        snapshot = {key: dict(item) for key, item in items.items()}
        zone = ZoneState.from_galevo(1, {"heat": not cooling_active}, snapshot)
        return ElcoData(GATEWAY_ID, PlantState.from_galevo(snapshot), {1: zone}), snapshot

    def write(zone, *, comfort, reduced, cooling):
        # The reported controller mirrors a write into both seasons and the live target.
        for key in ("ZoneComfortTemp:1", "ZoneComfortCoolingTemp:1", "ZoneDesiredTemp:1"):
            items[key]["value"] = comfort

    api.async_get_galevo_data.side_effect = read
    api.async_set_zone_temperatures.side_effect = write
    registry = er.async_get(hass)
    prefix = "" if cooling_active else "cooling_"
    preserved_suffixes = (
        "zone_1_off_season_heating_comfort_temperature",
        "zone_1_off_season_cooling_comfort_temperature",
        f"zone_1_{prefix}comfort_temperature",
    )
    existing_ids = {}
    if upgrading:
        for index, suffix in enumerate(preserved_suffixes):
            registered = registry.async_get_or_create(
                "number",
                DOMAIN,
                f"{GATEWAY_ID}_{suffix}",
                suggested_object_id=f"custom_temperature_{index}",
                config_entry=entry,
            )
            existing_ids[suffix] = registered.entity_id

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    inactive_numbers = [
        registry.async_get_entity_id(
            "number", DOMAIN, f"{GATEWAY_ID}_zone_1_{prefix}reduced_temperature"
        )
    ]
    for suffix in preserved_suffixes:
        entity_id = registry.async_get_entity_id("number", DOMAIN, f"{GATEWAY_ID}_{suffix}")
        assert entity_id == existing_ids.get(suffix)
        if entity_id is not None:
            inactive_numbers.append(entity_id)

    for entity_id in inactive_numbers:
        assert hass.states.get(entity_id).state == "unavailable"
        await hass.services.async_call(
            "number", "set_value", {"entity_id": entity_id, "value": 16.5}, blocking=True
        )
    for kind in (f"{prefix}comfort", f"{prefix}reduced"):
        with pytest.raises(HomeAssistantError, match="temperatures can only be changed"):
            await entry.runtime_data.async_set_zone_temperature(1, kind, 16.5)
    api.async_set_zone_temperatures.assert_not_awaited()
    assert items["ZoneComfortTemp:1"]["value"] == 20
    assert items["ZoneComfortCoolingTemp:1"]["value"] == 20
    assert items["ZoneDesiredTemp:1"]["value"] == 20

    thermostat = registry.async_get_entity_id("climate", DOMAIN, f"{GATEWAY_ID}_zone_1_thermostat")
    await hass.services.async_call(
        "climate", "set_temperature", {"entity_id": thermostat, "temperature": 20.5}, blocking=True
    )
    assert api.async_set_zone_temperatures.await_args.kwargs == {
        "comfort": 20.5,
        "reduced": reduced,
        "cooling": cooling_active,
    }
    assert hass.states.get(thermostat).attributes["temperature"] == 20.5
    assert items["PlantMode:0"]["value"] == plant_mode


async def test_water_heater_rechecks_read_only_target_before_writing(hass, galevo_fixture):
    api, entry, items = galevo_fixture
    items.update(_scheduled_dhw_items())
    items["DhwTemp:0"] = {"value": 55}
    comfort = items["DhwTimeProgComfortTemp:0"]
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    registry = er.async_get(hass)
    heater = registry.async_get_entity_id(
        "water_heater", DOMAIN, f"{GATEWAY_ID}_domestic_hot_water"
    )
    service_data = {"entity_id": heater, "temperature": 52}
    assert hass.states.get(heater).attributes["temperature"] == 53

    comfort["readOnly"] = True
    with pytest.raises(HomeAssistantError, match="scheduled DHW comfort setpoint is read-only"):
        await hass.services.async_call(
            "water_heater", "set_temperature", service_data, blocking=True
        )
    api.async_set_galevo_dhw_settings.assert_not_awaited()

    await entry.runtime_data.async_refresh()
    features = hass.states.get(heater).attributes["supported_features"]
    assert not features & WaterHeaterEntityFeature.TARGET_TEMPERATURE
    assert features & WaterHeaterEntityFeature.OPERATION_MODE

    comfort["readOnly"] = False
    await entry.runtime_data.async_refresh()
    assert (
        hass.states.get(heater).attributes["supported_features"]
        & WaterHeaterEntityFeature.TARGET_TEMPERATURE
    )


@pytest.mark.parametrize(
    "target_result", ["confirmed", "rejected", "read_only", "becomes_read_only"]
)
async def test_native_water_heater_combines_mode_and_target_with_partial_result(
    hass, galevo_fixture, target_result
):
    api, entry, items = galevo_fixture
    items.update(_scheduled_dhw_items())
    items["DhwTemp:0"] = {
        "value": 55,
        "min": 42,
        "max": 65,
        "readOnly": target_result == "read_only",
    }

    def write(plant, *, comfort, reduced, mode):
        if mode is not None:
            items["DhwMode:0"]["value"] = GALEVO_DHW_MODE_TO_RAW[mode]
            if target_result == "becomes_read_only":
                items["DhwTemp:0"]["readOnly"] = True
        if comfort is not None:
            assert plant.dhw_mode.value == 3
            if target_result == "rejected":
                raise ElcoResponseError("Rejected")
            items["DhwTemp:0"]["value"] = comfort

    api.async_set_galevo_dhw_settings.side_effect = write
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    api.async_get_galevo_data.reset_mock()
    registry = er.async_get(hass)
    heater = registry.async_get_entity_id(
        "water_heater", DOMAIN, f"{GATEWAY_ID}_domestic_hot_water"
    )
    assert registry.async_get_entity_id("select", DOMAIN, f"{GATEWAY_ID}_dhw_mode") is None
    for key in ("dhw_comfort_temperature", "dhw_scheduled_comfort_temperature"):
        assert registry.async_get_entity_id("number", DOMAIN, f"{GATEWAY_ID}_{key}") is None
    assert (
        registry.async_get_entity_id("number", DOMAIN, f"{GATEWAY_ID}_dhw_reduced_temperature")
        is not None
    )
    call = hass.services.async_call(
        "water_heater",
        "set_temperature",
        {"entity_id": heater, "temperature": 56, "operation_mode": "performance"},
        blocking=True,
    )
    if target_result == "read_only":
        with pytest.raises(HomeAssistantError, match="always-active DHW setpoint is read-only"):
            await call
        api.async_set_galevo_dhw_settings.assert_not_awaited()
        assert api.async_get_galevo_data.await_count == 1
        assert hass.states.get(heater).state == "heat_pump"
        return
    if target_result in {"rejected", "becomes_read_only"}:
        with pytest.raises(HomeAssistantError, match="mode changed, but the temperature"):
            await call
    else:
        await call
    submitted_targets = [
        call.kwargs["comfort"] for call in api.async_set_galevo_dhw_settings.await_args_list
    ]
    assert submitted_targets == ([None] if target_result == "becomes_read_only" else [None, 56])
    assert api.async_get_galevo_data.await_count == (3 if target_result == "confirmed" else 2)
    assert hass.states.get(heater).state == "performance"
    assert hass.states.get(heater).attributes["temperature"] == (
        56 if target_result == "confirmed" else 55
    )
    assert hass.states.get(heater).attributes["min_temp"] == 42


@pytest.mark.parametrize("family", [5, 4])
async def test_manual_gateway_validates_a_bsb_snapshot_and_rejects_unsupported_families(
    hass, monkeypatch, family
):
    from test_lifecycle import FIXTURE

    api, _ = fake_gateway(monkeypatch)
    api.system_type = family
    api.async_get_features.return_value = FIXTURE["features"]
    monkeypatch.setattr(config_flow, "ElcoApiClient", lambda *args: api)
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "user"},
        data={
            "username": "synthetic@example.invalid",
            "password": "synthetic",
            "gateway_id": GATEWAY_ID.lower(),
        },
    )
    if family == 5:
        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert result["result"].unique_id == GATEWAY_ID
        await hass.async_block_till_done(wait_background_tasks=True)
        assert api.async_get_data.await_args_list[0].kwargs == {"use_cache": False}
    else:
        assert result["errors"] == {"base": "unsupported_controller"}
        api.async_get_data.assert_not_awaited()
    api.async_list_plants.assert_not_awaited()
