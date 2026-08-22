# Changelog

All notable changes to ELCO Aerotop for Home Assistant are documented here.

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
