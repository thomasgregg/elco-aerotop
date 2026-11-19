"""Writable temperatures for ELCO Aerotop."""

from __future__ import annotations

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory

from .bsb_controls import BSB_NUMBER_CONTROL_SPECS, BsbNumberControlSpec, bsb_point_number
from .capabilities import supports_cooling
from .const import SYSTEM_TYPE_GALEVO
from .coordinator import ElcoDataUpdateCoordinator
from .entity import ElcoAerotopEntity, async_setup_platform_entities
from .models import NumericVariable


class ElcoTemperatureNumber(ElcoAerotopEntity, NumberEntity):
    _attr_device_class = NumberDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_mode = NumberMode.SLIDER
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: ElcoDataUpdateCoordinator,
        key: str,
        name: str,
        variable_fn,
        write_fn,
        *,
        enabled_default: bool = True,
    ) -> None:
        super().__init__(coordinator, key)
        self._attr_name = name
        self._attr_entity_registry_enabled_default = enabled_default
        self._variable_fn = variable_fn
        self._write_fn = write_fn

    @property
    def _variable(self) -> NumericVariable:
        return self._variable_fn(self.coordinator.data)

    @property
    def native_value(self) -> float | None:
        return self._variable.value

    @property
    def native_min_value(self) -> float:
        return self._variable.minimum if self._variable.minimum is not None else 5.0

    @property
    def native_max_value(self) -> float:
        return self._variable.maximum if self._variable.maximum is not None else 65.0

    @property
    def native_step(self) -> float:
        return self._variable.step if self._variable.step is not None else 0.5

    async def async_set_native_value(self, value: float) -> None:
        await self._write_fn(value)


class ElcoBsbNumber(ElcoAerotopEntity, NumberEntity):
    """Control one reviewed scalar from Remocon's BSB settings menu."""

    _attr_mode = NumberMode.BOX
    _attr_entity_category = EntityCategory.CONFIG
    _attr_entity_registry_enabled_default = False

    def __init__(
        self,
        coordinator: ElcoDataUpdateCoordinator,
        spec: BsbNumberControlSpec,
    ) -> None:
        super().__init__(coordinator, spec.key)
        self._spec = spec
        self._attr_name = spec.name
        if spec.temperature:
            self._attr_device_class = NumberDeviceClass.TEMPERATURE
            self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
            self._attr_mode = NumberMode.SLIDER

    @property
    def native_value(self) -> float | None:
        return bsb_point_number(self.coordinator.data.discovery.bsb_points.get(self._spec.address))

    @property
    def native_min_value(self) -> float:
        return self._spec.minimum

    @property
    def native_max_value(self) -> float:
        maximum = self._spec.maximum
        if self._spec.maximum_address is not None:
            dynamic = bsb_point_number(
                self.coordinator.data.discovery.bsb_points.get(self._spec.maximum_address)
            )
            if dynamic is None and self.coordinator.data.zones:
                dynamic = next(iter(self.coordinator.data.zones.values())).reduced_temperature.value
            if dynamic is not None:
                maximum = min(maximum, dynamic)
        return maximum

    @property
    def native_step(self) -> float:
        return self._spec.step

    @property
    def available(self) -> bool:
        return super().available and self.native_value is not None

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set_bsb_number(self._spec.key, value)


class ElcoGalevoItemNumber(ElcoAerotopEntity, NumberEntity):
    """One writable Galevo data item exposed as a number.

    Bounds and step follow the item metadata the controller returns; the
    write goes through ``dataItems/{gw}/set`` and is blocked by
    ``GALEVO_WRITES_ENABLED`` until the contract is exercised.
    """

    # Slider for all item numbers — the controller's own bounds are
    # symmetric around 0 for offsets (e.g. -7..+7), so 0 sits centered.
    _attr_mode = NumberMode.SLIDER
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: ElcoDataUpdateCoordinator,
        key: str,
        item_id: str,
        zone_number: int,
        name: str,
        *,
        temperature: bool = True,
    ) -> None:
        super().__init__(coordinator, key)
        self._item_id = item_id
        self._zone_number = zone_number
        self._attr_name = name
        if temperature:
            self._attr_device_class = NumberDeviceClass.TEMPERATURE
            self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    def _item(self) -> dict | None:
        return self.coordinator.data.discovery.system_item(self._item_id, self._zone_number)

    @property
    def native_value(self) -> float | None:
        item = self._item()
        value = item.get("value") if item else None
        return float(value) if isinstance(value, int | float) else None

    @property
    def native_min_value(self) -> float:
        item = self._item()
        minimum = item.get("min") if item else None
        return float(minimum) if isinstance(minimum, int | float) else 0.0

    @property
    def native_max_value(self) -> float:
        item = self._item()
        maximum = item.get("max") if item else None
        return float(maximum) if isinstance(maximum, int | float) else 100.0

    @property
    def native_step(self) -> float:
        item = self._item()
        step = item.get("step") if item else None
        return float(step) if isinstance(step, int | float) and step > 0 else 0.5

    @property
    def available(self) -> bool:
        return super().available and self.native_value is not None

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set_galevo_data_item(
            f"{self._item_id}:{self._zone_number}",
            value,
        )


