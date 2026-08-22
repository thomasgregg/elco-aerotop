"""Constants for the ELCO Aerotop integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "elco_aerotop"
DEFAULT_BASE_URL: Final = "https://www.remocon-net.remotethermo.com"
DEFAULT_SCAN_INTERVAL: Final = 3600
MIN_SCAN_INTERVAL: Final = 60
REQUEST_TIMEOUT: Final = 70
USER_AGENT: Final = "ELCO-Aerotop-Home-Assistant/0.3.6"

CONF_BASE_URL: Final = "base_url"
CONF_GATEWAY_ID: Final = "gateway_id"
CONF_SCAN_INTERVAL: Final = "scan_interval"

PLATFORMS: Final = [
    "binary_sensor",
    "calendar",
    "climate",
    "number",
    "select",
    "sensor",
    "water_heater",
]

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
PLANT_HEADER_PATH: Final = "/R2/Plant/PlantHeader/{gateway_id}"
PLANT_USER_DATA_PATH: Final = "/R2/PlantData/GetUserData?id={gateway_id}"
BSB_TIME_PROGRAM_PATH: Final = "/R2/PlantTimeProgBsb/GetData/{gateway_id}"
METERING_PATH: Final = "/R2/PlantMetering/GetData/{gateway_id}"
MAINTENANCE_PATH: Final = "/R2/PlantData/GetMaintenanceData?id={gateway_id}"
AUTOMATED_MONITORING_PATH: Final = "/R2/AutomatedMonitoring/GetDrawerData/{gateway_id}"
BSB_BOILER_DATA_PATH: Final = "/R2/PlantData/GetBsbBoilerData?id={gateway_id}"
BUS_ERRORS_PATH: Final = "/api/v2/busErrors?gatewayId={gateway_id}&blockingOnly=False&culture=en-US"
BSB_READ_PATH: Final = "/R2/PlantMenuBsb/ReadDataPoints/{gateway_id}?addresses={addresses}"

BSB_TIME_PROGRAM_IDS: Final = {
    **{f"ChZn{zone}": zone for zone in range(1, 7)},
    "Dhw": 7,
    "Extra": 8,
    **{f"CoolZn{zone}": zone + 8 for zone in range(1, 7)},
}

# Annual energy records are controller-backed BSB values. Remocon exposes ten
# fixed-date slots, each with a performance factor and six energy totals. The
# address families and their slot offsets were verified from the authenticated
# structured BSB menu metadata; no displayed-line-number arithmetic is used.
BSB_ENERGY_HISTORY_ADDRESSES: Final = {
    slot: {
        "record_date": str(333480 + slot),
        "performance_factor": str(333490 + slot),
        "heat_delivered_heating": str(333500 + slot),
        "heat_delivered_dhw": str(333510 + slot),
        "refrigeration_delivered": str(334190 + slot),
        "energy_input_heating": str(333520 + slot),
        "energy_input_dhw": str(333530 + slot),
        "energy_input_cooling": str(334200 + slot),
    }
    for slot in range(1, 11)
}
BSB_ENERGY_HISTORY_ADDRESS_LIST: Final = tuple(
    address
    for slot_addresses in BSB_ENERGY_HISTORY_ADDRESSES.values()
    for address in slot_addresses.values()
)

BSB_ENTITY_ADDRESSES: Final = {
    # Verified from the authenticated Remocon BSB menu's structured accordion
    # metadata for line 7000 (Service/special operation > Message).
    "7000_maintenance_message": "327836",
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
    # Diagnostics consumers > 8786/8787, verified from structured BSB
    # accordion metadata. These are queried only for cooling-capable plants.
    "cooling_2_flow_temperature": "6949497",
    "cooling_2_flow_setpoint": "6949405",
}
BSB_DISCOVERY_ADDRESSES: Final = (
    *BSB_ENTITY_ADDRESSES.values(),
    *BSB_ENERGY_HISTORY_ADDRESS_LIST,
    "2950565",
    "328993",
    "5838456",
    "5838457",
    "327691",
    "329138",
    "460210",
    "329139",
    "2950338",
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
    # Keep the multi-field maintenance read isolated so a controller timeout
    # cannot suppress otherwise healthy BSB datapoints.
    "maintenance_message": (BSB_ENTITY_ADDRESSES["7000_maintenance_message"],),
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
    "cooling_2": (
        BSB_ENTITY_ADDRESSES["cooling_2_flow_temperature"],
        BSB_ENTITY_ADDRESSES["cooling_2_flow_setpoint"],
    ),
    # Verified safe reads retained in diagnostics. Values already represented
    # by a primary control (holiday level) or one-shot reset actions (schedule
    # defaults) intentionally do not create duplicate entities.
    "other_settings_clock": (
        "327691",  # Time of day and date > Clock time
    ),
    "other_settings_defaults": (
        "329138",  # Heating/cooling program 1 > 516 Default values
        "460210",  # Program 3/HC3 > 556 Default values
        "329139",  # Program 4/DHW > 576 Default values
        "2950338",  # Holidays heating/cooling 1 > 648 Operating level
    ),
    # One controller-bus request per annual slot is deliberate. A live gateway
    # returned "Communication error" for one 80-address request even though the
    # same points are readable in smaller structured JSON batches.
    **{
        f"energy_history_{slot}": tuple(addresses.values())
        for slot, addresses in BSB_ENERGY_HISTORY_ADDRESSES.items()
    },
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
