"""Read-only heat pump status for Galevo plants with an EM20 energy manager."""

from __future__ import annotations

import asyncio

import pytest
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.helpers import entity_registry as er
from test_galevo_beta import _plant_mode
from test_galevo_beta import galevo_fixture as galevo_fixture
from test_lifecycle import DOMAIN, GATEWAY_ID

from custom_components.elco_aerotop import coordinator as coordinator_module
from custom_components.elco_aerotop.api import ElcoConnectionError, ElcoResponseError
from custom_components.elco_aerotop.const import MENU_ITEM_HP_STATUS_IDS


@pytest.fixture
def em20_fixture(galevo_fixture):
    api, entry, core_items = galevo_fixture
    api.async_get_features.return_value = {
        **api.async_get_features.return_value,
        "hasEm20": True,
    }
    status_items = {
        "337": {"value": 0.0, "error": False},
        "340": {"value": 3.0, "error": False, "options": {"3": "Heating"}},
        "341": {"value": -2.0, "error": False},
        "348": {"value": 31.0, "error": False},
        "349": {"value": 31.5, "error": False},
    }

    async def get_menu_items(ids=()):
        if ids == MENU_ITEM_HP_STATUS_IDS:
            return {key: dict(value) for key, value in status_items.items()}
        return {"119": {"value": 4}}

    api.async_get_menu_items.side_effect = get_menu_items
    return api, entry, core_items, status_items


def _entity_id(hass, key):
    return er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{GATEWAY_ID}_{key}")


def _state(hass, key):
    return hass.states.get(_entity_id(hass, key))


async def _setup(hass, entry):
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    return entry.runtime_data


async def _refresh(hass, coordinator):
    await coordinator.async_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)


async def test_em20_status_sensors_survive_deferred_discovery(hass, em20_fixture):
    api, entry, _, status_items = em20_fixture
    coordinator = await _setup(hass, entry)

    for key, expected in (
        ("heat_pump_mode", "heating"),
        ("ch_backup_resistance", "off"),
        ("heat_pump_flow_temperature", "31.0"),
        ("heating_circuit_flow_temperature", "31.5"),
        ("heat_pump_outside_air_temperature", "-2.0"),
    ):
        assert _state(hass, key).state == expected

    assert _state(hass, "heat_pump_mode").attributes["options"].count("defrost") == 1

    for raw in (9, 17):
        status_items["340"] = {"value": raw, "options": {str(raw): "Abtauen"}}
        await _refresh(hass, coordinator)
        assert _state(hass, "heat_pump_mode").state == "defrost"

    status_reads = [
        call
        for call in api.async_get_menu_items.await_args_list
        if call.args == (MENU_ITEM_HP_STATUS_IDS,)
    ]
    assert len(status_reads) == 3
    await coordinator.async_refresh_deferred_discovery()
    await hass.async_block_till_done()
    assert _state(hass, "heat_pump_mode").state == "defrost"
    assert coordinator.data.discovery.menu_items["119"]["value"] == 4


@pytest.mark.parametrize("controller, has_em20", [(3, False), (5, True)])
async def test_em20_status_requires_galevo_and_em20(hass, galevo_fixture, controller, has_em20):
    api, entry, _ = galevo_fixture
    api.last_features_response = {"data": {"plant": {"systemType": controller}}}
    api.async_get_features.return_value = {
        **api.async_get_features.return_value,
        "hasEm20": has_em20,
    }
    await _setup(hass, entry)
    assert all(
        call.args != (MENU_ITEM_HP_STATUS_IDS,) for call in api.async_get_menu_items.await_args_list
    )
    assert _entity_id(hass, "heat_pump_mode") is None


