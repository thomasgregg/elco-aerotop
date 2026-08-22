"""Tests for anonymized discovery diagnostics."""

import json
from pathlib import Path

from custom_components.elco_aerotop.diagnostic_utils import (
    sanitize_diagnostics,
    schema_inventory,
)


def test_diagnostics_redact_identifiers_and_known_secrets() -> None:
    raw = {
        "gatewayId": "ABC123",
        "gateway-ABC123": {"value": "safe"},
        "zones": [{"name": "Living room", "value": "ABC123 at home@example.test"}],
        "maintenance": {"technician": "Named person"},
        "plant": {
            "gwSerial": "SERIAL-987",
            "plantName": "Private home",
            "location": {"addr": "Private street", "cityName": "Private city"},
        },
        "safe": 23.5,
    }

    sanitized = sanitize_diagnostics(raw, {"ABC123", "home@example.test"})

    assert sanitized["gatewayId"] == "<redacted>"
    assert sanitized["gateway-<redacted>"] == {"value": "safe"}
    assert sanitized["zones"][0]["name"] == "<redacted>"
    assert sanitized["zones"][0]["value"] == "<redacted> at <redacted>"
    assert sanitized["maintenance"]["technician"] == "<redacted>"
    assert sanitized["plant"]["gwSerial"] == "<redacted>"
    assert sanitized["plant"]["plantName"] == "<redacted>"
    assert sanitized["plant"]["location"] == "<redacted>"
    assert sanitized["safe"] == 23.5


def test_schema_inventory_captures_all_list_item_keys_without_values() -> None:
    inventory = schema_inventory(
        {"items": [{"id": "Pressure", "value": 1.5}, {"id": "Mode", "text": "Auto"}]}
    )

    assert inventory["items[].id"] == "str"
    assert inventory["items[].value"] == "float"
    assert inventory["items[].text"] == "str"
    assert "Pressure" not in inventory


def test_schema_inventory_redacts_dynamic_identifier_keys() -> None:
    inventory = schema_inventory({"plants": {"ABC123": {"value": 1}}}, {"ABC123"})

    assert "plants.<redacted>.value" in inventory
    assert not any("ABC123" in path for path in inventory)


def test_anonymized_discovery_fixture_covers_every_endpoint_family() -> None:
    fixture = json.loads(
        (Path(__file__).parent / "fixtures" / "discovery.json").read_text(encoding="utf-8")
    )

    assert fixture["gateway_id"] == "<redacted>"
    assert fixture["features_response"]["data"]["features"]
    assert fixture["get_data_responses"][0]["data"]["plantData"]
    for family in (
        "system_items",
        "schedules",
        "metering",
        "maintenance",
        "bus_errors",
        "bsb_points",
        "bsb_plant_data",
        "menu_items",
    ):
        assert family in fixture


def test_real_gateway_fixture_is_anonymized_and_preserves_sentinel_fields() -> None:
    fixture = json.loads(
        (Path(__file__).parent / "fixtures" / "aerotop_one_zone.json").read_text(encoding="utf-8")
    )

    assert fixture["gateway_id"] == "<redacted>"
    assert fixture["features"]["hpSys"] is True
    assert fixture["features"]["hasMetering"] is False
    assert fixture["plant_metadata"]["gwSerial"] == "<redacted>"
    assert fixture["get_data"]["zoneData"]["hasRoomSensor"] is False
    assert fixture["get_data"]["zoneData"]["roomTemp"] == 0
    assert fixture["get_data"]["zoneData"]["coolComfortTemp"]["value"] == 0
    assert fixture["system_items"]["ChFlowTemp:0"]["readOnly"] is True
    assert "<redacted>" in json.dumps(fixture)
