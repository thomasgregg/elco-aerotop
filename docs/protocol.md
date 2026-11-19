# Remocon R2 protocol notes

Remocon.net does not publish a supported public API for this equipment. These notes document the
browser-facing JSON calls used by this integration so that changes can be reviewed deliberately.
Never include credentials, session cookies, gateway IDs, or complete real-device responses in an
issue.

## Authentication

1. `GET /R2/Account/Login`
2. Read the hidden `__RequestVerificationToken` value.
3. Store it in the `__formRequestVerificationToken` cookie.
4. `POST /R2/Account/Login` as JSON with `email`, `password`, `rememberMe`, and `language`.

Each Home Assistant config entry owns an isolated cookie session. A `401`, `403`, or login-page
HTML response invalidates the session and causes one controlled reauthentication attempt.

Mobile-only endpoints such as `menuItems` require an independent token:

```text
POST /api/v2/accounts/login  {"usr": "...", "pwd": "..."}
GET  /api/v2/menuItems/...   ar.authToken: <in-memory token>
```

The credentials are sent in the HTTPS JSON body, never in the URL. The token is kept only in the
config entry's private API client and is not included in diagnostics.

## Reading state

Configured zones are discovered through:

```text
GET /R2/Plant/Features/{gateway}?eagerMode=true
```

Plant and zone state comes from:

```text
POST /R2/PlantHomeBsb/GetData/{gateway}
```

The request selects a zone and independently requests the plant and zone blocks. A write always
starts by reading uncached state.

The integration retains the full `Features` response and every per-zone `GetData` response for
schema inventory in redacted Home Assistant diagnostics. It does not log these payloads.

## Optional read-only discovery

The following calls expand model discovery without enabling any corresponding write operation:

| Family | Method and path |
|---|---|
| Plant metadata | `GET /api/v2/remote/plants/lite` |
| Live plant header | `GET /R2/Plant/PlantHeader/{gateway}` |
| Plant owner metadata | `GET /R2/PlantData/GetUserData?id={gateway}` |
| System data | `POST /api/v2/remote/dataItems/{gateway}/get?umsys=si` |
| Native BSB plant snapshot | `GET /api/v2/remote/bsbPlantData/{gateway}` |
| Mobile menu items | `GET /api/v2/menuItems/{gateway}?menuItems={id}` for one feature-selected documented ID |
| Time programs | `POST /R2/PlantTimeProgBsb/GetData/{gateway}` with a program-ID filter |
| Metering | `POST /R2/PlantMetering/GetData/{gateway}` |
| Maintenance | `GET /R2/PlantData/GetMaintenanceData?id={gateway}` |
| Automated monitoring | `GET /R2/AutomatedMonitoring/GetDrawerData/{gateway}` |
| BSB appliance data | `GET /R2/PlantData/GetBsbBoilerData?id={gateway}` |
| Controller errors | `GET /api/v2/busErrors?gatewayId={gateway}&blockingOnly=False&culture=en-US` |
| BSB parameters | `GET /R2/PlantMenuBsb/ReadDataPoints/{gateway}?addresses=...` |

BSB display line numbers such as 700 and 720 are not accepted as read addresses. The integration
uses a code-reviewed allowlist of controller-internal datapoint IDs. Optional calls run
independently from core polling, have their own time bound, and expose an availability status in
diagnostics. A `404`, changed response shape, or other endpoint-specific failure must not disable
normal plant/zone updates.

