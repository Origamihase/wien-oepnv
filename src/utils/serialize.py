"""Utilities for serializing provider data structures for caching."""

from __future__ import annotations

from datetime import datetime
from typing import Any


__all__ = ["serialize_for_cache"]


def serialize_for_cache(value: Any, _stack: set[int] | None = None) -> Any:
    """Recursively convert *value* into a JSON-serializable structure.

    Handles cycles by raising ValueError, similar to json.dumps.
    """
    # Simple types - return immediately
    if value is None or isinstance(value, (str, int, float, bool)):
        return value

    if isinstance(value, datetime):
        return value.isoformat()

    # Container types - check for cycles
    if isinstance(value, (dict, list, tuple, set)):
        if _stack is None:
            _stack = set()

        obj_id = id(value)
        if obj_id in _stack:
            raise ValueError("Circular reference detected")

        _stack.add(obj_id)
        try:
            if isinstance(value, dict):
                return {key: serialize_for_cache(val, _stack) for key, val in value.items()}
            if isinstance(value, (list, tuple)):
                return [serialize_for_cache(item, _stack) for item in value]
            if isinstance(value, set):
                serialized = [serialize_for_cache(item, _stack) for item in value]
                # Sort for deterministic output
                try:
                    return sorted(serialized, key=str)
                except TypeError:
                    # Fallback if str() fails or comparison fails
                    return serialized
        finally:
            _stack.remove(obj_id)

    # Unknown types pass through (let json.dump handle or fail later)
    return value
