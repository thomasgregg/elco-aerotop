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

> [!WARNING]
> **Real-system testers are wanted.** The write controls, holiday calendars, and read-only schedule
> discovery have automated coverage but have not yet been sufficiently field-tested across ELCO
> models, firmware versions, and populated schedules. If you can test them, please report the
> controller model, observed result, and a carefully inspected redacted diagnostic export in a
> [GitHub issue](https://github.com/thomasgregg/elco-aerotop/issues). Never include credentials,
> session cookies, access tokens, or unredacted personal data.

## Contents

- [Overview](#overview)
- [Features](#features)
- [Entities](#entities)
  - [Complete entity reference](docs/entities.md)
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

All entities belong to one ELCO Aerotop device, and the exact set is capability-driven. Primary
thermostat and domestic-hot-water controls plus everyday operational values are enabled by
default. Advanced configuration, duplicate controls, low-level BSB diagnostics, and all 80 fixed
energy-history entities are organized separately and selectively disabled to keep the device page
useful.

The entity reference explains every entity family, including:

- the Home Assistant section and default enabled state;
- exact entity-ID patterns and conditional creation rules;
- read-only versus read/write behavior;
- thermostat, DHW, holiday, maintenance, and energy semantics;
- why native controls overlap with some compatibility entities;
- unavailable versus disabled entities and upgrade behavior.

See the **[complete entity reference](docs/entities.md)** before enabling large diagnostic or energy
groups. Existing entity IDs and existing enabled/disabled choices are preserved during upgrades;
new defaults apply only when an entity is first registered.

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
- Testing reports for write controls, holiday calendars, and schedule discovery are especially
  welcome. Please say exactly what was tested, what the ELCO controller or official application
  showed before and after, and whether Home Assistant matched it.
- Pull requests should include tests for parser changes and every new write payload.

## Disclaimer

This is an unofficial community project and is not affiliated with or endorsed by ELCO, Ariston
Group, or the Remocon.net service. Remocon.net is a cloud service with an undocumented internal API;
upstream changes can break the integration without notice. Use write controls at your own risk.

Licensed under the [MIT License](LICENSE).