For gateways that advertise heat-pump capability (`hpSys`), the integration reads line 8430
(minimum source outlet temperature) at address `5838697` in a separate `ReadDataPoints` request.
The read was verified on an
[Aquatop S11 with an RVS61 controller](https://github.com/thomasgregg/elco-aerotop/issues/2#issuecomment-6792821726).
A timeout of this probe is recorded under `bsb_points:source_outlet_minimum` and discovery
continues. The controller's reset action has no verified Remocon request.

The cloud maintenance endpoint and the controller's BSB `7000 – Message` group are different data
sources. BSB maintenance-code entities require verified internal datapoint IDs; the integration
does not infer them from the visible line number.

The BSB time-program request uses program IDs 1–6 for heating zones, 7 for DHW, 8 for the extra
program, and 9–14 for cooling zones. Heating/cooling requests select the matching zone block; DHW
selects the plant block. The similarly named mobile `timeProgs` route is not used for BSB plants.

Annual energy history is also a BSB menu family, not the cloud metering response. Ten verified
fixed-date slots are read as ten isolated eight-address requests. Each slot has a record date, yearly
performance factor, delivered heating/DHW/cooling energy, and input heating/DHW/cooling energy.
This read remains applicable when `Features.hasMetering` is false.

The menu-item IDs are a bounded mobile-API catalog, not raw BSB addresses. Reads are made one ID at
a time because some controller families reject an unsupported ID rather than omitting it. Capturing
successful results is read-only capability discovery; it does not authorize a matching write or
prove that an absent feature can be enabled.

`menuItems` belongs to the Galevo controller family. When `Features` reports `systemType: 5` (BSB),
the integration skips that family and records `unsupported:bsb_system`; BSB discovery continues
through `bsbPlantData` and the reviewed `PlantMenuBsb/ReadDataPoints` allowlist.

## Galevo controllers (`systemType: 3`)

The BSB `GetData` snapshot does not exist on Galevo plants; the endpoint returns HTTP 500 even
after authentication. `Features` reports the controller family in `data.plant.systemType`
(APK `eGwSystemTypeDto`: Galileo1=1, Galileo2=2, Galevo=3, Velis=4, BSB=5, AC=10). When the
features response reports `3`, the coordinator builds plant/zone state from the mobile
data-item and zone endpoints instead of `PlantHomeBsb`:

| Family | Method and path |
|---|---|
| Home items | `POST /R2/PlantHome/GetData/{gateway}` with `{features, filter, useCache, zone}` body |
| System data items | `POST /api/v2/remote/dataItems/{gateway}/get?umsys=si` (shared with BSB discovery) |
| Zone state | `GET /api/v2/remote/zones/{gateway}/{zone}` |
| Time programs | `GET /api/v2/remote/timeProgs/{gateway}/{programId}` (same eProgId values 1–6, 7=DHW, 8, 9–14) |
| Menu items | `GET /api/v2/menuItems/{gateway}?menuItems={id}` |
| Consumption series | `GET /api/v2/remote/reports/{gateway}/consSequencesApi8?usages=…&hasSlp=…` |
| System state | `GET /api/v2/remote/plantData/{gateway}/systemState` |


`PlantHome/GetData` is the R2 home-screen snapshot and the true counterpart of the BSB
`GetData`: a GET returns 404, but a POST with the app's body shape
(`features` map including `gatewayId`, `filter: {notEssentials, plant, zone, dhw}`,
`useCache`, `zone`) returns `data.items` plus `hasAdvancedSettings`/`hasDhw`. The item shape
matches `dataItems`, additionally carrying localized `optTexts`, real min/max bounds and
`readOnly` flags. `filter.notEssentials: false` returns only the home-screen essentials;
`true` adds `ZoneHeatRequest`, `IsHeatingPumpOn`, `IsQuite`, `IsDhwBoost` and `IsResistorOn`.
The integration merges both item sources per zone; R2 entries win on conflicts.

`dataItems` returns a map keyed `"<itemId>:<zone>"` whose entries carry `id`, `zone`, `kind`,
`value`, `min`/`max`/`step`, `options`/`optTexts`, `readOnly`, `error` and `invalid` flags. Items
for zones the plant marks hidden/virtual are absent. Zone payloads carry `mod`
(`value` + `allowedOptions`, no labels), `room`, `desired`, `comf`/`econ` (`value`/`min`/`max`/`step`),
`heat`, `der`, `antiFreezeTemp`, `heatReqAir` and `pilot`.

**Single-item reads:** `dataItems/get` is also the per-item read — `items` may
contain just one `{id, zn}` entry. `GetDataItemsRequestV2` requires `useCache`,
`items`, `features` (`RemotePlantFeaturesDto`, from `Plant/Features`) and
`culture`. Omitting `features` or `culture` → HTTP 500 even with a valid token
(live-verified 2026-11-19; token-only requests succeed with both included).
There is no dedicated single-item endpoint and no `ReadDataPoints`-style route in
the app — that path is the R2 web surface (`PlantMenuBsb`, BSB-only) which the
integration uses for BSB address reads.

**`culture` controls server-side localization** (live-verified 2026-11-19):
`culture=en` returns `ZoneName` as `"Home"`, `culture=de` returns the German
value (`"Zuhause"`) — including inside the echoed `features.zones[].name`.
The field accepts a bare language code only: `de-DE` → HTTP 500. The APK sends
`Locale.getDefault(Locale.Category.DISPLAY).getLanguage()` (language part only,
`SystemCulture_androidKt.getSystemCultureName()`), so the integration sends the
Home Assistant UI language instead of hardcoding `en`.

**Live diff 2026-11-19 (Galevo gateway):** every item `PlantHome/GetData`
returned was already present in the mobile `dataItems` response
(`r2-only: 0`; the mobile answer additionally carried 19 items, e.g.
`ZoneName`, `ZoneMeasuredTemp`, `HeatingFlowTemp`, `QuietModeStart/End`).
The only shape difference: R2 items add `gatewayId`, `expiresOn` and `unit`
keys and use `[]` instead of `null` for absent `options`/`optTexts`.
The per-zone R2 `GetData` call was therefore removed from the poll path —
the poll is now `dataItems/get` + `zones/{n}` per zone, plus the cached
Features lookup. R2 remains in use for Features, the deferred
plant-metadata/header/user-data probes and `SetData` writes.

Galevo enum values differ from the BSB constants used elsewhere in this document
(APK `eZoneMode`/`eDhwMode`): zone modes are `0=Off, 1=ManualNight, 2=Manual, 3=TimeProg` and DHW
modes are `0=Disabled, 1=Time Based, 2=Always active`. `ZoneState.from_galevo` normalizes them
onto the canonical model values (Off→protection, ManualNight→reduced, Manual→comfort,
TimeProg→automatic; Disabled→off, Time Based→eco, Always active→on) so entity mappings stay
family-agnostic. Zones without a room probe report `ZoneMeasuredTemp`/`room` as `0` rather than an
error flag; the parser suppresses that placeholder when `features.zones[].roomSens` is false.

Verified against a live Galevo gateway (`tools/remocon_probe.py`, `captures/`): `dataItems`,
`zones/{1..3}`, `systemState`, `timeProgs/{1,7}`, `menuItems`, `busDevices`, `gatewayInfo`,
`actions/mostRecent`, `plants`, `plants/features`, and the shared R2 `Features` endpoint all
respond 200. `plantData/{mode,holiday,dhwTemp}` return HTTP 405 on GET (write-only paths).
`GetMaintenanceData` and `GetDrawerData` report "session expired" under the R2 cookie session on
this plant and are marked `unavailable` by the optional-probe policy. BSB-only families
(`ReadDataPoints`, `GetBsbBoilerData`, `bsbPlantData`, `PlantMetering`) are skipped and recorded
as `unsupported:system_family`.

Additional probes:

- `GET /R2/PlantAdvancedSettings/{Index,Refresh}` → 404; POST with the app's features body →
  **403 Forbidden** from the integration's R2 session, while the browser (HAR capture) gets
  200 HTML — the page is not installer-gated, but our R2 cookie session is missing some
  browser context the endpoint requires. Its content is anti-legionella + quiet-mode
  settings (see below).

### R2 web-app routes (browser HAR evidence)

The R2 UI reaches several routes the app never calls (evidence: user-provided HAR):

- `POST /R2/PlantZonesSettings/GetData/{gw}` with `{gatewayId, zones:[{num,name,...}]}`
  returns `zoneDataItems` (a `ZoneName` item per zone, incl. hidden ones, `ro=false`,
  `options` 0–25), `zones` (`zoneNumber`, `zoneName`, `visible`, `canSetVisibility`) and the
  26-entry `zoneNames` label catalog (localized, e.g. "Wohnzimmer"/"Keller"). Zone-1 name
  resolves through the shared `ZoneName:1` item `optTexts` (mobile localization: "Home").
- `POST /R2/PlantTimeProg/GetData/{gw}` with `{gatewayId, progId, filters:{timeProg,
  temperatures, automaticThermoregulation}}` returns `timeProg` (`progId`, `weeklyPlan`
  with `plans[].days`/`slices`, `allowedTemp`, `baseTemp`, `tick`, `maxSwitches`, `ext`,
  `pilot`) plus `items`. HAR shows `progId: 7` (DHW) and **`progId: 15` (buffer program)**
  — buffer scheduling exists on `bufferTimeProgAvailable` plants. The plan structure is
  identical to `GET /api/v2/remote/timeProgs/{gw}/{id}`, which the integration already uses.
- `POST /R2/PlantHome/GetData/{gw}` with `filter.progId: <n>` and `plant/zone/dhw: false`
  returns the schedule for program `n` embedded as `data.timeProg` — alternative schedule
  source; not wired since `remote/timeProgs` already covers it.
- `POST /R2/PlantDhw/Index/{gw}` and `POST /R2/PlantAdvancedSettings/Index/{gw}` return MVC
  HTML views whose fields bind client-side to the same `dataItems` model — DHW page: current
  temp, comfort/reduced temp, setpoint, mode, "Zero Cold Water" (absent here), boost.
  Advanced page: **anti-legionella** (on/off, frequency, temperatures, hybrid mode) and
  **quiet mode** (`IsQuite` + start/end times). Corresponding item IDs
  (`AntilegionellaOnOff|Temp|Freq`, `QuietModeStart|End`, `Em30QuietOptions`,
  `SavingEcologyLogic`) are included in the `dataItems` request list; this plant returns
  only `IsQuite`.
- `GET /R2/PlantMenu/Refresh` returns user-menu parameter leaves (`U6_3_0_1`,
  "6.3.0.1 - Temperatur Vorlauf Zone 1", etc. — the 6.x user-menu parameter family).
  `GET /R2/PlantMenu/UserMenu/{gw}` renders the full 27-node menu tree. These overlap the
  `dataItems`/`menuItems` coverage and are not wired as entities.
- `GET /R2/PlantData/Index/{gw}` renders plant metadata: location (lat/long, region,
  province, country, postal code, address, city, geocoding names), nickname and a
  "Software-Version Geräte" section — candidates for future device-info enrichment.
- `GET /api/v2/remote/plants/{gw}/busDevices` returns the bus inventory (`name`, `cat`,
  `zone`, `occ`, `sn`, `code` per device: Master UI, Hybrid Energy Manager, Remote Modem,
  Heat Pump) — wired to the `bus_devices` diagnostic sensor + diagnostics payload.
- `GET /api/v2/remote/reports/{gw}/energyAccount` → period-keyed map (`{"LastMonth": []}` —
  `eEnergyAccountPeriod` 1–5); empty on this plant, no entity consumes `metering` today.
- `GET /api/v2/remote/reports/{gw}/dailyRoomTemp` → `[]`; `co2Report` → HTTP 599 (unsupported).
- `GET /api/v2/remote/plants/{gw}/consumptionsSettings` → 405 (write path).
  `POST .../getConsumptionsSettings` → `{currency, gasType, gasEnergyUnit, elecCost}`.
- Galevo write paths (APK proxies — bodies verified from source, requests still gated):
  `POST dataItems/{gw}/set`, `zones/{gw}/{n}/{mode,temperatures,deroga,name}`,
  `plantData/{gw}/{mode,dhwTemp,dhwMode,dhwTimeProgTemperatures,holiday}`.
- `GET /api/v2/remote/reports/{gw}/consSequencesApi8?usages=Ch,Dhw,Cooling&hasSlp=false`
  → consumption time series `[{k, p, v}]`: `k` = `eEnergyUsageDto` value (1=heating, 2=DHW,
  3=cooling; the gateway also returns aggregates like 24=`ChHpResAggr`, 25=`DhwHpResAggr`),
  `p` = `eSequencePeriod` (1=last 24 h hourly, 2=last 7 days, 3=last 30 days, 4=last
  12+ months), `v` = kWh buckets, oldest first, the last bucket is the in-progress
  period (inferred: the two non-zero runs in the cooling series line up with the two
  most recent cooling seasons). Verified live 2026-11-19 on a Galevo gateway
  (heating ≈2772 kWh / 22 months, DHW ≈2355 kWh). Requesting detail usages the plant
  does not track → HTTP 500 (live probe 2026-11-19: `ProdChHeatingPump`,
  `ProdDhwHeatingPump`, `ProdCoolHeatingPump` all rejected), so the catalog is
  kept at the base trio.
- `Weather` data item: value maps to APK `eWeather` — `0=Unavailable, 1=Clear,
  2=Variable, 3=Cloudy, 4=Rainy, 5=Rainstorm, 6=Snow, 7=Fog, 8=Windy` plus the
  `+128` night variants (`129=ClearByNight` … `136=WindyByNight`), `137=Invalid`.
  The item carries no `optTexts`, so the enum names are mapped in code.

### Galevo write contracts (APK `*Proxy` classes, verified from source)

Scalar and enum writes serialize a `ChangeValueDto` `{new, old}` so the
gateway can reject concurrent changes; temperature pairs use
`TemperaturesDto {comf, econ}`:

```text
POST /api/v2/remote/zones/{gw}/{n}/mode            {new: int, old: int}   (eZoneMode)
POST /api/v2/remote/zones/{gw}/{n}/temperatures    {new: {comf, econ}, old: {…}}
POST /api/v2/remote/plantData/{gw}/mode            {new, old}             (ePlantMode)
POST /api/v2/remote/plantData/{gw}/dhwTemp         {new, old}             (float)
POST /api/v2/remote/plantData/{gw}/dhwMode         {new, old}             (eDhwMode)
POST /api/v2/remote/plantData/{gw}/dhwTimeProgTemperatures  ChangeValueDto<TemperaturesDto>
POST /api/v2/remote/plantData/{gw}/holiday         {new: start, old: end}  (set)
POST /api/v2/remote/plantData/{gw}/holiday         {old: now}              (reset)
POST /api/v2/remote/dataItems/{gw}/set             {features: {…}, items: [SetDataItemDto]}
```

**Wire names** (kotlinx `@SerialName`, verified in the decompiled
`ChangeValueDtoSerializer`/`TemperaturesDto`/`SetDataItemDto`): the outer
ChangeValueDto serializes its Kotlin properties `newValue`/`oldValue` as
`new`/`old`, and TemperaturesDto serializes `comfort`/`economy` as
`comf`/`econ`. Sending the long names makes the server deserialize nulls —
live evidence 2026-11-19: `{newValue: {comfort: 52, economy: 40}, …}` on
`dhwTimeProgTemperatures` → HTTP 500, and `{newValue: 0, oldValue: 1}` on
`plantData/mode` → `{"success": true}` but no state change (an empty
change deserializing to defaults). **Verified live 2026-11-19:** the
corrected `{"new":{"comf":52,"econ":40},"old":{"comf":53,"econ":40}}` on
`dhwTimeProgTemperatures` returned `{"success": true}` and a fresh
`dataItems/get` confirmed `DhwTimeProgComfortTemp=52` (and `DhwTemp=52`).
The R2 web app instead writes menu
items via `POST /R2/PlantMenu/Submit/{gw}?userActivity=SaveOtherSettings`
with `[{id: "U6_9_0", value: 52, prevValue: 53}]` (browser capture).

Holiday uses the same `ChangeValueDto<LocalDateTime>` shape: `setHoliday(start, end)`
serializes start as `new` and end as `old` (ISO local date-times, no zone);
`resetHoliday(now)` sends `old` only. The `Holiday` data item (options [0,1])
mirrors the controller's holiday flag and backs `switch.<device>_holiday`, whose
pending window is edited via `date.<device>_holiday_start`/`_holiday_end`.

`SetDataItemDto` = the full `DataItemDto` as returned by `dataItems/get` plus
`prevValue` (the previous `value`) and `prevExpiresOn` (the previous `expiresOn`,
sent only when present — the gateway capture omits `expiresOn`, so null-tolerance
is unverified). Zone cooling setpoints (`ZoneComfortCoolingTemp`/`ZoneEconomyCoolingTemp`)
are data items — they write through `dataItems/set`, not the `temperatures` route.

Sources: `DataItemProxy.setDataItems`, `ZoneProxy.setMode`/`setTemperatures`,
`PlantDataProxy.set*` methods (`com.remotethermo.aristonnet.data.remote.proxies`),
`ChangeValueDto`/`TemperaturesDto`/`SetDataItemsRequest`/`SetDataItemDto` DTOs.

Galevo write paths are gated by `GALEVO_WRITES_ENABLED` (`const.py`) — enabled by
the user on 2026-11-19 after the APK contracts were verified; set it back to
`False` to disable every Galevo write without touching entity definitions. `async_set_zone_temperatures`, `async_set_dhw` and `async_set_zone_mode`
dispatch on `system_type`: Galevo uses the mobile routes above (zone/DHW enum values
are translated back from canonical via `GALEVO_*_MODE_TO_RAW`), other families keep
the BSB paths.

**Verified write contract** (browser HAR, plant mode Winter→Summer):

```text
POST /R2/PlantHome/SetData/{gateway}?umsys=si
{
  "features": {…enriched features map…},
  "prevItems": […all 27 current data items, same shape as GetData…],
  "viewModel": {"plantMode": 0, "plantModeAsText": "Sommer", "zoneMode": 2,
                "zoneNumber": 1, "desiredTemp": {…item…}, "comfortTemp": 20,
                "reducedTemp": 17, …full page view model…}
}
→ {"data": {"changedItems": [PlantMode{value:0}, ZoneDesiredTemp, ZoneHeatRequest]},
   "ok": true}
```

German `optTexts` confirm PlantMode semantics: `0=Sommer (nur Brauchwasser)`,
`1=Winter (Heizen und TWW)`, `2=Nur Heizung`, `3=Kühlen`, `5=AUS` — note option `4`
is absent on this plant.

**Live evidence 2026-11-19 — the mobile mode route works with the correct
wire keys:** `POST /api/v2/remote/plantData/{gw}/mode` with the Kotlin
*property* names (`{"newValue":0,"oldValue":1}`) returned HTTP 200
`{"success": true}` yet a fresh `dataItems/get` 14 s later still reported
`PlantMode=1` — the server deserialized an empty change. With the real
wire names (`{"new":0,"old":1}` — `ePlantMode`: Summer 0, Winter 1,
HeatingOnly 2, Cooling 3, CoolingOnly 4, Off 5) the same route applies the
mode: `{"success": true}` followed by `PlantMode=0`, verified both
directions (Winter→Summer→Winter) on 2026-11-19. The integration uses this
mobile route for plant-mode writes; the R2 `PlantHome/SetData` contract
(`{features, prevItems, viewModel}` → `changedItems`) remains documented
below as the browser-app alternative. `viewModel` carries the semantic fields
(zone context, temperature items, mode flags); pure display strings
(bottomSheet/weather icon texts) are omitted as they cannot affect the
server-side command.

**viewModel flag semantics — live-verified the hard way (2026-11-19):**
`isWinter`/`isSummer`/`isHeating`/`isCooling`/`isOff` are 1:1 mirrors of the
`ePlantMode` options — `isHeating` means "Heating only" (mode 2), *not*
"any heating". Sending `isWinter=true` together with `isHeating=true`
(target Winter, flags computed as `mode in (1,2)`) made `SetData` return
a bare HTTP 500; the same request with `isHeating=false` was accepted
(`changedItems: PlantMode=1, ZoneHeatRequest=ON`). `isCooling` covers
Cooling(3) and CoolingOnly(4). Items in `prevItems`/`desiredTemp` are
normalized to the R2 shape (`gatewayId`, `expiresOn`, `options`/`optTexts`
as lists, `unit`) since the mobile `dataItems` shape omits those keys.

### Family separation convention

`ReadOnlyDiscovery.system_type` carries the `plant.systemType` from the Features response
and is the single family signal for entity platforms. Registration rules:

- **BSB data-point entities** (controller clock, heating-circuit 7xx parameters, heat-pump
  temperatures, cooling-circuit-2, annual energy records, maintenance/automated-monitoring
  sensors, `ElcoBsbNumber` controls, `HolidayOperatingLevel` select) register only when
  `system_type != SYSTEM_TYPE_GALEVO` — they have no data-item equivalent and would be dead
  entities (or hit BSB write paths) on Galevo.
- **Galevo entities** are data-item gated (`_has_system_item`) or deferred-probe gated
  (`probe_status`); nothing Galevo-specific registers on BSB.
- `GALEVO_GLOBAL_DATA_ITEM_IDS`/`GALEVO_ZONE_DATA_ITEM_IDS` are the APK data-item catalog,
  verified on Galevo; on BSB the same `dataItems` endpoint is probed opportunistically.
- R2 view POST bodies (`PlantAdvancedSettings/Index`, `PlantDhw/Index`, …) are **never
  hardcoded**: build them from the live `Features/{gw}` response
  (`build_r2_view_body` in `tools/remocon_probe.py` whitelists the key set the web app
  sends and enriches zone names from `ZoneName` items). The web app's client-side
  enrichment of `virtInfo`, `dhwProgSupported`, `dhwHidden`, `dhwBoilerPresent`,
  `hvInputOff` and `hydraulicScheme` is unverified — Features reports them as `null`
  and they are passed through unchanged.

## Writing state

Heating and cooling comfort/reduced setpoints use the same atomic-pair endpoint:

```text
POST /R2/PlantTimeProgBsb/SetTemperature/{gateway}
```

The body contains `zoneNum`, both active-season temperatures, `plantData: null`, and the full fresh
`zoneData` snapshot. Cooling values are sent only when that fresh snapshot reports
`isCoolingActive: true`; heating values are sent otherwise. The integration enforces heating
comfort >= reduced and cooling comfort <= reduced. This contract does not switch the controller's
heating/cooling season. Sending temperatures through `PlantHomeBsb/SetData` can return success
without propagating the values to the controller, so this integration does not do that.

Domestic-hot-water comfort temperature, reduced temperature, and mode are also an atomic command:

```text
POST /R2/PlantDhwBsb/Save/{gateway}
```

Its body contains the full fresh `plantData` snapshot plus `comfortTemp`, `reducedTemp`, and
`dhwMode`.

Runtime polling, background discovery, and write sequences share one gateway-traffic lock. A user
command cancels optional discovery before its uncached read and write, and its immediate state
refresh does not start another slow discovery pass. A zone write reads only that zone; a DHW write
requests plant state through one known zone instead of reading every zone.

`GetData` has a complete-operation deadline of 70 seconds per requested zone. Within that same
budget, the whole snapshot restarts once after a fast non-TLS transport interruption, a controller
`Communication error`, or HTTP `408`, `500`, `502`, `503`, or `504`. Partial multi-zone snapshots
are discarded. Full timeouts, certificate/TLS failures, authentication failures, `Retry-After`,
rate limits, and other response errors are not immediately retried. After the read fails, the
coordinator requests an earlier Home Assistant refresh at 60 seconds, 5 minutes, and 15 minutes,
then returns to the configured polling interval; server-provided `Retry-After` takes precedence.

Writes are not repeated after a transport failure, timeout, HTTP `408`, or `5xx`, because the
controller may already have accepted the command. Instead, one fresh scoped read reconciles the
requested values. A matching read confirms success, an old value reports an unconfirmed command,
and a failed read reports an unknown outcome. `Retry-After` suppresses that immediate reconciliation
read. Only an explicit authentication rejection can renew the session and replay once; the rejected
request was not accepted as an authenticated controller command.

A heating-zone mode uses:

```text
POST /R2/PlantHomeBsb/SetData/{gateway}
```

The body contains complete fresh `plantData` and `zoneData` snapshots plus
`viewModel.zoneNumber`. A mode change mutates only `zoneData.mode.value` inside that copied
snapshot.

Remocon's current-holiday workflow uses the same endpoint and complete snapshot. Creating a period
appends a holiday DTO with `index`, `fromAsIso` set to now, the inclusive `toAsIso` final day,
`added: true`, and the remaining command flags false. Updating changes only `toAsIso` and sets
`changed: true`. Cancelling sets `deleted: true`. Create/update also selects verified Automatic
mode `1`; cancellation preserves the resulting mode. Home Assistant deliberately does not expose
future start dates or arbitrary access to the controller's eight underlying slots.

Four reviewed heating-circuit settings use Remocon's BSB compare-and-set endpoint:

```text
POST /R2/PlantMenuBsb/WriteDataPoints/{gateway}
```

| Display line | Internal address | Writable value |
|---:|---:|---|
| 648 | `2950338` | Holiday operating level: Reduced or Frost protection |
| 714 | `2950546` | Frost-protection setpoint, additionally capped by fresh line 712 (`2950544`) |
| 720 | `2950646` | Heating-curve slope |
| 730 | `2950653` | Summer/winter heating limit |

Each request contains exactly one item with `address`, `oldOsv`, `oldValueAsString`,
`oldValueAsNumber`, `newOsv`, `newValueAsString`, and `newValueAsNumber`. The old values come from
an immediate `ReadDataPoints` call. A second immediate read must confirm the new numeric value even
after an HTTP success. Line 648's numeric value is selected by matching the server-provided enum
text; no numeric code is assumed. The API layer rejects every address outside this four-address
allowlist.

The command-path BSB reads use the same bounded safe-read policy as `GetData`: one transparent
authentication renewal and one immediate retry for a fast retryable transport interruption,
controller `Communication error`, or HTTP `408`, `500`, `502`, `503`, or `504`, within one overall
request deadline.
Timeouts, TLS failures, authentication failures after renewal, and responses with `Retry-After`
are not immediately retried. Background BSB discovery retains its isolated no-retry/no-session-
invalidation behavior. Neither policy ever retries a write.

## Change policy

- Do not expose arbitrary BSB writes.
- Preserve companion values required by atomic endpoints.
- Validate against limits and options returned by the gateway.
- Add captured, anonymized fixtures and tests before supporting another writable field.
- Treat an HTTP success as insufficient; a real-gateway test must confirm that the controller
  retains the value after a fresh read.
