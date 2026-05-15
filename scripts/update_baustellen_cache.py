#!/usr/bin/env python3
"""Refresh the cache with construction work information for Vienna."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, cast
from collections.abc import Iterable, Sequence
from urllib.parse import urlparse

from dateutil import parser as dtparser
from requests.exceptions import RequestException
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
# Also add the repo root so ``from src.feed.logging_safe`` resolves.
# ``src/feed/logging_safe.py`` itself uses ``from ..utils.logging``
# (relative import past the ``feed`` package), which only works when
# the package is loaded as ``src.feed``, not ``feed``.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.feed.logging_safe import setup_script_logging  # noqa: E402
from utils.cache import write_cache  # noqa: E402
from utils.files import read_capped_json  # noqa: E402
from utils.http import fetch_content_safe, session_with_retries, validate_http_url  # noqa: E402
from utils.ids import make_guid  # noqa: E402
from utils.logging import sanitize_log_arg  # noqa: E402
from utils.serialize import serialize_for_cache  # noqa: E402

# Security cap against wide-but-flat JSON size-bomb attacks on the
# bundled fallback geojson. The depth-bomb catch alone misses
# ``MemoryError`` (a ``BaseException`` subclass) so a planted-huge file
# (~1 GiB of ``[0,0,…]``) buffered via ``path.read_text()`` propagates
# past the loader and crashes the cache update on the very path used
# when the network is unreachable. Mirrors the canonical
# ``MAX_*_FILE_BYTES`` contract from ``src/utils/cache.py`` /
# ``src/utils/stations.py``.
MAX_JSON_FILE_BYTES = 50 * 1024 * 1024

LOGGER = logging.getLogger("update_baustellen_cache")


def _path_fingerprint(path: Path) -> str:
    """Return a one-way SHA-256 fingerprint of ``str(path)`` (12 hex chars).

    Security (Path-Log Sibling Drift Round 3, cron-pipeline ``scripts/``
    closure): mirrors :func:`src.utils.env._path_fingerprint` and the
    Round-2 siblings in ``scripts/{enrich_station_aliases,
    update_station_directory, update_wl_stations}.py``. The fallback-path
    argument logged at every caller-side ERROR / WARNING / INFO line
    below is derived from the operator-controlled
    ``BAUSTELLEN_FALLBACK_PATH`` env var. Interpolating the raw path
    bytes lets a hostile env value carrying Trojan-Source primitives
    (BiDi RLO, zero-width, 8-bit C1 CSI/OSC, Tag block, Variation
    Selectors, newline log-forgery, ANSI ESC) flow verbatim into the
    aggregated cron log file ``$log_dir/baustellen.log`` (captured by
    ``.github/workflows/update-cycle.yml`` and ingested by any SIEM
    forwarder), and into any ``LogRecord`` consumer that reads
    ``record.args`` before :class:`SafeFormatter` sanitisation. The
    hex-only fingerprint is Trojan-Source-clean and a CodeQL-recognised
    barrier for the ``py/clear-text-logging-sensitive-data`` taint.
    Operators correlate by re-hashing the candidate path locally.
    """
    return hashlib.sha256(
        str(path).encode("utf-8", errors="replace")
    ).hexdigest()[:12]

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

# Security: ``MAX_BAUSTELLEN_TIMEOUT`` is the Slowloris-defence ceiling for the
# OGD WFS fetch budget. ``BAUSTELLEN_TIMEOUT`` is consumed by ``_fetch_remote``
# as both connect and read budget for ``fetch_content_safe``; without an upper
# bound an env override such as ``BAUSTELLEN_TIMEOUT=99999`` (intentional
# misconfig, leaked CI env, or compromised secret store) would let a sluggish
# or attacker-controlled upstream peer hold the cron job for ~28 hours,
# stalling the whole feed-build pipeline. The cap can only TIGHTEN — env
# overrides may lower the timeout (tests use 1–5s) but never raise it above
# the documented ceiling. Mirrors the ``MAX_PROVIDER_TIMEOUT`` cap in
# ``src/feed/config.py`` and the ``MAX_TIMEOUT_S`` cap in
# ``src/places/client.py``.
DEFAULT_BAUSTELLEN_TIMEOUT = 20
MAX_BAUSTELLEN_TIMEOUT = DEFAULT_BAUSTELLEN_TIMEOUT

TITLE_KEYS: tuple[str, ...] = (
    "BEZEICHNUNG",
    "MASSNAHME",
    "MASSNAHME_TEXT",
    "BAUMASSNAHME",
    "TITLE",
    "NAME",
)
STREET_KEYS: tuple[str, ...] = (
    "STRASSE",
    "STRASSENNAME",
    "STRASSEN",
    "STR",
)
FROM_KEYS: tuple[str, ...] = (
    "VON",
    "ABSCHNITT_VON",
    "VON_NR",
    "VON_KM",
)
TO_KEYS: tuple[str, ...] = (
    "BIS",
    "ABSCHNITT_BIS",
    "BIS_NR",
    "BIS_KM",
)
INFO_KEYS: tuple[str, ...] = (
    "HINWEIS",
    "INFO",
    "BESCHREIBUNG",
    "BEMERKUNG",
    "DETAIL",
    "DETAILS",
    "ANMERKUNG",
)
START_KEYS: tuple[str, ...] = (
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
END_KEYS: tuple[str, ...] = (
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
    starts_at: datetime | None
    ends_at: datetime | None
    pub_date: datetime
    source: str = "Stadt Wien – Baustellen"
    category: str = "Baustelle"
    link: str = DEFAULT_INFO_URL
    context: dict[str, Any] | None = None
    location: dict[str, Any] | None = None

    def to_item(self) -> dict[str, Any]:
        item: dict[str, Any] = {
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
    # Sentinel: route through SafeFormatter so any raw exception text
    # logged via %s in this script is sanitised at the formatter layer.
    setup_script_logging(logging.INFO)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def _load_json_from_content(content: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        # Resilience: include ``RecursionError`` so a malicious or pathological
        # upstream serving deeply-nested JSON cannot crash the cron job.
        # ``json.loads`` on a deeply-nested array/object exceeds Python's
        # recursion limit and raises ``RecursionError`` (NOT a subclass of
        # ``JSONDecodeError``). Mirrors the canonical defence already in place
        # at ``src/providers/wl_fetch.py`` and ``src/providers/vor.py``.
        raise ValueError(f"Invalid JSON payload: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(
            f"Invalid JSON payload: expected object, got {type(payload).__name__}"
        )
    return payload


def _fetch_remote(url: str, timeout: int) -> dict[str, Any] | None:
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
                # Security: pin the response Content-Type to JSON shapes the OGD
                # WFS endpoint actually emits. Without this, a CDN/WAF error page
                # (text/html) or a misconfigured upstream would feed non-JSON
                # bytes into _load_json_from_content. The other providers
                # (WL/VOR/ÖBB) already enforce this at the request layer; this
                # closes the last gap. text/json covers older Apache mod_geowfs
                # variants; application/geo+json is the RFC 7946 registration.
                allowed_content_types=(
                    "application/json",
                    "application/geo+json",
                    "text/json",
                ),
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


def _load_fallback(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        LOGGER.error(
            "Baustellen: Fallback-Datei [path-sha256=%s] fehlt",
            _path_fingerprint(path),
        )
        return None
    LOGGER.info(
        "Baustellen: Verwende Fallback-Datei [path-sha256=%s]",
        _path_fingerprint(path),
    )
    # Defence-in-depth: the bundled fallback file lives in-tree, but a
    # compromised contributor (or accidental commit) could replace it
    # with a depth-bomb document or a wide-but-flat size-bomb that
    # propagates ``MemoryError`` (``BaseException``) past a depth-only
    # catch. ``read_capped_json`` enforces both axes; on miss the cron
    # job logs a clear error instead of terminating the process when
    # the network is unreachable.
    payload = read_capped_json(
        path, MAX_JSON_FILE_BYTES, label="Baustellen Fallback", logger=LOGGER,
    )
    if payload is None:
        LOGGER.error(
            "Baustellen: Fallback-Datei [path-sha256=%s] enthält ungültiges JSON oder ist unlesbar",
            _path_fingerprint(path),
        )
        return None
    # Zero Trust: a JSON-decodable file does not guarantee a JSON object.
    # ``_iter_features`` calls ``payload.get("type")`` / ``payload.get("features")``
    # which would raise ``AttributeError`` on a list / scalar / null body. The
    # previous ``cast(dict[str, Any], json.loads(raw))`` lied to the type
    # checker without enforcing the shape at runtime, so a malformed (or
    # tampered) on-disk fallback would crash the cache update on the very
    # path we use when the network is unreachable. Mirror the
    # ``_load_json_from_content`` guard above and fail securely instead.
    if not isinstance(payload, dict):
        LOGGER.error(
            "Baustellen: Fallback-Datei [path-sha256=%s] enthält kein JSON-Objekt (got %s)",
            _path_fingerprint(path),
            type(payload).__name__,
        )
        return None
    return payload


# Security: only accept env overrides that point at the official Stadt Wien
# Open-Data host. ``data_url`` content is parsed and merged into the public
# Baustellen feed cache (titles, descriptions, item links). An env-var
# override to ``https://evil.com`` therefore lets an attacker inject arbitrary
# construction notices into the public feed and (via JSON ``properties.HINWEIS``
# fields and similar) attach attacker-controlled text under the project's
# brand. ``validate_http_url()`` only checks SSRF/DNS-rebinding properties,
# not host identity.
#
# 2026-05-10 (HTTPS-only Provider URL Drift, Round 2 — ``scripts/`` sibling):
# the validator additionally pins the scheme to ``https``. ``validate_http_url``
# accepts both ``http`` and ``https``; without this pin, an env override such
# as ``BAUSTELLEN_DATA_URL=http://data.wien.gv.at/...`` would be accepted, the
# fetcher would issue a plaintext request, and an MITM (compromised network,
# BGP hijack, hostile public WiFi gateway) could substitute arbitrary GeoJSON
# that flows verbatim into the public ``docs/feed.xml`` artefact. Mirrors the
# canonical ``validate_public_feed_url`` HTTPS-only pin
# (``src/utils/http.py``) and the ``_validated_vor_base_url`` /
# ``_validated_oebb_url`` / ``_validated_wl_base`` siblings closed by
# PR #1415. The closing-checklist grep for that round was scoped to ``src/``
# only, missing this ``scripts/`` cousin — see the journal entry of the same
# date for the full closing-checklist rule update.
_BAUSTELLEN_TRUSTED_HOSTS = frozenset({"data.wien.gv.at"})


def _validated_baustellen_data_url(raw: str) -> str | None:
    safe = validate_http_url(raw)
    if not safe:
        return None
    parsed = urlparse(safe)
    # Security: refuse plaintext HTTP — see header above.
    if parsed.scheme.lower() != "https":
        return None
    host = (parsed.hostname or "").lower()
    if host not in _BAUSTELLEN_TRUSTED_HOSTS:
        return None
    return cast(str, safe)


def _resolve_data_url(candidate: str | None) -> str:
    text = (candidate or "").strip()
    if not text:
        return DEFAULT_DATA_URL
    validated = _validated_baustellen_data_url(text)
    if validated is None:
        # Security (Path-Log Sibling Drift Round 4, env-repr closure):
        # ``text`` is the operator-controlled ``BAUSTELLEN_DATA_URL``
        # value. Pre-fix the WARNING line interpolated it via ``%r`` —
        # Python's repr() escapes most attack bytes but lets all 256
        # Variation Selectors (U+FE00-U+FE0F + U+E0100-U+E01EF) through
        # verbatim into ``record.args[0]`` and ``record.getMessage()``.
        # Route through ``sanitize_log_arg`` so the canonical
        # ``_INVISIBLE_DANGEROUS_RE`` strips them BEFORE the value lands
        # in caplog / non-SafeFormatter handlers (mirrors the canonical
        # contract from PR #1475).
        LOGGER.warning(
            "Baustellen: BAUSTELLEN_DATA_URL %s ist kein bekannter Stadt-Wien-OGD-Host; verwende Standard.",
            sanitize_log_arg(text),
        )
        return DEFAULT_DATA_URL
    return validated


def _resolve_fallback_path(candidate: str | None) -> Path:
    """Resolve the fallback-path env override against ``REPO_ROOT``.

    Security: ``BAUSTELLEN_FALLBACK_PATH`` is read from the environment and
    later passed to ``Path.read_text()``. Without containment, an env-var-
    controlled path (or a symlink it points at, since ``resolve()`` follows
    symlinks) could read any file the process can access — exposing JSON-
    shaped local files via the generated feed. We mirror the
    ``_resolve_path`` pattern used by ``src/providers/vor.py`` to keep the
    fallback strictly inside the repository tree.
    """
    text = (candidate or "").strip()
    if not text:
        return DEFAULT_FALLBACK_PATH
    raw_path = Path(text)
    if raw_path.is_absolute():
        resolved = raw_path.resolve()
    else:
        resolved = (REPO_ROOT / raw_path).resolve()
    try:
        resolved.relative_to(REPO_ROOT)
    except ValueError:
        LOGGER.warning(
            "Baustellen: Pfad-Traversal erkannt oder Pfad außerhalb von %s: [path-sha256=%s]. Nutze Standard.",
            REPO_ROOT,
            _path_fingerprint(raw_path),
        )
        return DEFAULT_FALLBACK_PATH
    return resolved


def _iter_features(payload: dict[str, Any]) -> Iterable[dict[str, Any]]:
    if payload.get("type") == "FeatureCollection":
        features: Any = payload.get("features")
    elif "features" in payload:
        features = payload.get("features")
    elif "data" in payload and isinstance(payload["data"], dict):
        features = payload["data"].get("features")
    else:
        features = None
    # Zero Trust: ``payload`` is verified as a dict by ``_load_json_from_content``
    # / ``_load_fallback`` (top-level shape), but the ``features`` value extracted
    # from it is still ``Any`` — a misbehaving / compromised upstream peer (or a
    # tampered local fallback file) could ship a truthy non-list shape such as
    # ``42``, ``True``, ``{"a":"b"}`` or ``"abc"``. The existing ``or []``
    # collapses falsy values but lets truthy non-lists through; the resulting
    # ``for f in features`` then either raises ``TypeError`` (int/bool) — which
    # is not caught by ``_collect_events`` and crashes the whole cache update —
    # or silently iterates dict keys / string characters and emits zero events
    # (looking like an empty upstream). Mirror the ``for entry in payload`` /
    # ``for item in payload`` shape guards landed for the sibling VOR mapping
    # loaders (``_load_vor_mapping`` in ``scripts/enrich_station_aliases.py``,
    # ``scripts/update_wl_stations.py``, ``src/providers/vor.py``) so the
    # documented ``return None`` / ``return []`` fallback runs instead.
    if not isinstance(features, list):
        return []
    return [f for f in features if isinstance(f, dict)]


def _first_match(properties: dict[str, Any], keys: Sequence[str]) -> str | None:
    for key in keys:
        value = properties.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _parse_datetime(value: str | float | int | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, int | float):
        try:
            return datetime.fromtimestamp(float(value), tz=VIENNA_TZ)
        except (ValueError, OSError):
            return None
    # value is narrowed to str here by the type annotation and prior branches
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
    return cast(datetime, parsed)


def _parse_range(properties: dict[str, Any]) -> tuple[datetime | None, datetime | None]:
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


def _normalize_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=VIENNA_TZ)
    return value.astimezone(VIENNA_TZ)


def _build_context(properties: dict[str, Any]) -> dict[str, Any]:
    context: dict[str, Any] = {}
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


def _build_location(properties: dict[str, Any], geometry: dict[str, Any]) -> dict[str, Any]:
    location: dict[str, Any] = {}
    address_parts: list[str] = []
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
        if isinstance(coordinates, list | tuple) and len(coordinates) >= 2:
            try:
                lon, lat = float(coordinates[0]), float(coordinates[1])
            except (TypeError, ValueError):
                pass
            else:
                # Security (Coordinate finite/range drift, parser-level
                # floor): mirror the canonical scrub pinned at
                # ``src/places/hafas_client.py:_extract_first_location``,
                # ``src/places/client.py:_parse_place`` and
                # ``src/places/osm_client.py:filter_complete_places``
                # (Round 1485 / Round 1486). A compromised
                # ``data.wien.gv.at`` upstream (or MITM / DNS rebind)
                # planting GeoJSON ``coordinates: [NaN, Infinity]``
                # otherwise lands the poisoned float in
                # ``location["coordinates"]`` — which propagates verbatim
                # through ``write_cache`` into ``cache/baustellen/
                # events.json`` (committed to ``main`` by
                # ``update-cycle.yml``).
                # ``math.isfinite`` is the strict superset of
                # ``not math.isnan`` — it rejects ``NaN`` AND ``±Inf``
                # in one check. The WGS84-range guard (-90 <= lat <= 90,
                # -180 <= lon <= 180) rejects pathological integer-
                # overflow shapes. Pairs failing either guard are
                # silently dropped (the rest of the event's address /
                # metadata is preserved); the writer-side
                # ``allow_nan=False`` pin in
                # ``src/utils/cache.py:write_cache`` is the
                # defence-in-depth second layer.
                if (
                    math.isfinite(lat)
                    and math.isfinite(lon)
                    and -90.0 <= lat <= 90.0
                    and -180.0 <= lon <= 180.0
                ):
                    location["coordinates"] = {"lat": lat, "lon": lon}
    return location


def _format_description(properties: dict[str, Any], start: datetime | None, end: datetime | None) -> str:
    info = _first_match(properties, INFO_KEYS)
    segments: list[str] = []
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


def _feature_to_event(feature: dict[str, Any]) -> ConstructionEvent | None:
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


def _collect_events(payload: dict[str, Any]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for feature in _iter_features(payload):
        event = _feature_to_event(feature)
        if not event:
            continue
        events.append(serialize_for_cache(event.to_item()))
    return events


def main() -> int:
    configure_logging()
    data_url = _resolve_data_url(os.getenv("BAUSTELLEN_DATA_URL"))
    fallback_path = _resolve_fallback_path(os.getenv("BAUSTELLEN_FALLBACK_PATH"))
    timeout_raw = os.getenv("BAUSTELLEN_TIMEOUT", "")
    timeout = DEFAULT_BAUSTELLEN_TIMEOUT
    if timeout_raw.strip():
        try:
            # Security: clamp the env override to ``MAX_BAUSTELLEN_TIMEOUT`` to
            # defeat the Slowloris vector documented at the constant declaration
            # above. The lower bound keeps the timeout finite and positive so
            # ``fetch_content_safe`` never falls back to "no read deadline".
            timeout = min(max(int(timeout_raw), 1), MAX_BAUSTELLEN_TIMEOUT)
        except ValueError:
            # Security (Path-Log Sibling Drift Round 4, env-repr closure):
            # see ``_resolve_data_url`` — same env-repr drift shape on the
            # operator-controlled ``BAUSTELLEN_TIMEOUT`` value.
            LOGGER.warning(
                "Baustellen: Ungültiger Timeout-Wert %s – verwende Standard",
                sanitize_log_arg(timeout_raw),
            )
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
