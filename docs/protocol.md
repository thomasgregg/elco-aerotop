# Remocon.net protocol notes

Remocon.net does not publish a supported public API for this equipment. These notes cover the
browser and mobile JSON endpoints used by the integration for BSB and Galevo controllers.
Never include credentials, session cookies, gateway IDs, or complete real-device responses in an
issue.

## Authentication

1. `GET /R2/Account/Login`
2. Read the hidden `__RequestVerificationToken` value.
3. Store it in the `__formRequestVerificationToken` cookie.
4. `POST /R2/Account/Login` as JSON with `email`, `password`, `rememberMe`, and `language`.

Each Home Assistant config entry owns an isolated cookie session. A `401`, `403`, or login-page
HTML response invalidates the session and causes one controlled reauthentication attempt.

Mobile endpoints such as `menuItems` require an independent token.

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

The integration reads `data.plant.systemType` to select the snapshot for each gateway.

| Controller | Plant and zone snapshot |
|---|---|
| BSB (`5`) | `POST /R2/PlantHomeBsb/GetData/{gateway}`. Each request selects a zone and requests the needed plant and zone blocks. |
| Galevo (`3`) | Mobile `dataItems/get` and `GET /api/v2/remote/zones/{gateway}/{zone}` for each configured zone. |

Commands start with uncached state. Redacted Home Assistant diagnostics include the `Features`
response and the current family's snapshot data for schema inspection.

## Optional read-only discovery

The following calls expand model discovery without enabling any corresponding write operation:

| Source | Controller | Method and path |
|---|---|---|
| Plant metadata | Both | `GET /api/v2/remote/plants/lite` |
| Live plant header | Both | `GET /R2/Plant/PlantHeader/{gateway}` |
| Controller errors | Both | `GET /api/v2/busErrors?gatewayId={gateway}&blockingOnly=False&culture=en-US` |
| System data items | BSB | `POST /api/v2/remote/dataItems/{gateway}/get?umsys=si`. Galevo uses this endpoint for its core snapshot. |
| Plant owner metadata | BSB | `GET /R2/PlantData/GetUserData?id={gateway}` |
| Native BSB plant snapshot | BSB | `GET /api/v2/remote/bsbPlantData/{gateway}` |
| Time programs | BSB | `POST /R2/PlantTimeProgBsb/GetData/{gateway}` with a program-ID filter |
| Web metering | Both, if advertised | `POST /R2/PlantMetering/GetData/{gateway}` |
| Maintenance | BSB | `GET /R2/PlantData/GetMaintenanceData?id={gateway}` |
| Automated monitoring | BSB | `GET /R2/AutomatedMonitoring/GetDrawerData/{gateway}` |
| Appliance data | BSB | `GET /R2/PlantData/GetBsbBoilerData?id={gateway}` |
| Controller datapoints | BSB | `GET /R2/PlantMenuBsb/ReadDataPoints/{gateway}?addresses=...` |
| Mobile menu items | Galevo | `GET /api/v2/menuItems/{gateway}?menuItems={id}` |
| Time programs | Galevo | `GET /api/v2/remote/timeProgs/{gateway}/{programId}` |
| Bus inventory | Galevo | `GET /api/v2/remote/plants/{gateway}/busDevices` |
| Consumption series | Galevo | `GET /api/v2/remote/reports/{gateway}/consSequencesApi8?usages=Ch,Dhw,Cooling&hasSlp=false` |

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

Galevo menu-item IDs come from a bounded mobile API catalog. The integration tries a batch and
falls back to individual IDs when the batch yields no usable results. Menu-item discovery is
read-only.

`menuItems` belongs to the Galevo controller family. When `Features` reports `systemType: 5` (BSB),
the integration skips that family and records `unsupported:bsb_system`; BSB discovery continues
through `bsbPlantData` and the reviewed `PlantMenuBsb/ReadDataPoints` allowlist.

## Galevo controllers (`systemType: 3`)

The BSB `GetData` snapshot does not exist on Galevo plants; the endpoint returns HTTP 500 even
after authentication. `Features` reports the controller family in `data.plant.systemType`
(APK `eGwSystemTypeDto`: Galileo1=1, Galileo2=2, Galevo=3, Velis=4, BSB=5, AC=10). When the
features response reports `3`, the coordinator builds plant and zone state from the mobile
data-item and zone endpoints.

