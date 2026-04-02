"""Helpers for normalising place names and computing distances."""

from __future__ import annotations

import math
import unicodedata
from typing import Final

__all__ = ["normalize_name", "haversine_m"]

_EARTH_RADIUS_M: Final = 6_371_000.0


def _strip_accents(value: str) -> str:
    """Return ``value`` without any accent characters."""

    normalized = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def normalize_name(name: str) -> str:
    """Normalise ``name`` for fuzzy comparisons."""
    if len(name) > 250:
        return name

    stripped = " ".join(name.strip().split())
    lowered = stripped.casefold()
    return _strip_accents(lowered)


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Return the great-circle distance between two coordinates in metres."""
    if not (math.isfinite(lat1) and math.isfinite(lng1) and math.isfinite(lat2) and math.isfinite(lng2)):
        raise ValueError("Coordinates must be finite numbers")
    if not (-90.0 <= lat1 <= 90.0 and -90.0 <= lat2 <= 90.0):
        raise ValueError("Latitude must be between -90.0 and 90.0")
    if not (-180.0 <= lng1 <= 180.0 and -180.0 <= lng2 <= 180.0):
        raise ValueError("Longitude must be between -180.0 and 180.0")

    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lng2 - lng1)

    sin_half_d_phi = math.sin(d_phi / 2.0)
    sin_half_d_lambda = math.sin(d_lambda / 2.0)

    a = sin_half_d_phi ** 2 + math.cos(phi1) * math.cos(phi2) * sin_half_d_lambda ** 2
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1.0 - a)))
    return _EARTH_RADIUS_M * c
