<p align="center">
  <img src="https://raw.githubusercontent.com/thomasgregg/elco-aerotop/main/custom_components/elco_aerotop/brand/icon.png" width="160" alt="ELCO Aerotop heat pump icon">
</p>

<h1 align="center">ELCO Aerotop for Home Assistant</h1>

<p align="center">
  A native Home Assistant integration for monitoring and controlling ELCO Aerotop heat pumps through Remocon.net.
</p>

<p align="center">
  <a href="https://github.com/thomasgregg/elco-aerotop/actions/workflows/validate.yml"><img src="https://github.com/thomasgregg/elco-aerotop/actions/workflows/validate.yml/badge.svg" alt="Validation status"></a>
  <a href="LICENSE"><img src="https://img.shields.io/github/license/thomasgregg/elco-aerotop" alt="MIT License"></a>
  <img src="https://img.shields.io/badge/HACS-Default-41BDF5" alt="HACS Default">
</p>

## Contents

- [Overview](#overview)
- [What you can do](#what-you-can-do)
- [Entities](#entities)
- [Holidays](#holidays)
- [How data is discovered](#how-data-is-discovered)
- [Installation](#installation)
- [Home Assistant setup](#home-assistant-setup)
- [Options](#options)
- [Write-safety model](#write-safety-model)
- [Availability and refresh behavior](#availability-and-refresh-behavior)
- [Diagnostics and privacy](#diagnostics-and-privacy)
- [Troubleshooting](#troubleshooting)
- [Compatibility](#compatibility)
- [Development](#development)

## Overview

ELCO Aerotop brings a Remocon-connected heat pump into Home Assistant as one native device. It is
intended for everyday monitoring, heating and hot-water control, automations, and troubleshooting
without having to open the Remocon website.

The integration uses the structured JSON endpoints behind Remocon.net. It is a **cloud-polling**
integration, so it requires internet access and a working Remocon.net account; it does not connect
directly to the heat pump over the local network.

> [!IMPORTANT]
> The integration supports both the **BSB controller family** (`systemType: 5`), whose Remocon
> plant URL contains `/R2/BsbPlantDashboard/Index/`, and the **GALEVO controller family**
> (`systemType: 3`), whose plant URL contains `/R2/Plant/Index/`. The family is detected per
> gateway during setup, and each controller only exposes the entities its API surface supports.
> Compatibility is determined by the controller and gateway family, not by the Aerotop model
> name alone.

![ELCO Aerotop heating and hot-water dashboard in Home Assistant](docs/images/home-assistant-dashboard.png)

## What you can do

| Use case | Home Assistant experience |
|---|---|
| Monitor the system | Temperatures, pressure, flow values, operating state, and heat demand |
| Control room heating and cooling | A thermostat for each supported zone, with cooling controls while Remocon reports cooling active |
| Control hot water | A native water-heater entity with target temperature and operating mode |
| Switch the plant mode | Summer, winter, heating-only, cooling, and off modes where the gateway offers them |
| Manage the current holiday | Read holiday periods in a calendar and set or cancel Remocon's immediate holiday; GALEVO additionally exposes a holiday switch plus start/end dates |
| Tune heating behavior | Allowlisted controls for holiday level, frost setpoint, heating curve, and summer/winter limit; quiet mode, DHW boost, and automatic thermoregulation on GALEVO |
| Build automations | Use temperatures, demand, operating state, and holiday periods as inputs |
| Find problems | Controller errors, probe faults, maintenance codes, and appliance-health data |
| Check the installation | Gateway connectivity, plant status, controller model, owner, location, and gateway version |
| Investigate efficiency | Energy consumption history (GALEVO: heating/DHW/cooling usage from Remocon reports; BSB: optional historical heat, cooling, input-energy, and performance records) |

The exact controls and readings depend on what the gateway reports. Unsupported capabilities are
not presented as working controls, while temporarily missing controller values become unavailable
instead of being guessed.

```text
Home Assistant entities
          │
          ▼
ELCO Aerotop coordinator ──► Remocon.net cloud ──► ELCO gateway ──► Heat pump
          │
          └── one fresh, validated and serialized request path for all writes
```

Setup is handled through the Home Assistant interface with no YAML required. After signing in,
the gateways registered to the Remocon account are listed and one or more can be selected; each
selected gateway becomes its own config entry. The controller family, heating zones, and
available values are discovered automatically, and the first data fetch runs in the background
so a slow gateway does not delay Home Assistant startup. The default polling interval is one
hour, writes are serialized and validated against current gateway data, and authentication
renewal is handled through Home Assistant's normal reauthentication flow.

## Entities

All entities belong to the ELCO Aerotop device, and the exact set is determined by the gateway's
controller family and reported capabilities. Entities are family-aware: BSB datapoint entities
(low-level 7xx parameters, boiler/maintenance details, the 80 historical energy records) are
only created on BSB gateways, while GALEVO data-item entities (plant mode, quiet mode, DHW
boost, thermoregulation, energy consumption reports) are only created on GALEVO gateways. The
default device page focuses on native thermostat and hot-water controls, everyday measurements,
and important fault information. Alternative controls and specialist diagnostics are available
but mostly disabled by default to avoid overwhelming a normal installation.

The [entity reference](docs/entities.md) contains the complete tables, entity-ID patterns, default
states, creation conditions, read/write behavior, and explanations of maintenance, holiday, and
energy values—including which entities exist for each controller family. Existing entity IDs and
enabled/disabled choices are preserved during upgrades; new defaults apply only when an entity is
first registered.

Remocon installation metadata is exposed as diagnostic entities when its JSON APIs return a
value. This includes gateway online state, overall plant status, appliance model and serial,
plant owner, account language, plant name/location, gateway serial, and gateway version. Some
metadata endpoints are BSB-only; the corresponding entities are not created on GALEVO. Owner
phone fields are optional and disabled by default. A separate **Last successful update**
timestamp records when the integration most recently completed a core Remocon data capture,
including successful polls whose values did not change. Remocon's **Connectivity Gateway**
software value and the integration's **Gateway firmware** value both come from `gwFwVer`; the
integration creates one version sensor and also reuses that value in Home Assistant's device
information.

## Holidays

Holiday support deliberately separates **viewing** from **writing**, and the write path differs by
controller family:

- **BSB**: each zone's Home Assistant calendar is a read-only view of the valid holiday periods
  returned by the controller. The separate operating-level select, **Holiday until** date, and
  **Cancel holiday** button provide the reviewed write path for Remocon's immediate
  current-holiday workflow.
- **GALEVO**: holiday state is exposed as data items—a **Holiday** switch plus **Holiday start**
  and **Holiday end** date entities, and the zone holiday operating level as a select. Writes use
  the mobile data-item endpoints with optimistic concurrency.

Setting a final day starts a new holiday now or changes the current holiday's inclusive final day,
and changes the zone to Automatic mode just like Remocon. Cancelling the holiday does not restore
the mode that was active beforehand. Reduced and Frost protection use different controller
setpoints and do not mean that heating is completely off.

The integration does not currently expose Remocon's full underlying eight-slot planner. For the
complete behavior, availability rules, calendar rationale, operating-level explanation, and a
native Home Assistant dashboard example, see [Holiday periods in Home Assistant](docs/holidays.md).

## How data is discovered

Remocon gateways differ by controller, firmware, and installed options. The integration therefore
starts with the capabilities and values reported by the gateway, then performs additional
read-only discovery for:

- plant and gateway information, live connectivity/status, and owner metadata;
- heating, hot-water, zone, and controller-bus values;
- schedules and holiday periods;
- errors, maintenance messages, and appliance-health information; and
- metering and historical energy records.

Core heating data loads first. Slower optional requests run independently in the background, so an
unsupported schedule, maintenance, or BSB endpoint does not prevent the integration from loading.
A temporarily failed datapoint becomes unavailable and is restored automatically after a
successful read.

All runtime data comes from structured JSON endpoints; rendered web pages are not scraped. The
integration also avoids scanning arbitrary controller addresses because unsupported BSB requests
can delay a gateway. New response fields are captured in redacted diagnostics and become entities
only after their meaning, unit, and availability behavior have been verified.

For endpoint coverage, maintenance and energy implementation details, and the anonymized fixture
workflow, see [Gateway discovery and anonymized fixtures](docs/discovery.md).

## Installation

### Option 1: HACS (recommended)

[![Open ELCO Aerotop in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=thomasgregg&repository=elco-aerotop&category=integration)

ELCO Aerotop is included in the HACS default repository list. With HACS installed,
use the button above or:

1. Open **HACS** in Home Assistant.
2. Search for **ELCO Aerotop** and choose **Download**.
3. Restart Home Assistant.
4. Continue with [Home Assistant setup](#home-assistant-setup).

No custom repository setup is needed. Existing installations continue to receive
updates through HACS without reinstalling.

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
| Remocon.net URL | No | `https://www.remocon-net.remotethermo.com` | Service base URL. Keep the default unless the plant uses another branded Remocon endpoint. |

After the credentials are validated, the integration lists the gateways registered to the
account—including plant name, location, and controller family—and one or more gateways can be
selected. Each selected gateway becomes its own config entry and device. A gateway can only be
configured once; gateways already set up are hidden from the selection.

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
2. Stop optional background discovery and acquire the shared gateway-traffic lock.
3. Fetch uncached state only for the affected zone, or one zone plus plant state for a DHW change.
4. Validate the requested value against current gateway limits or allowed options.
5. Enforce the relationship between comfort and reduced temperatures.
6. Preserve all unchanged companion values required by the endpoint.
7. Send the typed command once through the dedicated Remocon endpoint. An explicit `401`/`403`
   rejection may renew the session and replay once because the unauthenticated request was rejected.
8. Never resend a command after a timeout, disconnect, or ambiguous server failure. When Remocon
   permits another request, perform one uncached read to determine whether the command took effect.
9. Refresh core Home Assistant state without launching slow discovery behind the write.

If the verification read confirms the requested values, Home Assistant treats the command as
successful. If it confirms old values, the command reports that it was not applied. If verification
also fails—or Remocon supplies `Retry-After`—the integration reports that the outcome is unknown
and asks the user to check the controller or official application. This avoids duplicate writes
while still recovering automatically from a lost success response.

On GALEVO, an HTTP `200` success is **not** treated as proof the value changed: the mobile
`plantData/mode` endpoint acknowledges commands without applying them, so the plant-mode write
uses the R2 `SetData` route with the full feature/item/view-model payload, and every write is
confirmed by `changedItems` or a fresh read. A response that acknowledges without changing state
is reported as a failed write, not a successful one.

Polling and optional discovery use the same gateway-traffic lock. Optional discovery releases it
between requests so a user command can proceed without overlapping controller traffic.

The native and advanced controls are two views of those same validated values:

| User-facing control | Remocon value written | Preserved companion values |
|---|---|---|
| Thermostat target | Active heating or cooling comfort temperature | Matching reduced temperature and current zone payload |
| Thermostat HVAC mode/preset | Zone operating mode | Fresh plant and zone payload |
| Water-heater target | DHW comfort temperature | DHW reduced temperature and current mode |
| Water-heater operation/on/off | DHW mode | DHW comfort and reduced temperatures |
| Advanced number/select | Its corresponding value above | Same companions as the native entity |
| Holiday-until date / cancel button | Current zone holiday period; setting also selects Automatic mode | Complete fresh plant and zone snapshots |
| Holiday level and BSB tuning numbers | One exact allowlisted BSB address | Fresh compare-and-set values; controller readback required |

All writable temperature numbers declare Home Assistant's temperature device class and native
degrees Celsius unit, enabling correct temperature semantics and configured-unit conversion.

The integration deliberately does not expose an arbitrary BSB-address write service. New writable
parameters should only be added after their request shape and controller behavior have been
captured and tested.

The holiday date and cancel button reproduce Remocon's current-holiday workflow, not the
controller's underlying eight-slot table. Setting a final day starts the holiday immediately,
uses that day inclusively, and changes the zone to Automatic. Cancelling does not restore the mode
that was active before the holiday.

The thermostat exposes `cool` and writes cooling comfort/reduced values only while a fresh
controller snapshot reports cooling as active. It does not use `HVACMode.COOL` to switch the plant
from heating into cooling; seasonal enablement remains under the controller's own configuration.

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
- Optional entities are created when the gateway advertises the feature or initial discovery
  finds the source value. An entity based on an advertised capability can remain unavailable
  until its first successful read.
- Writable native, number, and select entities are omitted if Remocon does not provide enough
  information to use them safely. Modes that a particular gateway does not offer are omitted from
  that entity's supported mode list.
- Authentication expiry triggers one controlled login and request replay. A second authentication
  rejection starts Home Assistant's reauthentication flow.
- Rejected credentials start Home Assistant's reauthentication flow.
- A complete multi-zone snapshot is accepted only when every requested zone succeeds; partial data
  from a failed attempt is discarded.
- Core reads immediately retry once after a fast transport interruption, the controller's
  `Communication error`, or HTTP `408`, `500`, `502`, `503`, or `504`. Full timeouts, TLS/certificate
  failures, authentication errors, rate limits, and permanent response errors are not immediately
  retried.
- After a failed core refresh, Home Assistant schedules recovery after 60 seconds, then 5 minutes,
  then 15 minutes, and finally the configured polling interval. Each delay is capped at the normal
  interval, so a short user-selected interval is never made slower by the backoff.
- HTTP `Retry-After` is honored for rate limits and temporary service failures; a rate limit without
  a usable delay defaults to 5 minutes. Parsed server delays are bounded to one day.
- Each HTTP request has a 15-second connection phase, a 65-second response-read phase, and a
  70-second total limit because valid gateways can take close to a minute. A complete `GetData`
  operation has one shared budget of 70 seconds per requested zone, so restarting a failed
  multi-zone snapshot never doubles its maximum duration.
- Deferred optional discovery can stop early on connection failures, probe timeouts, or rate
  limits. It resumes later without making core heating and hot-water entities unavailable.
  See [gateway discovery](docs/discovery.md) for the full rules.

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

The integration targets ELCO Aerotop systems exposed through Remocon's BSB plant dashboard. An
Aerotop product name alone does not establish compatibility: similar installations can use
different controller and gateway families.

| Controller family | How to identify it | Current status |
|---|---|---|
| BSB (`systemType: 5`) | The plant URL contains `/R2/BsbPlantDashboard/Index/` and the gateway exposes `PlantHomeBsb/GetData` | Supported; available entities still depend on the capabilities and values reported by the gateway |
| GALEVO (`systemType: 3`) | The plant URL contains `/R2/Plant/Index/`; BSB `GetData` requests are rejected | Supported via the mobile `api/v2` surface plus R2 `SetData` plant-mode writes; available entities differ from BSB—see [entities](docs/entities.md) |

Other controller families are not supported. The cloud API is undocumented and model capabilities
vary, so formal compatibility is still being established. If the plant uses another URL or
controller family, do not assume that setup will work solely because the appliance is branded
Aerotop.

When reporting a working or non-working model, include the exact Aerotop model, gateway type,
controller family or plant URL shape, firmware version if known, number of heating zones, and which
entities were returned. Do not post credentials, gateway IDs, serial numbers, authentication
tokens, cookies, or unredacted payloads.

## Development

The reverse-engineered request contract, endpoint selection, and contribution safety rules are
documented in [`docs/protocol.md`](docs/protocol.md).

The project is managed with [uv](https://docs.astral.sh/uv/) (requires Python 3.12 or newer):

```bash
uv sync --group dev
```

Run all local checks:

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run ty check
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
