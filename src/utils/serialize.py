"""Utilities for serializing provider data structures for caching."""

from __future__ import annotations

from datetime import datetime
from typing import Any


__all__ = ["serialize_for_cache"]


def serialize_for_cache(value: Any) -> Any:
    """Recursively convert *value* into a JSON-serializable structure."""

    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: serialize_for_cache(val) for key, val in value.items()}
    if isinstance(value, (list, tuple)):
        return [serialize_for_cache(item) for item in value]
    if isinstance(value, set):
        serialized = [serialize_for_cache(item) for item in value]
        return sorted(serialized, key=str)
    return value