# Galevo per-zone items the APK exposes as user settings (dataItems/set).
# (item id, key/name suffix, temperature unit)
_GALEVO_ZONE_NUMBER_ITEMS: tuple[tuple[str, str, bool], ...] = (
    ("HeatingFlowTemp", "heating_flow_temperature", True),
    ("HeatingFlowOffset", "heating_flow_offset", False),
    ("CoolingFlowTemp", "cooling_flow_temperature", True),
    ("CoolingFlowOffset", "cooling_flow_offset", False),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities,
) -> None:
    async_setup_platform_entities(entry, async_add_entities, _create_entities)


def _create_entities(
    coordinator: ElcoDataUpdateCoordinator,
    async_add_entities,
) -> None:
    entities: list[NumberEntity] = []
    if coordinator.data.plant.dhw_comfort_temperature.value is not None:
        entities.append(
            ElcoTemperatureNumber(
                coordinator,
                "dhw_comfort_temperature",
                "Domestic hot water comfort temperature",
                lambda data: data.plant.dhw_comfort_temperature,
                lambda value: coordinator.async_set_dhw(comfort=value),
                enabled_default=False,
            )
        )
    if coordinator.data.plant.dhw_reduced_temperature.value is not None:
        entities.append(
            ElcoTemperatureNumber(
                coordinator,
                "dhw_reduced_temperature",
                "Domestic hot water reduced temperature",
                lambda data: data.plant.dhw_reduced_temperature,
                lambda value: coordinator.async_set_dhw(reduced=value),
            )
        )
    for zone_number in coordinator.data.zones:
        zone = coordinator.data.zones[zone_number]
        if zone.comfort_temperature.value is not None:
            entities.append(
                ElcoTemperatureNumber(
                    coordinator,
                    f"zone_{zone_number}_comfort_temperature",
                    f"Zone {zone_number} comfort temperature",
                    lambda data, zone=zone_number: data.zones[zone].comfort_temperature,
                    lambda value, zone=zone_number: coordinator.async_set_zone_temperature(
                        zone, "comfort", value
                    ),
                    enabled_default=False,
                )
            )
        if zone.reduced_temperature.value is not None:
            entities.append(
                ElcoTemperatureNumber(
                    coordinator,
                    f"zone_{zone_number}_reduced_temperature",
                    f"Zone {zone_number} reduced temperature",
                    lambda data, zone=zone_number: data.zones[zone].reduced_temperature,
                    lambda value, zone=zone_number: coordinator.async_set_zone_temperature(
                        zone, "reduced", value
                    ),
                )
            )
        if supports_cooling(coordinator.data.discovery.features, zone):
            if zone.cooling_comfort_temperature.value is not None:
                entities.append(
                    ElcoTemperatureNumber(
                        coordinator,
                        f"zone_{zone_number}_cooling_comfort_temperature",
                        f"Zone {zone_number} cooling comfort temperature",
                        lambda data, zone=zone_number: data.zones[zone].cooling_comfort_temperature,
                        lambda value, zone=zone_number: coordinator.async_set_zone_temperature(
                            zone, "cooling_comfort", value
                        ),
                        enabled_default=False,
                    )
                )
            if zone.cooling_reduced_temperature.value is not None:
                entities.append(
                    ElcoTemperatureNumber(
                        coordinator,
                        f"zone_{zone_number}_cooling_reduced_temperature",
                        f"Zone {zone_number} cooling reduced temperature",
                        lambda data, zone=zone_number: data.zones[zone].cooling_reduced_temperature,
                        lambda value, zone=zone_number: coordinator.async_set_zone_temperature(
                            zone, "cooling_reduced", value
                        ),
                    )
                )

    # BSB data-point number controls have no Galevo equivalent (and the
    # Galevo write paths are unverified), so they are only registered on
    # BSB/unknown families.
    if coordinator.data.zones and coordinator.data.discovery.system_type != SYSTEM_TYPE_GALEVO:
        entities.extend(
            ElcoBsbNumber(coordinator, spec) for spec in BSB_NUMBER_CONTROL_SPECS.values()
        )
    # Galevo data-item numbers (flow temperatures/offsets); item-gated so
    # only what the plant advertises as writable gets an entity.
    if coordinator.data.discovery.system_type == SYSTEM_TYPE_GALEVO:
        for zone_number in coordinator.data.zones:
            for item_id, suffix, is_temperature in _GALEVO_ZONE_NUMBER_ITEMS:
                item = coordinator.data.discovery.system_item(item_id, zone_number)
                if item is None or item.get("readOnly"):
                    continue
                label = suffix.replace("_", " ")
                entities.append(
                    ElcoGalevoItemNumber(
                        coordinator,
                        f"zone_{zone_number}_{suffix}",
                        item_id,
                        zone_number,
                        f"Zone {zone_number} {label}",
                        temperature=is_temperature,
                    )
                )
    async_add_entities(entities)
