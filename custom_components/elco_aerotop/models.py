"""Data models and tolerant Remocon response parsing."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any


def _first(mapping: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return default


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _boolean(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "on", "yes", "1"}:
            return True
        if normalized in {"false", "off", "no", "0"}:
            return False
    return None


def _holiday_date(value: Any) -> date | None:
    """Parse the date shapes used by current and older BSB holiday responses."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, int | float) and not isinstance(value, bool):
        if value == 0:
            return None
        seconds = value / 1000 if abs(value) > 100_000_000_000 else value
        try:
            return datetime.fromtimestamp(seconds, tz=UTC).date()
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(value, str) or not value.strip():
        return None

    rendered = value.strip()
    dotnet_epoch = re.fullmatch(r"/Date\((-?\d+)(?:[+-]\d{4})?\)/", rendered)
    if dotnet_epoch:
        return _holiday_date(int(dotnet_epoch.group(1)))
    try:
        return datetime.fromisoformat(rendered.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(rendered[:10])
        except ValueError:
            return None


def bsb_point_value(point: Any) -> Any:
    """Return a BSB value, translating a numeric enum to its server label."""
    if not isinstance(point, dict):
        return None

    for key in ("valueAsString", "textualValue", "text"):
        if point.get(key) not in (None, ""):
            return point[key]

    numeric_value = point.get("valueAsNumber")
    options = point.get("enumOptions")
    if numeric_value is not None and isinstance(options, list):
        for option in options:
            if isinstance(option, dict) and option.get("value") == numeric_value:
                return option.get("text", numeric_value)

    value = point.get("value", numeric_value)
    if isinstance(value, dict):
        return value.get("value", value.get("text"))
    return value


def bsb_point_field_value(point: Any, name: str, index: int) -> Any:
    """Return a named value from a multi-field BSB datapoint.

    Remocon currently returns line 7000 as an ordered ``fields`` array.  Name
    matching is preferred so the integration remains correct if field order
    changes; the verified field position is retained as a tolerant fallback.
    """
    if not isinstance(point, dict):
        return None
    fields = point.get("fields")
    if not isinstance(fields, list):
        return None

    normalized_name = " ".join(name.casefold().split())
    for item in fields:
        if not isinstance(item, dict):
            continue
        field_name = item.get("name", item.get("label"))
        if (
            isinstance(field_name, str)
            and " ".join(field_name.casefold().split()) == normalized_name
        ):
            return bsb_point_value(item)

    if 0 <= index < len(fields) and isinstance(fields[index], dict):
        return bsb_point_value(fields[index])
    return None


def bsb_point_date(point: Any) -> date | None:
    """Return the calendar date stored in a compound BSB datapoint."""
    if not isinstance(point, dict):
        return None
    fields = point.get("fields")
    if isinstance(fields, list):
        components: dict[str, int] = {}
        positional = ("yyyy", "MM", "dd")
        for index, field_name in enumerate(positional):
            value = bsb_point_field_value(point, field_name, index)
            try:
                components[field_name] = int(value)
            except (TypeError, ValueError):
                break
        else:
            try:
                return date(components["yyyy"], components["MM"], components["dd"])
            except ValueError:
                pass

    # Live ReadDataPoints responses flatten compound fixed-day values to a
    # localized string such as ``2026/06/30 *:*:*`` and return ``fields: null``.
    rendered = bsb_point_value(point)
    if not isinstance(rendered, str):
        return None
    match = re.match(r"^\s*(\d{4})[/.\-](\d{1,2})[/.\-](\d{1,2})(?:\s|$)", rendered)
    if match is None:
        return None
    try:
        return date(*(int(component) for component in match.groups()))
    except ValueError:
        return None


def bsb_point_datetime(point: Any) -> datetime | None:
    """Return the controller-local date and time stored in a BSB datapoint."""
    if not isinstance(point, dict):
        return None
    fields = point.get("fields")
    if isinstance(fields, list):
        components: dict[str, int] = {}
        positional = ("yyyy", "MM", "dd", "HH", "mm", "ss")
        for index, field_name in enumerate(positional):
            # Month (``MM``) and minute (``mm``) differ only by case, so the
            # general case-insensitive BSB field helper is not suitable here.
            matching_field = next(
                (
                    field
                    for field in fields
                    if isinstance(field, dict)
                    and field.get("name", field.get("label")) == field_name
                ),
                None,
            )
            if matching_field is None and index < len(fields) and isinstance(fields[index], dict):
                matching_field = fields[index]
            value = bsb_point_value(matching_field)
            try:
                components[field_name] = int(value)
            except (TypeError, ValueError):
                break
        else:
            try:
                return datetime(
                    components["yyyy"],
                    components["MM"],
                    components["dd"],
                    components["HH"],
                    components["mm"],
                    components["ss"],
                )
            except ValueError:
                pass

    # ReadDataPoints commonly flattens the clock to ``YYYY/MM/DD HH:mm:ss``.
    rendered = bsb_point_value(point)
    if not isinstance(rendered, str):
        return None
    match = re.match(
        r"^\s*(\d{4})[/.-](\d{1,2})[/.-](\d{1,2})[ T](\d{1,2}):(\d{1,2}):(\d{1,2})(?:\s|$)",
        rendered,
    )
    if match is None:
        return None
    try:
        year, month, day, hour, minute, second = (int(component) for component in match.groups())
        return datetime(year, month, day, hour, minute, second)
    except ValueError:
        return None


def bsb_point_available(point: Any) -> bool:
    """Return whether Remocon reports a currently readable BSB datapoint."""
    if not isinstance(point, dict):
        return False
    if any(point.get(flag) is True for flag in ("osv", "anyError", "deviceFailure")):
        return False
    for error_code in ("bsbErrorCode", "commErrorCode"):
        if point.get(error_code) not in (None, 0, "0"):
            return False
    return True


@dataclass(frozen=True, slots=True)
class Option:
    """A selectable numeric Remocon option."""

    value: int
    label: str


@dataclass(frozen=True, slots=True)
class NumericVariable:
    """A numeric value with constraints supplied by Remocon."""

    value: float | None
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None

    @classmethod
    def parse(cls, raw: Any) -> NumericVariable:
        if not isinstance(raw, dict):
            return cls(value=_number(raw))
        return cls(
            value=_number(raw.get("value")),
            minimum=_number(raw.get("min")),
            maximum=_number(raw.get("max")),
            step=_number(raw.get("step")),
        )

    def validate(self, value: float) -> None:
        if self.minimum is not None and value < self.minimum:
            raise ValueError(f"Value {value} is below the minimum {self.minimum}")
        if self.maximum is not None and value > self.maximum:
            raise ValueError(f"Value {value} is above the maximum {self.maximum}")
        if self.step and self.minimum is not None:
            steps = (value - self.minimum) / self.step
            if abs(steps - round(steps)) > 1e-6:
                raise ValueError(f"Value {value} does not align with step {self.step}")


@dataclass(frozen=True, slots=True)
class SelectVariable:
    """A selectable value with server-provided options."""

    value: int | None
    options: tuple[Option, ...] = ()

    @classmethod
    def parse(cls, raw: Any) -> SelectVariable:
        if not isinstance(raw, dict):
            number = _number(raw)
            return cls(value=int(number) if number is not None else None)

        value = _number(raw.get("value"))
        parsed_value = int(value) if value is not None else None
        options: list[Option] = []

        allowed = raw.get("allowedOptions")
        labels = raw.get("allowedOptionTexts") or raw.get("optTexts") or []
        if isinstance(allowed, list):
            for index, option_value in enumerate(allowed):
                if not isinstance(option_value, int | float):
                    continue
                label = labels[index] if index < len(labels) else str(int(option_value))
                options.append(Option(int(option_value), str(label)))

        legacy_options = raw.get("options")
        if not options and isinstance(legacy_options, list):
            for option in legacy_options:
                if isinstance(option, dict) and isinstance(option.get("value"), int | float):
                    options.append(
                        Option(
                            int(option["value"]),
                            str(option.get("text", option["value"])),
                        )
                    )
                elif isinstance(option, int | float):
                    options.append(Option(int(option), str(int(option))))

        return cls(value=parsed_value, options=tuple(options))

    @property
    def current_label(self) -> str | None:
        return next((option.label for option in self.options if option.value == self.value), None)

    def value_for_label(self, label: str) -> int:
        for option in self.options:
            if option.label == label:
                return option.value
        raise ValueError(f"Unsupported option: {label}")


# Galevo zone modes (APK eZoneMode: Off=0, ManualNight=1, Manual=2, TimeProg=3)
# normalized onto the canonical values used by control_mapping
# (Protection=0, Automatic=1, Reduced=2, Comfort=3).
_GALEVO_ZONE_MODE_MAP: dict[int, int] = {0: 0, 1: 2, 2: 3, 3: 1}

# Galevo DHW modes (0=Disabled, 1=Time Based, 2=Always active) normalized onto
# the canonical DHW values (0=off, 1=on, 3=performance). Time Based is the
# regular scheduled mode (comfort inside the program windows, reduced outside)
# and maps to "on"; Always active keeps the comfort temperature permanently,
# which is the strongest mode -> "performance". None of the three modes means
# a permanent reduced setpoint, so canonical "eco" is unused on Galevo.
_GALEVO_DHW_MODE_MAP: dict[int, int] = {0: 0, 1: 1, 2: 3}

# Fallback labels when a Galevo enum item arrives without optTexts (APK eZoneMode).
_GALEVO_ZONE_MODE_LABELS: dict[int, str] = {
    0: "Off",
    1: "Manual night",
    2: "Manual",
    3: "Time program",
}
_GALEVO_DHW_MODE_LABELS: dict[int, str] = {
    0: "Disabled",
    1: "Time Based",
    2: "Always active",
}

# Inverse maps for writes: canonical -> raw Galevo enum values. Both maps are
# bijective, so the inverse is unambiguous.
GALEVO_ZONE_MODE_TO_RAW: dict[int, int] = {v: k for k, v in _GALEVO_ZONE_MODE_MAP.items()}
GALEVO_DHW_MODE_TO_RAW: dict[int, int] = {v: k for k, v in _GALEVO_DHW_MODE_MAP.items()}


def _galevo_item(
    items: dict[str, dict[str, Any]],
    item_id: str,
    zone: int = 0,
) -> dict[str, Any] | None:
    """Return one remote dataItems entry, keyed as ``<id>:<zone>``."""
    item = items.get(f"{item_id}:{zone}")
    return item if isinstance(item, dict) else None


def _galevo_value(item: dict[str, Any] | None) -> float | None:
    if item is None or item.get("invalid") is True:
        return None
    return _number(item.get("value"))


def _galevo_bool(item: dict[str, Any] | None) -> bool | None:
    if item is None:
        return None
    return _boolean(item.get("value"))


def _galevo_numeric(item: dict[str, Any] | None) -> NumericVariable:
    if not isinstance(item, dict):
        return NumericVariable(value=None)
    return NumericVariable(
        value=_galevo_value(item),
        minimum=_number(item.get("min")),
        maximum=_number(item.get("max")),
        step=_number(item.get("step")),
    )


def _galevo_select(
    item: dict[str, Any] | None,
    remap: dict[int, int] | None = None,
    default_labels: dict[int, str] | None = None,
) -> SelectVariable:
    """Build a SelectVariable from a dataItems enum entry (options + optTexts)."""
    if not isinstance(item, dict):
        return SelectVariable(value=None)
    raw_value = _galevo_value(item)
    value = int(raw_value) if raw_value is not None else None
    options: list[Option] = []
    allowed = item.get("options")
    labels = item.get("optTexts") or []
    if isinstance(allowed, list):
        for index, option_value in enumerate(allowed):
            if not isinstance(option_value, int | float):
                continue
            raw_int = int(option_value)
            if index < len(labels):
                label = str(labels[index])
            elif default_labels and raw_int in default_labels:
                label = default_labels[raw_int]
            else:
                label = str(raw_int)
            options.append(Option(raw_int, label))
    if remap:
        value = remap.get(value) if value is not None else None
        options = [
            Option(remap[option.value], option.label) for option in options if option.value in remap
        ]
    return SelectVariable(value=value, options=tuple(options))


def _galevo_error(item: dict[str, Any] | None) -> bool | None:
    if item is None:
        return None
    return item.get("error") is True or item.get("invalid") is True


@dataclass(frozen=True, slots=True)
class BsbHoliday:
    """One controller-backed holiday period for a heating zone."""

    index: int | None
    start: date
    end: date
    added: bool = False
    changed: bool = False
    deleted: bool = False
    out_of_service: bool = False

    @classmethod
    def parse(cls, raw: Any) -> BsbHoliday | None:
        """Parse current Remocon and older API holiday field names."""
        if not isinstance(raw, dict):
            return None

        def parse_first(*keys: str) -> date | None:
            for key in keys:
                if (parsed := _holiday_date(raw.get(key))) is not None:
                    return parsed
            return None

        start = parse_first("fromAsIso", "from", "start", "fromAsEpoch")
        end = parse_first("toAsIso", "to", "end", "toAsEpoch")
        if start is None or end is None or end < start:
            return None
        parsed_index = _number(raw.get("index"))
        return cls(
            index=int(parsed_index) if parsed_index is not None else None,
            start=start,
            end=end,
            added=_boolean(raw.get("added")) is True,
            changed=_boolean(raw.get("changed")) is True,
            deleted=_boolean(raw.get("deleted")) is True,
            out_of_service=_boolean(raw.get("osv")) is True,
        )


@dataclass(frozen=True, slots=True)
class PlantState:
    """Plant-level state."""

    raw: dict[str, Any]
    outside_temperature: float | None
    heat_pump_on: bool | None
    flame_on: bool | None
    dhw_enabled: bool | None
    dhw_current_temperature: float | None
    outside_temperature_error: bool | None
    dhw_temperature_error: bool | None
    has_outside_temperature_probe: bool | None
    has_dhw_temperature_probe: bool | None
    dhw_comfort_temperature: NumericVariable
    dhw_reduced_temperature: NumericVariable
    dhw_mode: SelectVariable

    @classmethod
    def parse(cls, raw: dict[str, Any]) -> PlantState:
        return cls(
            raw=raw,
            outside_temperature=_number(_first(raw, "outsideTemp", "outTemp")),
            heat_pump_on=_boolean(_first(raw, "heatPumpOn", "hpOn")),
            flame_on=_boolean(_first(raw, "flameSensor", "flame")),
            dhw_enabled=_boolean(raw.get("dhwEnabled")),
            dhw_current_temperature=_number(_first(raw, "dhwStorageTemp", "dhwTemp")),
            outside_temperature_error=_boolean(raw.get("outsideTempError")),
            dhw_temperature_error=_boolean(raw.get("dhwStorageTempError")),
            has_outside_temperature_probe=_boolean(raw.get("hasOutsideTempProbe")),
            has_dhw_temperature_probe=_boolean(raw.get("hasDhwStorageProbe")),
            dhw_comfort_temperature=NumericVariable.parse(
                _first(raw, "dhwComfortTemp", "dhwComfTemp", default={})
            ),
            dhw_reduced_temperature=NumericVariable.parse(
                _first(raw, "dhwReducedTemp", "dhwReduTemp", default={})
            ),
            dhw_mode=SelectVariable.parse(raw.get("dhwMode", {})),
        )

    @classmethod
    def from_galevo(cls, items: dict[str, dict[str, Any]]) -> PlantState:
        """Build plant state from a Galevo remote dataItems response."""
        dhw_mode = _galevo_select(
            _galevo_item(items, "DhwMode"),
            remap=_GALEVO_DHW_MODE_MAP,
            default_labels=_GALEVO_DHW_MODE_LABELS,
        )
        return cls(
            raw={"dataItems": items},
            outside_temperature=_galevo_value(_galevo_item(items, "OutsideTemp")),
            heat_pump_on=_galevo_bool(_galevo_item(items, "IsHeatingPumpOn")),
            flame_on=_galevo_bool(_galevo_item(items, "IsFlameOn")),
            dhw_enabled=None if dhw_mode.value is None else dhw_mode.value != 0,
            dhw_current_temperature=_galevo_value(
                _galevo_item(items, "DhwStorageTemperature") or _galevo_item(items, "DhwTemp")
            ),
            outside_temperature_error=_galevo_error(_galevo_item(items, "OutsideTemp")),
            dhw_temperature_error=_galevo_error(
                _galevo_item(items, "DhwStorageTemperature") or _galevo_item(items, "DhwTemp")
            ),
            has_outside_temperature_probe=None,
            has_dhw_temperature_probe=None,
            dhw_comfort_temperature=_galevo_numeric(
                _galevo_item(items, "DhwTimeProgComfortTemp") or _galevo_item(items, "DhwTemp")
            ),
            dhw_reduced_temperature=_galevo_numeric(_galevo_item(items, "DhwTimeProgEconomyTemp")),
            dhw_mode=dhw_mode,
        )


@dataclass(frozen=True, slots=True)
class ZoneState:
    """Heating-zone state."""

    number: int
    raw: dict[str, Any]
    holidays: tuple[BsbHoliday, ...]
    mode: SelectVariable
    comfort_temperature: NumericVariable
    reduced_temperature: NumericVariable
    cooling_comfort_temperature: NumericVariable
    cooling_reduced_temperature: NumericVariable
    heating_protection_temperature: float | None
    cooling_protection_temperature: float | None
    heating_holiday_temperature: float | None
    cooling_holiday_temperature: float | None
    desired_temperature: float | None
    room_temperature: float | None
    room_temperature_error: bool | None
    has_room_sensor: bool | None
    use_reduced_operation_mode_on_holiday: bool | None
    heating_active: bool | None
    cooling_active: bool | None
    heat_or_cool_request: bool | None

    @classmethod
    def parse(cls, number: int, raw: dict[str, Any]) -> ZoneState:
        return cls(
            number=number,
            raw=raw,
            holidays=tuple(
                holiday
                for item in raw.get("holidays", [])
                if (holiday := BsbHoliday.parse(item)) is not None
            )
            if isinstance(raw.get("holidays"), list)
            else (),
            mode=SelectVariable.parse(raw.get("mode", {})),
            comfort_temperature=NumericVariable.parse(
                _first(raw, "chComfortTemp", "chComfTemp", default={})
            ),
            reduced_temperature=NumericVariable.parse(
                _first(raw, "chReducedTemp", "chRedTemp", default={})
            ),
            cooling_comfort_temperature=NumericVariable.parse(
                _first(raw, "coolComfortTemp", "coolComfTemp", default={})
            ),
            cooling_reduced_temperature=NumericVariable.parse(
                _first(raw, "coolReducedTemp", "coolRedTemp", default={})
            ),
            heating_protection_temperature=_number(raw.get("chProtectionTemp")),
            cooling_protection_temperature=_number(raw.get("coolProtectionTemp")),
            heating_holiday_temperature=_number(raw.get("chHolidayTemp")),
            cooling_holiday_temperature=_number(raw.get("coolHolidayTemp")),
            desired_temperature=_number(raw.get("desiredRoomTemp")),
            room_temperature=_number(raw.get("roomTemp")),
            room_temperature_error=_boolean(raw.get("roomTempError")),
            has_room_sensor=_boolean(raw.get("hasRoomSensor")),
            use_reduced_operation_mode_on_holiday=_boolean(
                raw.get("useReducedOperationModeOnHoliday")
            ),
            heating_active=_boolean(_first(raw, "isHeatingActive", "heatingOn")),
            cooling_active=_boolean(_first(raw, "isCoolingActive", "coolingOn")),
            heat_or_cool_request=_boolean(_first(raw, "heatOrCoolRequest", "heatOrCoolReq")),
        )

    @classmethod
    def from_galevo(
        cls,
        number: int,
        raw: dict[str, Any],
        items: dict[str, dict[str, Any]],
        feature_zone: dict[str, Any] | None = None,
    ) -> ZoneState:
        """Build zone state from a Galevo zones/{zn} payload plus dataItems."""
        raw_mod = raw.get("mod")
        mod = raw_mod if isinstance(raw_mod, dict) else {}
        mode = _galevo_select(
            _galevo_item(items, "ZoneMode", number),
            remap=_GALEVO_ZONE_MODE_MAP,
            default_labels=_GALEVO_ZONE_MODE_LABELS,
        )
        if mode.value is None and not mode.options:
            # zones/{zn}/mod carries value+allowedOptions but no labels.
            mode = _galevo_select(
                {"value": mod.get("value"), "options": mod.get("allowedOptions")},
                remap=_GALEVO_ZONE_MODE_MAP,
                default_labels=_GALEVO_ZONE_MODE_LABELS,
            )
        comfort = NumericVariable.parse(raw.get("comf", {}))
        if comfort.value is None:
            comfort = _galevo_numeric(_galevo_item(items, "ZoneComfortTemp", number))
        reduced = NumericVariable.parse(raw.get("econ", {}))
        if reduced.value is None:
            reduced = _galevo_numeric(_galevo_item(items, "ZoneEconomyTemp", number))
        has_room_sensor = _boolean((feature_zone or {}).get("roomSens"))
        room_temperature = (
            _number(raw.get("room"))
            if _number(raw.get("room")) not in (None, 0.0)
            else _galevo_value(_galevo_item(items, "ZoneMeasuredTemp", number))
        )
        if has_room_sensor is False:
            # Galevo reports 0 °C for zones without a room probe.
            room_temperature = None
        return cls(
            number=number,
            raw=raw,
            holidays=(),
            mode=mode,
            comfort_temperature=comfort,
            reduced_temperature=reduced,
            cooling_comfort_temperature=_galevo_numeric(
                _galevo_item(items, "ZoneComfortCoolingTemp", number)
            ),
            cooling_reduced_temperature=_galevo_numeric(
                _galevo_item(items, "ZoneEconomyCoolingTemp", number)
            ),
            heating_protection_temperature=_number(raw.get("antiFreezeTemp"))
            or _galevo_value(_galevo_item(items, "ZoneAntiFreezeTemp", number)),
            cooling_protection_temperature=None,
            heating_holiday_temperature=None,
            cooling_holiday_temperature=None,
            desired_temperature=_number(raw.get("desired"))
            if raw.get("desired") is not None
            else _galevo_value(_galevo_item(items, "ZoneDesiredTemp", number)),
            room_temperature=room_temperature,
            room_temperature_error=_galevo_error(_galevo_item(items, "ZoneMeasuredTemp", number)),
            has_room_sensor=has_room_sensor,
            use_reduced_operation_mode_on_holiday=None,
            heating_active=_boolean(raw.get("heat")),
            cooling_active=None,
            heat_or_cool_request=_galevo_bool(_galevo_item(items, "ZoneHeatRequest", number)),
        )


@dataclass(frozen=True, slots=True)
class ReadOnlyDiscovery:
    """Optional read-only endpoint data and availability results."""

    features: dict[str, Any] = field(default_factory=dict)
    features_response: Any = None
    plant_metadata: dict[str, Any] = field(default_factory=dict)
    plant_header: dict[str, Any] = field(default_factory=dict)
    plant_user_data: dict[str, Any] = field(default_factory=dict)
    system_items: dict[str, dict[str, Any]] = field(default_factory=dict)
    schedules: dict[str, Any] = field(default_factory=dict)
    metering: Any = None
    maintenance: Any = None
    automated_monitoring: Any = None
    bsb_boiler_data: Any = None
    bus_errors: Any = None
    bsb_points: dict[str, Any] = field(default_factory=dict)
    bsb_plant_data: dict[str, Any] = field(default_factory=dict)
    menu_items: dict[str, dict[str, Any]] = field(default_factory=dict)
    bus_devices: list[dict[str, Any]] = field(default_factory=list)
    # Galevo consumption series from reports/consSequencesApi8: [{k, p, v}]
    # with k=eEnergyUsageDto value, p=eSequencePeriod, v=kWh buckets.
    energy_sequences: list[dict[str, Any]] = field(default_factory=list)
    probe_status: dict[str, str] = field(default_factory=dict)
    # Controller family from the Features response (const SYSTEM_TYPE_*).
    # Entity platforms use this to register only entities whose backing
    # protocol the family supports (BSB point/address entities vs Galevo
    # data-item entities).
    system_type: int | None = None

    def system_item(self, item_id: str, zone: int = 0) -> dict[str, Any] | None:
        """Return a discovered system-data item."""
        return self.system_items.get(f"{item_id}:{zone}")

    def system_value(self, item_id: str, zone: int = 0) -> Any:
        """Return a discovered system-data item value."""
        item = self.system_item(item_id, zone)
        return item.get("value") if item else None


@dataclass(frozen=True, slots=True)
class ElcoData:
    """Complete coordinator snapshot."""

    gateway_id: str
    plant: PlantState
    zones: dict[int, ZoneState] = field(default_factory=dict)
    get_data_responses: list[dict[str, Any]] = field(default_factory=list)
    discovery: ReadOnlyDiscovery = field(default_factory=ReadOnlyDiscovery)
    captured_at: datetime = field(default_factory=lambda: datetime.now(UTC), compare=False)
