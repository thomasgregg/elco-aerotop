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
uses a code-reviewed allowlist of controller-internal datapoint IDs and keeps the display line in
the Home Assistant entity name. Optional calls run independently from core
polling, have their own time bound, and expose an availability status in diagnostics. A `404`,
changed response shape, or other endpoint-specific failure must not disable normal plant/zone
updates.

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

## Writing state

Heating comfort and reduced setpoints are an atomic pair:

```text
POST /R2/PlantTimeProgBsb/SetTemperature/{gateway}
```

The body contains `zoneNum`, both temperatures, `plantData: null`, and the full fresh `zoneData`
snapshot. Sending the temperatures through `PlantHomeBsb/SetData` can return success without
propagating the values to the controller, so this integration does not do that.

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

The body intentionally includes only the DHW values required by the endpoint, the selected zone
and mode, and `viewModel.zoneNumber`. Unrelated writable fields are omitted.

## Change policy

- Do not expose arbitrary BSB writes.
- Preserve companion values required by atomic endpoints.
- Validate against limits and options returned by the gateway.
- Add captured, anonymized fixtures and tests before supporting another writable field.
- Treat an HTTP success as insufficient; a real-gateway test must confirm that the controller
  retains the value after a fresh read.
