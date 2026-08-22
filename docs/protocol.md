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
| System data | `POST /api/v2/remote/dataItems/{gateway}/get?umsys=si` |
| Native BSB plant snapshot | `GET /api/v2/remote/bsbPlantData/{gateway}` |
| Mobile menu items | `GET /api/v2/menuItems/{gateway}?menuItems={id}` for one feature-selected documented ID |
| Time programs | `GET /api/v2/remote/timeProgs/{gateway}/{program}?umsys=si` |
| Metering | `POST /R2/PlantMetering/GetData/{gateway}` |
| Maintenance | `GET /R2/PlantData/GetMaintenanceData?id={gateway}` |
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
