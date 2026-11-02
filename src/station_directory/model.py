"""Data model and name/key helpers of the station-directory updater.

Moved verbatim out of ``scripts/update_station_directory.py``, which
re-exports every name it or its tests use.
"""

from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass, field
from collections.abc import Mapping

from src.utils.geo import apply_coordinate_inertia
from src.utils.text import strip_accents


@dataclass
class Station:
    """Representation of a single station entry."""

    bst_id: str
    bst_code: str
    name: str
    in_vienna: bool = False
    pendler: bool = False
    vor_id: str | None = None
    extras: dict[str, object] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "bst_id": str(self.bst_id),
            "bst_code": self.bst_code,
            "name": self.name,
            "in_vienna": self.in_vienna,
            "pendler": self.pendler,
        }
        if self.vor_id:
            payload["vor_id"] = self.vor_id

        for key, value in self.extras.items():
            if key in {"bst_id", "bst_code", "name", "in_vienna", "pendler", "vor_id", "_lat", "_lng", "_types", "_google_place_id"}:
                continue
            payload[key] = value
        # Tag every Excel-imported entry with source="oebb" if no other
        # source has been merged in. Without this, the next run treats the
        # entry as `manual` (see _load_existing_station_entries) and appends
        # it on top of a fresh Excel import — producing the duplicate-name
        # NamingIssues seen in the 2026-05 cron after PR #1201.
        if "source" not in payload:
            payload["source"] = "oebb"
        return payload

    def update_from_entry(self, entry: Mapping[str, object]) -> None:
        base_keys = {"bst_id", "bst_code", "name", "in_vienna", "pendler", "vor_id"}

        # Coordinate Inertia: capture the existing coords BEFORE the
        # generic extras-loop (below) overwrites them. We use these
        # later to decide whether to absorb upstream drift via
        # ``apply_coordinate_inertia``.
        existing_lat_raw = self.extras.get("latitude")
        existing_lon_raw = self.extras.get("longitude")
        existing_lat = (
            float(existing_lat_raw)
            if isinstance(existing_lat_raw, int | float)
            else None
        )
        existing_lon = (
            float(existing_lon_raw)
            if isinstance(existing_lon_raw, int | float)
            else None
        )

        for key, value in entry.items():
            if key == "vor_id":
                if self.vor_id is None and isinstance(value, str) and value.strip():
                    self.vor_id = value.strip()
                continue
            if key in base_keys:
                continue
            self.extras[key] = deepcopy(value)

        lat = entry.get("latitude") or entry.get("_lat")
        lng = entry.get("longitude") or entry.get("_lng")
        new_lat = float(lat) if isinstance(lat, int | float) else None
        new_lon = float(lng) if isinstance(lng, int | float) else None

        # Resolve the final coordinate via the inertia helper. Drifts
        # below ``STATION_DRIFT_TOLERANCE_METERS`` (default 150 m) are
        # absorbed — the existing coords win, eliminating churn-only
        # diffs in ``data/stations.json``. Drifts at or above the
        # threshold are accepted as legitimate relocations. First-time
        # coords (no existing) and missing-from-upstream cases are
        # handled by the helper's rules 1+2.
        merged_lat, merged_lon = apply_coordinate_inertia(
            existing_lat, existing_lon, new_lat, new_lon
        )
        if merged_lat is not None:
            self.extras["latitude"] = merged_lat
        if merged_lon is not None:
            self.extras["longitude"] = merged_lon


@dataclass
class VORStop:
    """Minimal representation of a VOR stop for ID matching."""

    vor_id: str
    name: str
    municipality: str | None = None
    short_name: str | None = None


@dataclass
class LocationInfo:
    """Coordinates collected from auxiliary data sources."""

    latitude: float
    longitude: float
    sources: set[str]

    def add_source(self, source: str) -> None:
        self.sources.add(source)


def _normalize_location_keys(name: str | None) -> list[str]:
    if not name:
        return []

    candidates = [name]
    without_parens = re.sub(r"\s*\([^)]*\)\s*", " ", name)
    if without_parens not in candidates:
        candidates.append(without_parens)
    stripped_suffix = re.sub(
        r"\b(?:Bahnsteig|Bahnsteige|Gleis)\b[^,;/]*",
        " ",
        without_parens,
        flags=re.IGNORECASE,
    )
    if stripped_suffix not in candidates:
        candidates.append(stripped_suffix)

    tokens: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        token = strip_accents(candidate)
        token = token.replace("ß", "ss")
        token = token.casefold()
        token = re.sub(r"\b(?:bahnhof|bahnhst|bhf|hbf|bf)\b", "", token)
        token = re.sub(r"\b(?:bahnsteig|bahnsteige|gleis)\b", "", token)
        token = token.replace("-", " ").replace("/", " ")
        token = re.sub(r"[^a-z0-9]+", " ", token)
        token = re.sub(r"\s{2,}", " ", token).strip()
        if token and token not in seen:
            seen.add(token)
            tokens.append(token)
        numeric_stripped = re.sub(r"\d+", "", token).strip()
        if numeric_stripped and numeric_stripped not in seen:
            seen.add(numeric_stripped)
            tokens.append(numeric_stripped)
    return tokens


def _harmonize_station_name(name: str) -> str:
    """Return *name* with unified whitespace for consistent lookups."""

    text = "".join(" " if ch in {"\u00a0", "\u2007", "\u202f"} else ch for ch in str(name))
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip()


def _coerce_float_value(value: object | None) -> float | None:
    if value is None:
        return None
    if isinstance(value, int | float):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    text = text.replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


def _store_location(
    locations: dict[str, LocationInfo],
    key: str,
    lat: float,
    lon: float,
    source: str,
) -> None:
    info = locations.get(key)
    if info is None:
        locations[key] = LocationInfo(latitude=lat, longitude=lon, sources={source})
        return
    info.add_source(source)
    # Prefer coordinates from station level data when replacing placeholder zeros
    if (info.latitude, info.longitude) == (0.0, 0.0):
        info.latitude = lat
        info.longitude = lon


_WL_DIVA_KEY_PREFIX = "diva:"


def _wl_diva_key(diva: str) -> str:
    """Return the namespaced location-index key for a WL DIVA identifier.

    ``_normalize_location_keys`` strips ``:`` (and casefolds), so a DIVA
    key can never collide with a name-derived key — letting callers that
    hold a station's ``wl_diva`` resolve coordinates by the authoritative
    identifier instead of the lossy ``Wien <name> (WL)`` <-> ``<name>``
    name match.
    """

    return f"{_WL_DIVA_KEY_PREFIX}{diva}"
