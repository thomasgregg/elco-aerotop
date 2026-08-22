# Complete entity, sensor, and control catalogue

[Back to the main README](../README.md)

This page documents every Home Assistant entity family exposed by ELCO Aerotop: where it appears
on the device page, whether it is enabled by default, when it is created, and whether it can write
to Remocon.

Entity creation is capability-driven. Two installations can legitimately have different entity
counts because controllers, connected probes, firmware, and advertised Remocon features vary. The
integration uses structured Remocon JSON APIs for runtime data; it does not scrape web pages.

## Reading the tables

- `<device>` is Home Assistant's generated slug for ELCO Aerotop plus the gateway ID.
- `<zone>` is the heating-zone number returned by Remocon.
- `<slot>` is one of ten controller energy-history records.
- **Section** is where Home Assistant groups the entity. Sensors means no special category;
  Configuration and Diagnostics are Home Assistant entity categories.
- **Default** applies when an entity is first registered. Home Assistant preserves an existing
  user's enabled/disabled registry choice during upgrades.
- **Access** is authoritative: read/write entities send commands; read-only entities never do.

A **disabled** entity exists in the entity registry but is not loaded until enabled. An
**unavailable** entity is loaded, but its backing datapoint currently has no usable value. A
temporarily unavailable entity remains registered and can recover on a later poll.

## Testing status and how to help

The integration has automated parser, mapping, diagnostics, and API-request tests. However, its
**write functionality, holiday calendars, and read-only schedule discovery are not yet sufficiently
tested on real ELCO installations**. Controller models and firmware can expose different options
and response shapes, so field testing is important before these features can be considered mature.

Testing is especially useful for:

- a conservative thermostat or DHW setpoint change that can be checked immediately on the ELCO
  controller or official application;
- every returned operating-mode choice, while avoiding changes that could make the installation
  unsafe or uncomfortable;
- populated holiday periods, including active, future, deleted, and out-of-service entries;
- heating, cooling, and DHW weekly schedules with different day/time layouts.