The core snapshot uses the mobile `dataItems/get` and zone endpoints. The optional mobile
time-program probe uses IDs 1–6 for heating, 7 for domestic hot water, and 9–14 for cooling.

The R2 home-screen snapshot `PlantHome/GetData` was compared during development. A POST with
(`features` map including `gatewayId`, `filter: {notEssentials, plant, zone, dhw}`,
`useCache`, `zone`) returns `data.items` plus `hasAdvancedSettings`/`hasDhw`. The item shape
matches `dataItems`, additionally carrying localized `optTexts`, real min/max bounds and
`readOnly` flags. `filter.notEssentials: false` returns only the home-screen essentials;
`true` adds `ZoneHeatRequest`, `IsHeatingPumpOn`, `IsQuite`, `IsDhwBoost` and `IsResistorOn`.
The R2 route is recorded here as protocol evidence. The poll uses the mobile item catalog.

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
the poll uses `dataItems/get` and `zones/{n}`, plus a cached Features lookup.
R2 supplies Features and the metadata and header probes. Galevo uses the mobile plant-mode
write route with a fresh readback. The core cookie-authenticated item request renews its
R2 session once on an authentication rejection, independently of mobile token renewal.

Galevo enum values differ from the BSB constants used elsewhere in this document
(APK `eZoneMode`/`eDhwMode`): zone modes are `0=Off, 1=ManualNight, 2=Manual, 3=TimeProg` and DHW
modes are `0=Disabled, 1=Time Based, 2=Always active`. `ZoneState.from_galevo` normalizes them
onto the canonical model values (Off→off, ManualNight→reduced, Manual→comfort,
TimeProg→automatic; Disabled→off, Time Based→heat pump, Always active→performance).
Zones without a room probe can report a zero placeholder, which is suppressed when the
feature map explicitly reports no room sensor.

Verified against a live Galevo gateway (`tools/remocon_probe.py`, `captures/`): `dataItems`,
`zones/{1..3}`, `systemState`, `timeProgs/{1,7}`, `menuItems`, `busDevices`, `gatewayInfo`,
`actions/mostRecent`, `plants`, `plants/features`, and the shared R2 `Features` endpoint all
respond 200. `plantData/{mode,holiday,dhwTemp}` return HTTP 405 on GET (write-only paths).
The captured Galevo session could not read the BSB maintenance and automated-monitoring routes.
The integration uses the family-specific optional sources listed above and marks BSB-only
probes `unsupported:system_family` on Galevo.

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
  `eEnergyAccountPeriod` 1–5); empty on this plant.
- `POST /R2/PlantMetering/GetData/{gw}` is a read through the authenticated web session,
  with `{features, hasCooling}` in the request body. On Galevo, the coordinator reads it
  after publishing the regular discovery result, when `hasMetering` is true. The request
  has a 30-second budget including at most one web-session renewal and honors shared server
  backoff. It stores only the parsed factor and year for Galevo.
