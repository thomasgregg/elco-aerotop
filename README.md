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
application. It appears in Home Assistant as one device with native sensors, binary sensors,
numbers, and selects.

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
- Conservative five-minute polling interval by default
- Configurable polling interval from 60 to 3,600 seconds
- Read-only temperatures and operating-state entities
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

For `N` discovered heating zones, the integration creates up to **6 plant entities plus 7 entities
per zone**. Writable entities are only created when Remocon returns the value or allowed options
needed to control them safely.

### Sensors

| Entity name | Typical entity ID pattern | Scope | Unit | Description |
|---|---|---:|---:|---|
| Outside temperature | `sensor.<device>_outside_temperature` | Plant | °C | Outdoor temperature reported by the ELCO controller. |
| Domestic hot water temperature | `sensor.<device>_domestic_hot_water_temperature` | Plant | °C | Current temperature measured in the DHW storage tank. |
| Zone `<zone>` desired temperature | `sensor.<device>_zone_<zone>_desired_temperature` | Per zone | °C | Effective room-temperature target currently requested by the controller. |
| Zone `<zone>` room temperature | `sensor.<device>_zone_<zone>_room_temperature` | Per zone | °C | Room temperature reported for the zone. The state is unknown if no room sensor is available. |
| Zone `<zone>` mode | `sensor.<device>_zone_<zone>_mode` | Per zone | — | Read-only representation of the current controller mode. The label comes from Remocon when available. |

Temperature sensors use Home Assistant's `temperature` device class and `measurement` state class.

### Binary sensors

| Entity name | Typical entity ID pattern | Scope | Device class | Description |
|---|---|---:|---|---|
| Heat pump running | `binary_sensor.<device>_heat_pump_running` | Plant | Running | Indicates that the heat pump is reported as running. |
| Zone `<zone>` heat request | `binary_sensor.<device>_zone_<zone>_heat_request` | Per zone | Running | Controller demand flag for the zone. On systems with cooling support, the source value may represent a combined heating-or-cooling request. |

### Number controls

| Entity name | Typical entity ID pattern | Scope | Unit | Write behavior |
|---|---|---:|---:|---|
| Domestic hot water comfort temperature | `number.<device>_domestic_hot_water_comfort_temperature` | Plant | °C | Changes the normal DHW target while preserving the reduced target and current DHW mode. |
| Domestic hot water reduced temperature | `number.<device>_domestic_hot_water_reduced_temperature` | Plant | °C | Changes the reduced DHW target while preserving the comfort target and current DHW mode. |
| Zone `<zone>` comfort temperature | `number.<device>_zone_<zone>_comfort_temperature` | Per zone | °C | Changes the zone's comfort setpoint while preserving its reduced setpoint. |
| Zone `<zone>` reduced temperature | `number.<device>_zone_<zone>_reduced_temperature` | Per zone | °C | Changes the zone's reduced setpoint while preserving its comfort setpoint. |

The controls use Remocon-provided minimum, maximum, and step values when present. The integration
also rejects a comfort temperature below its corresponding reduced temperature.

### Select controls

| Entity name | Typical entity ID pattern | Scope | Write behavior |
|---|---|---:|---|
| Domestic hot water mode | `select.<device>_domestic_hot_water_mode` | Plant | Changes the DHW operating mode while preserving both DHW temperature targets. |
| Zone `<zone>` mode | `select.<device>_zone_<zone>_mode` | Per zone | Changes the operating mode of that heating zone while preserving required plant values. |

Mode names and the number of available choices are supplied by Remocon and can differ between
controllers, firmware versions, and account languages. A select is not created if the gateway does
not return a list of allowed choices.

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
| Polling interval | 300 seconds | 60 seconds | 3,600 seconds | A shorter interval increases traffic to the undocumented Remocon service. |

The integration reloads automatically when the option changes.

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

The integration deliberately does not expose an arbitrary BSB-address write service. New writable
parameters should only be added after their request shape and controller behavior have been
captured and tested.

## Availability and refresh behavior

- Plant and zone data are coordinated into a single Home Assistant snapshot.
- Zone entities are created from the zones discovered during initial setup.
- A value unsupported by the controller may appear as unknown.
- Writable number and select entities are omitted if Remocon does not provide enough information
  to use them safely.
- Authentication expiry triggers one controlled login retry.
- Rejected credentials start Home Assistant's reauthentication flow.
- Communication failures mark coordinator-backed entities unavailable until a later poll succeeds.
- The default request timeout is 30 seconds.

## Diagnostics and privacy

Home Assistant diagnostics are available from the integration's device page. The integration
redacts the configured email address, password, and gateway ID from the configuration section.

Remocon credentials are stored in the Home Assistant config entry and are only sent to the
configured Remocon service. Each entry uses its own cookie session, preventing authentication
cookies from being shared between configured accounts.

Before sharing diagnostics or logs publicly, check them yourself and remove any remaining plant,
location, serial-number, or account information.

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
- Heating and cooling schedules
- Advanced BSB values such as 700, 710, 712, 714, 720, and 730
- Energy and long-term statistics where reliable source data exists
- Expanded anonymized response fixtures and coordinator/config-flow tests
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
