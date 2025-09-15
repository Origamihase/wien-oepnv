"""Helpers for working with the ÖBB station directory."""

from __future__ import annotations

import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Dict, Iterable, List, NamedTuple

__all__ = ["canonical_name", "is_in_vienna", "is_pendler", "station_info"]


class StationInfo(NamedTuple):
    """Normalized metadata for a single station entry."""

    name: str
    in_vienna: bool
    pendler: bool

_STATIONS_PATH = Path(__file__).resolve().parents[2] / "data" / "stations.json"


def _strip_accents(value: str) -> str:
    """Return *value* without diacritic marks."""

    return "".join(
        ch for ch in unicodedata.normalize("NFKD", value) if not unicodedata.combining(ch)
    )


def _normalize_token(value: str) -> str:
    """Produce a canonical lookup token for a station alias."""

    if not value:
        return ""

    text = _strip_accents(value)
    text = text.replace("ß", "ss")
    text = text.casefold()
    text = re.sub(r"\ba\s*(?:[./]\s*)?d(?:[./]\s*)?\b", "an der ", text)
    text = text.replace("ae", "a").replace("oe", "o").replace("ue", "u")
    text = re.sub(r"\bst[. ]?\b", "sankt ", text)
    text = re.sub(r"\b(?:bahnhof|bahnhst|bhf|hbf|bf)\b", "", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip()


def _iter_aliases(name: str, code: str | None) -> Iterable[str]:
    """Yield alias strings for a station entry (canonical name first)."""

    variants: List[str] = []
    seen: set[str] = set()

    def add(raw: str) -> None:
        candidate = re.sub(r"\s{2,}", " ", raw.strip())
        if candidate and candidate not in seen:
            seen.add(candidate)
            variants.append(candidate)

    add(name)
    if code:
        add(code)

    no_paren = re.sub(r"\s*\([^)]*\)\s*", " ", name)
    add(no_paren)
    add(no_paren.replace("-", " "))
    add(no_paren.replace("/", " "))
    add(name.replace("-", " "))
    add(name.replace("/", " "))

    if re.search(r"\bSt\.?\b", name):
        add(re.sub(r"\bSt\.?\b", "St", name))
        add(re.sub(r"\bSt\.?\b", "St ", name))
        add(re.sub(r"\bSt\.?\b", "Sankt ", name))
    if re.search(r"\bSankt\b", name):
        add(re.sub(r"\bSankt\b", "St.", name))
        add(re.sub(r"\bSankt\b", "St ", name))
        add(re.sub(r"\bSankt\b", "St", name))

    return variants


@lru_cache(maxsize=1)
def _station_lookup() -> Dict[str, StationInfo]:
    """Return a mapping from normalized aliases to :class:`StationInfo` records."""

    try:
        with _STATIONS_PATH.open("r", encoding="utf-8") as handle:
            entries = json.load(handle)
    except (OSError, json.JSONDecodeError):  # pragma: no cover - defensive
        return {}

    mapping: Dict[str, StationInfo] = {}
    if not isinstance(entries, list):
        return mapping

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name", "")).strip()
        if not name:
            continue
        code_raw = entry.get("bst_code")
        code = str(code_raw).strip() if code_raw is not None else ""
        record = StationInfo(
            name=name,
            in_vienna=bool(entry.get("in_vienna")),
            pendler=bool(entry.get("pendler")),
        )
        for alias in _iter_aliases(name, code or None):
            key = _normalize_token(alias)
            if not key:
                continue
            if key not in mapping:
                mapping[key] = record
    return mapping


def _candidate_values(value: str) -> List[str]:
    """Generate possible textual variants for *value* supplied by the caller."""

    candidates: List[str] = []
    seen: set[str] = set()
    for variant in (
        value,
        value.strip(),
        re.sub(r"\s*\([^)]*\)\s*", " ", value),
        value.replace("-", " "),
        value.replace("/", " "),
    ):
        cleaned = re.sub(r"\s{2,}", " ", variant.strip())
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            candidates.append(cleaned)
    extras: List[str] = []
    for variant in candidates:
        if re.search(r"\b(?:bei|b[./-]?)\s*wien\b", variant, re.IGNORECASE):
            stripped = re.sub(r"\b(?:bei|b[./-]?)\s*wien\b", "", variant, flags=re.IGNORECASE)
            extras.append(stripped)
    for extra in extras:
        cleaned = re.sub(r"\s{2,}", " ", extra.strip())
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            candidates.append(cleaned)
    return candidates


def canonical_name(name: str) -> str | None:
    """Return the canonical ÖBB station name for *name* or ``None`` if unknown."""

    info = station_info(name)
    return info.name if info else None


def station_info(name: str) -> StationInfo | None:
    """Return :class:`StationInfo` for *name* or ``None`` if the station is unknown."""

    if not isinstance(name, str):  # pragma: no cover - defensive
        return None

    lookup = _station_lookup()
    if not lookup:
        return None

    for candidate in _candidate_values(name):
        key = _normalize_token(candidate)
        if not key:
            continue
        info = lookup.get(key)
        if info:
            return info
    return None


def is_in_vienna(name: str) -> bool:
    """Return ``True`` if *name* refers to a station located in Vienna."""

    info = station_info(name)
    if info:
        return bool(info.in_vienna)
    if isinstance(name, str):
        token = _normalize_token(name)
        if token == "wien" or token.startswith("wien "):
            return True
    return False


def is_pendler(name: str) -> bool:
    """Return ``True`` if *name* is part of the configured commuter belt."""

    info = station_info(name)
    return bool(info and info.pendler)
