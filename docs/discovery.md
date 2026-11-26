# Gateway discovery and anonymized fixtures

ELCO Aerotop discovers BSB and Galevo gateways through Remocon's JSON API. It combines each
gateway's feature flags with the values it returns. Each gateway has its own Home Assistant
entry, session, and coordinator, including when two gateways share a Remocon account.
Available entities can vary by controller, firmware, and installed options. Registered
entities can recover after a missing reading.

## Captured data

Home Assistant diagnostics include the gateway's `Features` response, plant metadata and status,
controller errors, supported weekly programs, probe status, and an inventory of observed JSON
paths and value types in `response_schema`.

| Source | BSB | Galevo |
|---|---|---|
| Plant and zones | `PlantHomeBsb/GetData` snapshots and optional system data items | Mobile `dataItems` and per-zone responses |
| Weekly programs | `PlantTimeProgBsb/GetData` | Mobile `timeProgs` |
| Controller details | Plant-owner payload, maintenance, automated monitoring, `bsbPlantData`, and boiler details | Mobile menu items and bus device inventory |
| Energy | Cloud metering and ten fixed annual records when returned | Heating, hot-water, and cooling consumption series, plus the previous-year estimate from supported web metering |
| Extra datapoints | Isolated, allowlisted groups for heating-circuit settings, line 7000 messages, and heat-pump data | Returned mobile fields and feature-selected menu items |

Galevo plants with `data.discovery.features.hasEm20: true` also read a five-item status
batch after regular core polling. Returned items appear in `data.discovery.menu_items`
alongside the slower settings catalog. Check keys `337`, `340`, `341`, `348`, and `349`.
A missing key means the item was omitted or the status read did not complete. A returned
item can still be marked unavailable or hold a value the integration cannot use.
See the [EM20 protocol notes](protocol.md#optional-read-only-discovery).

Slow controller-backed families run after the first valid core snapshot, including after
recovery from a failed first poll. A pending entry can export redacted diagnostics.
Optional reads use `deferred:background` until a result is available.

Deferred requests are serialized with core polling but release the gateway lock between probes.
The pass stops early on a connection failure, on an HTTP rate limit, or after two consecutive
gateway-level `502`–`504` responses. Probe timeouts also stop the pass, except for the isolated
source outlet minimum read (line 8430). A `500` from one optional endpoint is treated as
endpoint-specific and does not open the circuit. `Retry-After` postpones the next optional pass;
otherwise later discovery can recover without affecting core entity availability.

BSB annual history is queried independently of cloud metering. Gateways can expose the fixed
records while reporting `hasMetering: false`. The diagnostic export redacts Galevo bus-device
serials, including the vendor's `sn` field.

Galevo publishes its other optional data before attempting web metering. This read follows
the deferred discovery schedule and contributes only the estimated factor and year. The
`metering` probe status records failures or missing full-year coverage independently of
the gateway's current heating and hot-water readings.

The diagnostics implementation recursively redacts configured credentials and gateway IDs,
common identity and location fields, serial numbers, technician details, and matching identifiers
embedded in object keys. Arrays longer than 100 items are truncated in the value snapshot; the
schema inventory is generated before truncation so keys in later items are still represented.

## Contributing a gateway fixture

1. Install a version that supports your controller family and restart Home Assistant.
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

BSB holiday periods come from each zone's `holidays` array. The read-only calendar accepts
`fromAsIso`/`toAsIso` and older `from`/`to` fields. The date entity and cancel button manage the
current zone holiday. Galevo uses a plant-wide `Holiday` flag and two local date drafts. The
switch submits or cancels the draft using the plant holiday endpoint. [Holiday guide](holidays.md)

Discovery does not authorize writes. A new writable field requires a separately reviewed endpoint
contract, an anonymized read/write fixture, validation against gateway-provided limits/options,
and a real-controller test proving that a fresh read retains the command. Arbitrary BSB writes are
out of scope.

Plant discovery, state polling, and commands use JSON endpoints. The browser login handshake
obtains the anti-forgery token. Mobile endpoints use a separate login token when required.
