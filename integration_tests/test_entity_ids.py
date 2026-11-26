"""Verify language-independent defaults and compatibility with existing ELCO IDs."""

import pytest
from homeassistant.helpers import entity_registry as er
from test_galevo_beta import galevo_fixture as galevo_fixture
from test_galevo_beta import galevo_metering as galevo_metering
from test_lifecycle import DOMAIN, GATEWAY_ID, fake_gateway, make_entry

from custom_components.elco_aerotop.const import MENU_ITEM_HP_STATUS_IDS

DEVICE_SLUG = f"elco_aerotop_{GATEWAY_ID.lower()}"


@pytest.mark.freeze_time("2026-07-01T12:00:00Z", real_asyncio=True)
@pytest.mark.parametrize("family", ["bsb", "galevo"])
@pytest.mark.parametrize("language", ["en", "de"])
async def test_default_ids_match_across_languages(hass, monkeypatch, request, family, language):
    if family == "bsb":
        fake_gateway(monkeypatch)
        entry = make_entry(hass)
    else:
        api, entry, _ = request.getfixturevalue("galevo_metering")
        api.async_get_features.return_value = {
            **api.async_get_features.return_value,
            "hasEm20": True,
        }

        async def get_menu_items(ids=()):
            if ids == MENU_ITEM_HP_STATUS_IDS:
                return {"340": {"value": 3}}
            return {"119": {"value": 4}}

        api.async_get_menu_items.side_effect = get_menu_items

    await hass.config.async_update(language=language, country="DE")
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    registry = er.async_get(hass)

    def check_id(domain, key, suffix=None):
        entity_id = registry.async_get_entity_id(domain, DOMAIN, f"{GATEWAY_ID}_{key}")
        assert entity_id == f"{domain}.{DEVICE_SLUG}_{suffix or key}"
        return registry.async_get(entity_id)

    check_id("button", "refresh")
    check_id("climate", "zone_1_thermostat")
    check_id("sensor", "last_successful_update")
    if family == "bsb":
        outside = check_id("sensor", "outside_temperature")
        assert outside.original_name == (
            "Außentemperatur" if language == "de" else "Outside temperature"
        )
        check_id("sensor", "dhw_current_temperature", "domestic_hot_water_temperature")
        check_id("sensor", "annual_performance_factor_1")
    else:
        spf = check_id("sensor", "galevo_estimated_jaz", "estimated_spf_previous_calendar_year")
        assert spf.original_name == (
            "Geschätzte Jahresarbeitszahl – Vorjahr"
            if language == "de"
            else "Estimated SPF – previous calendar year"
        )
        check_id("sensor", "energy_heating_last_12_complete_months")
        mode = check_id("sensor", "heat_pump_mode")
        assert mode.original_name == ("Wärmepumpenmodus" if language == "de" else "Heat pump mode")
        assert hass.states.get(mode.entity_id).state == "heating"


@pytest.mark.parametrize("family", ["bsb", "galevo"])
async def test_upgrade_keeps_legacy_and_custom_entity_ids(hass, monkeypatch, request, family):
    if family == "bsb":
        fake_gateway(monkeypatch)
        entry = make_entry(hass)
    else:
        _, entry, _ = request.getfixturevalue("galevo_fixture")

    registry = er.async_get(hass)
    legacy = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{GATEWAY_ID}_last_successful_update",
        suggested_object_id=f"{DEVICE_SLUG}_letzte_erfolgreiche_aktualisierung",
        config_entry=entry,
    )
    refresh = registry.async_get_or_create(
        "button",
        DOMAIN,
        f"{GATEWAY_ID}_refresh",
        suggested_object_id=f"{DEVICE_SLUG}_aktualisieren",
        config_entry=entry,
    )
    custom = registry.async_update_entity(
        refresh.entity_id, new_entity_id="button.my_heat_pump_refresh", name="My heat pump refresh"
    )
    disabled = registry.async_get_or_create(
        "binary_sensor",
        DOMAIN,
        f"{GATEWAY_ID}_gateway_online",
        suggested_object_id=f"{DEVICE_SLUG}_gateway_alt",
        config_entry=entry,
        disabled_by=er.RegistryEntryDisabler.USER,
    )
    await hass.config.async_update(language="de", country="DE")
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    def check_preserved_ids():
        for original in (legacy, custom, disabled):
            entity_id = registry.async_get_entity_id(original.domain, DOMAIN, original.unique_id)
            assert entity_id == original.entity_id
        assert registry.async_get(custom.entity_id).name == "My heat pump refresh"
        assert registry.async_get(disabled.entity_id).disabled_by is er.RegistryEntryDisabler.USER
        assert hass.states.get(legacy.entity_id) is not None
        assert hass.states.get(custom.entity_id) is not None

    check_preserved_ids()
    await hass.config.async_update(language="en")
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    check_preserved_ids()
