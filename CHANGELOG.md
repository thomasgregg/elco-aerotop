# Changelog

All notable changes to ELCO Aerotop for Home Assistant are documented here.

## 0.2.20

- Align all energy-history display names with the authenticated ELCO Remocon Energy meter page:
  **Fixed day**, **Yearly perf factor**, **Heat delivered**, **Refrigeration delivered**, and
  **Energy brought in**.
- Preserve existing entity IDs and unique IDs so the naming correction does not break entity
  history, dashboards, or automations.

## 0.2.19

- Add model-conditional sensors for BSB diagnostics lines **8786 – Flow temp cooling 2** and
  **8787 – Flow temp setp cooling 2**, using their verified structured JSON addresses.
- Query those datapoints only when the gateway advertises cooling; non-cooling plants record the
  family as `unsupported:feature` and do not create misleading zero-value entities.

## 0.2.18

- Parse the live annual-record date shape returned by `ReadDataPoints`
  (`YYYY/MM/DD *:*:*` with `fields: null`) while retaining the compound-field fallback for other
  firmware versions.
- Update the anonymized schedule and energy fixtures to the exact live BSB response envelopes
  observed after v0.2.17 installation.

## 0.2.17

- Split annual BSB energy history into ten independent eight-address reads after live v0.2.16
  validation proved that one 80-address request is rejected with a controller communication error.
- Read schedules, maintenance, monitoring, and native BSB plant data before annual history so an
  optional slow energy slot cannot suppress otherwise supported discovery families.
- Isolate the compound clock value from the schedule-default/holiday values for more precise
  capability diagnostics.

## 0.2.16

- Replace the non-responsive mobile schedule route with Remocon's verified BSB-native
  `PlantTimeProgBsb/GetData` JSON contract and its real heating, cooling, and DHW program IDs.
- Add ten annual energy-history records discovered from structured BSB menu metadata: fixed date,
  yearly performance factor, delivered heating/DHW/cooling energy, and input heating/DHW/cooling
  energy. Slot 1 is enabled by default; older slots are disabled by default but remain available.
- Add five automated-monitoring health sensors and a predictive-maintenance notice count from the
  structured monitoring drawer endpoint.
- Capture structured BSB boiler/appliance data, verified schedule-default values, clock time, and
  holiday operating level in redacted diagnostics.
- Keep all runtime reads JSON-only; no Remocon page content is parsed for entity state.

## 0.2.15

- Supersede 0.2.14 with the same BSB line 7000 maintenance support and a formatting-only test-file
  correction required by the repository validation workflow.

## 0.2.14

- Add four read-only diagnostic sensors for BSB line **7000 – Message**: maintenance code 1,
  priority 1, maintenance code 2, and priority 2.
- Use controller-internal address `327836`, verified from the authenticated Remocon BSB menu's
  structured metadata, and parse the returned multi-field JSON response by field name with a
  verified positional fallback.
- Isolate the maintenance datapoint in its own BSB discovery request so a controller timeout does
  not affect other heating-circuit or heat-pump values. Runtime polling remains JSON-only and does
  not scrape the website.

## 0.2.13

- Stop querying the Galevo-only `menuItems` API when `Features` reports controller system type 5
  (BSB). Live v0.2.12 diagnostics and the upstream client architecture confirmed that valid,
  individually token-authenticated menu IDs are not implemented for BSB plants.
- Report `unsupported:bsb_system` explicitly while retaining the complete native BSB plant
  snapshot, allowlisted BSB datapoints, system items, and error history.

## 0.2.12

- Read mobile menu items one at a time after live token-authenticated requests proved that one
  unsupported ID causes Remocon to reject an otherwise valid batch.
- Select model-specific catalog families from `Features`: VMC 133–191, SLP 192–220, hybrid
  221–250, heat pump 251–269, and cascade/BMS 270–274. All systems also probe the compact
  service/diagnostic range 119–130.
- Preserve every successful item and mark the aggregate `partially_available` when other IDs are
  unsupported by that gateway.

## 0.2.11

- Authenticate mobile-only menu-item reads with Remocon's separate JSON token flow. Live v0.2.10
  diagnostics proved that the valid batched endpoint still returns HTTP 500 when called with only
  the R2 browser cookie.
- Submit mobile credentials in a POST JSON body, retain the returned token only in memory, and send
  it only in the `ar.authToken` request header. Credentials and tokens are never placed in URLs,
  logs, or diagnostics.

## 0.2.10

- Read the documented mobile menu-item catalog in serialized batches of 15. Live v0.2.9
  diagnostics proved that Remocon returns HTTP 500 when all IDs are placed in one request.
- Exclude the undocumented gap at ID 132 and preserve successful batches if another catalog family
  is rejected, reporting `partially_available` instead of discarding all returned values.

## 0.2.9

- Move controller-bus, schedule, metering, maintenance, and broad mobile-API discovery into a
  managed background task after platform setup. Optional endpoint timeouts no longer hold the
  config entry in Home Assistant's startup path.
- Capture the complete structured `bsbPlantData` mobile response in redacted diagnostics.
- Query the bounded Remocon menu-item catalog (IDs 1–274) read-only and retain every item the
  configured gateway actually returns. This includes model-dependent signal, runtime, cycle,
  resistor, defrost, cooling, flow, return, compressor, and anti-legionella values.
