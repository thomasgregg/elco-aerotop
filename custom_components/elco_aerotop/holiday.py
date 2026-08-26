"""Shared helpers for Remocon's current-holiday workflow."""

from __future__ import annotations

from .models import BsbHoliday, ZoneState


def current_holiday(zone: ZoneState) -> BsbHoliday | None:
    """Return the first usable period, matching Remocon's current-holiday model."""
    return next(
        (
            holiday
            for holiday in zone.holidays
            if not holiday.deleted and not holiday.out_of_service
        ),
        None,
    )


def has_holiday_source(zone: ZoneState) -> bool:
    """Return whether the core zone response advertises holiday periods."""
    return isinstance(zone.raw.get("holidays"), list)
