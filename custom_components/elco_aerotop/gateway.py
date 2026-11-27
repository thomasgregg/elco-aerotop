"""Normalize Remocon gateway identifiers at the input boundary."""

from __future__ import annotations

import re
from collections.abc import Mapping
from urllib.parse import unquote, urlsplit

_GATEWAY_ID = re.compile(r"[A-Za-z0-9._~-]+")
_DASHBOARD_PATH = re.compile(r"/?R2/(?:Plant|BsbPlantDashboard)/Index/([^/]+)/?", re.IGNORECASE)


def normalize_gateway_id(value: str) -> str:
    """Extract one gateway ID from a bare identifier or a Remocon dashboard URL."""
    value = value.strip()
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError("Invalid Remocon gateway ID")

    parts = urlsplit(value)
    if (parts.scheme or parts.netloc) and (
        parts.scheme not in ("http", "https") or not parts.hostname or parts.username is not None
    ):
        raise ValueError("Invalid Remocon dashboard URL")

    if parts.scheme or "/" in parts.path:
        match = _DASHBOARD_PATH.fullmatch(parts.path)
        if match is None:
            raise ValueError("Invalid Remocon dashboard path")
        candidate = match[1]
    else:
        candidate = parts.path

    gateway_id = unquote(candidate)
    if gateway_id in (".", "..") or not _GATEWAY_ID.fullmatch(gateway_id):
        raise ValueError("Invalid Remocon gateway ID")
    return gateway_id.upper()


def plant_gateway_ids(plant: Mapping[str, object]) -> tuple[str, ...]:
    """Return the valid gateway identifiers in an account's plant record, ID first."""
    identifiers = []
    for key in ("gwId", "gwSerial"):
        value = plant.get(key)
        if not isinstance(value, (str, int)) or isinstance(value, bool):
            continue
        try:
            gateway_id = normalize_gateway_id(str(value))
        except ValueError:
            continue
        if gateway_id not in identifiers:
            identifiers.append(gateway_id)
    return tuple(identifiers)
