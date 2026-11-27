<p align="center">
  <img src="https://raw.githubusercontent.com/thomasgregg/elco-aerotop/main/custom_components/elco_aerotop/brand/icon.png" width="160" alt="ELCO Aerotop heat pump icon">
</p>

<h1 align="center">ELCO Aerotop for Home Assistant</h1>

<p align="center">
  A native Home Assistant integration for monitoring and controlling BSB and Galevo heat pumps through Remocon.net.
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
- [Dashboard example](#dashboard-example)
- [Annual performance](#annual-performance)
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

ELCO Aerotop brings Remocon-connected heat pumps with **BSB** or **Galevo** controllers into Home
Assistant. Use it for everyday monitoring, heating and hot-water control, automations, and
troubleshooting.

The integration uses the structured JSON endpoints behind Remocon.net. It is a **cloud-polling**
integration, so it requires internet access and a working Remocon.net account; it does not connect
directly to the heat pump over the local network.

> [!NOTE]
> BSB (`systemType: 5`) and Galevo (`systemType: 3`) can run in the same Home Assistant instance.
> Add one integration entry per Remocon gateway.

![ELCO Aerotop heating and hot-water dashboard in Home Assistant](docs/images/home-assistant-dashboard.png)

[Set up a dashboard like this](docs/dashboard.md) · [Copy the BSB Heating YAML](docs/examples/heat-pump-dashboard-bsb.yaml)

## What you can do

The first stable release with Galevo support is [v0.4.0](https://github.com/thomasgregg/elco-aerotop/releases/tag/v0.4.0).
Available controls and readings depend on the features reported by each gateway.

| Feature | BSB (`systemType: 5`) | Galevo (`systemType: 3`) |
|---|---|---|
| **Room heating and cooling** | A thermostat per supported zone. Cooling controls follow the controller's cooling-active flag. | A thermostat per supported zone. The selected plant mode determines whether the thermostat controls heating or cooling. |
| **Zone temperature targets** | Adjust comfort and reduced temperatures in the active season. | Adjust comfort and reduced temperatures in the active season. |
| **Domestic hot water** | Native water heater with Off, On, and Eco where offered, plus comfort and reduced targets. | Native water heater with Disabled, Time Based, and Always active. Adjust scheduled comfort and economy, or the always-active target. |
| **Whole-system mode** | — | Select from the plant's offered modes, such as Summer, Winter, Heating Only, Cooling, Cooling Only, and Off. |
| **Holidays** | View holidays in a read-only zone calendar. Start, extend, or cancel the current holiday. Zone 1 offers Reduced or Frost protection. | Enter plant-wide start and end dates. Use the Holiday switch to submit or cancel the period. |
| **Heating configuration** | Frost-protection temperature, heating-curve slope, and summer/winter limit. | Heating and cooling flow temperatures and offsets, plus automatic thermoregulation. |
| **Quiet mode and hot-water boost** | — | Dedicated on/off switches. |
| **EM20 heat-pump status** | — | With an EM20 energy manager, shows heat-pump mode (including sampled defrost), electric backup heater status, heat-pump flow and outside air temperatures, and a fallback for heating-circuit flow temperature. |
| **Energy history** | Optional annual history for energy used and delivered, cooling, and performance factor. | Heating, hot-water, and cooling consumption for four periods, plus an estimated seasonal performance factor (SPF) for the previous calendar year when Remocon provides complete metering data. |
| **Weekly programs** | Read-only schedule data in diagnostics. | Read-only schedule data in diagnostics. |
| **Controller diagnostics** | Controller faults, maintenance, appliance health, and supported BSB datapoints. | Controller faults, bus device inventory, gateway signal level, and supported Remocon data items. |

Both families provide current readings such as temperatures, pressure, flow, heat demand, and
operating state, plus a manual refresh button. The gateway and installed probes determine which
rows and readings apply. A dash means the feature is unavailable on that controller
family. See the [complete entity guide](docs/entities.md) and the
[changelog](CHANGELOG.md).

```text
Home Assistant entities
          │
          ▼
ELCO Aerotop coordinator ──► Remocon.net cloud ──► ELCO gateway ──► Heat pump
          │
          └── one fresh, validated and serialized request path for all writes
```

Set up the integration in Home Assistant. Sign in and select one gateway per setup run.
Repeat the flow for any additional gateway, using the same Remocon account or different
accounts. Each gateway has its own session, entities, and polling. Existing BSB entries
upgrade in place. After restart, entities become available after the first valid snapshot,
which the integration fetches in the background.

## Entities

Each gateway has its own ELCO Aerotop device. BSB adds controller datapoints for heating
configuration, boiler and maintenance details, and annual energy history. Galevo adds plant
mode, quiet mode, hot-water boost, automatic thermoregulation, and consumption reports.
[Galevo EM20 sensors](docs/entities.md#galevo-heat-pump-status-with-em20) appear when the
gateway supplies usable readings. At the default one-hour polling interval, a short defrost
cycle can occur between readings.
The device page includes native thermostat and hot-water controls, everyday readings, and
fault information. You can enable optional diagnostics in the device's entity settings.

The [entity reference](docs/entities.md) contains the complete tables, entity-ID patterns, default
states, creation conditions, read/write behavior, and explanations of maintenance, holiday, and
energy values, including which entities exist for each controller family. New entities get
language-independent default IDs and localized display names. Upgrades preserve registered IDs
and enabled or disabled settings.

## Dashboard example

The [dashboard guide](docs/dashboard.md) includes the [anonymized BSB Heating view](docs/examples/heat-pump-dashboard-bsb.yaml)
from the screenshot and a [built-in-card example](docs/examples/heat-pump-dashboard.yaml) for
either controller family. It shows how to add Galevo EM20 status and energy or BSB maintenance
and annual performance cards. Use your actual entity IDs from the Home Assistant device page.

## Annual performance

Galevo's **Estimated SPF – previous calendar year** uses Remocon's estimated heat production
and electricity consumption for space heating and hot water. It appears after Remocon provides
matching monthly data for a complete January–December period. BSB's **Yearly performance factor**
comes from the controller and can be enabled under Diagnostics. See [energy-history entities](docs/entities.md#energy-history-entities)
for the calculation, supported periods, and availability requirements.

Remocon installation metadata is exposed as diagnostic entities when its JSON APIs return a
value. This includes gateway online state, overall plant status, appliance model and serial,
plant name and location, gateway serial, and gateway version. BSB also exposes plant owner and
account language where returned. Owner phone fields are disabled by default. **Last successful
update** records when the integration most recently completed a core Remocon data capture,
including successful polls whose values did not change. Remocon's **Connectivity Gateway**
software value and the integration's **Gateway firmware** value both come from `gwFwVer`; the
integration creates one version sensor and also reuses that value in Home Assistant's device
information.

## Holidays

Holiday controls follow the controller family.

- **BSB**: each zone's Home Assistant calendar is a read-only view of the valid holiday periods
  returned by the controller. The separate operating-level select, **Holiday until** date, and
  **Cancel holiday** button provide the reviewed write path for Remocon's immediate
  current-holiday workflow.
- **Galevo** uses **Pending holiday start**, **Pending holiday end**, and a **Holiday** switch.
  Set both dates, then turn on the switch to send the draft. The controller's reported flag
  drives the switch state. Turning it off requests cancellation.

On BSB, setting a final day starts a new holiday now or changes the current holiday's inclusive final day,
and changes the zone to Automatic mode just like Remocon. Cancelling the holiday does not restore
the mode that was active beforehand. Reduced and Frost protection use different controller
setpoints and do not mean that heating is completely off.

For both workflows, including the BSB calendar and its dashboard example, see
[Holiday periods in Home Assistant](docs/holidays.md).

## How data is discovered

Remocon gateways differ by controller, firmware, and installed options. The integration therefore
starts with the capabilities and values reported by the gateway, then performs additional
read-only discovery for:

- plant and gateway information, live connectivity/status, and owner metadata;
- heating, hot-water, zone, and controller-bus values;
- schedules and holiday periods;
- errors, maintenance messages, and appliance-health information; and
- metering and historical energy records.

Core heating data loads first. Optional requests run in the background. BSB probes include
maintenance and annual records, while Galevo probes include menu items, bus inventory, and
consumption reports. A temporarily failed datapoint can recover on a successful read.

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
2. Search for **ELCO Aerotop** and choose **Download**. For Galevo, install `v0.4.0` or later.
3. Restart Home Assistant.
4. Continue with [Home Assistant setup](#home-assistant-setup).

Existing BSB installations can update through HACS and restart Home Assistant. Keep the existing
integration entry to preserve its entity IDs and enabled settings. To add a Galevo gateway
alongside it, run the setup below for that gateway.

If the version you need is unavailable in HACS, install it manually using the instructions below.

### Option 2: Manual installation

1. Download `elco_aerotop.zip` from the chosen [GitHub release](https://github.com/thomasgregg/elco-aerotop/releases)
   or check out the Git branch you want to install.
2. Extract the contents of `elco_aerotop.zip` into the directory below, or copy the
   complete `custom_components/elco_aerotop` directory from the repository.

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
| Gateway ID | No | — | Enter the dashboard gateway ID to validate a gateway directly if account discovery cannot list it. |

After sign-in, the integration lists unconfigured gateways on the account. One available
gateway is selected automatically; choose one gateway when several are available. Repeat
the setup to add another gateway, including a Galevo gateway alongside an existing BSB
entry. Explicitly unsupported controller families are rejected. An existing config entry
keeps its saved gateway and title on upgrade.

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

The integration reloads automatically when the option changes. A successful write requests
a core refresh. The **Refresh** button requests the same core refresh and honors any
server-imposed backoff; the deferred diagnostic schedule is independent.

## Write-safety model

Remocon's write endpoints expect related values together, even when only one Home Assistant entity
changes. Sending an incomplete or stale request can be ignored by the controller or overwrite a
companion value. Every write therefore follows this sequence:

1. Acquire a per-device command lock.
2. Stop optional background discovery and acquire the shared gateway-traffic lock.
3. Fetch uncached state for the affected zone or plant. BSB hot-water commands read one known
   zone with the plant data. Galevo plant-wide commands use the mobile snapshot.
4. Validate the requested value against current gateway limits or allowed options.
5. Enforce the relationship between comfort and reduced temperatures.
6. Preserve all unchanged companion values required by the endpoint.
7. Send the typed command once through the dedicated Remocon endpoint. An explicit `401`/`403`
   rejection may renew the session and replay once because the unauthenticated request was rejected.
8. Never resend a command after a timeout, disconnect, or ambiguous server failure. When Remocon
   permits another request, use an uncached read to check whether the command took effect.
9. Publish the refreshed core state. BSB updates optional system readings in the background.
   Galevo plant-wide controls publish the complete verification snapshot directly.

Galevo plant-wide controls allow one additional uncached read if the first snapshot does not
confirm the requested values. The command succeeds when a readback confirms the change and
reports an unconfirmed result if the final read still differs. Failed verification can leave
the outcome unknown. The integration honors Remocon's `Retry-After`.

Galevo plant mode uses the mobile `POST /api/v2/remote/plantData/{gateway}/mode`
route with `{new, old}`. The integration refreshes the current mode and choices before sending,
then reads back the result. A rejected request or a readback that still shows the old mode
is reported as a failure. If a follow-up read fails after an acknowledgement, the outcome
is reported as unknown.

Use the native Galevo water heater to set scheduled comfort in **Time Based** mode and the
always-active target in **Always active** mode. A scheduled comfort change preserves the economy
setpoint and checks both setpoints on readback. Existing **Scheduled hot water comfort temperature**
numbers continue to work in Time Based mode. The integration honors read-only targets.
A request to change both mode and temperature checks the destination target before switching mode
and again after the mode is confirmed. It reports a partial result if only the mode changes.

Polling and optional discovery use the same gateway-traffic lock. Optional discovery releases it
between requests so a user command can proceed without overlapping controller traffic.

Write commands preserve the controller values required by their endpoint.

| User-facing control | Remocon value written | Preserved companion values |
|---|---|---|
| Thermostat target | Active heating or cooling comfort temperature | Matching reduced temperature and current zone payload |
| Zone HVAC mode or preset | Zone operating mode | BSB uses fresh plant and zone data. Galevo sends the previous mode. |
| Hot-water target or mode | Comfort, reduced/economy, or operating mode | BSB uses the full comfort/reduced/mode group. Galevo preserves the scheduled pair and sends the previous value for individual mode or always-active target changes. |
| BSB Holiday until or cancel | Current zone holiday. Setting it also selects Automatic mode. | Fresh plant and zone data |
| Galevo Holiday switch | Plant-wide holiday period or cancellation | Local start and end dates for activation, and the local current time for cancellation |
| BSB holiday level and tuning | One allowlisted BSB address | Fresh previous value and confirmed readback |

All writable temperature numbers declare Home Assistant's temperature device class and native
degrees Celsius unit, enabling correct temperature semantics and configured-unit conversion.

The integration deliberately does not expose an arbitrary BSB-address write service. New writable
parameters should only be added after their request shape and controller behavior have been
captured and tested.

The holiday date and cancel button reproduce Remocon's current-holiday workflow, not the
controller's underlying eight-slot table. Setting a final day starts the holiday immediately,
uses that day inclusively, and changes the zone to Automatic. Cancelling does not restore the mode
that was active before the holiday.

The thermostat exposes `cool` when BSB reports cooling active or Galevo selects **Cooling**
or **Cooling Only** as its plant mode. Use the Galevo plant-mode select to change the plant season.
Galevo zone temperature changes require the current plant mode and both comfort and reduced
values for the selected season. The thermostat's activity follows the controller's separate
activity flags.

Season-specific zone setpoint writes require the matching active season. On a tested Galevo
gateway, setting cooling comfort in Remocon also changed heating comfort. Previously registered
off-season comfort controls keep their IDs and appear as unavailable.

## Availability and refresh behavior

- Plant and zone data are coordinated into a single Home Assistant snapshot.
- Normal plant, zone, and system-item values refresh at the configured polling interval: 60
  minutes by default.
- On Galevo with EM20, the status sensors refresh after each regular core update. Missing or
  unreadable status readings become unavailable. The heating-circuit flow sensor can still use
  the primary reading when it is valid.
- Schedules, maintenance, monitoring, BSB diagnostics, and energy history refresh in a deferred
  background cycle approximately every 60 minutes, independently of the selected normal polling
  interval. Their first deferred refresh starts after the first successful core snapshot, including
  when that snapshot follows a failed poll.
- Zone entities are created from the discovered zones. A valid item that first appears in
  a later snapshot can register its entity then. Existing entities recover on later polls.
- A reported comfort target or mapped operating mode identifies a native thermostat or water heater.
  Each entity offers the controls that the current snapshot supports. Missing data can temporarily
  hide an action or make the entity unavailable. The entity recovers under its existing ID.
- Standalone numbers expose separate targets such as reduced and economy. Standalone mode selects
  cover additional controller modes. Previously registered controls keep their IDs and enabled settings.
- Optional entities are created when the gateway advertises the feature or initial discovery
  finds the source value. An entity based on an advertised capability can remain unavailable
  until its first successful read.
- Every write still checks current gateway data. Native modes come from the options the gateway
  reports for that entity.
- Authentication expiry triggers one controlled login and request replay. Rejected credentials
  or a second authentication rejection start Home Assistant's reauthentication flow.
- During Galevo polling, fresh plant readings and healthy zones remain available if an individual
  zone read fails. Home Assistant marks the failed zone's entities unavailable and restores them
  under their existing IDs when a later poll succeeds. **Last successful update** records the last
  complete refresh. Authentication failures and server-requested pauses affect the whole gateway.
- BSB polling and the fresh reads used for commands still require every requested zone to succeed.
- Core reads immediately retry once after a fast transport interruption, the controller's
  `Communication error`, or HTTP `408`, `500`, `502`, `503`, or `504`. Full timeouts, TLS/certificate
  failures, authentication errors, rate limits, and permanent response errors are not immediately
  retried.
- After a transient core failure, an unusable Galevo plant response, or an incomplete Galevo zone
  refresh, Home Assistant schedules recovery after 60 seconds, then 5 minutes, then 15 minutes,
  and finally the configured polling interval. Each delay is capped at the normal interval.
- HTTP `Retry-After` is honored for rate limits and temporary service failures; a rate limit without
  a usable delay defaults to 5 minutes. Parsed server delays are bounded to one day.
- Each HTTP request has a 15-second connection phase, a 65-second response-read phase, and a
  70-second total limit because valid gateways can take close to a minute. A complete `GetData`
  operation has one shared budget of 70 seconds per requested zone, so restarting a failed
  multi-zone snapshot never doubles its maximum duration. Regular Galevo polling has a separate
  70-second budget for the plant read and each zone read, including any immediate retry.
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

If you're looking for EM20 sensors, check `data.discovery.features.hasEm20` in a downloaded
diagnostic. A Galevo gateway must report `true` and return a usable value before each new
EM20-only sensor appears. See the [EM20 entity IDs and availability rules](docs/entities.md#galevo-heat-pump-status-with-em20).

### The gateway is online, but Remocon shows only outdoor temperature

Open the same plant in the Remocon app and web portal. If both are missing heating or
hot-water readings, compare them with the values shown on your local controller. On a
Remocon NET B, check both the `WEB` and `BUS` LEDs. The gateway reports internet connectivity
and communication with the BSB controller separately.

For a GitHub issue, include the LED states, which readings are present on the controller,
and an inspected, redacted [Home Assistant diagnostic](docs/discovery.md#contributing-a-gateway-fixture).
If a read fails, include the relevant [debug log lines](#enable-debug-logging) with gateway
identifiers removed.

### A write is rejected or does not remain set

- Check the comfort and reduced targets. Heating comfort must be at or above reduced, while
  cooling comfort must be at or below reduced.
- On Galevo, check the current plant mode. Use the thermostat or the matching season's reduced
  temperature control for a zone setpoint.
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

Remove the override after troubleshooting. Debug logs contain endpoint names and outcomes.
Inspect logs and diagnostic exports before sharing them.

## Compatibility

The integration supports Remocon.net gateways with BSB or Galevo controllers. Check the
controller family because installations with the same Aerotop model name can use different
controllers. The gateway's reported capabilities determine the available features.

The integration selects the read protocol from `data.plant.systemType` in Remocon's
`Features` response. In Home Assistant diagnostics, look for `data.discovery.system_type`.

| Supported controller | `systemType` |
|---|---|
| BSB | `5` |
| Galevo | `3` |

Remocon's browser dashboard routes are separate from these JSON reads. Use the reported
`systemType` to identify the integration's controller family, even if the portal opens
`/R2/Plant/Index/` or a manually entered `/R2/BsbPlantDashboard/Index/` URL returns 404.
Supported features can vary with gateway firmware, connected probes, and installed options.
See the [Remocon read protocol](docs/protocol.md#reading-state).

When reporting a working or non-working model, include the exact Aerotop model, gateway type,
reported `systemType` if available, firmware version if known, number of heating zones, and which
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
uv run --locked pytest tests
uv run --locked ruff check .
uv run --locked ruff format --check .
# Run the following with Python 3.14.2+ and the locked Home Assistant dependencies
uv run --locked pytest integration_tests
uv run --locked ty check
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
