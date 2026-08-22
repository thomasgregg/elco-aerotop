"""Pure helpers for safe ELCO diagnostic exports."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

SENSITIVE_KEYS = {
    "address",
    "city",
    "email",
    "gateway",
    "gatewayid",
    "gwid",
    "latitude",
    "location",
    "longitude",
    "name",
    "nickname",
    "password",
    "postalcode",
    "serial",
    "technician",
    "username",
}
MAX_DIAGNOSTIC_LIST_ITEMS = 100


def _normalized_key(key: str) -> str:
    return "".join(character for character in key.lower() if character.isalnum())


def _is_sensitive_key(key: str) -> bool:
    normalized = _normalized_key(key)
    return normalized in SENSITIVE_KEYS or any(
        marker in normalized for marker in ("gatewayid", "serialnumber", "userid")
    )


def _redact_secrets(value: str, secrets: set[str]) -> str:
    redacted = value
    for secret in secrets:
        if secret:
            redacted = redacted.replace(secret, "<redacted>")
    return redacted


def sanitize_diagnostics(value: Any, secrets: set[str], *, key: str = "") -> Any:
    """Recursively redact identifiers and bound large diagnostic arrays."""
    if key and _is_sensitive_key(key):
        return "<redacted>"
    if isinstance(value, dict):
        return {
            _redact_secrets(str(child_key), secrets): sanitize_diagnostics(
                child_value,
                secrets,
                key=str(child_key),
            )
            for child_key, child_value in value.items()
        }
    if isinstance(value, list | tuple):
        sanitized = [
            sanitize_diagnostics(item, secrets) for item in value[:MAX_DIAGNOSTIC_LIST_ITEMS]
        ]
        if len(value) > MAX_DIAGNOSTIC_LIST_ITEMS:
            sanitized.append({"_truncated_items": len(value) - MAX_DIAGNOSTIC_LIST_ITEMS})
        return sanitized
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, str):
        return _redact_secrets(value, secrets)
    return value


def schema_inventory(value: Any, secrets: set[str] | None = None) -> dict[str, str]:
    """Return every response key path and the types observed at that path."""
    observed: dict[str, set[str]] = {}
    identifiers = secrets or set()

    def visit(item: Any, path: str) -> None:
        type_name = type(item).__name__
        observed.setdefault(path or "$", set()).add(type_name)
        if isinstance(item, dict):
            for child_key, child_value in item.items():
                safe_key = _redact_secrets(str(child_key), identifiers)
                child_path = f"{path}.{safe_key}" if path else safe_key
                visit(child_value, child_path)
        elif isinstance(item, list | tuple):
            child_path = f"{path}[]" if path else "$[]"
            for child in item:
                visit(child, child_path)

    visit(value, "")
    return {path: "|".join(sorted(type_names)) for path, type_names in sorted(observed.items())}
