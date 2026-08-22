"""Constants for the ELCO Aerotop integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "elco_aerotop"
DEFAULT_BASE_URL: Final = "https://www.remocon-net.remotethermo.com"
DEFAULT_SCAN_INTERVAL: Final = 300
MIN_SCAN_INTERVAL: Final = 60
REQUEST_TIMEOUT: Final = 30
USER_AGENT: Final = "ELCO-Aerotop-Home-Assistant/0.2.12"

CONF_BASE_URL: Final = "base_url"
CONF_GATEWAY_ID: Final = "gateway_id"
CONF_SCAN_INTERVAL: Final = "scan_interval"

PLATFORMS: Final = ["binary_sensor", "calendar", "number", "select", "sensor"]

LOGIN_PATH: Final = "/R2/Account/Login"
MOBILE_LOGIN_PATH: Final = "/api/v2/accounts/login"
FEATURES_PATH: Final = "/R2/Plant/Features/{gateway_id}?eagerMode=true"
GET_DATA_PATH: Final = "/R2/PlantHomeBsb/GetData/{gateway_id}"
SET_TEMPERATURE_PATH: Final = "/R2/PlantTimeProgBsb/SetTemperature/{gateway_id}"
SAVE_DHW_PATH: Final = "/R2/PlantDhwBsb/Save/{gateway_id}"
SET_DATA_PATH: Final = "/R2/PlantHomeBsb/SetData/{gateway_id}"

DATA_ITEMS_PATH: Final = "/api/v2/remote/dataItems/{gateway_id}/get?umsys=si"
BSB_PLANT_DATA_PATH: Final = "/api/v2/remote/bsbPlantData/{gateway_id}"
MENU_ITEMS_PATH: Final = "/api/v2/menuItems/{gateway_id}?menuItems={item_ids}"
PLANTS_LITE_PATH: Final = "/api/v2/remote/plants/lite"
TIME_PROGRAM_PATH: Final = "/api/v2/remote/timeProgs/{gateway_id}/{program}?umsys=si"
METERING_PATH: Final = "/R2/PlantMetering/GetData/{gateway_id}"
MAINTENANCE_PATH: Final = "/R2/PlantData/GetMaintenanceData?id={gateway_id}"
BUS_ERRORS_PATH: Final = "/api/v2/busErrors?gatewayId={gateway_id}&blockingOnly=False&culture=en-US"
BSB_READ_PATH: Final = "/R2/PlantMenuBsb/ReadDataPoints/{gateway_id}?addresses={addresses}"

BSB_ENTITY_ADDRESSES: Final = {
    "700": "2950516",
    "710": "2950542",
    "712": "2950544",
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
    "2950565",
    "328993",
    "5838456",
    "5838457",
)
BSB_DISCOVERY_GROUPS: Final = {
    # Preserve the address grouping verified by v0.2.6 diagnostics. Address
    # 2950542 is read separately even though it is now correctly mapped to line 710.
    "heating_circuit": (
        BSB_ENTITY_ADDRESSES["700"],
        BSB_ENTITY_ADDRESSES["712"],
        "2950565",
        BSB_ENTITY_ADDRESSES["714"],
        BSB_ENTITY_ADDRESSES["720"],
        BSB_ENTITY_ADDRESSES["730"],
    ),
    "plant_pressure": (BSB_ENTITY_ADDRESSES["heating_circuit_pressure"],),
    "plant_auxiliary_2950542": (BSB_ENTITY_ADDRESSES["710"],),
    "plant_auxiliary_328993": ("328993",),
    "heat_pump": (
        BSB_ENTITY_ADDRESSES["heat_pump_flow_temperature"],
        BSB_ENTITY_ADDRESSES["heat_pump_return_temperature"],
        BSB_ENTITY_ADDRESSES["heat_pump_flow_setpoint"],
        BSB_ENTITY_ADDRESSES["heat_pump_gas_temperature"],
        BSB_ENTITY_ADDRESSES["source_outlet_temperature"],
        BSB_ENTITY_ADDRESSES["hot_gas_temperature"],
        "5838456",
        "5838457",
    ),
}
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
    "AutomaticThermoregulation",
    "AntilegionellaOnOff",
    "AntilegionellaTemp",
    "AntilegionellaFreq",
    "HybridMode",
    "BufferControlMode",
    "BufferTimeProgComfortHeatingTemp",
    "BufferTimeProgEconomyHeatingTemp",
    "BufferTimeProgComfortCoolingTemp",
    "BufferTimeProgEconomyCoolingTemp",
    # This spelling is used by the Remocon API.
    "IsQuite",
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
    "VirtTempOffsetHeat",
    "ZoneName",
    "VirtTempSetpointHeat",
    "VirtTempSetpointCool",
    "VirtComfortTemp",
    "VirtReducedTemp",
    "VirtTempOffsetCool",
)

# The mobile endpoint returns HTTP 500 when a request contains an item the
# configured gateway does not support. Read documented service/diagnostic IDs
# individually and select optional families from the Features response.
MENU_ITEM_BASE_IDS: Final = tuple(range(119, 131))
MENU_ITEM_VMC_IDS: Final = tuple(range(133, 192))
MENU_ITEM_SLP_IDS: Final = tuple(range(192, 221))
MENU_ITEM_HYBRID_IDS: Final = tuple(range(221, 251))
MENU_ITEM_HP_IDS: Final = tuple(range(251, 270))
MENU_ITEM_CASCADE_IDS: Final = tuple(range(270, 275))

DEFAULT_UPDATE_INTERVAL: Final = timedelta(seconds=DEFAULT_SCAN_INTERVAL)
