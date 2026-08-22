# Changelog

All notable changes to ELCO Aerotop for Home Assistant are documented here.

## 0.1.1

- Identify integration requests with a dedicated user agent. Remocon rejects Home Assistant's
  default HTTP user agent with `403 Forbidden`, which previously appeared as “Unable to connect to
  Remocon.net” during setup.
- Add more useful debug logging for setup connection and unexpected-response failures.

## 0.1.0

- Initial native Home Assistant integration.
- Add Remocon authentication, coordinated polling, zone discovery, sensors, setpoint controls,
  operating-mode controls, diagnostics, and HACS packaging.
