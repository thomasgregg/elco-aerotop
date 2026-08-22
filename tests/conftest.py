"""Test helpers that load API modules without requiring Home Assistant."""

from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType

PACKAGE_PATH = Path(__file__).parents[1] / "custom_components" / "elco_aerotop"

package = ModuleType("custom_components.elco_aerotop")
package.__path__ = [str(PACKAGE_PATH)]
sys.modules.setdefault("custom_components.elco_aerotop", package)
