"""Tests for current-holiday selection shared by calendar and controls."""

from custom_components.elco_aerotop.holiday import current_holiday, has_holiday_source
from custom_components.elco_aerotop.models import ZoneState


def test_current_holiday_skips_deleted_and_out_of_service_slots() -> None:
    zone = ZoneState.parse(
        1,
        {
            "holidays": [
                {
                    "index": 0,
                    "fromAsIso": "2027-01-01T00:00:00+01:00",
                    "toAsIso": "2027-01-02T00:00:00+01:00",
                    "deleted": True,
                },
                {
                    "index": 1,
                    "fromAsIso": "2027-02-01T00:00:00+01:00",
                    "toAsIso": "2027-02-02T00:00:00+01:00",
                    "osv": True,
                },
                {
                    "index": 2,
                    "fromAsIso": "2027-03-01T00:00:00+01:00",
                    "toAsIso": "2027-03-05T00:00:00+01:00",
                },
            ]
        },
    )

    assert has_holiday_source(zone) is True
    assert current_holiday(zone).index == 2


def test_holiday_source_requires_controller_list() -> None:
    assert has_holiday_source(ZoneState.parse(1, {})) is False