- Expand `dataItems` discovery to the complete known plant and zone property lists, including
  automatic thermoregulation, anti-legionella, hybrid/buffer, quiet-mode, and virtual-zone fields.
- Keep all newly discovered fields diagnostic-only until a real gateway response establishes
  availability, type, unit, and semantics; existing write endpoints remain unchanged.

## 0.2.8

- Mark the backward-compatible read-only zone-mode sensor as diagnostic and disable it by default
  for new installations. The writable zone-mode select remains the primary state and control.

## 0.2.7

- Add a read-only Home Assistant calendar for each zone's controller-backed BSB holiday periods,
  including tolerant parsing of current and older Remocon date fields.
- Expose the per-zone holiday operating level and convert ELCO's inclusive final holiday day to
  Home Assistant's exclusive calendar end.
- Keep known BSB and capability-backed heat-pump entities registered while their current datapoint
  is out of service, marking them unavailable until a later successful read.
- Keep holiday-temperature entities registered when Remocon returns its inactive zero placeholder;
  their state remains unknown until the controller supplies an effective holiday temperature.
- Treat a returned datapoint without a value as unknown rather than unsupported.
- Correct the internal BSB mapping for heating-circuit lines 710 and 712 using the gateway values
  and ELCO controller documentation.
- Preserve the complete fresh plant and zone payload, including holiday fields, when changing a
  zone mode. Holiday create/update/delete writes remain disabled pending real round-trip fixtures.
- Disable overlapping low-level BSB 700–730 diagnostic sensors by default for new installations;
  normal number/select controls remain the primary entities.

## 0.2.6

- Treat BSB datapoints marked `osv` (out of service), failed, or carrying a bus/communication
  error as unavailable instead of exposing their placeholder zero as an entity.
- Render BSB enum values using the labels returned by Remocon, such as `Automatic`, rather than
  their numeric codes.
- Isolate the three plant BSB addresses individually after v0.2.5 diagnostics showed that the
  gateway rejects their combined request.

## 0.2.5

- Split read-only BSB discovery into independent heating-circuit, plant, and heat-pump JSON API
  requests. A controller rejecting one address group no longer hides values returned by another.
- Report per-group BSB availability in diagnostics and retain a combined `bsb_points` status.
- Confirm that discovery and entities remain JSON API based; no rendered device pages are scraped.

## 0.2.4

- Explicitly clear the legacy device-registry `configuration_url`, removing the **Visit** button
  from devices that were originally created by an earlier release.
- Allow up to 30 seconds for the controller-backed BSB discovery request; real gateway diagnostics
  showed that the previous 15-second optional-probe limit cancelled this request prematurely.

## 0.2.3

- Remove the device `configuration_url`. Home Assistant rendered it as a generic **Visit** button,
  which was unnecessary and unclear for this integration.

## 0.2.2

- Add the native plant-list endpoint for gateway serial, plant name, structured location, firmware,
  system type, link type, and MQTT API version.
- Show gateway serial and firmware in Home Assistant device information and add diagnostic sensors
  for gateway serial, plant name, plant location, and firmware when returned.
- Add controller-error count and problem entities from the verified bus-errors endpoint; current
  error records are available as bounded state attributes.
- Replace BSB display line numbers with the controller's internal read addresses and query known
  Aerotop flow, return, setpoint, gas, source, hot-gas, and pressure values.
- Serialize controller-bus probes to prevent overlapping BSB and schedule reads from timing out.
- Do not query metering when the gateway explicitly reports that metering is unsupported.
- Suppress zero-filled cooling, holiday, flame, and room-temperature placeholders using the real
  gateway capability flags captured in the anonymized one-zone fixture.
- Expand diagnostic redaction for plant names, locations, addresses, and every serial-like field.

## 0.2.1

- Prevent an authorization response from an optional discovery endpoint from incorrectly starting
  Home Assistant's reauthentication repair flow after core plant polling already succeeded.
- Disable authentication retries for optional probes. A genuine expired session is still detected
  and renewed by the next core plant request.

## 0.2.0

- Capture the complete `Features` and per-zone `PlantHomeBsb/GetData` response structures for
  redacted diagnostics and schema discovery.
- Add isolated, read-only probes for system data, heating/cooling/DHW schedules, metering,
  maintenance, controller errors, and allowlisted BSB parameters 700–730.
- Add per-endpoint availability reporting, 15-second optional-probe timeouts, and low-frequency
  refreshes so optional API differences cannot take core plant polling offline.
- Create new sensor and binary-sensor entities only when their source values are returned by the
  configured gateway.
- Add recursive identifier redaction, complete key-path/type inventory, bounded diagnostic lists,
  and an anonymized discovery fixture.
- Keep all newly discovered endpoint families read-only; existing writes remain isolated and
  unchanged.

## 0.1.1

- Identify integration requests with a dedicated user agent. Remocon rejects Home Assistant's
  default HTTP user agent with `403 Forbidden`, which previously appeared as “Unable to connect to
  Remocon.net” during setup.
- Add more useful debug logging for setup connection and unexpected-response failures.

## 0.1.0

- Initial native Home Assistant integration.
- Add Remocon authentication, coordinated polling, zone discovery, sensors, setpoint controls,
  operating-mode controls, diagnostics, and HACS packaging.
