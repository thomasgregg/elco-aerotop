# ELCO Aerotop for Home Assistant

Native Home Assistant custom integration for ELCO Aerotop heat pumps connected through
[Remocon.net](https://www.remocon-net.remotethermo.com/).

This project communicates with the structured JSON endpoints used by the Remocon.net web
application. It does not run a browser, scrape rendered HTML, publish MQTT discovery messages,
or require a Home Assistant add-on.

> [!IMPORTANT]
> This is an early development version. Reading is non-destructive. Temperature and domestic-hot-
> water writes use observed Remocon R2 request formats, but must still be tested against real ELCO
> gateways before the integration is considered stable.

## Current functionality

- UI-based setup with Remocon.net credentials and gateway ID
- Automatic session login and reauthentication support
- Coordinated cloud polling with a conservative five-minute default
- Outside and domestic-hot-water temperature sensors
- Heat-pump and zone-request binary sensors
- Per-zone comfort and reduced temperature controls
- Per-zone operating-mode controls
- Domestic-hot-water comfort/reduced temperature and mode controls
- Server-provided minimum, maximum, step, and allowed-option validation
- Redacted diagnostics

Not yet implemented:

- Heating/cooling schedules
- Advanced BSB parameters such as 700, 710, 712, 714, 720, and 730
- Energy statistics
- Formal compatibility testing across Aerotop models

## Installation for development

1. Copy `custom_components/elco_aerotop` into the Home Assistant `config/custom_components`
   directory.
2. Restart Home Assistant.
3. Go to **Settings → Devices & services → Add integration**.
4. Search for **ELCO Aerotop**.
5. Enter the Remocon.net email, password, and gateway ID.

The gateway ID is the final portion of a plant dashboard URL, for example:

```text
https://www.remocon-net.remotethermo.com/BsbPlantDashboard/Index/GATEWAY_ID
```

## Safety model

Every write:

1. Acquires a per-device command lock.
2. Fetches fresh plant and zone state.
3. Validates the requested value against limits reported by Remocon.net.
4. Preserves all companion values required by Remocon's full-state commands.
5. Refreshes Home Assistant state after the command.

The integration intentionally does not provide arbitrary BSB-address write services.

## Development

The reverse-engineered request contract and safety rules are recorded in
[`docs/protocol.md`](docs/protocol.md).

Run the API and parser tests:

```bash
python -m pytest
```

Run lint checks:

```bash
ruff check .
ruff format --check .
```

## Disclaimer

This is an unofficial community project and is not affiliated with ELCO or Ariston Group.
Remocon.net is a cloud service with an undocumented API, so upstream changes may break the
integration.
