# Changelog

All notable changes to ELCO Aerotop for Home Assistant are documented here.

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
