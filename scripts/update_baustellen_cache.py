#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Refresh the cache with construction work information for Vienna."""

from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

from dateutil import parser as dtparser
from requests.exceptions import RequestException
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from utils.cache import write_cache  # noqa: E402
from utils.http import fetch_content_safe, session_with_retries, validate_http_url  # noqa: E402
from utils.ids import make_guid  # noqa: E402
from utils.serialize import serialize_for_cache  # noqa: E402

LOGGER = logging.getLogger("update_baustellen_cache")

DEFAULT_DATA_URL = (
    "https://data.wien.gv.at/daten/geo?service=WFS&request=GetFeature&version=1.1.0"
    "&typeName=ogdwien:BAUSTELLEOGD&srsName=EPSG:4326&outputFormat=json"
)
DEFAULT_INFO_URL = (
    "https://www.data.gv.at/katalog/en/dataset/baustellen-wien-verkehrsbeeintraechtigungen"
)
DEFAULT_FALLBACK_PATH = REPO_ROOT / "data" / "samples" / "baustellen_sample.geojson"
VIENNA_TZ = ZoneInfo("Europe/Vienna")
USER_AGENT = "Origamihase-wien-oepnv/3.1 (+https://github.com/Origamihase/wien-oepnv)"

TITLE_KEYS: Tuple[str, ...] = (
    "BEZEICHNUNG",
    "MASSNAHME",
    "MASSNAHME_TEXT",
    "BAUMASSNAHME",
    "TITLE",
    "NAME",
)
STREET_KEYS: Tuple[str, ...] = (
    "STRASSE",
    "STRASSENNAME",
    "STRASSEN",
    "STR",
)
FROM_KEYS: Tuple[str, ...] = (
    "VON",
    "ABSCHNITT_VON",
    "VON_NR",
    "VON_KM",
)
TO_KEYS: Tuple[str, ...] = (
    "BIS",
    "ABSCHNITT_BIS",
    "BIS_NR",
    "BIS_KM",
)
INFO_KEYS: Tuple[str, ...] = (
    "HINWEIS",
    "INFO",
    "BESCHREIBUNG",
    "BEMERKUNG",
    "DETAIL",
    "DETAILS",
    "ANMERKUNG",
)
START_KEYS: Tuple[str, ...] = (
    "ANFANGSZEIT",
    "BEGINN",
    "BEGINN_DATUM",
    "BEGINNZEIT",
    "START",
    "START_DATUM",
    "STARTZEIT",
    "DATUM_VON",
    "VON_DATUM",
)
END_KEYS: Tuple[str, ...] = (
    "ENDEZEIT",
    "ENDE",
    "END_DATUM",
    "ENDZEIT",
    "ENDE_DATUM",
    "DATUM_BIS",
    "BIS_DATUM",
    "BIS",
)


@dataclass(slots=True)
class ConstructionEvent:
    """Internal representation for cache items."""

    guid: str
    title: str
    description: str
    starts_at: Optional[datetime]
    ends_at: Optional[datetime]
    pub_date: datetime
    source: str = "Stadt Wien – Baustellen"
    category: str = "Baustelle"
    link: str = DEFAULT_INFO_URL
    context: Dict[str, Any] | None = None
    location: Dict[str, Any] | None = None

    def to_item(self) -> Dict[str, Any]:
        item: Dict[str, Any] = {
            "source": self.source,
            "category": self.category,
            "title": self.title,
            "description": self.description,
            "link": self.link,
            "guid": self.guid,
            "pubDate": self.pub_date,
        }
        if self.starts_at:
            item["starts_at"] = self.starts_at
        if self.ends_at:
            item["ends_at"] = self.ends_at
        if self.context:
            item["context"] = self.context
        if self.location:
            item["location"] = self.location
        return item


def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def _load_json_from_content(content: bytes) -> Dict[str, Any]:
    try:
        return json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:  # pragma: no cover - defensive
        raise ValueError(f"Invalid JSON payload: {exc}") from exc


