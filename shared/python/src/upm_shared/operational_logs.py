"""Shared safe structured operational-log primitives."""

from __future__ import annotations

from collections.abc import Mapping

SENSITIVE_FRAGMENTS = (
    "password",
    "secret",
    "token",
    "csrf",
    "credential",
    "authorization",
    "private_key",
)


def redact_context(value: object) -> object:
    """Recursively redact known secret-bearing fields before durable persistence."""
    if isinstance(value, Mapping):
        return {
            str(key): "[REDACTED]"
            if any(fragment in str(key).casefold() for fragment in SENSITIVE_FRAGMENTS)
            else redact_context(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_context(item) for item in value]
    return value
