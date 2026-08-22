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
        return datetime(*(int(component) for component in match.groups()))
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
    probe_status: dict[str, str] = field(default_factory=dict)

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
    captured_at: datetime = field(default_factory=datetime.now, compare=False)
