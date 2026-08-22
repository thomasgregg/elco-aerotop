# Gateway discovery and anonymized fixtures

Remocon's JSON API is undocumented and varies by controller, firmware, plant options, and account.
ELCO Aerotop therefore combines the gateway's feature flags with observed JSON fields. Stable
capability-backed entities remain registered across temporary out-of-service or missing readings;
features explicitly reported as unsupported are omitted.

## Captured data

Home Assistant diagnostics contain:

- the full `Features` response and parsed feature map;
- plant-list metadata including serial, name, location, firmware, and system/link types;
- every full `PlantHomeBsb/GetData` response used for the current plant and zones;
- requested read-only system-data items;
- the complete native mobile `bsbPlantData` snapshot;
- every returned item from the base service range and feature-selected mobile menu families;
- supported heating, cooling, and domestic-hot-water programs;
- metering, maintenance, and controller-error responses;
- values returned for allowlisted heating-circuit and Aerotop BSB datapoints, queried in isolated
  JSON API groups;
- the four fields returned by BSB line 7000 at verified internal address `327836`, queried in its
  own isolated read-only group;
- a probe-status map showing `available`, `partially_available`, or an unavailable error class; and
- a `response_schema` inventory of every observed JSON path and Python value type.

Slow controller-backed families run after config-entry and entity-platform setup. Their initial
diagnostic status is `deferred:background` and changes to `available`, `partially_available`, or an
error class when the background pass completes. This prevents an optional schedule or controller
timeout from delaying Home Assistant startup.

The diagnostics implementation recursively redacts configured credentials and gateway IDs,
common identity and location fields, serial numbers, technician details, and matching identifiers
embedded in object keys. Arrays longer than 100 items are truncated in the value snapshot; the
schema inventory is generated before truncation so keys in later items are still represented.

## Contributing a gateway fixture

1. Install the latest release and restart Home Assistant.
2. Open **Settings → Devices & services → ELCO Aerotop → device**.
3. Select **Download diagnostics**.
4. Open the JSON file locally and inspect it for anything you do not want to share.
5. Attach the inspected file to a private development conversation or a GitHub issue.

Do not attach logs containing login requests, cookies, credentials, or an unredacted gateway ID.
A contributed diagnostic should be reduced to stable API examples and saved under
`tests/fixtures/` with synthetic identifiers before it is committed.

## Entity and write policy

Entities backed by an explicit feature flag are added when that capability is advertised. Other
optional entities require their source field or datapoint to be observed. An explicit BSB
out-of-service/error flag marks an existing entity unavailable; a successfully returned datapoint
without a value is represented as unknown. The integration does not invent state values or expose
whole payloads as state attributes.

BSB holiday periods are parsed from the per-zone `holidays` array and exposed through a read-only
Home Assistant calendar. Current `fromAsIso`/`toAsIso` and older `from`/`to` field names are
accepted. Holiday writes remain disabled until populated read/write captures verify index reuse,
date normalization, and create/update/delete round trips.

Discovery does not authorize writes. A new writable field requires a separately reviewed endpoint
contract, an anonymized read/write fixture, validation against gateway-provided limits/options,
and a real-controller test proving that a fresh read retains the command. Arbitrary BSB writes are
out of scope.

Rendered Remocon pages are never used as an entity data source. The login handshake reads the
anti-forgery token required by Remocon, after which plant discovery, state polling, and commands use
JSON endpoints.