@pytest.mark.parametrize("primary_first", [False, True])
async def test_em20_flow_switches_sources_using_one_entity(hass, em20_fixture, primary_first):
    _, entry, core_items, status_items = em20_fixture
    key = "heating_circuit_flow_temperature"
    if primary_first:
        core_items["ChFlowTemp:0"] = {"value": 40.0}
    coordinator = await _setup(hass, entry)
    entity_id = _entity_id(hass, key)
    assert float(_state(hass, key).state) == (40.0 if primary_first else 31.5)

    core_items["ChFlowTemp:0"] = {"value": 0.0, "available": True}
    await _refresh(hass, coordinator)
    assert float(_state(hass, key).state) == 0.0

    core_items["ChFlowTemp:0"]["available"] = False
    await _refresh(hass, coordinator)
    assert float(_state(hass, key).state) == 31.5

    core_items["ChFlowTemp:0"] = {"value": 10**350}
    await _refresh(hass, coordinator)
    assert float(_state(hass, key).state) == 31.5

    del status_items["349"]
    await _refresh(hass, coordinator)
    assert _state(hass, key).state == STATE_UNAVAILABLE

    core_items["ChFlowTemp:0"] = {"value": 39.0}
    await _refresh(hass, coordinator)
    assert _entity_id(hass, key) == entity_id
    assert float(_state(hass, key).state) == 39.0


@pytest.mark.parametrize("value", [True, 9.9, 99, float("nan"), float("inf"), 10**350])
async def test_invalid_em20_fields_do_not_publish_bogus_readings(hass, em20_fixture, value):
    _, entry, _, status_items = em20_fixture
    coordinator = await _setup(hass, entry)
    status_items["340"]["value"] = value
    status_items["348"]["value"] = "Infinity"
    await _refresh(hass, coordinator)

    assert _state(hass, "heat_pump_mode").state == STATE_UNAVAILABLE
    assert _state(hass, "heat_pump_flow_temperature").state == STATE_UNAVAILABLE
    assert float(_state(hass, "heat_pump_outside_air_temperature").state) == -2.0


async def test_em20_partial_and_failed_reads_clear_only_affected_status(hass, em20_fixture):
    api, entry, _, status_items = em20_fixture
    coordinator = await _setup(hass, entry)
    read = api.async_get_menu_items.side_effect

    del status_items["340"]
    status_items["119"] = {"value": 99}
    await _refresh(hass, coordinator)
    assert _state(hass, "heat_pump_mode").state == STATE_UNAVAILABLE
    assert float(_state(hass, "heat_pump_flow_temperature").state) == 31.0
    assert coordinator.data.discovery.menu_items["119"]["value"] == 4

    async def fail(ids=()):
        if ids == MENU_ITEM_HP_STATUS_IDS:
            raise ElcoConnectionError("gateway status temporarily offline")
        return await read(ids)

    api.async_get_menu_items.side_effect = fail
    await _refresh(hass, coordinator)
    assert coordinator.last_update_success
    assert coordinator.data.zones[1] is not None
    assert _state(hass, "heat_pump_flow_temperature").state == STATE_UNAVAILABLE

    api.async_get_menu_items.side_effect = read
    status_items["340"] = {"value": 3}
    await _refresh(hass, coordinator)
    assert _state(hass, "heat_pump_mode").state == "heating"
    status_items.clear()
    await _refresh(hass, coordinator)
    assert _state(hass, "heat_pump_mode").state == STATE_UNAVAILABLE


async def test_slow_em20_read_does_not_delay_core_and_expires_on_timeout(
    hass, em20_fixture, monkeypatch
):
    api, entry, core_items, _ = em20_fixture
    coordinator = await _setup(hass, entry)
    monkeypatch.setattr(coordinator_module, "_HP_STATUS_TIMEOUT", 0.25)
    started = asyncio.Event()
    read = api.async_get_menu_items.side_effect

    async def slow(ids=()):
        if ids == MENU_ITEM_HP_STATUS_IDS:
            started.set()
            await asyncio.Event().wait()
        return await read(ids)

    api.async_get_menu_items.side_effect = slow
    core_items["ChFlowTemp:0"] = {"value": 42.0}
    await coordinator.async_refresh()
    await asyncio.wait_for(started.wait(), 2)
    assert float(_state(hass, "heating_circuit_flow_temperature").state) == 42.0
    assert _state(hass, "heat_pump_mode").state == "heating"
    await hass.async_block_till_done(wait_background_tasks=True)
    assert coordinator.last_update_success
    assert _state(hass, "heat_pump_mode").state == STATE_UNAVAILABLE
    assert float(_state(hass, "heating_circuit_flow_temperature").state) == 42.0