- The SPF input is the unrounded `asKwhRaw.histogramData` view. It uses the
  `PreviousYear` row in each combination of `ProducedEnergy` and `ConsumedElectricity`
  with `Heating` and `Dhw`. Each row must supply twelve matching monthly labels and
  numeric kWh values, and `oldestMonth` must reach January of the target year or earlier.
  See [ELCO web metering](https://github.com/macschlingel/ha-remocon/pull/13) and the
  [Ariston Galevo previous-year verification](https://github.com/wojciech-aniol/python-ariston-net-api/pull/9).
- `GET /api/v2/remote/reports/{gw}/dailyRoomTemp` → `[]`; `co2Report` → HTTP 599 (unsupported).
- `GET /api/v2/remote/plants/{gw}/consumptionsSettings` → 405 (write path).
  `POST .../getConsumptionsSettings` → `{currency, gasType, gasEnergyUnit, elecCost}`.
- Galevo write paths documented from the APK proxies:
  `POST dataItems/{gw}/set`, `zones/{gw}/{n}/{mode,temperatures,deroga,name}`,
  `plantData/{gw}/{mode,dhwTemp,dhwMode,dhwTimeProgTemperatures,holiday}`.
- `GET /api/v2/remote/reports/{gw}/consSequencesApi8?usages=Ch,Dhw,Cooling&hasSlp=false`
  → consumption time series `[{k, p, v}]`: `k` = `eEnergyUsageDto` value (1=heating, 2=DHW,
  3=cooling; the gateway also returns aggregates like 24=`ChHpResAggr`, 25=`DhwHpResAggr`),
  `p` = `eSequencePeriod` (1=last 24 h hourly, 2=last 7 days, 3=daily history,
  4=completed calendar months), `v` = kWh buckets, oldest first. The final monthly
  bucket matched the web portal's previous calendar month in the Galevo capture from
  November 2026. The daily series covered 56 days in that capture. The integration
  uses the final monthly bucket for the last complete month and the final 12 buckets
  for the last 12 complete months. [Galevo diagnostics](https://github.com/thomasgregg/elco-aerotop/issues/1#issuecomment-7191652268)
  Requesting detail usages the plant does not track → HTTP 500 (live probe 2026-11-19: `ProdChHeatingPump`,
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
mirrors the controller's holiday flag and backs both its diagnostic and Holiday switch.
The integration keeps explicit draft dates in memory and uses Home Assistant's configured
time zone for validation. It checks the returned expiry when available. Without it, a
transition to active can confirm a holiday starting today. A future schedule with no
confirming readback is reported as unconfirmed.

`SetDataItemDto` = the full `DataItemDto` as returned by `dataItems/get` plus
`prevValue` (the previous `value`) and `prevExpiresOn` (the previous `expiresOn`,
sent only when present — the gateway capture omits `expiresOn`, so null-tolerance
is unverified). Zone cooling setpoints (`ZoneComfortCoolingTemp`/`ZoneEconomyCoolingTemp`)
are data items — they write through `dataItems/set`, not the `temperatures` route.

Sources: `DataItemProxy.setDataItems`, `ZoneProxy.setMode`/`setTemperatures`,
`PlantDataProxy.set*` methods (`com.remotethermo.aristonnet.data.remote.proxies`),
`ChangeValueDto`/`TemperaturesDto`/`SetDataItemsRequest`/`SetDataItemDto` DTOs.

Galevo supports the controls listed in the [entity guide](entities.md). Recorded hardware checks
cover plant mode, scheduled hot-water comfort, and the heating thermostat setpoint. The broader
controls have Home Assistant regression coverage with mocked responses. Commands use a fresh read,
gateway-provided limits where available, and serialized readback checks.

Zone setpoint writes require fresh `PlantMode` and both values for the active season. On one Galevo
gateway, writing `ZoneComfortCoolingTemp` in Winter changed both `ZoneComfortTemp` and the effective
target (`ZoneDesiredTemp`). Changing only cooling comfort in Remocon R2 also changed the value shown
in its separate heating comfort menu. The two comfort settings are coupled on the tested gateway.
Whether reduced setpoints are coupled is still unknown. The integration requires the active season
for the thermostat and standalone comfort and reduced controls.
[Original hardware report](https://github.com/thomasgregg/elco-aerotop/issues/3#issuecomment-7079412225)
[Remocon retest](https://github.com/thomasgregg/elco-aerotop/issues/3#issuecomment-7095033094)

Hot-water writes honor `readOnly` and `ro` on changed setpoints. A combined hot-water request
validates the destination setpoint before switching modes and again after confirming the mode.
It reports a partial result when only the mode changes. BSB uses its existing endpoints.

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
directions (Winter→Summer→Winter) on 2026-11-19. The integration uses this mobile route and
confirms the result with an uncached read. The browser-app contract shown above uses
`{features, prevItems, viewModel}` and returns `changedItems`. `viewModel` carries the semantic fields
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
- The Galevo item catalog is queried only on Galevo. BSB retains its original optional
  catalog and request language.
- R2 view POST bodies (`PlantAdvancedSettings/Index`, `PlantDhw/Index`, …) are **never
  hardcoded**: build them from the live `Features/{gw}` response
  (`build_r2_view_body` in `tools/remocon_probe.py` whitelists the key set the web app
  sends and enriches zone names from `ZoneName` items). The web app's client-side
  enrichment of `virtInfo`, `dhwProgSupported`, `dhwHidden`, `dhwBoilerPresent`,
  `hvInputOff` and `hydraulicScheme` is unverified — Features reports them as `null`
  and they are passed through unchanged.

## BSB write contracts

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

Both families serialize polling, discovery, and writes through a gateway-traffic lock. A command
cancels optional discovery before its uncached read and write. A BSB zone write reads the affected
zone, and a BSB hot-water write requests plant state through one known zone.

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
