<p align="center">
  <img src="custom_components/elco_aerotop/brand/icon.png" width="160" alt="ELCO Aerotop heat pump icon">
</p>

<h1 align="center">ELCO Aerotop for Home Assistant</h1>

<p align="center">
  A native Home Assistant integration for monitoring and controlling ELCO Aerotop heat pumps through Remocon.net.
</p>

<p align="center">
  <a href="https://github.com/thomasgregg/elco-aerotop/actions/workflows/validate.yml"><img src="https://github.com/thomasgregg/elco-aerotop/actions/workflows/validate.yml/badge.svg" alt="Validation status"></a>
  <a href="LICENSE"><img src="https://img.shields.io/github/license/thomasgregg/elco-aerotop" alt="MIT License"></a>
  <img src="https://img.shields.io/badge/HACS-Custom-41BDF5" alt="HACS custom repository">
</p>

> [!IMPORTANT]
> **Project status: early development.** Read access is non-destructive. Write requests match the
> current Remocon web application, but retained values still need broader testing on real ELCO
> gateways and Aerotop models. Begin with monitoring, then test one conservative setpoint change
> while you can verify the result on the controller or official application.

## Contents

- [Overview](#overview)
- [Features](#features)
- [Entities](#entities)
- [Read-only capability discovery](#read-only-capability-discovery)
- [Installation](#installation)
- [Home Assistant setup](#home-assistant-setup)
- [Options](#options)
- [Write-safety model](#write-safety-model)
- [Availability and refresh behavior](#availability-and-refresh-behavior)
- [Diagnostics and privacy](#diagnostics-and-privacy)
- [Troubleshooting](#troubleshooting)
- [Compatibility](#compatibility)
- [Roadmap](#roadmap)
- [Development](#development)

## Overview

ELCO Aerotop communicates directly with the structured JSON endpoints used by the Remocon.net web
application. It appears in Home Assistant as one device with native thermostats, a domestic-hot-
water controller, sensors, binary sensors, calendars, and advanced number/select controls.

Unlike the earlier add-on approach, this integration:

- does not launch a browser or scrape rendered HTML;
- does not require a Home Assistant add-on or separate container;
- does not create entities through MQTT discovery;
- supports both reading and controlled writes;
- uses Home Assistant's config flow, device registry, coordinator, reauthentication, and
  diagnostics systems;
- discovers the heating zones reported by Remocon.

```text
Home Assistant entities
          │
          ▼
ELCO Aerotop coordinator ──► Remocon.net cloud ──► ELCO gateway ──► Heat pump
          │
          └── one fresh, validated and serialized request path for all writes
```

This is a **cloud-polling** integration. It requires internet access and a working Remocon.net
account; it does not communicate with the heat pump over the local network.

## Features

- UI-based setup—no YAML configuration required
- Automatic login, session renewal, and Home Assistant reauthentication flow
- Isolated cookie session for every configured Remocon account
- Automatic heating-zone discovery
- Full key capture for the gateway's Features and plant/zone `GetData` responses
- Isolated read-only discovery of the complete known system-property set, native BSB plant data,
  supported mobile menu items, BSB-native schedules, metering, automated monitoring, predictive
  maintenance, errors, annual energy history, and allowlisted BSB parameters
- Conservative 60-minute polling interval by default
- Configurable polling interval from 60 to 3,600 seconds
- Read-only temperatures and operating-state entities
- Native Home Assistant thermostat control for each supported heating zone
- Native Home Assistant water-heater control for domestic hot water
- Writable heating and domestic-hot-water setpoints
- Writable heating-zone and domestic-hot-water operating modes
- Limits, step sizes, and mode options supplied by Remocon where available
- Fresh-state read before every command to preserve related controller values
- Per-device command locking to prevent overlapping writes
- Redacted Home Assistant diagnostics
- HACS, hassfest, lint, formatting, and automated test validation

## Entities

All entities are grouped under a device named `ELCO Aerotop <gateway ID>`. Home Assistant generates
the final entity ID from the device and entity name, and users may rename it. The patterns below
therefore use `<device>` and `<zone>` placeholders.

Entity creation is capability-driven. Stable plant, zone, and heat-pump entities are created when
the gateway advertises the relevant capability, even when a particular reading is temporarily out
of service. Such entities remain in Home Assistant and become available automatically when Remocon
starts returning a valid value. Features explicitly reported as unsupported are omitted, so two
Aerotop installations can still expose different entity sets. Writable entities additionally
require the current value, limits, or allowed options needed to send a validated command.

### Primary controls

The native entities below are the recommended controls for dashboards, voice assistants,
automations, and Home Assistant's standard device cards. They use the same fresh-read,
validation, serialization, and companion-value preservation as the lower-level controls.

| Entity name | Typical entity ID pattern | Scope | Native model | What it controls |
|---|---|---:|---|---|
| Zone `<zone>` thermostat | `climate.<device>_zone_<zone>_thermostat` | Per zone | Climate | Zone mode, direct-heating preset, and comfort setpoint. Also reports controller activity and a real room reading when the zone has a room sensor. |
| Domestic hot water | `water_heater.<device>_domestic_hot_water` | Plant | Water heater | DHW mode and comfort target. Also reports the measured storage temperature when a healthy DHW probe is present. |

#### Heating-zone thermostat behavior

Remocon models a heating zone as one scheduled mode plus several stored setpoints rather than as a
simple on/off thermostat. The integration maps that controller model to Home Assistant as follows:

| ELCO controller mode | Home Assistant HVAC mode | Home Assistant preset | Write behavior |
|---|---|---|---|
| Automatic | `auto` | None | Runs the ELCO time program. Selecting `auto` writes Automatic. |
| Comfort | `heat` | `comfort` | Runs continuously at the ELCO comfort level. Selecting `heat` prefers Comfort; selecting the `comfort` preset writes Comfort directly. |
| Reduced | `heat` | `eco` | Runs continuously at the reduced level. Selecting the `eco` preset writes Reduced directly. |
| Protection | `heat` | `Protection` | Keeps ELCO frost protection active. It is deliberately **not** represented as `off`, because the controller may still call for heat to protect the installation. |

The thermostat target is the stored **comfort setpoint**. Changing it calls the same verified
Remocon temperature endpoint as the advanced comfort-temperature number and preserves the reduced
setpoint. The effective target can differ while Automatic, Reduced, Protection, or a holiday
program is active; `sensor.<device>_zone_<zone>_desired_temperature` reports the controller's
current effective request.

`current_temperature` is populated only from that zone's actual room sensor. A gateway without a
room sensor therefore shows no current room temperature in the thermostat card. The integration
does not substitute outdoor, heating-flow, or desired temperature, because each has a different
physical meaning. `hvac_action` reports `heating`, `cooling`, or `idle` only when Remocon supplies
the corresponding activity flags.

Cooling readings remain available on cooling-capable systems, and Automatic may report a cooling
action. A writable `cool` HVAC mode is not exposed yet because a real cooling-mode write contract
has not been verified. This avoids presenting a control that might write the wrong controller
state.

#### Domestic-hot-water behavior

| ELCO DHW mode code | ELCO display meaning | Home Assistant operation | Notes |
|---:|---|---|---|
| 0 | Off | `off` | DHW heating is disabled. |
| 1 | On | `heat_pump` | Normal Aerotop DHW operation. This operation describes the selected mode, not whether the compressor is running at that instant. |
| 2 | Eco | `eco` | Offered only when the configured gateway returns Eco as an allowed option. |

Only modes listed by the gateway are exposed. The water heater's target is the DHW **comfort
temperature**, while its current temperature is the measured storage temperature. Changing the
target or operation preserves the reduced target and every unchanged DHW value required by
Remocon. Home Assistant's turn-on and turn-off services are advertised only when both ELCO On and
Off are supported. The separate reduced-temperature number remains available for time programs
and Eco/reduced operation.

The numeric mode codes above are used only after Remocon returns them in the gateway's allowed
options. Mapping by stable code instead of translated display text keeps the native controls
working when the Remocon account language is not English.

### Sensors

| Entity name | Typical entity ID pattern | Scope | Unit | Description |
|---|---|---:|---:|---|
| Outside temperature | `sensor.<device>_outside_temperature` | Plant | °C | Outdoor temperature reported by the ELCO controller. |
| Domestic hot water temperature | `sensor.<device>_domestic_hot_water_temperature` | Plant | °C | Current temperature measured in the DHW storage tank. |
| Gateway serial | `sensor.<device>_gateway_serial` | Diagnostic | — | Gateway serial returned by the native plant-list API. |
| Plant name | `sensor.<device>_plant_name` | Diagnostic | — | Remocon plant name. |
| Plant location | `sensor.<device>_plant_location` | Diagnostic | — | Address and locality configured for the plant. |
| Gateway firmware | `sensor.<device>_gateway_firmware` | Diagnostic | — | Gateway firmware version. |
| Controller error count | `sensor.<device>_controller_error_count` | Diagnostic | — | Number of current records returned by the controller-error API. |
| Maintenance code 1 | `sensor.<device>_maintenance_code_1` | Diagnostic | — | Primary BSB line 7000 maintenance code and controller-provided description. |
| Maintenance priority 1 | `sensor.<device>_maintenance_priority_1` | Diagnostic | — | Priority associated with the primary maintenance message. |
| Maintenance code 2 | `sensor.<device>_maintenance_code_2` | Diagnostic | — | Secondary simultaneous BSB line 7000 maintenance code and description. |
| Maintenance priority 2 | `sensor.<device>_maintenance_priority_2` | Diagnostic | — | Priority associated with the secondary maintenance message. |
| Heating circuit pressure | `sensor.<device>_heating_circuit_pressure` | Plant | bar | System pressure from the read-only system-data endpoint. |
| Heating circuit flow temperature | `sensor.<device>_heating_circuit_flow_temperature` | Plant | °C | Current primary heating-flow temperature. |
| Heating circuit flow setpoint temperature | `sensor.<device>_heating_circuit_flow_setpoint_temperature` | Plant | °C | Current primary heating-flow target. |
| Zone `<zone>` desired temperature | `sensor.<device>_zone_<zone>_desired_temperature` | Per zone | °C | Effective room-temperature target currently requested by the controller. |
| Zone `<zone>` room temperature | `sensor.<device>_zone_<zone>_room_temperature` | Per zone | °C | Room temperature reported for the zone. The state is unknown if no room sensor is available. |
| Zone `<zone>` mode | `sensor.<device>_zone_<zone>_mode` | Diagnostic | — | Backward-compatible read-only representation of the current controller mode. Disabled by default for new installations because the zone-mode select already exposes this state. |
| Zone `<zone>` cooling comfort temperature | `sensor.<device>_zone_<zone>_cooling_comfort_temperature` | Per zone | °C | Cooling comfort target reported by `GetData`. |
| Zone `<zone>` cooling reduced temperature | `sensor.<device>_zone_<zone>_cooling_reduced_temperature` | Per zone | °C | Cooling reduced target reported by `GetData`. |
| Zone `<zone>` heating protection temperature | `sensor.<device>_zone_<zone>_heating_protection_temperature` | Per zone | °C | Heating frost/protection target. |
| Zone `<zone>` cooling protection temperature | `sensor.<device>_zone_<zone>_cooling_protection_temperature` | Per zone | °C | Cooling protection target. |
| Zone `<zone>` heating holiday temperature | `sensor.<device>_zone_<zone>_heating_holiday_temperature` | Per zone | °C | Heating target used during holidays. |
| Zone `<zone>` cooling holiday temperature | `sensor.<device>_zone_<zone>_cooling_holiday_temperature` | Per zone | °C | Cooling target used during holidays. |
| Zone `<zone>` holiday operating level | `sensor.<device>_zone_<zone>_holiday_operating_level` | Per zone | — | Whether BSB holiday periods use Frost protection or Reduced operation. |
| Zone `<zone>` heating flow temperature | `sensor.<device>_zone_<zone>_heating_flow_temperature` | Per zone | °C | Zone heating-flow value from read-only system data. |
| Zone `<zone>` heating flow offset | `sensor.<device>_zone_<zone>_heating_flow_offset` | Per zone | °C | Zone heating-flow correction. |
| Zone `<zone>` cooling flow temperature | `sensor.<device>_zone_<zone>_cooling_flow_temperature` | Per zone | °C | Zone cooling-flow value from read-only system data. |
| Zone `<zone>` cooling flow offset | `sensor.<device>_zone_<zone>_cooling_flow_offset` | Per zone | °C | Zone cooling-flow correction. |
| Zone `<zone>` derogation temperature | `sensor.<device>_zone_<zone>_derogation_temperature` | Per zone | °C | Temporary zone override reported by system data. |
| Heating circuit 700 operating mode | `sensor.<device>_heating_circuit_700_operating_mode` | BSB | — | Read-only controller parameter 700. |
| Heating circuit 710 comfort setpoint | `sensor.<device>_heating_circuit_710_comfort_setpoint` | BSB | °C | Read-only controller parameter 710. |
| Heating circuit 712 reduced setpoint | `sensor.<device>_heating_circuit_712_reduced_setpoint` | BSB | °C | Read-only controller parameter 712. |
| Heating circuit 714 frost protection setpoint | `sensor.<device>_heating_circuit_714_frost_protection_setpoint` | BSB | °C | Read-only controller parameter 714. |
| Heating circuit 720 heating curve slope | `sensor.<device>_heating_circuit_720_heating_curve_slope` | BSB | — | Read-only controller parameter 720. |
| Heating circuit 730 summer/winter heating limit | `sensor.<device>_heating_circuit_730_summer_winter_heating_limit` | BSB | °C | Read-only controller parameter 730. |
| Heat pump flow temperature | `sensor.<device>_heat_pump_flow_temperature` | BSB | °C | Aerotop heat-pump flow temperature when the BSB datapoint is available. |
| Heat pump return temperature | `sensor.<device>_heat_pump_return_temperature` | BSB | °C | Aerotop heat-pump return temperature. |
| Heat pump flow setpoint | `sensor.<device>_heat_pump_flow_setpoint` | BSB | °C | Aerotop heat-pump flow target. |
| Heat pump gas temperature | `sensor.<device>_heat_pump_gas_temperature` | BSB | °C | Heat-pump gas temperature datapoint. |
| Source outlet temperature | `sensor.<device>_source_outlet_temperature` | BSB | °C | Heat-source outlet temperature. |
| Hot gas temperature | `sensor.<device>_hot_gas_temperature` | BSB | °C | Heat-pump hot-gas temperature. |
| Cooling circuit 2 flow temperature | `sensor.<device>_cooling_circuit_2_flow_temperature` | BSB cooling | °C | BSB diagnostics line 8786, created only when the gateway advertises cooling. |
| Cooling circuit 2 flow setpoint | `sensor.<device>_cooling_circuit_2_flow_setpoint` | BSB cooling | °C | BSB diagnostics line 8787, created only when the gateway advertises cooling. |
| Hydraulic pressure health | `sensor.<device>_hydraulic_pressure_health` | Diagnostic | level | Automated-monitoring health level returned by Remocon. |
| Refrigerant circuit health | `sensor.<device>_refrigerant_circuit_health` | Diagnostic | level | Refrigerant-circuit health level returned by Remocon. |
| Circulation health | `sensor.<device>_circulation_health` | Diagnostic | level | Circulation health level returned by Remocon. |
| Combustion health | `sensor.<device>_combustion_health` | Diagnostic | level | Combustion health level returned by Remocon. |
| Other appliance health | `sensor.<device>_other_appliance_health` | Diagnostic | level | Catch-all appliance health level returned by Remocon. |
| Predictive maintenance notice count | `sensor.<device>_predictive_maintenance_notice_count` | Diagnostic | — | Number of active structured predictive-maintenance notices. Notice payloads remain in redacted diagnostics. |
| Fixed day `<slot>` | `sensor.<device>_annual_energy_record_<slot>_date` | Diagnostic | date | Controller fixed date associated with energy-history slot 1–10. |
| Yearly perf factor `<slot>` | `sensor.<device>_annual_performance_factor_<slot>` | Diagnostic | — | ELCO yearly performance factor for history slot 1–10. |
| Heat delivered heating/DHW `<slot>` | `sensor.<device>_annual_heat_delivered_<type>_<slot>` | Diagnostic | kWh | Delivered heating or DHW energy for history slot 1–10. |
| Refrigeration delivered `<slot>` | `sensor.<device>_annual_refrigeration_delivered_<slot>` | Diagnostic | kWh | Delivered cooling energy for history slot 1–10. |
| Energy brought in heating/DHW/cooling `<slot>` | `sensor.<device>_annual_energy_input_<type>_<slot>` | Diagnostic | kWh | Electrical energy brought in for history slot 1–10. |

Temperature sensors use Home Assistant's `temperature` device class and `measurement` state class.
The read-only zone-mode sensor and low-level BSB 700/710/712/714/720/730 sensors are diagnostic
entities and are disabled by default for new installations because their values overlap the normal
select and number controls. They remain available for backward compatibility, controller
troubleshooting, and cross-checking.

The ten annual history slots are stable controller records rather than continuously increasing
lifetime counters, so their kWh entities use Home Assistant's `energy` device class and `total`
state class—not `total_increasing`. Slot 1 is enabled by default. Slots 2–10 are registered but
disabled by default to avoid adding 72 historical entities to every dashboard; users can enable
any older record in the entity registry. A controller `osv` or communication-error flag makes only
the affected record unavailable.

### Holiday calendars

| Entity name | Typical entity ID pattern | Scope | Access | Description |
|---|---|---:|---|---|
| Zone `<zone>` holidays | `calendar.<device>_zone_<zone>_holidays` | Per zone | Read-only | All valid BSB holiday periods returned in `zoneData.holidays`. The calendar is on while a holiday period is active and exposes upcoming periods to calendar automations. |

Remocon represents BSB holidays as per-zone periods with an index, start and end dates, and
out-of-service/change flags. ELCO treats the configured final day as part of the holiday, while Home
Assistant calendar ends are exclusive; the integration performs that one-day conversion. Deleted
or out-of-service periods are not shown. The operating level is exposed separately because it is a
zone-wide choice shared by the periods.

Holiday editing is intentionally not enabled yet. The Remocon write operation sends the complete
plant and zone state and also forces the zone to Automatic mode. Read support is therefore kept
separate until create, update, and delete operations have been verified on a real populated gateway
response. No holiday data is obtained by website scraping.

### Binary sensors

| Entity name | Typical entity ID pattern | Scope | Device class | Description |
|---|---|---:|---|---|
| Heat pump running | `binary_sensor.<device>_heat_pump_running` | Plant | Running | Indicates that the heat pump is reported as running. |
| Controller error | `binary_sensor.<device>_controller_error` | Diagnostic | Problem | Turns on when the bus-error endpoint reports records. Up to ten current records are included in the entity attributes. |
| Flame on | `binary_sensor.<device>_flame_on` | Plant | Running | Burner/flame state when supplied by the controller. |
| Domestic hot water enabled | `binary_sensor.<device>_domestic_hot_water_enabled` | Plant | Running | Whether domestic-hot-water operation is enabled. |
| Outside temperature sensor problem | `binary_sensor.<device>_outside_temperature_sensor_problem` | Plant | Problem | Controller error flag for the outdoor probe. |
| Domestic hot water temperature sensor problem | `binary_sensor.<device>_domestic_hot_water_temperature_sensor_problem` | Plant | Problem | Controller error flag for the DHW storage probe. |
| Zone `<zone>` heat request | `binary_sensor.<device>_zone_<zone>_heat_request` | Per zone | Running | Controller demand flag for the zone. On systems with cooling support, the source value may represent a combined heating-or-cooling request. |
| Zone `<zone>` heating active | `binary_sensor.<device>_zone_<zone>_heating_active` | Per zone | Running | Heating-active state returned for the zone. |
| Zone `<zone>` cooling active | `binary_sensor.<device>_zone_<zone>_cooling_active` | Per zone | Running | Cooling-active state returned for the zone. |
| Zone `<zone>` room temperature sensor problem | `binary_sensor.<device>_zone_<zone>_room_temperature_sensor_problem` | Per zone | Problem | Controller error flag for the room sensor. |

### Advanced and backward-compatible controls

The number and select entities expose the original Remocon control model directly. They are kept
for precise configuration, unusual controller variants, existing dashboards, and automations that
already reference their entity IDs. They now use Home Assistant's **Configuration** entity
category, so the native thermostat and water-heater entities remain the primary device controls.
No entity ID or unique ID is changed by this reclassification.

If the native entities cover your use case, the comfort-temperature numbers and mode selects may
be disabled in **Settings → Devices & services → Entities** without affecting the thermostat or
water heater. Keep the reduced-temperature numbers enabled when you want to adjust the stored
reduced/Eco levels independently.

#### Number controls

| Entity name | Typical entity ID pattern | Scope | Unit | Write behavior |
|---|---|---:|---:|---|
| Domestic hot water comfort temperature | `number.<device>_domestic_hot_water_comfort_temperature` | Plant | °C | Changes the normal DHW target while preserving the reduced target and current DHW mode. |
| Domestic hot water reduced temperature | `number.<device>_domestic_hot_water_reduced_temperature` | Plant | °C | Changes the reduced DHW target while preserving the comfort target and current DHW mode. |
| Zone `<zone>` comfort temperature | `number.<device>_zone_<zone>_comfort_temperature` | Per zone | °C | Changes the zone's comfort setpoint while preserving its reduced setpoint. |
| Zone `<zone>` reduced temperature | `number.<device>_zone_<zone>_reduced_temperature` | Per zone | °C | Changes the zone's reduced setpoint while preserving its comfort setpoint. |

The controls use Remocon-provided minimum, maximum, and step values when present. The integration
also rejects a comfort temperature below its corresponding reduced temperature.

#### Select controls

| Entity name | Typical entity ID pattern | Scope | Write behavior |
|---|---|---:|---|
| Domestic hot water mode | `select.<device>_domestic_hot_water_mode` | Plant | Changes the DHW operating mode while preserving both DHW temperature targets. |
| Zone `<zone>` mode | `select.<device>_zone_<zone>_mode` | Per zone | Changes the operating mode of that heating zone while preserving required plant values. |

Mode names and the number of available choices are supplied by Remocon and can differ between
controllers, firmware versions, and account languages. A select is not created if the gateway does
not return a list of allowed choices. These selects and the native controls call the same
coordinator methods; changing one updates the other after the normal post-command refresh rather
than issuing a duplicate command.

## Read-only capability discovery

The normal `Features` and `PlantHomeBsb/GetData` responses are retained in the in-memory snapshot
so diagnostics can inventory every JSON key returned by the gateway. The integration additionally
probes these non-mutating endpoint families:

| Family | Data collected | Refresh |
|---|---|---:|
| Plant metadata | Serial, plant name, structured location, gateway firmware, link/system type, and API version | Approximately hourly |
| System data | Pressure, flow values, plant/DHW state, and zone values requested by stable item IDs | Every normal poll |
| Time programs | Complete BSB-native heating, supported cooling, and DHW weekly programs from `PlantTimeProgBsb/GetData` | Approximately hourly |
| Metering | The complete metering response, without assuming one firmware-specific schema | Approximately hourly |
| Maintenance | Cloud maintenance response when the account is authorized for it | Approximately hourly |
| Automated monitoring | Five appliance-health levels, urgency/connection details, and structured predictive-maintenance notices | Approximately hourly |
| BSB appliance data | Structured controller/boiler identification returned by Remocon | Approximately hourly |
| Controller errors | Read-only bus-error response | Approximately hourly |
| BSB | Allowlisted heating-circuit, Aerotop temperature/pressure, time/default, maintenance, and annual-energy datapoints, read in independent JSON API groups | Approximately hourly |
| Native BSB plant snapshot | Complete plant, zone, capability, holiday, and setpoint response used by Remocon mobile clients | Approximately hourly |
| Mobile menu items | Supported service values from IDs 119–130 plus feature-selected VMC, SLP, hybrid, heat-pump, and cascade/BMS catalog families on non-BSB controller families | Approximately hourly |

Each optional family has an independent availability result. Core state and fast metadata load in
the config-entry setup path; controller-bus, schedule, metering, maintenance, and broad mobile-API
probes begin as a managed background task only after Home Assistant has set up the entity
platforms. Consequently, a slow optional endpoint cannot produce a slow-integration setup warning.
Most probes use a 15-second timeout; controller-backed BSB groups use 30 seconds. A missing,
unsupported, or changed optional endpoint is recorded in diagnostics but does not make the core
plant and zone entities unavailable. BSB groups are isolated so one rejected group cannot discard
values from supported groups. These raw discovery responses are not exposed wholesale as entity
attributes, which avoids oversized Home Assistant states and accidental identifier leaks.

“Complete capture” means every key and value returned by every endpoint the integration calls. It
does not mean every theoretical BSB controller address: Remocon does not publish that address map,
and unsupported addresses can block or time out a gateway. The integration therefore combines the
full known mobile property list, feature-selected mobile menu families, complete native BSB snapshots,
and a reviewed BSB address allowlist. Newly observed values are first retained in redacted
diagnostics; entities are added only after their response type, unit, availability behavior, and
meaning are verified.

Known BSB entities remain registered when Remocon flags a datapoint as out of service, failed, or
carrying a bus/communication error. Home Assistant marks the entity unavailable for that snapshot
and restores it when a later BSB read succeeds. A returned datapoint with no current value is shown
as unknown. Enum datapoints use the labels supplied by Remocon instead of raw numeric codes.

Discovery is strictly read-only. It does not add generic BSB writing, schedule editing,
maintenance actions, or metering commands. See [`docs/discovery.md`](docs/discovery.md) for the
capture and fixture workflow.

The earlier add-on's “maintenance code” values came from the BSB **7000 – Message** menu, not from
the Remocon maintenance API. An authenticated protocol inspection verified controller-internal
address `327836` and its four-field JSON response: maintenance code 1, priority 1, maintenance code
2, and priority 2. These values are exposed as read-only diagnostic sensors. The address is read in
its own discovery group so a controller timeout cannot affect other BSB values. Runtime operation
uses only the structured `ReadDataPoints` JSON endpoint; the integration does not scrape the
website.

Remocon's mobile `menuItems` endpoint is not a substitute on Aerotop/BSB plants. It is used by the
Galevo controller family and returns HTTP 500 for valid, individually token-authenticated IDs when
`Features` reports BSB system type 5. The integration records that family as unsupported instead of
repeatedly sending requests that cannot succeed.

Remocon advertises a separate metering capability, but some BSB Aerotop controllers expose annual
energy history even when `Features.hasMetering` is false. The integration therefore reads the ten
verified BSB annual records independently of the cloud metering feature flag. The 80 internal
addresses were captured from structured BSB menu metadata and are queried as ten isolated,
read-only JSON slot groups so one controller-bus failure cannot hide the other annual records.

## Installation

### Option 1: HACS custom repository (recommended)

[![Open your Home Assistant instance and add this repository to HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=thomasgregg&repository=elco-aerotop&category=integration)

HACS must already be installed and configured in Home Assistant.

1. Open **HACS** in Home Assistant.
2. Open the three-dot menu in the upper-right corner and choose **Custom repositories**.
3. Enter this repository URL:

   ```text
   https://github.com/thomasgregg/elco-aerotop
   ```

4. Select **Integration** as the category and choose **Add**.
5. Search HACS for **ELCO Aerotop** and choose **Download**.
6. Restart Home Assistant when HACS asks you to do so.
7. Continue with [Home Assistant setup](#home-assistant-setup).

These steps follow the [HACS custom-repository procedure](https://www.hacs.xyz/docs/faq/custom_repositories/).

### Option 2: Manual installation

1. Download or clone this repository.
2. Copy the complete `custom_components/elco_aerotop` directory into your Home Assistant
   configuration directory:

   ```text
   <home-assistant-config>/custom_components/elco_aerotop
   ```

3. Verify that `manifest.json` is directly inside that directory—not inside another nested
   `elco-aerotop` folder.
4. Restart Home Assistant.
5. Continue with [Home Assistant setup](#home-assistant-setup).

### Home Assistant setup

1. Go to **Settings → Devices & services**.
2. Select **Add integration**.
3. Search for **ELCO Aerotop**.
4. Enter the requested connection details and submit the form.

| Field | Required | Default | Description |
|---|:---:|---|---|
| Email | Yes | — | Email address used to sign in to Remocon.net. |
| Password | Yes | — | Remocon.net account password. |
| Gateway ID | Yes | — | Identifier at the end of the plant dashboard URL. It is normalized to uppercase. |
| Remocon.net URL | No | `https://www.remocon-net.remotethermo.com` | Service base URL. Keep the default unless the plant uses another branded Remocon endpoint. |

The gateway ID is normally the final part of a dashboard URL, for example:

```text
https://www.remocon-net.remotethermo.com/BsbPlantDashboard/Index/GATEWAY_ID
```

One gateway can only be configured once. Multiple gateways may be added as separate integrations.

## Options

Open **Settings → Devices & services → ELCO Aerotop → Configure** to change the polling interval.

| Option | Default | Minimum | Maximum | Notes |
|---|---:|---:|---:|---|
| Polling interval | 3,600 seconds | 60 seconds | 3,600 seconds | The default is one hour. A shorter interval increases traffic to the undocumented Remocon service. |

This option controls the normal plant, zone, and system-item refresh. The slower read-only
discovery families—schedules, maintenance, automated monitoring, controller diagnostics, and
energy history—run once in the background after setup and then approximately every 60 minutes.
Their cadence is calculated from the selected polling interval, so choosing a shorter normal poll
does not make these controller-heavy requests run unnecessarily often, and the one-hour default
does not postpone them for 12 hours.

The integration reloads automatically when the option changes. A successful write also requests
an immediate state refresh; it does not wait for the next scheduled poll.

## Write-safety model

Remocon's write endpoints expect related values together, even when only one Home Assistant entity
changes. Sending an incomplete or stale request can be ignored by the controller or overwrite a
companion value. Every write therefore follows this sequence:

1. Acquire a per-device command lock.
2. Fetch uncached plant and zone data.
3. Validate the requested value against current gateway limits or allowed options.
4. Enforce the relationship between comfort and reduced temperatures.
5. Preserve all unchanged companion values required by the endpoint.
6. Send one typed command through the dedicated Remocon endpoint.
7. Refresh Home Assistant state from Remocon.

The native and advanced controls are two views of those same validated values:

| User-facing control | Remocon value written | Preserved companion values |
|---|---|---|
| Thermostat target | Zone comfort temperature | Zone reduced temperature and current zone payload |
| Thermostat HVAC mode/preset | Zone operating mode | Fresh plant and zone payload |
| Water-heater target | DHW comfort temperature | DHW reduced temperature and current mode |
| Water-heater operation/on/off | DHW mode | DHW comfort and reduced temperatures |
| Advanced number/select | Its corresponding value above | Same companions as the native entity |

The integration deliberately does not expose an arbitrary BSB-address write service. New writable
parameters should only be added after their request shape and controller behavior have been
captured and tested.

## Availability and refresh behavior

- Plant and zone data are coordinated into a single Home Assistant snapshot.
- Normal plant, zone, and system-item values refresh at the configured polling interval: 60
  minutes by default.
- Schedules, maintenance, monitoring, BSB diagnostics, and energy history refresh in a deferred
  background cycle approximately every 60 minutes, independently of the selected normal polling
  interval. Their first deferred refresh starts after entity setup.
- Zone entities are created from the zones discovered during initial setup.
- A zone thermostat is created only when Remocon returns a comfort target and at least one verified
  zone operating mode.
- The water-heater entity is created only when Remocon returns a DHW comfort target and at least
  one verified, allowed DHW mode.
- Optional sensors and binary sensors are created only when their source value exists during that
  initial discovery; unsupported values do not create placeholder entities.
- Writable native, number, and select entities are omitted if Remocon does not provide enough
  information to use them safely. Modes that a particular gateway does not offer are omitted from
  that entity's supported mode list.
- Authentication expiry triggers one controlled login retry.
- Rejected credentials start Home Assistant's reauthentication flow.
- Communication failures mark coordinator-backed entities unavailable until a later poll succeeds.
- The default request timeout is 30 seconds.

## Diagnostics and privacy

Home Assistant diagnostics are available from the integration's device page. The export includes
an anonymized data snapshot, endpoint availability, and a `response_schema` map containing every
observed key path and value type. It redacts the configured email, password, gateway ID, common
identity/location fields, serial numbers, technician details, and identifiers embedded in object
keys. Long arrays are bounded to keep the diagnostic file manageable.

Remocon credentials are stored in the Home Assistant config entry and are only sent to the
configured Remocon service. Each entry uses its own cookie session, preventing authentication
cookies from being shared between configured accounts.

The anonymizer is deliberately defensive, but Remocon is undocumented and may introduce a new
identity field. Before sharing diagnostics publicly, inspect the file and remove anything you
consider personal. The project accepts anonymized diagnostics as test fixtures only after review.

## Troubleshooting

### The integration does not appear in Home Assistant

- Confirm the directory is exactly `config/custom_components/elco_aerotop`.
- Confirm `manifest.json` is inside that directory.
- Restart Home Assistant after installing or updating the files.
- Clear the browser cache if the integration search still shows old information.

### Authentication fails

- Sign in to the Remocon.net website with the same email and password.
- Check that the configured base URL is correct for the account.
- Re-enter credentials from **Settings → Devices & services** if Home Assistant reports that the
  integration needs attention.

### A documented entity is missing

Entity availability depends on the data returned by the controller. Zone entities require that
zone to be discovered. Writable numbers require a current value, while selects require a list of
allowed modes. Check diagnostics to see what the integration parsed.

### A write is rejected or does not remain set

- Check that the comfort target is not below the corresponding reduced target.
- Test a small change and verify it in the official Remocon application or on the controller.
- Allow a few moments for the gateway and cloud to synchronize.
- Restore the original value if the system behaves unexpectedly.
- Include the Home Assistant error and a redacted diagnostic file when opening an issue.

### Enable debug logging

Add the following to `configuration.yaml`, restart Home Assistant, reproduce the problem, and then
download the logs:

```yaml
logger:
  default: warning
  logs:
    custom_components.elco_aerotop: debug
```

Remove the override after troubleshooting to avoid unnecessary log volume.

## Compatibility

The integration targets ELCO Aerotop systems exposed through Remocon's BSB plant dashboard. The
cloud API is undocumented and model capabilities vary, so formal compatibility is still being
established.

When reporting a working or non-working model, include the exact Aerotop model, gateway type,
firmware version if known, number of heating zones, and which entities were returned. Do not post
credentials, gateway IDs, serial numbers, or unredacted payloads.

## Roadmap

- Real-gateway validation across additional Aerotop models and zone configurations
- Read-only schedule entities after weekly-plan presentation semantics are finalized
- Additional predictive-maintenance entities after more non-empty real responses are contributed
- Expanded anonymized real-gateway fixtures across models and firmware versions
- Additional translations

## Development

The reverse-engineered request contract, endpoint selection, and contribution safety rules are
documented in [`docs/protocol.md`](docs/protocol.md).

Create a Python 3.12 or newer environment and install the test dependencies:

```bash
python -m pip install aiohttp pytest pytest-asyncio ruff
```

Run all local checks:

```bash
ruff check .
ruff format --check .
pytest
```

GitHub Actions additionally runs Home Assistant hassfest and HACS repository validation.

## Support and contributions

- Use [GitHub Issues](https://github.com/thomasgregg/elco-aerotop/issues) for reproducible defects
  and compatibility reports.
- Pull requests should include tests for parser changes and every new write payload.

## Disclaimer

This is an unofficial community project and is not affiliated with or endorsed by ELCO, Ariston
Group, or the Remocon.net service. Remocon.net is a cloud service with an undocumented internal API;
upstream changes can break the integration without notice. Use write controls at your own risk.

Licensed under the [MIT License](LICENSE).
