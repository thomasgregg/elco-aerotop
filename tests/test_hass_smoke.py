"""Smoke tests for the pytest-homeassistant-custom-component harness.

Verifies the phacc ``hass`` fixture works in this project and that the
integration's manifest/plattform modules load under a real Home Assistant
runtime instead of the local stubs.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest
from homeassistant.core import HomeAssistant


def _ha_real() -> bool:
    """True when homeassistant and its submodules come from the package.

    ``test_coordinator`` injects stub ``homeassistant.*`` submodules into
    ``sys.modules``; real modules always carry ``__file__``.
    """
    return bool(
        getattr(sys.modules.get("homeassistant"), "__path__", None)
        and getattr(
            sys.modules.get("homeassistant.helpers.update_coordinator"),
            "__file__",
            None,
        )
    )


requires_real_ha = pytest.mark.skipif(
    not _ha_real(),
    reason="local homeassistant stubs were installed by an earlier test module",
)

DOMAIN = "elco_aerotop"
COMPONENT_DIR = Path(__file__).parents[1] / "custom_components" / DOMAIN


async def test_hass_fixture_boots(hass: HomeAssistant) -> None:
    """The phacc harness provides a running HA instance."""
    assert hass is not None
    assert hass.state.name == "Home Assistant" or hass.config is not None


async def test_integration_manifest_is_valid(hass: HomeAssistant) -> None:
    """The custom component manifest parses and declares config flow."""
    manifest = json.loads((COMPONENT_DIR / "manifest.json").read_text())
    assert manifest["domain"] == DOMAIN
    assert manifest["config_flow"] is True


@requires_real_ha
async def test_platform_modules_import_under_real_ha(hass: HomeAssistant) -> None:
    """Entity platforms import cleanly against the real HA package."""
    for platform in ("number", "select", "sensor", "switch", "water_heater"):
        importlib.import_module(f"custom_components.elco_aerotop.{platform}")
