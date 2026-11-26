# Heat pump dashboard

Choose the example that suits your installation.

| Example | Layout and requirements |
|---|---|
| [BSB Heating dashboard](examples/heat-pump-dashboard-bsb.yaml) | The Heating layout shown in the README screenshot. Includes thermostat dials, a three-series hot-water graph, two activity timelines with a shared legend, gateway status, and maintenance tiles. Uses Mini Graph Card, Uptime Card, and Button Card. |
| [Native-card dashboard](examples/heat-pump-dashboard.yaml) | Uses Home Assistant's built-in cards for thermostat controls, history, status, and refresh. Works with BSB or Galevo entities where available. |

## Use the BSB Heating layout

Install [Mini Graph Card](https://github.com/kalkih/mini-graph-card),
[Uptime Card](https://github.com/dylandoamaral/uptime-card), and
[Button Card](https://github.com/custom-cards/button-card) through HACS and reload the
dashboard. The **Status** section shows two days in 36 bars per timeline, with a shared
**80 min/bar** legend for **On**, **Changed**, **Off**, and **No data**. Keep the legend
colors in sync with both timelines if you customize them. The native-card example uses
Home Assistant's History graph for activity and has no custom card requirements.

The refresh shortcut calls the integration's **Refresh** button. The example uses
placeholder entity IDs.
The [Shortcut card](https://www.home-assistant.io/dashboards/shortcut/) requires Home
Assistant 2026.5 or later. On older versions, copy the refresh button from the native example.

In addition to the shared entities listed below, the BSB example uses **Domestic hot water
reduced temperature**, **Zone 1 heating active**, and the two **Maintenance code** and
**Maintenance priority** sensors. Enable the heating-active diagnostic if your gateway
provides it. Remove any maintenance tiles or graph series the gateway lacks. The comfort line
reads the `temperature` attribute of the water heater, and the other two lines read the tank
sensor and reduced-temperature control.

## Add the dashboard

1. In Home Assistant, go to **Settings → Dashboards → Add dashboard** and create an empty
   dashboard. Open it and select **Edit dashboard**.
2. Open the three-dot menu and choose **Raw configuration editor**. Paste the complete
   YAML from your chosen example into the new dashboard. To add it as a view to a dashboard
   you already use, copy just the item under `views:` into that dashboard's existing
   `views:` list. Keep the indentation and choose a unique `path` for the view.
3. Replace every `elco_example_…` reference with the corresponding **Entity ID** from
   **Settings → Devices & services → ELCO Aerotop → Devices**. Replace repeated references
   in the graph and refresh action too. For a different heating zone, use that zone's
   thermostat and heat-request entities.
4. Remove any card or graph series your gateway lacks. Save and check that the temperature,
   activity, and last-update cards show values. Use the refresh button to request a poll.
   History graphs need Home Assistant Recorder history for the selected entities.

| Placeholder in the example | Entity to select on your ELCO device |
|---|---|
| `water_heater.elco_example_domestic_hot_water` | Domestic hot water control |
| `climate.elco_example_zone_1_thermostat` | Thermostat for the chosen heating zone |
| `sensor.elco_example_domestic_hot_water_temperature` | Measured domestic hot water temperature |
| `binary_sensor.elco_example_heat_pump_running` | Heat pump running |
| `binary_sensor.elco_example_zone_1_heat_request` | Heat request for the chosen zone |
| `binary_sensor.elco_example_gateway_online` | Gateway online |
| `sensor.elco_example_last_successful_update` | Last successful update |
| `button.elco_example_refresh` | Refresh |

The example suffixes use the language-independent defaults for new entities. Home Assistant
adds a prefix based on the device name. Existing installations can have localized or customized
IDs, so copy the actual ID from the entity settings. The [entity reference](entities.md) lists
the English names and the conditions for each entity.
On a cooling-capable system the request flag may cover both heating and cooling. The
thermostat's activity shows the current action when the controller provides it.

## Add target temperatures to the hot-water graph

On BSB, add the comfort and reduced hot-water number entities to the graph's `entities`
list if they are present and enabled. For Galevo, add **Domestic hot water target temperature**
to show the current mode's target. Use **Current** for the tank reading and **Target** for the
setpoint so users can tell them apart.

```yaml
              - entity: sensor.elco_example_domestic_hot_water_target_temperature
                name: Target
```

## Add Galevo mode and energy

Paste this optional section alongside the other entries under `sections:`. Keep the cards
for entities your device exposes. Add the SPF tile after the sensor appears on your device.
If the sensor was registered when its English name used JAZ, use its existing entity ID.
The heating and hot-water totals in the example are for a rolling window of
12 complete months.

```yaml
      - type: grid
        cards:
          - type: heading
            heading: Mode and energy
            icon: mdi:lightning-bolt
          - type: entities
            entities:
              - entity: select.elco_example_plant_mode
              - entity: sensor.elco_example_energy_heating_last_12_complete_months
              - entity: sensor.elco_example_energy_dhw_last_12_complete_months
          - type: tile
            name: Estimated SPF · previous calendar year
            entity: sensor.elco_example_estimated_spf_previous_calendar_year
```

## Add BSB maintenance and annual history

Add the section below if the device supplies these diagnostics. Open the annual performance
factor entity in the device's entity settings and enable it when you want it in the dashboard.
Enable the matching **Fixed day** sensor to see the date attached to that annual record.
Slot 1 is the newest record.

```yaml
      - type: grid
        cards:
          - type: heading
            heading: Maintenance and performance
            icon: mdi:wrench
          - type: entities
            entities:
              - entity: sensor.elco_example_maintenance_code_1
              - entity: sensor.elco_example_maintenance_code_2
              - entity: sensor.elco_example_maintenance_priority_1
              - entity: sensor.elco_example_maintenance_priority_2
              - entity: sensor.elco_example_annual_performance_factor_1
```

For holiday controls and the BSB calendar, see the [holiday dashboard examples](holidays.md).
