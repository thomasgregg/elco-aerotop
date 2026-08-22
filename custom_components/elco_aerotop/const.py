"""Constants for the ELCO Aerotop integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "elco_aerotop"
DEFAULT_BASE_URL: Final = "https://www.remocon-net.remotethermo.com"
DEFAULT_SCAN_INTERVAL: Final = 300
MIN_SCAN_INTERVAL: Final = 60
REQUEST_TIMEOUT: Final = 30
USER_AGENT: Final = "ELCO-Aerotop-Home-Assistant/0.2.4"

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

DATA_ITEMS_PATH: Final = "/api/v2/remote/dataItems/{gateway_id}/get?umsys=si"
PLANTS_LITE_PATH: Final = "/api/v2/remote/plants/lite"
TIME_PROGRAM_PATH: Final = "/api/v2/remote/timeProgs/{gateway_id}/{program}?umsys=si"
METERING_PATH: Final = "/R2/PlantMetering/GetData/{gateway_id}"
MAINTENANCE_PATH: Final = "/R2/PlantData/GetMaintenanceData?id={gateway_id}"
BUS_ERRORS_PATH: Final = "/api/v2/busErrors?gatewayId={gateway_id}&blockingOnly=False&culture=en-US"
BSB_READ_PATH: Final = "/R2/PlantMenuBsb/ReadDataPoints/{gateway_id}?addresses={addresses}"

BSB_ENTITY_ADDRESSES: Final = {
    "700": "2950516",
    "710": "2950544",
    "712": "2950565",
    "714": "2950546",
    "720": "2950646",
    "730": "2950653",
    "heating_circuit_pressure": "340067",
    "heat_pump_flow_temperature": "5834029",
    "heat_pump_return_temperature": "5834039",
    "heat_pump_flow_setpoint": "5834599",
    "heat_pump_gas_temperature": "334189",
    "source_outlet_temperature": "5834170",
    "hot_gas_temperature": "5834175",
}
BSB_DISCOVERY_ADDRESSES: Final = (
    *BSB_ENTITY_ADDRESSES.values(),
    "2950542",
    "328993",
    "5838456",
    "5838457",
)
GLOBAL_DATA_ITEM_IDS: Final = (
    "HeatingCircuitPressure",
    "ChFlowTemp",
    "ChFlowSetpointTemp",
    "OutsideTemp",
    "Weather",
    "PlantMode",
    "Holiday",
    "IsFlameOn",
    "DhwTemp",
    "DhwMode",
    "DhwStorageTemperature",
    "DhwTimeProgComfortTemp",
    "DhwTimeProgEconomyTemp",
    "IsHeatingPumpOn",
)
ZONE_DATA_ITEM_IDS: Final = (
    "ZoneHeatRequest",
    "ZoneMode",
    "ZoneDesiredTemp",
    "ZoneMeasuredTemp",
    "ZoneDeroga",
    "ZoneComfortTemp",
    "IsZonePilotOn",
    "ZoneEconomyTemp",
    "HeatingFlowTemp",
    "HeatingFlowOffset",
    "CoolingFlowTemp",
    "CoolingFlowOffset",
)

DEFAULT_UPDATE_INTERVAL: Final = timedelta(seconds=DEFAULT_SCAN_INTERVAL)
