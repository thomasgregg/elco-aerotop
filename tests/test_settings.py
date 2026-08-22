"""Tests for integration defaults."""

from datetime import timedelta

from custom_components.elco_aerotop.const import (
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_UPDATE_INTERVAL,
    REQUEST_TIMEOUT,
)


def test_default_polling_interval_is_one_hour() -> None:
    assert DEFAULT_SCAN_INTERVAL == 3600
    assert timedelta(hours=1) == DEFAULT_UPDATE_INTERVAL


def test_remocon_request_timeout_allows_a_full_minute() -> None:
    assert REQUEST_TIMEOUT >= 60
