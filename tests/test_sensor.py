"""Tests for Home Assistant sensor-platform constraints."""

from __future__ import annotations

import ast
from pathlib import Path


def test_sensor_platform_never_uses_config_entity_category() -> None:
    """Home Assistant rejects CONFIG on every SensorEntity."""
    sensor_path = Path(__file__).parents[1] / "custom_components" / "elco_aerotop" / "sensor.py"
    tree = ast.parse(sensor_path.read_text(encoding="utf-8"), filename=str(sensor_path))

    invalid_lines = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and node.attr == "CONFIG"
        and isinstance(node.value, ast.Name)
        and node.value.id == "EntityCategory"
    ]

    assert invalid_lines == []
