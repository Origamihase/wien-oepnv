"""Helpers for working with the ÖBB station directory."""

from __future__ import annotations

import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Dict, Iterable, List

__all__ = ["canonical_name"]

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
def _station_lookup() -> Dict[str, str]:
    """Return a mapping from normalized aliases to canonical station names."""

    try:
        with _STATIONS_PATH.open("r", encoding="utf-8") as handle:
            entries = json.load(handle)
    except (OSError, json.JSONDecodeError):  # pragma: no cover - defensive
        return {}

    mapping: Dict[str, str] = {}
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
        for alias in _iter_aliases(name, code or None):
            key = _normalize_token(alias)
            if not key:
                continue
            mapping.setdefault(key, name)
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
    return candidates


def canonical_name(name: str) -> str | None:
    """Return the canonical ÖBB station name for *name* or ``None`` if unknown."""

    if not isinstance(name, str):  # pragma: no cover - defensive
        return None

    lookup = _station_lookup()
    if not lookup:
        return None

    for candidate in _candidate_values(name):
        key = _normalize_token(candidate)
        if key and key in lookup:
            return lookup[key]
    return None
