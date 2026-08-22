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
        "safe": 23.5,
    }

    sanitized = sanitize_diagnostics(raw, {"ABC123", "home@example.test"})

    assert sanitized["gatewayId"] == "<redacted>"
    assert sanitized["gateway-<redacted>"] == {"value": "safe"}
    assert sanitized["zones"][0]["name"] == "<redacted>"
    assert sanitized["zones"][0]["value"] == "<redacted> at <redacted>"
    assert sanitized["maintenance"]["technician"] == "<redacted>"
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
    ):
        assert family in fixture
