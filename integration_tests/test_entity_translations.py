"""Verify ELCO entity translations and automation compatibility."""

import json
import re
from pathlib import Path

from homeassistant.helpers import entity_registry as er
from test_galevo_beta import galevo_fixture as galevo_fixture
from test_lifecycle import DOMAIN, GATEWAY_ID, existing_entry, fake_gateway

COMPONENT = Path(__file__).parents[1] / "custom_components" / DOMAIN


def _unique_keys(pairs):
    mapping = dict(pairs)
    assert len(mapping) == len(pairs), "Duplicate translation key"
    return mapping


def _messages(catalog, path=()):
    for key, value in catalog.items():
        if isinstance(value, dict):
            yield from _messages(value, (*path, key))
        else:
            yield (*path, key), value


def test_translation_catalogs_cover_setup_options_and_entity_names():
    english, german, source = (
        json.loads((COMPONENT / file).read_text(encoding="utf-8"), object_pairs_hook=_unique_keys)
        for file in ("translations/en.json", "translations/de.json", "strings.json")
    )
    assert source == english
    originals = dict(_messages(english))
    translations = dict(_messages(german))
    assert translations.keys() == originals.keys()
    for path, original in originals.items():
        translated = translations[path]
        assert isinstance(translated, str) and translated.strip(), path
        assert sorted(re.findall(r"\{[^{}]+\}", original)) == sorted(
            re.findall(r"\{[^{}]+\}", translated)
        ), path
        if path[0] == "entity":
            assert path[-1] == "name" or (path[1] == "sensor" and path[-2] == "state"), path


def test_german_labels_match_for_shared_sensors_and_controls():
    german = json.loads((COMPONENT / "translations/de.json").read_text(encoding="utf-8"))["entity"]
    for left, right in (("number", "sensor"), ("select", "sensor"), ("binary_sensor", "switch")):
        for key in german[left].keys() & german[right].keys():
            assert german[left][key] == german[right][key], key


async def test_bsb_locale_upgrade_preserves_registry_and_automation_targets(hass, monkeypatch):
    api, _ = fake_gateway(monkeypatch)
    entry, thermostat, disabled = existing_entry(hass)
    registry = er.async_get(hass)
    outside = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{GATEWAY_ID}_outside_temperature",
        config_entry=entry,
        has_entity_name=True,
        original_name="Outside temperature",
        suggested_object_id="existing_outside_temperature",
    ).entity_id
    await hass.config.async_update(language="en", country="DE")
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    def ids():
        return {
            (entity.domain, entity.unique_id): entity.entity_id
            for entity in er.async_entries_for_config_entry(registry, entry.entry_id)
        }

    before = ids()
    heater = before["water_heater", f"{GATEWAY_ID}_domestic_hot_water"]
    factor = before["sensor", f"{GATEWAY_ID}_annual_performance_factor_1"]
    before_value = hass.states.get(outside).state
    before_mode = hass.states.get(heater).state
    registry.async_update_entity(thermostat, name="My thermostat")
    assert registry.async_get(outside).original_name == "Outside temperature"
    assert registry.async_get(factor).original_name == (
        "Annual energy record 1 – Yearly performance factor"
    )

    await hass.config.async_update(language="de")
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert ids() == before
    assert registry.async_get(disabled).disabled_by is er.RegistryEntryDisabler.USER
    assert registry.async_get(thermostat).name == "My thermostat"
    assert registry.async_get(thermostat).original_name == "Zone 1 – Thermostat"
    assert registry.async_get(outside).original_name == "Außentemperatur"
    assert registry.async_get(factor).original_name == "Energiejahresbilanz 1 – Jahresarbeitszahl"
    assert registry.async_get(heater).original_name == "Warmwasser"
    assert hass.states.get(outside).state == before_value
    assert hass.states.get(heater).state == before_mode

    await hass.services.async_call(
        "water_heater",
        "set_temperature",
        {"entity_id": heater, "temperature": 47},
        blocking=True,
    )
    assert api.async_set_dhw.await_args.kwargs["comfort"] == 47


async def test_galevo_localized_select_keeps_state_options_and_service_values(hass, galevo_fixture):
    api, entry, items = galevo_fixture
    items["PlantMode:0"] = {
        "value": 1,
        "options": [0, 1],
        "optTexts": ["Summer", "Winter"],
        "readOnly": False,
    }

    def apply_mode(mode, *, old_mode):
        assert old_mode == 1
        items["PlantMode:0"]["value"] = mode

    api.async_set_galevo_plant_mode.side_effect = apply_mode
    await hass.config.async_update(language="de", country="DE")
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    registry = er.async_get(hass)
    select = registry.async_get_entity_id("select", DOMAIN, f"{GATEWAY_ID}_plant_mode")

    assert registry.async_get(select).original_name == "Anlagenbetriebsart"
    assert hass.states.get(select).state == "Winter"
    assert hass.states.get(select).attributes["options"] == ["Summer", "Winter"]
    await hass.services.async_call(
        "select", "select_option", {"entity_id": select, "option": "Summer"}, blocking=True
    )
    api.async_set_galevo_plant_mode.assert_awaited_once_with(0, old_mode=1)
    assert hass.states.get(select).state == "Summer"
