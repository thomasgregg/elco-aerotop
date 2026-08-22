"""Constants for the ELCO Aerotop integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "elco_aerotop"
DEFAULT_BASE_URL: Final = "https://www.remocon-net.remotethermo.com"
DEFAULT_SCAN_INTERVAL: Final = 300
MIN_SCAN_INTERVAL: Final = 60
REQUEST_TIMEOUT: Final = 30
USER_AGENT: Final = "ELCO-Aerotop-Home-Assistant/0.1.1"

CONF_BASE_URL: Final = "base_url"
CONF_GATEWAY_ID: Final = "gateway_id"
CONF_SCAN_INTERVAL: Final = "scan_interval"

PLATFORMS: Final = ["binary_sensor", "number", "select", "sensor"]

LOGIN_PATH: Final = "/R2/Account/Login"
FEATURES_PATH: Final = "/R2/Plant/Features/{gateway_id}?eagerMode=true"
GET_DATA_PATH: Final = "/R2/PlantHomeBsb/GetData/{gateway_id}"
SET_TEMPERATURE_PATH: Final = "/R2/PlantTimeProgBsb/SetTemperature/{gateway_id}"
SAVE_DHW_PATH: Final = "/R2/PlantDhwBsb/Save/{gateway_id}"
SET_DATA_PATH: Final = "/R2/PlantHomeBsb/SetData/{gateway_id}"

DEFAULT_UPDATE_INTERVAL: Final = timedelta(seconds=DEFAULT_SCAN_INTERVAL)