def _fetch_remote(url: str, timeout: int) -> Optional[Dict[str, Any]]:
    # Security: validate remote URL before fetching (SSRF/DNS rebinding protection).
    if not validate_http_url(url):
        LOGGER.warning("Baustellen: Unsichere oder ungültige URL: %s", url)
        return None
    try:
        LOGGER.info("Baustellen: Lade Daten von %s", url)
        with session_with_retries(USER_AGENT, raise_on_status=False) as session:
            content = fetch_content_safe(
                session,
                url,
                timeout=timeout,
                headers={"Accept": "application/json"},
            )
    except (RequestException, ValueError) as exc:
        LOGGER.warning("Baustellen: Abruf fehlgeschlagen (%s)", exc)
        return None
    try:
        payload = _load_json_from_content(content)
    except ValueError as exc:
        LOGGER.warning("Baustellen: Ungültiges JSON vom Endpoint (%s)", exc)
        return None
    return payload


def _load_fallback(path: Path) -> Optional[Dict[str, Any]]:
    try:
        LOGGER.info("Baustellen: Verwende Fallback-Datei %s", path)
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        LOGGER.error("Baustellen: Fallback-Datei %s fehlt", path)
        return None
    except OSError as exc:
        LOGGER.error("Baustellen: Fallback-Datei %s nicht lesbar (%s)", path, exc)
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        LOGGER.error("Baustellen: Fallback-Datei %s enthält ungültiges JSON (%s)", path, exc)
        return None


def _iter_features(payload: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    if payload.get("type") == "FeatureCollection":
        features = payload.get("features") or []
    elif "features" in payload:
        features = payload.get("features") or []
    elif "data" in payload and isinstance(payload["data"], dict):
        features = payload["data"].get("features") or []
    else:
        features = []
    return [f for f in features if isinstance(f, dict)]


def _first_match(properties: Dict[str, Any], keys: Sequence[str]) -> Optional[str]:
    for key in keys:
        value = properties.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _combine_parts(properties: Dict[str, Any], keys: Sequence[str]) -> Optional[str]:
    parts = [properties.get(key) for key in keys]
    text_parts = [str(p).strip() for p in parts if isinstance(p, (str, int, float)) and str(p).strip()]
    if text_parts:
        return " ".join(text_parts)
    return None


def _parse_datetime(value: Union[str, float, int, None]) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=VIENNA_TZ)
        except (ValueError, OSError):
            return None
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate:
        return None
    try:
        parsed = dtparser.parse(candidate)
    except (ValueError, OverflowError):
        return None
    if not parsed.tzinfo:
        parsed = parsed.replace(tzinfo=VIENNA_TZ)
    else:
        parsed = parsed.astimezone(VIENNA_TZ)
    return parsed


def _parse_range(properties: Dict[str, Any]) -> Tuple[Optional[datetime], Optional[datetime]]:
    start = None
    end = None
    for key in START_KEYS:
        start = _parse_datetime(properties.get(key))
        if start:
            break
    for key in END_KEYS:
        end = _parse_datetime(properties.get(key))
        if end:
            break
    if (start and end) or (start and not end) or (end and not start):
        return start, end
    duration_value = properties.get("DAUER") or properties.get("ZEITRAUM")
    if isinstance(duration_value, str) and "/" in duration_value:
        raw_start, raw_end = duration_value.split("/", 1)
        start = _parse_datetime(raw_start)
        end = _parse_datetime(raw_end)
    return start, end


def _normalize_datetime(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=VIENNA_TZ)
    return value.astimezone(VIENNA_TZ)


def _build_context(properties: Dict[str, Any]) -> Dict[str, Any]:
    context: Dict[str, Any] = {}
    district = properties.get("BEZIRK") or properties.get("BZR")
    if district:
        context["district"] = str(district).strip()
    measure = properties.get("VERKEHRSMASSNAHME") or properties.get("VERKEHRSART")
    if measure:
        context["measure"] = str(measure).strip()
    status = properties.get("STATUS")
    if status:
        context["status"] = str(status).strip()
    return context


