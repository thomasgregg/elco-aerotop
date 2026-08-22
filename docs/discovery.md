# Gateway discovery and anonymized fixtures

Remocon's browser-facing API is undocumented and varies by controller, firmware, plant options,
and account. ELCO Aerotop therefore discovers values from the configured gateway and creates only
entities backed by values observed during the first coordinator refresh.

## Captured data

Home Assistant diagnostics contain:

- the full `Features` response and parsed feature map;
- plant-list metadata including serial, name, location, firmware, and system/link types;
- every full `PlantHomeBsb/GetData` response used for the current plant and zones;
- requested read-only system-data items;
- supported heating, cooling, and domestic-hot-water programs;
- metering, maintenance, and controller-error responses;
- values returned for allowlisted heating-circuit and Aerotop BSB datapoints, queried in isolated
  JSON API groups;
- a probe-status map showing `available`, `partially_available`, or an unavailable error class; and
- a `response_schema` inventory of every observed JSON path and Python value type.

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

An entity is added only when its source field or data item exists. The integration does not infer
support from a model name, invent default values, or expose whole payloads as state attributes.

Discovery does not authorize writes. A new writable field requires a separately reviewed endpoint
contract, an anonymized read/write fixture, validation against gateway-provided limits/options,
and a real-controller test proving that a fresh read retains the command. Arbitrary BSB writes are
out of scope.

Rendered Remocon pages are never used as an entity data source. The login handshake reads the
anti-forgery token required by Remocon, after which plant discovery, state polling, and commands use
JSON endpoints.