Holiday calendars and schedule discovery are read-only. Schedule editing is not implemented, and
holiday editing remains deliberately disabled. To report results, open a
[GitHub issue](https://github.com/thomasgregg/elco-aerotop/issues) with the controller model,
firmware, steps, expected/observed values, and a carefully inspected redacted Home Assistant
diagnostic export. Never share Remocon credentials, cookies, tokens, gateway identifiers, serial
numbers, addresses, or other personal data.

## Organization policy

| Entity group | Section | Default | Why |
|---|---|:---:|---|
| Zone thermostat and domestic hot water | Controls | Enabled | Primary user-facing controls |
| Zone/DHW reduced temperatures | Configuration | Enabled | Independent stored setpoints not represented by the native controls |
| Duplicate comfort numbers and mode selects | Configuration | Disabled | Compatibility and advanced access without duplicating primary controls |
| Holiday operating level | Configuration | Enabled | Relevant setting associated with holiday calendars; currently read-only |
| Cooling, protection, holiday, and flow-offset values | Configuration | Disabled | Advanced settings rather than live measurements; currently read-only |
| Outdoor, DHW, room, desired, pressure, flow, return, setpoint, demand, running, and flame values | Sensors | Enabled | Useful operational measurements and states |
| Metadata, faults, maintenance, and health | Diagnostics | Enabled | Important device and problem information kept out of normal sensors |
| Duplicate activity states and internal gas/source/hot-gas values | Diagnostics | Disabled | Native-control overlap or specialist troubleshooting |
| Low-level BSB 700–730 and cooling 8786/8787 values | Diagnostics | Disabled | Controller-level troubleshooting |
| All 80 energy-history entities | Diagnostics | Disabled | Fixed historical records, not increasing Energy Dashboard counters |

Entity category and entity domain answer different questions. Holiday operating level is a sensor
because it is read-only, but Home Assistant displays it in Configuration because it describes
controller configuration. A diagnostic temperature remains a temperature sensor even though it
appears under Diagnostics.

## Primary controls

| Entity | Typical entity ID | Default | Created when | Access |
|---|---|:---:|---|---|
| Zone `<zone>` thermostat | `climate.<device>_zone_<zone>_thermostat` | Enabled | The zone has a comfort setpoint and a safely mapped mode | Read/write |
| Domestic hot water | `water_heater.<device>_domestic_hot_water` | Enabled | DHW has a comfort setpoint and a safely mapped mode | Read/write |

### Zone thermostat mapping

| ELCO mode | HA HVAC mode | Preset | Write result |
|---|---|---|---|
| Automatic | auto | None | ELCO Automatic; its time program stays in control |
| Comfort | heat | comfort | Continuous Comfort operation |
| Reduced | heat | eco | Continuous Reduced operation |
| Protection | heat | Protection | ELCO frost-protection operation |

Protection is not mapped to off because the controller may still request heat to protect the
installation. A writable cool mode is not exposed because the cooling write contract has not been
verified, even when cooling readings exist.

The thermostat target is the stored **comfort setpoint**. The effective request may differ during
Automatic, Reduced, Protection, or holiday operation; the desired-temperature sensor reports that
effective request. Current temperature comes only from the zone's real room probe. Outdoor, flow,
and desired temperatures are not substituted because they have different physical meanings.
Heating/cooling/idle activity appears only when Remocon supplies the activity flags.

### Domestic-hot-water mapping

| ELCO code | ELCO meaning | HA operation | Availability |
|---:|---|---|---|
| 0 | Off | off | Only when returned in the allowed choices |
| 1 | On | heat_pump | Only when returned in the allowed choices |
| 2 | Eco | eco | Only when returned in the allowed choices |

The target is the DHW comfort temperature. Current temperature is the storage-tank reading and is
omitted when its probe is absent or faulty. Turn-on/off services are advertised only when both ELCO
On and Off are supported. Mapping uses stable numeric codes returned by Remocon, not translated
labels.

All native writes first refresh state, validate controller-provided limits/options, serialize
commands per device, and preserve companion values required by Remocon.

## Advanced and compatibility controls

The native thermostat and water-heater entities combine the values most people need. Lower-level
number and select entities remain for existing dashboards, precise configuration, and unusual
controller variants. This is the intentional overlap between Controls and Configuration.

| Entity | Typical entity ID | Default | Created when | Access |
|---|---|:---:|---|---|
| DHW comfort temperature | `number.<device>_domestic_hot_water_comfort_temperature` | Disabled | A comfort value is returned | Read/write |
| DHW reduced temperature | `number.<device>_domestic_hot_water_reduced_temperature` | Enabled | A reduced value is returned | Read/write |
| Zone `<zone>` comfort temperature | `number.<device>_zone_<zone>_comfort_temperature` | Disabled | A comfort value is returned | Read/write |
| Zone `<zone>` reduced temperature | `number.<device>_zone_<zone>_reduced_temperature` | Enabled | A reduced value is returned | Read/write |
| DHW mode | `select.<device>_domestic_hot_water_mode` | Disabled | Allowed DHW choices are returned | Read/write |
| Zone `<zone>` mode | `select.<device>_zone_<zone>_mode` | Disabled | Allowed zone choices are returned | Read/write |

Comfort numbers and mode selects duplicate the native controls, so new registrations keep them
disabled. Reduced values remain enabled because neither native model has a separate reduced target.
Both representations share one coordinator write path; changing one updates the other after the
post-command refresh without sending a second command.

Numbers use Remocon minimum, maximum, and step values when available. The integration rejects a
comfort temperature below its related reduced temperature and preserves unchanged setpoints/modes.

## Operational sensors

These appear under Sensors and are enabled by default.

| Entity | Typical entity ID | Unit | Created when |
|---|---|---:|---|
| Outside temperature | `sensor.<device>_outside_temperature` | °C | Reading or outdoor-probe capability exists |
| Domestic hot water temperature | `sensor.<device>_domestic_hot_water_temperature` | °C | Reading or DHW-probe capability exists |
| Heating circuit pressure | `sensor.<device>_heating_circuit_pressure` | bar | System data or BSB pressure fallback is supported |
| Heating circuit flow temperature | `sensor.<device>_heating_circuit_flow_temperature` | °C | System data is supported |
| Heating circuit flow setpoint temperature | `sensor.<device>_heating_circuit_flow_setpoint_temperature` | °C | System data is supported |
| Zone `<zone>` desired temperature | `sensor.<device>_zone_<zone>_desired_temperature` | °C | Effective zone request is returned |
| Zone `<zone>` room temperature | `sensor.<device>_zone_<zone>_room_temperature` | °C | Zone advertises a room probe |
| Zone `<zone>` heating flow temperature | `sensor.<device>_zone_<zone>_heating_flow_temperature` | °C | Zone system-data item is usable |
| Zone `<zone>` cooling flow temperature | `sensor.<device>_zone_<zone>_cooling_flow_temperature` | °C | Zone system-data item is usable |
| Zone `<zone>` derogation temperature | `sensor.<device>_zone_<zone>_derogation_temperature` | °C | Temporary zone override is returned |
| Heat pump flow temperature | `sensor.<device>_heat_pump_flow_temperature` | °C | Heat-pump capability or BSB point exists |
| Heat pump return temperature | `sensor.<device>_heat_pump_return_temperature` | °C | Heat-pump capability or BSB point exists |
| Heat pump flow setpoint | `sensor.<device>_heat_pump_flow_setpoint` | °C | Heat-pump capability or BSB point exists |

Temperature and pressure entities use measurement state classes. A BSB communication or
out-of-service flag makes only the affected entity unavailable.

## Operational binary sensors

| Entity | Typical entity ID | Default | Created when |
|---|---|:---:|---|
| Heat pump running | `binary_sensor.<device>_heat_pump_running` | Enabled | Heat-pump capability or running value exists |
| Flame on | `binary_sensor.<device>_flame_on` | Enabled | Boiler capability or flame state exists |
| Zone `<zone>` heat request | `binary_sensor.<device>_zone_<zone>_heat_request` | Enabled | A zone request flag is returned |

On cooling-capable systems, heat request may represent a combined heating-or-cooling request. The
thermostat activity is preferable when separate heating/cooling flags are returned.

## Configuration values

These are read-only sensors describing controller configuration. They do not become writable
merely because Home Assistant displays them under Configuration.

| Entity | Typical entity ID | Default | Created when |
|---|---|:---:|---|
| Zone `<zone>` cooling comfort temperature | `sensor.<device>_zone_<zone>_cooling_comfort_temperature` | Disabled | Cooling and value are reported |
| Zone `<zone>` cooling reduced temperature | `sensor.<device>_zone_<zone>_cooling_reduced_temperature` | Disabled | Cooling and value are reported |
| Zone `<zone>` heating protection temperature | `sensor.<device>_zone_<zone>_heating_protection_temperature` | Disabled | Value is returned |
| Zone `<zone>` cooling protection temperature | `sensor.<device>_zone_<zone>_cooling_protection_temperature` | Disabled | Cooling and value are reported |
| Zone `<zone>` heating holiday temperature | `sensor.<device>_zone_<zone>_heating_holiday_temperature` | Disabled | Nonzero value is returned |
| Zone `<zone>` cooling holiday temperature | `sensor.<device>_zone_<zone>_cooling_holiday_temperature` | Disabled | Cooling and nonzero value are reported |
| Zone `<zone>` holiday operating level | `sensor.<device>_zone_<zone>_holiday_operating_level` | Enabled | Reduced/Frost-protection choice is returned |
| Zone `<zone>` heating flow offset | `sensor.<device>_zone_<zone>_heating_flow_offset` | Disabled | Zone system-data item is usable |
| Zone `<zone>` cooling flow offset | `sensor.<device>_zone_<zone>_cooling_flow_offset` | Disabled | Zone system-data item is usable |

## Holiday calendars

| Entity | Typical entity ID | Default | Created when | Access |
|---|---|:---:|---|---|
| Zone `<zone>` holidays | `calendar.<device>_zone_<zone>_holidays` | Enabled | Native BSB zone response includes holidays | Read-only |

Each calendar contains every valid zone holiday. Deleted and out-of-service periods are omitted.
ELCO's final date is inclusive while Home Assistant calendar ends are exclusive, so the
integration adds one day when presenting an event. Holiday operating level says whether the
periods use Reduced or Frost protection.

Holiday editing is intentionally absent. The observed write sends complete plant/zone state and
forces Automatic mode, so create/update/delete must be verified on a populated gateway first.
The calendar conversion and schedule discovery also need reports from real populated controllers;
see [Testing status and how to help](#testing-status-and-how-to-help).

## Diagnostics enabled by default

| Entity | Typical entity ID | Kind | Created/available when |
|---|---|---|---|
| Gateway serial | `sensor.<device>_gateway_serial` | Metadata | Plant metadata returns it |
| Plant name | `sensor.<device>_plant_name` | Metadata | Plant metadata returns it |
| Plant location | `sensor.<device>_plant_location` | Metadata | Structured location can be rendered |
| Gateway firmware | `sensor.<device>_gateway_firmware` | Metadata | Plant metadata returns it |
| Controller error count | `sensor.<device>_controller_error_count` | Fault | Error API returns a list |
| Controller error | `binary_sensor.<device>_controller_error` | Problem | Error API returns a list; up to ten records are attributes |
| Maintenance code 1/2 | `sensor.<device>_maintenance_code_<1-2>` | Maintenance | BSB 7000 response is usable |
| Maintenance priority 1/2 | `sensor.<device>_maintenance_priority_<1-2>` | Maintenance | BSB 7000 response is usable |
| Hydraulic pressure health | `sensor.<device>_hydraulic_pressure_health` | Health | Automated monitoring returns a level |
| Refrigerant circuit health | `sensor.<device>_refrigerant_circuit_health` | Health | Automated monitoring returns a level |
| Circulation health | `sensor.<device>_circulation_health` | Health | Automated monitoring returns a level |
| Combustion health | `sensor.<device>_combustion_health` | Health | Automated monitoring returns a level |
| Other appliance health | `sensor.<device>_other_appliance_health` | Health | Automated monitoring returns a level |
| Predictive maintenance notice count | `sensor.<device>_predictive_maintenance_notice_count` | Maintenance | Structured notice collection is returned |
| Outside probe problem | `binary_sensor.<device>_outside_temperature_sensor_problem` | Problem | Outdoor probe/error flag exists |
| DHW probe problem | `binary_sensor.<device>_domestic_hot_water_temperature_sensor_problem` | Problem | DHW probe/error flag exists |
| Zone `<zone>` room probe problem | `binary_sensor.<device>_zone_<zone>_room_temperature_sensor_problem` | Problem | Room probe and error flag exist |

BSB line 7000 can return two simultaneous maintenance messages, so code and priority have two
entities each. Raw structured maintenance, monitoring, and notice payloads remain in redacted Home
Assistant diagnostics for fields that do not yet justify dedicated entities.

## Diagnostics disabled by default

| Entity | Typical entity ID | Created when |
|---|---|---|
| Zone `<zone>` mode | `sensor.<device>_zone_<zone>_mode` | Current mode exists; duplicates native/select state |
| Domestic hot water enabled | `binary_sensor.<device>_domestic_hot_water_enabled` | DHW is not hidden |
| Zone `<zone>` heating active | `binary_sensor.<device>_zone_<zone>_heating_active` | Flag is returned; duplicates thermostat activity |
| Zone `<zone>` cooling active | `binary_sensor.<device>_zone_<zone>_cooling_active` | Cooling and flag are returned |
| Heating circuit 700 operating mode | `sensor.<device>_heating_circuit_700_operating_mode` | A zone or BSB point exists |
| Heating circuit 710 comfort setpoint | `sensor.<device>_heating_circuit_710_comfort_setpoint` | A zone or BSB point exists |
| Heating circuit 712 reduced setpoint | `sensor.<device>_heating_circuit_712_reduced_setpoint` | A zone or BSB point exists |
| Heating circuit 714 frost setpoint | `sensor.<device>_heating_circuit_714_frost_protection_setpoint` | A zone or BSB point exists |
| Heating circuit 720 curve slope | `sensor.<device>_heating_circuit_720_heating_curve_slope` | A zone or BSB point exists |
| Heating circuit 730 summer/winter limit | `sensor.<device>_heating_circuit_730_summer_winter_heating_limit` | A zone or BSB point exists |
| Heat pump gas temperature | `sensor.<device>_heat_pump_gas_temperature` | Heat-pump capability or point exists |
| Source outlet temperature | `sensor.<device>_source_outlet_temperature` | Heat-pump capability or point exists |
| Hot gas temperature | `sensor.<device>_hot_gas_temperature` | Heat-pump capability or point exists |
| Cooling circuit 2 flow temperature | `sensor.<device>_cooling_circuit_2_flow_temperature` | Gateway advertises cooling |
| Cooling circuit 2 flow setpoint | `sensor.<device>_cooling_circuit_2_flow_setpoint` | Gateway advertises cooling |

BSB line numbers are not JSON addresses and cannot be derived arithmetically. Only verified,
allowlisted addresses are queried. Optional groups are isolated so a rejected address cannot
suppress unrelated data.

## Energy-history entities

The controller exposes ten fixed history slots with eight fields each: 80 diagnostic entities. All
are registered **disabled by default**, including slot 1.

| Per-slot entity | Typical entity ID | Unit | Meaning |
|---|---|---:|---|
| Fixed day `<slot>` | `sensor.<device>_annual_energy_record_<slot>_date` | date | Date attached by ELCO to the record |
| Yearly perf factor `<slot>` | `sensor.<device>_annual_performance_factor_<slot>` | — | ELCO's stored yearly performance factor |
| Heat delivered heating `<slot>` | `sensor.<device>_annual_heat_delivered_heating_<slot>` | kWh | Heat delivered for space heating |
| Heat delivered DHW `<slot>` | `sensor.<device>_annual_heat_delivered_dhw_<slot>` | kWh | Heat delivered for domestic hot water |
| Refrigeration delivered `<slot>` | `sensor.<device>_annual_refrigeration_delivered_<slot>` | kWh | Cooling delivered |
| Energy brought in heating `<slot>` | `sensor.<device>_annual_energy_input_heating_<slot>` | kWh | Input energy assigned to heating |
| Energy brought in DHW `<slot>` | `sensor.<device>_annual_energy_input_dhw_<slot>` | kWh | Input energy assigned to hot water |
| Energy brought in cooling `<slot>` | `sensor.<device>_annual_energy_input_cooling_<slot>` | kWh | Input energy assigned to cooling |

Display names follow the authenticated ELCO energy-meter page. Internal entity IDs retain the
earlier annual wording so histories, automations, and dashboards do not break.

These are controller-owned snapshots, not continuously increasing lifetime meters. Their kWh
sensors use the energy device class and total state class, not total_increasing. They are useful
for comparing ELCO's stored historic heating, DHW, cooling, input, output, and performance records.
They are **not suitable as direct Home Assistant Energy Dashboard inputs**, which require a
continuously increasing total or interval statistics with defined reset behavior. A communication
or out-of-service flag affects only its record.

## Why an expected entity may be absent or unavailable

| Situation | Result |
|---|---|
| Remocon explicitly reports a feature as unsupported | Related entity is not created |
| A write value lacks a current value, limits, or allowed options needed for validation | Write entity is not created |
| A stable capability exists but its current reading is out of service | Entity stays registered and is unavailable until recovery |
| An optional endpoint is unsupported, unauthorized, slow, or changed | Only that family is unavailable; core entities continue |
| Entity is disabled by default | It exists in the registry but is not loaded until enabled |
| Installation already has an enabled/disabled choice | Home Assistant preserves it on upgrade |

Diagnostics capture every key/value returned by each JSON endpoint the integration calls. That is
not every theoretical BSB address: Remocon does not publish the complete map, and unsupported
addresses can block or time out a gateway.

## Enabling and disabling entities

Open **Settings → Devices & services → ELCO Aerotop → Devices → ELCO Aerotop → Entities**, select
an entity, open its settings, and change **Enabled**. Alternatively use the main Entities page and
filter by the ELCO Aerotop integration.

A newer default does not overwrite an existing registry choice. Users who previously enabled all
energy or duplicate entities keep them enabled until changing them manually. Entity-category
changes are different: after the update loads, existing entities can move between Sensors,
Configuration, and Diagnostics without changing their IDs or enabled state.

## Activity history and duplicates

Home Assistant records state changes for enabled entities. Enabling a large group can make the
device Activity view much busier, especially when raw and native entities reflect the same state.
The disabled-by-default policy reduces this noise on new installations. Disabling a duplicate does
not disable the underlying controller feature or its native thermostat/water-heater control.
