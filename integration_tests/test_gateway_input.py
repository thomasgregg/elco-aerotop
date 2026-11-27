"""Exercise gateway setup and migration against Home Assistant registries."""

from unittest.mock import AsyncMock

import pytest
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant.common import MockConfigEntry
from test_lifecycle import DOMAIN, GATEWAY_ID, fake_gateway, make_entry

from custom_components import elco_aerotop
from custom_components.elco_aerotop.config_flow import ElcoAerotopConfigFlow
from custom_components.elco_aerotop.const import CONFIG_ENTRY_MINOR_VERSION

LEGACY_GATEWAY_ID = f"{GATEWAY_ID}?NAVMENUITEM=1&BREADCRUMBPATH=0"
PLANT_URL = f"https://example.invalid/R2/BsbPlantDashboard/Index/{LEGACY_GATEWAY_ID}"
INPUT = {
    "username": "synthetic@example.invalid",
    "password": "synthetic",
    "base_url": "https://example.invalid",
}


def legacy_entry(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={**INPUT, "gateway_id": LEGACY_GATEWAY_ID},
        unique_id=LEGACY_GATEWAY_ID,
        title="Custom heating title",
        options={"scan_interval": 600},
        version=1,
        minor_version=1,
    )
    entry.add_to_hass(hass)
    return entry


def legacy_device_and_entity(hass, entry):
    devices = dr.async_get(hass)
    device = devices.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, LEGACY_GATEWAY_ID), (DOMAIN, "SECONDARY-IDENTIFIER")},
        name="Old device",
        configuration_url=PLANT_URL,
    )
    devices.async_update_device(device.id, name_by_user="My heat pump")
    entities = er.async_get(hass)
    entity = entities.async_get_or_create(
        "number",
        DOMAIN,
        f"{LEGACY_GATEWAY_ID}_zone_1_comfort_temperature",
        config_entry=entry,
        device_id=device.id,
        suggested_object_id="old_comfort_number",
        disabled_by=er.RegistryEntryDisabler.USER,
    )
    entities.async_update_entity(entity.entity_id, name="My comfort setpoint")
    return device, entity


async def test_dashboard_url_is_normalized_before_setup(hass, monkeypatch):
    fake_gateway(monkeypatch)
    validate = AsyncMock(return_value={})
    monkeypatch.setattr(ElcoAerotopConfigFlow, "_async_validate_gateway", validate)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}, data={**INPUT, "gateway_id": PLANT_URL}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == GATEWAY_ID
    assert result["result"].data["gateway_id"] == GATEWAY_ID
    validate.assert_awaited_once_with({**INPUT, "gateway_id": GATEWAY_ID})
    await hass.async_block_till_done(wait_background_tasks=True)


@pytest.mark.parametrize(
    ("value", "result_type", "reason"),
    [
        (
            "https://example.invalid/R2/Account/Login",
            FlowResultType.FORM,
            "invalid_gateway_id",
        ),
        (PLANT_URL, FlowResultType.ABORT, "already_configured"),
    ],
)
async def test_invalid_or_duplicate_gateway_skips_api_validation(
    hass, monkeypatch, value, result_type, reason
):
    legacy_entry(hass)
    validate = AsyncMock()
    monkeypatch.setattr(ElcoAerotopConfigFlow, "_async_validate_gateway", validate)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}, data={**INPUT, "gateway_id": value}
    )

    assert result["type"] is result_type
    if result_type is FlowResultType.FORM:
        assert result["errors"] == {"gateway_id": reason}
    else:
        assert result["reason"] == reason
    validate.assert_not_awaited()


async def test_gateway_migration_keeps_registry_ids_and_user_settings(hass, monkeypatch):
    api, _ = fake_gateway(monkeypatch)
    entry = legacy_entry(hass)
    device, entity = legacy_device_and_entity(hass, entry)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert entry.data["gateway_id"] == GATEWAY_ID
    assert entry.unique_id == GATEWAY_ID
    assert entry.minor_version == CONFIG_ENTRY_MINOR_VERSION
    assert entry.title == "Custom heating title"
    assert entry.options["scan_interval"] == 600
    entities = er.async_get(hass)
    migrated = entities.async_get(entity.entity_id)
    assert migrated is not None
    assert migrated.unique_id == f"{GATEWAY_ID}_zone_1_comfort_temperature"
    assert migrated.device_id == device.id
    assert migrated.name == "My comfort setpoint"
    assert migrated.disabled_by == er.RegistryEntryDisabler.USER
    assert entities.async_get_entity_id("number", DOMAIN, entity.unique_id) is None
    devices = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert len(devices) == 1
    assert devices[0].id == device.id
    assert devices[0].identifiers == {(DOMAIN, GATEWAY_ID), (DOMAIN, "SECONDARY-IDENTIFIER")}
    assert devices[0].name_by_user == "My heat pump"
    assert devices[0].configuration_url is None
    assert await elco_aerotop.async_migrate_entry(hass, entry)
    api.async_get_data.assert_awaited()


@pytest.mark.parametrize("collision", ["config_entry", "device", "entity"])
async def test_gateway_migration_checks_conflicts_before_changing_registries(hass, collision):
    entry = legacy_entry(hass)
    device, entity = legacy_device_and_entity(hass, entry)
    entities = er.async_get(hass)
    devices = dr.async_get(hass)
    if collision == "config_entry":
        make_entry(hass)
    elif collision == "device":
        devices.async_get_or_create(
            config_entry_id=entry.entry_id, identifiers={(DOMAIN, GATEWAY_ID)}
        )
    else:
        entities.async_get_or_create(
            "number", DOMAIN, f"{GATEWAY_ID}_zone_1_comfort_temperature", config_entry=entry
        )

    assert not await elco_aerotop.async_migrate_entry(hass, entry)
    assert entry.data["gateway_id"] == LEGACY_GATEWAY_ID
    assert entry.unique_id == LEGACY_GATEWAY_ID
    assert entry.minor_version == 1
    assert entities.async_get(entity.entity_id).unique_id == entity.unique_id
    assert (DOMAIN, LEGACY_GATEWAY_ID) in devices.async_get(device.id).identifiers
