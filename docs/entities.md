# Complete entity, sensor, and control catalogue

[Back to the main README](../README.md) · [Compare BSB and Galevo support](../README.md#what-you-can-do)

This page documents every Home Assistant entity family exposed by ELCO Aerotop: where it appears
on the device page, whether it is enabled by default, when it is created, and whether it can write
to Remocon.

Entity creation is capability-driven. Two installations can legitimately have different entity
counts because controllers, connected probes, firmware, and advertised Remocon features vary. The
integration uses structured Remocon JSON APIs for runtime data; it does not scrape web pages.

## Controller families

Both BSB and Galevo support native zone thermostats, a native water heater, and readings such as
temperatures, pressure, flow, heat demand, and controller errors. Each gateway gets its own
Home Assistant device, with entities selected from its reported capabilities.

- **BSB** adds the zone holiday calendar and current-holiday controls, heating-circuit tuning,
  controller maintenance information, and optional annual energy records.
- **Galevo** adds plant mode, season-specific temperature and flow controls, quiet mode, hot-water
  boost, automatic thermoregulation, plant-wide holiday dates and switch, and consumption reports.
  Dedicated sensors show plant mode, hot-water operation, and the stored hot-water target.

## Reading the tables

- `<device>` is Home Assistant's generated slug for ELCO Aerotop plus the gateway ID.
- `<zone>` is the heating-zone number returned by Remocon.
- `<slot>` is one of ten controller energy-history records.
- **Section** is where Home Assistant groups the entity. Sensors means no special category;
  Configuration and Diagnostics are Home Assistant entity categories.
- **Default** applies when an entity is first registered. Home Assistant preserves an existing
  user's enabled/disabled registry choice during upgrades.
- **Access** describes the commands an entity supports when available.

A **disabled** entity exists in the entity registry but is not loaded until enabled. An
**unavailable** entity is loaded but currently lacks the required reading or conditions. A
temporarily unavailable entity can recover on a later poll under the same ID.

## Testing status and how to help

Automated tests cover BSB and Galevo, including running both in one Home Assistant instance.
A BSB installation passed an upgrade check from `v0.3.17`. Recorded Galevo hardware checks cover
plant mode, scheduled hot-water comfort, the active heating thermostat, and entity availability
after upgrading. The gateway's firmware and installed options determine which other controls
are available. [Galevo hardware retest](https://github.com/thomasgregg/elco-aerotop/issues/3#issuecomment-7095033094)

Testing is especially useful for:

- a conservative thermostat or DHW setpoint change that can be checked immediately on the ELCO
  controller or official application;
- one conservative change to a BSB tuning control, followed by checking lines 648, 714, 720, or
  730 on the controller or Remocon;
- cooling comfort and reduced targets while cooling is active on either family;
- every returned operating-mode choice, while avoiding changes that could make the installation
  unsafe or uncomfortable;
- populated holiday periods, including active, future, deleted, and out-of-service entries;
- heating, cooling, and DHW weekly schedules with different day/time layouts.

Weekly programs are read-only on both families. BSB exposes a read-only calendar and writes the
current zone holiday through dedicated controls. Galevo uses a plant-wide holiday switch and date
drafts. To report results, open a
[GitHub issue](https://github.com/thomasgregg/elco-aerotop/issues) with the controller model,
firmware, steps, expected/observed values, and a carefully inspected redacted Home Assistant
diagnostic export. Never share Remocon credentials, cookies, tokens, gateway identifiers, serial
numbers, addresses, or other personal data.

## Organization policy

| Entity group | Section | Default | Why |
|---|---|:---:|---|
| Zone thermostat and domestic hot water | Controls | Enabled | Primary user-facing controls |
| BSB Holiday until and Cancel holiday | Controls | Enabled | Set or cancel the current zone holiday |
| Galevo Holiday switch and draft dates | Controls and Configuration | Enabled | Manage a plant-wide holiday |
| Zone/DHW and cooling reduced temperatures | Configuration | Enabled | Adjust reduced or economy targets alongside the native controls |
| Existing comfort numbers and mode selects | Configuration | Existing registry setting | Preserve entity IDs and settings in use |
| Holiday operating-level select | Configuration | Enabled | Small, reversible controller setting associated with holiday calendars |
| BSB 714/720/730 controls | Configuration | Disabled | Advanced controller tuning with fixed, reviewed limits |
| Cooling, protection, holiday, and flow-offset values | Diagnostics | Disabled | Advanced read-only settings rather than live measurements |
| Outdoor, DHW, room, desired, pressure, flow, return, setpoint, demand, running, and flame values | Sensors | Enabled | Useful operational measurements and states |
| Metadata, faults, maintenance, and health | Diagnostics | Enabled | Important device and problem information kept out of normal sensors |
| Duplicate activity states and internal gas/source/hot-gas values | Diagnostics | Disabled | Native-control overlap or specialist troubleshooting |
| Low-level BSB 700–730 and cooling 8786/8787 values | Diagnostics | Disabled | Controller-level troubleshooting |
| BSB annual energy records | Diagnostics | Disabled | Fixed historical records |
| Galevo energy consumption reports | Sensors | Enabled | Consumption over a stated time window |
| Galevo estimated SPF | Sensors | Enabled, when complete data is returned | Heating and hot-water performance for the previous calendar year |

Entity category and entity domain answer different questions. The writable holiday operating
level is a Configuration select; its older sensor counterpart remains a read-only Diagnostic for
compatibility. A diagnostic temperature remains a temperature sensor even though it appears under
Diagnostics.

## Primary controls

| Entity | Typical entity ID | Default | Created when | Access |
|---|---|:---:|---|---|
| Zone `<zone>` thermostat | `climate.<device>_zone_<zone>_thermostat` | Enabled | The zone reports a comfort setpoint or a mapped mode | Read/write |
| Domestic hot water | `water_heater.<device>_domestic_hot_water` | Enabled | DHW reports a comfort setpoint or a mapped mode | Read/write |

The native entities keep their IDs when readings or mode options temporarily disappear. Actions
return as their data becomes available again. A partial response does not create extra comfort
numbers or mode selects.

### Zone thermostat mapping

| ELCO mode | HA HVAC mode | Preset | Write result |
|---|---|---|---|
| Automatic | auto | None | ELCO Automatic; its time program stays in control |
| Comfort | heat or cool | comfort | Continuous Comfort operation |
| Reduced | heat or cool | eco | Continuous Reduced operation |
| Protection | heat or cool | Protection | ELCO protection operation |

BSB Protection can still request heat. Galevo's separate Off option maps to `off`. BSB uses its
cooling-active flag to select cooling targets. On Galevo, **Cooling** or **Cooling Only** selects
cooling targets. The plant-mode select changes the Galevo plant season.

The thermostat target is the stored comfort setpoint for the selected season. When the Galevo
plant mode is missing, the thermostat hides the seasonal target and temperature adjustment.
Both comfort and reduced values must be present before a zone temperature can be changed.
The effective request may differ during Automatic, Reduced,
Protection, or holiday operation; the desired-temperature sensor reports that effective request.
Current temperature comes only from the zone's real room probe. Outdoor, flow, and desired
temperatures are not substituted because they have different physical meanings. Heating/cooling/
idle activity appears only when Remocon supplies the activity flags.

### Domestic-hot-water mapping

| Controller | Remocon mode | Home Assistant operation |
|---|---|---|
| BSB | Off | `off` |
| BSB | On | `heat_pump` |
| BSB | Eco | `eco` |
| Galevo | Disabled | `off` |
| Galevo | Time Based | `heat_pump` |
| Galevo | Always active | `performance` |

The native water heater exposes the operations offered by the gateway using stable codes.
An existing mode select shows the controller's labels. Turn-on and turn-off actions require
an Off choice and a supported On choice. The current temperature comes from the storage-tank
probe when usable.

BSB uses the comfort target. Galevo **Time Based** follows the controller's schedule and
uses scheduled comfort and economy targets. **Always active** uses its own target. The
integration honors read-only flags on those Galevo setpoints. Galevo also exposes separate
sensors for the operating mode and current target.

All native writes first refresh only the required current state, validate controller-provided
limits/options, serialize commands per device, and preserve companion values required by Remocon.
An ambiguous transport or server failure never causes the command to be resent; an uncached read
reconciles the result when Remocon permits another request.

## Advanced and compatibility controls

Use the thermostat and water heater for comfort temperatures and operating modes. Standalone
controls expose separate settings and additional controller modes. Previously registered controls
keep their entity IDs and enabled settings.

| Entity | Typical entity ID | Default | Created when | Access |
|---|---|:---:|---|---|
| DHW comfort temperature | `number.<device>_domestic_hot_water_comfort_temperature` | Existing registry setting | This number already exists and a comfort value is returned | Read/write |
| DHW reduced temperature | `number.<device>_domestic_hot_water_reduced_temperature` | Enabled | A reduced value is returned | Read/write |
| Zone `<zone>` comfort temperature | `number.<device>_zone_<zone>_comfort_temperature` | Existing registry setting | This number already exists and a heating comfort value is returned | Write during heating |
| Zone `<zone>` reduced temperature | `number.<device>_zone_<zone>_reduced_temperature` | Enabled | A reduced value is returned | Write during heating |
| Zone `<zone>` cooling comfort temperature | `number.<device>_zone_<zone>_cooling_comfort_temperature` | Existing registry setting | This number already exists and a cooling comfort value is returned | Write during cooling |
| Zone `<zone>` cooling reduced temperature | `number.<device>_zone_<zone>_cooling_reduced_temperature` | Enabled | Cooling capability and a reduced value are returned | Write during cooling |
| DHW mode | `select.<device>_domestic_hot_water_mode` | Enabled when first added for an extra mode | The gateway offers a mode without a native mapping, or this select already exists | Read/write |
| Zone `<zone>` mode | `select.<device>_zone_<zone>_mode` | Enabled when first added for an extra mode | The gateway offers a mode without a native mapping, or this select already exists | Read/write |
| Plant mode | `select.<device>_plant_mode` | Enabled | Galevo advertises a valid writable `PlantMode` item with options | Mobile `plantData/{gw}/mode` with fresh state and readback |
| Scheduled hot water comfort temperature | `number.<device>_scheduled_hot_water_comfort_temperature` | Existing registry setting | This number already exists. Available in Time Based mode with a writable, bounded comfort value and an economy value | Typed input, verified by reading back both scheduled setpoints |
| Zone heating and cooling flow temperatures | `number.<device>_zone_<zone>_<season>_flow_temperature` | Enabled | Galevo returns the available flow item and permits writing | Typed input |
| Zone heating and cooling flow offsets | `number.<device>_zone_<zone>_<season>_flow_offset` | Enabled | Galevo returns the available offset and permits writing | Slider |
| Quiet mode, hot-water boost, automatic thermoregulation | `switch.<device>_quiet_mode`, `switch.<device>_domestic_hot_water_boost`, `switch.<device>_automatic_thermoregulation` | Enabled | Galevo returns the corresponding writable item | Read/write |
| Zone 1 holiday operating level | `select.<device>_zone_1_holiday_operating_level` | Enabled | Heating circuit 1 exists; usable when BSB line 648 is returned | Read/write |
| Heating circuit 714 frost protection setpoint | `number.<device>_heating_circuit_frost_protection_setpoint_714` | Disabled | A zone exists; usable when BSB lines 712 and 714 are returned | Read/write |
| Heating circuit 720 heating curve slope | `number.<device>_heating_curve_slope_720` | Disabled | A zone exists; usable when BSB line 720 is returned | Read/write |
| Heating circuit 730 summer/winter heating limit | `number.<device>_summer_winter_heating_limit_730` | Disabled | A zone exists; usable when BSB line 730 is returned | Read/write |

The Galevo plant-mode select uses the choices returned by the controller. Labels come
from its option text, and the write validates and sends the associated numeric value.

For the Galevo **Holiday** switch and **Pending holiday start/end** date entities, see
[Holiday periods in Home Assistant](holidays.md#galevo-holiday-controls).

The thermostat follows the current heating or cooling season, and the water heater follows the DHW
operation. Use the standalone reduced and economy numbers to edit those separate targets.
Previously registered controls update from the same controller readback as the native entities.
Galevo's season-specific numbers are writable when their named season is active. Inactive
season-specific numbers appear as unavailable until their season becomes active. Previously
registered off-season comfort numbers keep their IDs and stay unavailable. On the tested
gateway, the Remocon heating and cooling comfort settings mirrored each other.

Temperature numbers declare Home Assistant's temperature device class and native degrees Celsius
unit. Zone values use Remocon limits. The reviewed BSB limits are 4–35 °C in 0.5 °C steps for line
714 (also capped by the fresh line-712 reduced setpoint), 0.1–4.0 in 0.02 steps for line 720, and
8–30 °C in 0.5 °C steps for line 730. The integration rejects heating comfort below reduced,
cooling comfort above reduced, and preserves unchanged companion values.

The line-648 select resolves the numeric enum from the fresh controller response, so translated
or model-specific option codes are not guessed. All four BSB writes are exact-address allowlisted,
read before writing, and read back after writing. The existing read-only sensor entities for the
same values remain available for dashboard and entity-ID compatibility.

## Operational sensors

These appear under Sensors and are enabled by default.

| Entity | Typical entity ID | Unit | Created when |
|---|---|---:|---|
| Outside temperature | `sensor.<device>_outside_temperature` | °C | Reading or outdoor-probe capability exists |
| Domestic hot water temperature | `sensor.<device>_domestic_hot_water_temperature` | °C | Reading or DHW-probe capability exists |
| Domestic hot water operation | `sensor.<device>_domestic_hot_water_operation` | — | Galevo reports the current hot-water mode |
| Domestic hot water target temperature | `sensor.<device>_domestic_hot_water_target_temperature` | °C | Galevo reports the target for the current mode |
| Plant mode status | `sensor.<device>_plant_mode_status` | — | Galevo returns `PlantMode` |
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
| Domestic hot water boost | `binary_sensor.<device>_domestic_hot_water_boost` | Enabled | Remocon returns `IsDhwBoost` |

On cooling-capable systems, heat request may represent a combined heating-or-cooling request. The
thermostat activity is preferable when separate heating/cooling flags are returned.

## Diagnostic configuration values

These are read-only sensors describing controller configuration. Home Assistant displays them
under Diagnostics because sensor entities cannot use the writable Configuration category.

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

Flow offsets use Home Assistant's temperature-difference device class; all other values in this
table that represent temperatures use the absolute-temperature device class.

## Holiday entities

See [Holiday periods in Home Assistant](holidays.md) for step-by-step instructions for both
families and a BSB dashboard example.

### Galevo plant-wide holiday

| Entity | Typical entity ID | Purpose |
|---|---|---|
| Pending holiday start | `date.<device>_pending_holiday_start` | Edit the first day of the local draft |
| Pending holiday end | `date.<device>_pending_holiday_end` | Edit the inclusive final day of the local draft |
| Holiday | `switch.<device>_holiday` | Submit the draft or cancel the holiday. State follows the controller's holiday flag. |

### BSB zone calendar and current holiday

| Entity | Typical entity ID | Default | Created when | Access |
|---|---|:---:|---|---|
| Zone `<zone>` holidays | `calendar.<device>_zone_<zone>_holidays` | Enabled | Native BSB zone response includes holidays | Read-only |
| Zone `<zone>` holiday until | `date.<device>_zone_<zone>_holiday_until` | Enabled | Native BSB zone response contains a holiday list | Read/write |
| Zone `<zone>` cancel holiday | `button.<device>_zone_<zone>_cancel_holiday` | Enabled | Native BSB zone response contains a holiday list; available while a usable period exists | Write |

Each calendar contains every valid zone holiday. Deleted and out-of-service periods are omitted.
ELCO's final date is inclusive while Home Assistant calendar ends are exclusive, so the
integration adds one day when presenting an event. Holiday operating level says whether the
periods use Reduced or Frost protection.

Setting **Holiday until** creates a holiday beginning at the current Home Assistant local time, or
changes the current period's final day. The final day is inclusive, past dates are
rejected, and the write changes the zone to Automatic just like Remocon. **Cancel holiday** marks
the current period deleted and deliberately leaves the resulting zone mode unchanged; it does not
restore the pre-holiday mode. Both commands preserve complete fresh plant/zone snapshots, use the
normal serialized single-write/reconciliation policy, and refresh immediately.

These two entities address only Remocon's first/current holiday. They do not expose future start
dates, slot selection, overlaps, or the controller's underlying eight-slot planner. The separate
holiday operating-level select changes only Reduced versus Frost protection. Calendar conversion,
current-holiday writes, and schedule discovery still need reports from populated controllers; see
[Testing status and how to help](#testing-status-and-how-to-help).

## Diagnostics enabled by default

| Entity | Typical entity ID | Kind | Created/available when |
|---|---|---|---|
| Gateway serial | `sensor.<device>_gateway_serial` | Metadata | Plant metadata returns it |
| Plant name | `sensor.<device>_plant_name` | Metadata | Plant metadata returns it |
| Plant location | `sensor.<device>_plant_location` | Metadata | Structured location can be rendered |
| Gateway firmware | `sensor.<device>_gateway_firmware` | Metadata | Plant metadata returns `gwFwVer`; Remocon labels the same value Connectivity Gateway software |
| Controller clock | `sensor.<device>_controller_clock` | Timestamp | BSB Clock time (address `327691`) is readable; shown in Home Assistant's configured time zone |
| Last successful update | `sensor.<device>_last_successful_update` | Timestamp | A complete core Remocon data refresh has succeeded |
| Gateway online | `binary_sensor.<device>_gateway_online` | Connectivity | Plant-header API returns a connectivity flag |
| Plant status | `sensor.<device>_plant_status` | Status | Plant-header API returns a status label or code |
| Appliance model | `sensor.<device>_appliance_model` | Metadata | Plant-header API returns it |
| Appliance serial | `sensor.<device>_appliance_serial` | Metadata | Plant-header or BSB appliance API returns it |
| Plant owner | `sensor.<device>_plant_owner` | Metadata | Plant-user API returns a first or last name |
| Account language | `sensor.<device>_account_language` | Metadata | Plant-user API returns it |
| Bus devices | `sensor.<device>_bus_devices` | Inventory | Galevo returns a bus-device list |
| Gateway signal level | `sensor.<device>_gateway_signal_level` | Connectivity | Galevo returns mobile menu item 119 |
| Weather | `sensor.<device>_weather` | Status | Remocon returns `Weather` |
| Quiet mode | `binary_sensor.<device>_quiet_mode` | Status | Remocon returns `IsQuite` |
| Automatic thermoregulation | `binary_sensor.<device>_automatic_thermoregulation` | Status | Remocon returns `AutomaticThermoregulation` |
| Holiday active | `binary_sensor.<device>_holiday_active` | Status | Remocon returns `Holiday` |
| Backup resistor active | `binary_sensor.<device>_backup_resistor_active` | Status | Remocon returns `IsResistorOn` |
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

Controller clock is read-only. It reflects Remocon's **Time of day and date → Clock time** value;
the integration does not write or synchronize the controller clock.

`sensor.<device>_gateway_firmware` is the only entity created for `gwFwVer`. Home Assistant also
shows that same value in the device's built-in software-version field; this is device metadata,
not a second entity. Owner names, phone numbers, and plant addresses are redacted from downloadable
diagnostics even when their dedicated entities are present locally.

## Diagnostics disabled by default

Remocon can also provide these optional readings.

| Entity | Typical entity ID | Created when |
|---|---|---|
| On-line outdoor temperature | `sensor.<device>_on_line_outdoor_temperature` | Galevo returns mobile menu item 130 |
| Buffer control mode | `sensor.<device>_buffer_control_mode` | Remocon returns `BufferControlMode` |
| Buffer comfort and economy temperatures | `sensor.<device>_buffer_<level>_<season>_temperature` | Remocon returns the heating or cooling buffer setpoint |

| Entity | Typical entity ID | Created when |
|---|---|---|
| Owner phone | `sensor.<device>_owner_phone` | Plant-user API returns it |
| Owner mobile phone | `sensor.<device>_owner_mobile_phone` | Plant-user API returns it |
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
| Source outlet temperature minimum (8430) | `sensor.<device>_source_outlet_temperature_minimum` | Remocon advertises heat-pump capability |
| Hot gas temperature | `sensor.<device>_hot_gas_temperature` | Heat-pump capability or point exists |
| Cooling circuit 2 flow temperature | `sensor.<device>_cooling_circuit_2_flow_temperature` | Gateway advertises cooling |
| Cooling circuit 2 flow setpoint | `sensor.<device>_cooling_circuit_2_flow_setpoint` | Gateway advertises cooling |

**Source outlet temperature minimum** is a read-only temperature sensor. The first read runs
after setup, and subsequent reads run about once an hour. If Remocon does not return the
datapoint, the sensor is unavailable.

To start recording, find the sensor under **Settings → Devices & services → Entities**, filter
by **Disabled**, then open the entity settings and turn on **Enabled**. Add it to a
[History graph card](https://www.home-assistant.io/dashboards/history-graph/) to see the readings
Home Assistant has recorded since activation.

Line 8430 shows the controller's stored minimum. A low reading can carry over into subsequent
days until cleared on the controller. The integration has no reset control for 8430. For a
separate minimum per day, use the live outlet sensor (8429) in a
[Statistics graph card](https://www.home-assistant.io/dashboards/statistics-graph/) with the
period set to day and the statistic set to min. This uses only the samples Home Assistant
receives, so it can miss short dips between polls.

BSB line numbers are not JSON addresses and cannot be derived arithmetically. Only verified,
allowlisted addresses are queried. Optional groups are isolated so a rejected address cannot
suppress unrelated data.

## Energy-history entities

### Galevo consumption reports

Galevo publishes separate energy sensors for heating, domestic hot water, and cooling
when the service returns the corresponding series. The periods are the last 24 hours,
last 7 days, last complete month, and last 12 complete months. The monthly series ends at
the previous calendar month. The integration uses its latest entry for the one-month
sensor and sums its latest 12 entries for the 12-month sensor. A reported zero is a valid
reading. Missing buckets leave the affected period unavailable. Each value is a
windowed consumption reading in kWh.

### Galevo estimated seasonal performance factor (SPF)

**Estimated SPF – previous calendar year** divides the total heat produced for space heating
and domestic hot water by the total electricity used for those same services over January
through December of the prior year. For example, 9,000 kWh of heat and 3,000 kWh of electricity
give an estimated SPF of 3.00. The ratio combines the kWh totals across all months and uses.
Its `year` attribute shows the year represented by the value. The calculation excludes cooling.
In German, the sensor is named **Geschätzte Jahresarbeitszahl – Vorjahr**.

The source is the unrounded **Energy produced** and **Electricity** data from Remocon web
metering. These figures are Remocon estimates. Any supplemental consumption is reflected to
the extent that Remocon includes it in the heating and hot-water electricity series. The
rolling **last 12 complete months** consumption sensors use a separate report and may span
parts of two calendar years.

The sensor first appears after Remocon reports metering support, history reaching at least
January of the target year, and 12 distinct matching monthly buckets for each of the four
series. Each bucket must contain a finite, nonnegative kWh value, and total annual electricity
must be positive. Explicit zero-consumption months can be valid. Missing months, ambiguous
duplicate rows, or unavailable web metering leave the estimate unavailable. An already
registered entity keeps its ID and recovers when a valid report returns.

The web report is an optional background read after normal discovery. If the sensor is
missing, check **Energy produced** and **Electricity** in the Remocon web portal for the
previous year, then check the `metering` probe status in the integration's redacted
diagnostics. `unavailable:incomplete_previous_year` means the response didn't contain a
usable complete year. The [dashboard guide](dashboard.md#add-galevo-mode-and-energy) includes
an optional SPF tile.

### BSB annual records

BSB exposes ten fixed history slots with eight fields each. These 80 diagnostic entities are
**disabled by default**. Every display name starts with `Annual energy record <slot> –` so
Home Assistant groups the eight fields of each record together.

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

Existing unique IDs are unchanged, so upgrades preserve entity IDs, history, registry settings,
automations, and dashboards. Home Assistant may derive a grouped object ID from the new display
name only when an entity is registered for the first time.

### Record hierarchy and OFF flags

Remocon presents every slot as one parent record. For example, record 8 uses line 3169 for its
fixed day and yearly performance factor, followed by lines 3170–3175 for its six energy values:

```text
Annual energy record 8 — line 3169
├── Fixed day
├── Yearly performance factor
├── 3170 Heat delivered heating
├── 3171 Heat delivered DHW
├── 3172 Refrigeration delivered
├── 3173 Energy brought in heating
├── 3174 Energy brought in DHW
└── 3175 Energy brought in cooling
```

The same structure applies to every stored year:

| Record | Parent line: fixed day + yearly factor | Six child energy lines |
|---:|---:|---:|
| 1 (newest) | 3120 | 3121–3126 |
| 2 | 3127 | 3128–3133 |
| 3 | 3134 | 3135–3140 |
| 4 | 3141 | 3142–3147 |
| 5 | 3148 | 3149–3154 |
| 6 | 3155 | 3156–3161 |
| 7 | 3162 | 3163–3168 |
| 8 | 3169 | 3170–3175 |
| 9 | 3176 | 3177–3182 |
| 10 (oldest) | 3183 | 3184–3189 |

The repeated visible line number does not mean that fixed day and performance factor are one JSON
value: Remocon returns them at separate verified internal addresses. Each of the eight children
also has its own BSB `osv` (out-of-service) flag. An unchecked **OFF** box means `osv: false` and
the child is usable; a checked **OFF** box means `osv: true` and only that corresponding Home
Assistant entity becomes unavailable. The integration reads these flags but does not expose them
as writable switches because the controller's per-field service write contract has not been
verified.

Slot 1 is always the newest stored record and older records move back one position. Values cover
the interval between two fixed days rather than being lifetime counters. Siemens defines yearly
performance factor as delivered thermal energy divided by input energy over the annual interval;
it is unavailable when the required delivered-heat or input-energy metering is not configured.
See the [Siemens RVS61 controller manual, pages 245–255](https://www.siemensbolt.hu/doc/siemens/pdf/RVS61_F%20series_U2355en_054.pdf).

These are controller-owned snapshots, not continuously increasing lifetime meters. Their kWh
sensors use the energy device class and total state class, not total_increasing. They are useful
for comparing ELCO's stored historic heating, DHW, cooling, input, output, and performance records.
They are **not suitable as direct Home Assistant Energy Dashboard inputs**, which require a
continuously increasing total or interval statistics with defined reset behavior. A communication
or out-of-service flag affects only its corresponding child entity.

## Why an expected entity may be absent or unavailable

| Situation | Result |
|---|---|
| Remocon explicitly reports a feature as unsupported | Related entity is not created |
| A required setpoint, limit, or allowed option is missing | The affected control is absent, unavailable, or has fewer actions until the gateway returns usable data |
| The named season of a Galevo zone temperature number is inactive | The control is unavailable until that season becomes active |
| An existing Galevo off-season comfort control is retired | Its entity ID is preserved and the control stays unavailable |
| A stable capability exists but its current reading is out of service | Entity stays registered and is unavailable until recovery |
| A Galevo zone read fails during a normal poll | The affected zone is unavailable. Fresh plant and healthy zone values stay available, and a recovery poll is scheduled |
| An optional endpoint is unsupported, unauthorized, slow, or changed | Only that family is unavailable; core entities continue |
| Entity is disabled by default | It exists in the registry but is not loaded until enabled |
| Installation already has an enabled/disabled choice | Home Assistant preserves it on upgrade |

Diagnostics capture every key/value returned by each JSON endpoint the integration calls. That is
not every theoretical BSB address: Remocon does not publish the complete map, and unsupported
addresses can block or time out a gateway.

## Display names and entity IDs

Home Assistant uses the configured language for the integration's translated display names.
An existing entity ID is retained when display names change, including a switch between
English and German. Automations and dashboards referencing those IDs keep working. Home
Assistant preserves custom names and enabled/disabled choices. For an entity created for the
first time, the generated ID can reflect the active language and device name.

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
Fresh setups omit redundant write entities. Optional read-only diagnostic entities can be enabled
for controller-specific states. Existing standalone controls can be disabled individually while the
native controls continue to work.