def _build_location(properties: Dict[str, Any], geometry: Dict[str, Any]) -> Dict[str, Any]:
    location: Dict[str, Any] = {}
    address_parts: List[str] = []
    street = _first_match(properties, STREET_KEYS)
    if street:
        address_parts.append(street)
    from_desc = _first_match(properties, FROM_KEYS)
    to_desc = _first_match(properties, TO_KEYS)
    if from_desc and to_desc:
        address_parts.append(f"zwischen {from_desc} und {to_desc}")
    elif from_desc:
        address_parts.append(f"auf Höhe {from_desc}")
    if address_parts:
        location["address"] = ", ".join(address_parts)
    if isinstance(geometry, dict):
        coordinates = geometry.get("coordinates")
        if isinstance(coordinates, (list, tuple)) and len(coordinates) >= 2:
            try:
                lon, lat = float(coordinates[0]), float(coordinates[1])
            except (TypeError, ValueError):
                pass
            else:
                location["coordinates"] = {"lat": lat, "lon": lon}
    return location


def _format_description(properties: Dict[str, Any], start: Optional[datetime], end: Optional[datetime]) -> str:
    info = _first_match(properties, INFO_KEYS)
    segments: List[str] = []
    if info:
        segments.append(info)
    if start:
        segments.append(f"Beginn: {start.strftime('%d.%m.%Y %H:%M')} Uhr")
    if end:
        segments.append(f"Geplant bis: {end.strftime('%d.%m.%Y %H:%M')} Uhr")
    context = _build_context(properties)
    if context.get("measure"):
        segments.append(f"Maßnahme: {context['measure']}")
    if context.get("district"):
        segments.append(f"Bezirk: {context['district']}")
    return " \n".join(segments) if segments else "Baustelle ohne weitere Angaben"


def _feature_to_event(feature: Dict[str, Any]) -> Optional[ConstructionEvent]:
    properties = feature.get("properties") or {}
    geometry = feature.get("geometry") or {}
    if not isinstance(properties, dict):
        return None
    title = _first_match(properties, TITLE_KEYS)
    if not title:
        street = _first_match(properties, STREET_KEYS)
        if street:
            title = f"Baustelle {street}"
        else:
            return None
    start, end = _parse_range(properties)
    start = _normalize_datetime(start)
    end = _normalize_datetime(end)
    description = _format_description(properties, start, end)
    location = _build_location(properties, geometry)
    context = _build_context(properties)
    identifier = (
        properties.get("OGD_ID")
        or properties.get("OBJECTID")
        or properties.get("ID")
        or title
    )
    guid = make_guid("baustellen", str(identifier), start.isoformat() if start else "", end.isoformat() if end else "")
    pub_date = start or end or datetime.now(tz=VIENNA_TZ)
    return ConstructionEvent(
        guid=guid,
        title=title,
        description=description,
        starts_at=start,
        ends_at=end,
        pub_date=pub_date,
        context=context or None,
        location=location or None,
    )


def _collect_events(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    for feature in _iter_features(payload):
        event = _feature_to_event(feature)
        if not event:
            continue
        events.append(serialize_for_cache(event.to_item()))
    return events


def main() -> int:
    configure_logging()
    data_url = os.getenv("BAUSTELLEN_DATA_URL", DEFAULT_DATA_URL).strip() or DEFAULT_DATA_URL
    fallback_path = Path(os.getenv("BAUSTELLEN_FALLBACK_PATH", str(DEFAULT_FALLBACK_PATH))).resolve()
    timeout_raw = os.getenv("BAUSTELLEN_TIMEOUT", "")
    timeout = 20
    if timeout_raw.strip():
        try:
            timeout = max(int(timeout_raw), 1)
        except ValueError:
            LOGGER.warning("Baustellen: Ungültiger Timeout-Wert %r – verwende Standard", timeout_raw)
    payload = _fetch_remote(data_url, timeout)
    if payload is None:
        payload = _load_fallback(fallback_path)
        if payload is None:
            return 1
    events = _collect_events(payload)
    write_cache("baustellen", events)
    LOGGER.info("Baustellen: Cache mit %d Einträgen aktualisiert.", len(events))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
