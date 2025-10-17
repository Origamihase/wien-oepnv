from __future__ import annotations

import re
from typing import List, Optional

from ..utils.stations import is_in_vienna, is_pendler

BAHNHOF_TRIM_RE = re.compile(
    r"\s*\b(?:Bahnhof|Bahnhst|Hbf|Bf)\b(?:\s*\(\s*[US]\d*\s*\))?",
    re.IGNORECASE,
)
BAHNHOF_COMPOUND_RE = re.compile(
    r"(?<=\S)(?:Bahnhof|Bahnhst|Hbf|Bf)(?=(?:\s|-|$))",
    re.IGNORECASE,
)
ARROW_ANY_RE = re.compile(r"\s*(?:<=>|<->|<>|→|↔|=>|=|–|—|\s-\s)\s*")
MULTI_ARROW_RE = re.compile(r"(?:\s*↔\s*){2,}")
_MULTI_SLASH_RE = re.compile(r"\s*/{2,}\s*")
_MULTI_COMMA_RE = re.compile(r"\s*,{2,}\s*")

_MAX_STATION_WINDOW = 4
_FAR_AWAY_RE = re.compile(
    r"\b(salzburg|innsbruck|villach|bregenz|linz|graz|klagenfurt|bratislava|muenchen|passau|freilassing)\b",
    re.IGNORECASE,
)


def clean_endpoint(value: str) -> str:
    text = BAHNHOF_TRIM_RE.sub("", value)
    text = _MULTI_SLASH_RE.sub("/", text)
    text = _MULTI_COMMA_RE.sub(", ", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip(" ,/")


def split_endpoints(title: str) -> Optional[List[str]]:
    arrow_markers = ("↔", "<=>", "<->", "→", "=>", "->", "—", "–")
    if not any(marker in title for marker in arrow_markers) and not re.search(r"\s-\s", title):
        return None
    parts = [
        token
        for token in re.split(r"\s*(?:↔|<=>|<->|→|=>|->|—|–|\s-\s)\s*", title)
        if token.strip()
    ]
    if len(parts) < 2:
        return None
    left, right = parts[0], parts[1]

    def explode(segment: str) -> List[str]:
        raw_tokens = re.split(r"\s*(?:/|,|bzw\.|oder|und)\s*", segment, flags=re.IGNORECASE)
        names: List[str] = []
        for token in raw_tokens:
            token = BAHNHOF_TRIM_RE.sub("", token)
            token = BAHNHOF_COMPOUND_RE.sub("", token)
            token = re.sub(r"\s*\([^)]*\)\s*", "", token)
            token = re.sub(r"\s{2,}", " ", token).strip(" .")
            if token:
                names.append(token)
        return names

    endpoints = explode(left) + explode(right)
    return list(dict.fromkeys(endpoints))


def _is_allowed_station(name: str, *, only_vienna: bool) -> bool:
    if is_in_vienna(name):
        return True
    if only_vienna:
        return False
    return is_pendler(name)


def has_allowed_station(blob: str, *, only_vienna: bool) -> bool:
    tokens = [token for token in re.split(r"\W+", blob) if token]
    if not tokens:
        return False
    window = min(_MAX_STATION_WINDOW, len(tokens))
    for size in range(window, 0, -1):
        for index in range(len(tokens) - size + 1):
            candidate = " ".join(tokens[index : index + size])
            if _is_allowed_station(candidate, only_vienna=only_vienna):
                return True
    return False


def keep_by_region(title: str, desc: str, *, only_vienna: bool = False) -> bool:
    endpoints = split_endpoints(title)
    if endpoints:
        return all(_is_allowed_station(endpoint, only_vienna=only_vienna) for endpoint in endpoints)
    blob = f"{title or ''} {desc or ''}"
    if not has_allowed_station(blob, only_vienna=only_vienna):
        return False
    if _FAR_AWAY_RE.search(blob):
        return False
    return True


__all__ = [
    "ARROW_ANY_RE",
    "BAHNHOF_COMPOUND_RE",
    "BAHNHOF_TRIM_RE",
    "MULTI_ARROW_RE",
    "clean_endpoint",
    "has_allowed_station",
    "keep_by_region",
    "split_endpoints",
]