async def test_em20_rate_limit_clears_status_and_applies_shared_backoff(hass, em20_fixture):
    api, entry, _, _ = em20_fixture
    coordinator = await _setup(hass, entry)
    read = api.async_get_menu_items.side_effect

    async def rate_limit(ids=()):
        if ids == MENU_ITEM_HP_STATUS_IDS:
            raise ElcoResponseError("rate limited", status=429, retry_after=180)
        return await read(ids)

    api.async_get_menu_items.side_effect = rate_limit
    await _refresh(hass, coordinator)
    assert coordinator.last_update_success
    assert _state(hass, "heat_pump_mode").state == STATE_UNAVAILABLE
    assert 175 < coordinator._gateway_backoff() <= 180

    api.async_get_galevo_data.reset_mock()
    await _refresh(hass, coordinator)
    api.async_get_galevo_data.assert_not_awaited()


async def test_em20_does_not_republish_old_defrost_after_core_failure(hass, em20_fixture):
    api, entry, _, status_items = em20_fixture
    status_items["340"] = {"value": 9}
    coordinator = await _setup(hass, entry)
    assert _state(hass, "heat_pump_mode").state == "defrost"

    read_core = api.async_get_galevo_data.side_effect
    api.async_get_galevo_data.side_effect = ElcoConnectionError("core gateway offline")
    await _refresh(hass, coordinator)
    assert _state(hass, "heat_pump_mode").state == STATE_UNAVAILABLE

    api.async_get_galevo_data.side_effect = read_core
    read_menu = api.async_get_menu_items.side_effect
    status_items["340"] = {"value": 3}
    release = asyncio.Event()

    async def wait_for_new_status(ids=()):
        if ids == MENU_ITEM_HP_STATUS_IDS:
            await release.wait()
        return await read_menu(ids)

    api.async_get_menu_items.side_effect = wait_for_new_status
    try:
        await coordinator.async_refresh()
        assert coordinator.last_update_success
        assert _state(hass, "heat_pump_mode").state == STATE_UNAVAILABLE
    finally:
        release.set()
        await hass.async_block_till_done(wait_background_tasks=True)
    assert _state(hass, "heat_pump_mode").state == "heating"


async def test_em20_status_task_is_drained_during_unload(hass, em20_fixture):
    api, entry, _, _ = em20_fixture
    coordinator = await _setup(hass, entry)
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def slow(_ids):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    api.async_get_menu_items.side_effect = slow
    await coordinator.async_refresh()
    await asyncio.wait_for(started.wait(), 2)
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert cancelled.is_set()
    assert not coordinator._gateway_lock.locked()


async def test_galevo_command_cancels_pending_em20_read(hass, em20_fixture):
    api, entry, core_items, _ = em20_fixture
    core_items["PlantMode:0"] = _plant_mode()
    coordinator = await _setup(hass, entry)
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def slow(_ids):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    async def write(mode, *, old_mode):
        assert cancelled.is_set()
        core_items["PlantMode:0"]["value"] = mode

    api.async_get_menu_items.side_effect = slow
    api.async_set_galevo_plant_mode.side_effect = write
    await coordinator.async_refresh()
    await asyncio.wait_for(started.wait(), 2)

    await asyncio.wait_for(coordinator.async_set_plant_mode(0), 2)

    api.async_set_galevo_plant_mode.assert_awaited_once_with(0, old_mode=1)
    assert coordinator.data.discovery.system_value("PlantMode") == 0
    assert _state(hass, "heat_pump_mode").state == STATE_UNAVAILABLE
