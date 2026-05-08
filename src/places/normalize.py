"""Helpers for normalising place names and computing distances."""

from __future__ import annotations

import unicodedata

# The canonical Haversine implementation lives in ``src.utils.geo``;
# re-export under the legacy ``haversine_m`` name so existing
# ``src.places.merge`` callers keep working without churn.
from ..utils.geo import calculate_distance_meters as haversine_m

__all__ = ["normalize_name", "haversine_m"]


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
