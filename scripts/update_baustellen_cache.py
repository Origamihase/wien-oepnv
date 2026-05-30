#!/usr/bin/env python3
"""Refresh the cache with construction work information for Vienna."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, cast
from collections.abc import Iterable, Sequence
from urllib.parse import quote, urlparse

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
from src.providers.baustellen import is_transit_relevant, oepnv_lead  # noqa: E402
from utils.cache import DataDegradationError, write_cache  # noqa: E402
from utils.files import loads_finite, read_capped_json  # noqa: E402
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
    "&typeName=ogdwien:BAUSTELLENLINOGD&srsName=EPSG:4326&outputFormat=json"
)

# The Stadt-Wien OGD migration (GeoServer "geoserverneuogd") split the old
# single ``BAUSTELLEOGD`` layer into two "verkehrswirksame Baustellen"
# feature types — one for line segments, one for point locations. Both are
# fetched and merged so neither geometry kind is lost.
_BAUSTELLEN_TYPENAMES: tuple[str, ...] = (
    "ogdwien:BAUSTELLENLINOGD",
    "ogdwien:BAUSTELLENPKTOGD",
)

# WFS servers disagree on the token that selects GeoJSON output: GeoServer
# accepts ``json`` / ``application/json``, MapServer wants ``geojson`` /
# ``GEOJSON``. When the configured token is not honoured the endpoint
# answers with a GML / XML ServiceException (Content-Type
# ``application/xml``) — which the strict ``_fetch_remote`` content-type
# pin then (correctly) rejects, leaving the cron stuck on the bundled
# fallback. Negotiating across the known tokens lets the fetch self-heal
# across an upstream server/config change WITHOUT relaxing that pin: each
# attempt still has to return a JSON content-type and parse as a GeoJSON
# object, so a WAF/error page never slips through.
_OUTPUT_FORMAT_CANDIDATES: tuple[str, ...] = (
    "json",
    "application/json",
    "geojson",
    "GEOJSON",
)

# Upper bound for the read-only diagnostic snippet logged when the WFS
# refuses every outputFormat. An OGC ``ServiceExceptionReport`` naming the
# offending parameter fits comfortably in 2 KiB; the cap keeps a hostile /
# oversized upstream body from being buffered into the log.
_DIAGNOSTIC_MAX_BYTES = 2048
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
    "PRESSETEXT",
    "HINWEIS",
    "INFO",
    "BESCHREIBUNG",
    "BEMERKUNG",
    "DETAIL",
    "DETAILS",
    "ANMERKUNG",
)
START_KEYS: tuple[str, ...] = (
    "OBJEKT_BEGINN",
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
    "OBJEKT_ENDE",
    "ENDEZEIT",
    "ENDE",
    "END_DATUM",
    "ENDZEIT",
    "ENDE_DATUM",
    "DATUM_BIS",
    "BIS_DATUM",
    # NB: bare "BIS" is intentionally NOT an end-date key. It is a spatial
    # "to" descriptor (sibling of BIS_NR / BIS_KM / ABSCHNITT_BIS in
    # TO_KEYS); the explicit date variants are DATUM_BIS / BIS_DATUM. Since
    # _parse_datetime uses the lenient dateutil parser, a spatial value such
    # as a bare house number ("22") would otherwise be misread as a date
    # (day-of-current-month), planting a bogus ends_at/GUID and risking the
    # item being aged out as already-expired.
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
            # ``starts_at`` is a schema-required key (events.schema.json) that
            # every other provider (WL/ÖBB/Stammstrecke) always emits, with a
            # null value when unknown. Emit it unconditionally here too — the
            # former ``if self.starts_at`` guard dropped the key entirely for a
            # Baustelle without a parseable start date, producing a cache item
            # that violates the published schema. ``ends_at`` stays optional
            # (it is NOT in the schema's ``required`` set).
            "starts_at": self.starts_at,
        }
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
        # Security: ``loads_finite`` pins parse_constant + parse_float
        # hooks (Round 1503 sibling) that reject NaN / Infinity / 1e1000
        # literals from a compromised ``data.wien.gv.at`` upstream / MITM
        # — the canonical-floor coordinate validator at
        # ``_build_location`` (Round 2026-05-14) rejects NaN ONCE, but
        # the rejection happens AFTER the planted float already poisoned
        # in-memory comparison sites and any non-coordinate float fields
        # (e.g. road-segment length, severity score) leak through.
        payload = loads_finite(content.decode("utf-8"))
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


def _log_endpoint_diagnostic(url: str, timeout: int) -> None:
    """Best-effort: log a short, sanitised snippet of a refusing WFS
    response so an operator can see *why* the endpoint rejects the request
    (typically an OGC ``ServiceExceptionReport`` naming the bad
    ``typeName`` / version).

    Strictly read-only and bounded — it reads at most
    ``_DIAGNOSTIC_MAX_BYTES``, follows no redirects, and the bytes are only
    sanitised and logged, never parsed as data or returned. The data path
    keeps refusing non-JSON content types unchanged; this is pure
    observability. Every error is swallowed so a diagnostic hiccup can
    never break the cron flow.
    """
    if not validate_http_url(url):
        return
    try:
        with session_with_retries(USER_AGENT, raise_on_status=False) as session:
            with session.get(
                url,
                timeout=timeout,
                stream=True,
                allow_redirects=False,
                headers={"Accept": "application/json"},
            ) as response:
                status = response.status_code
                content_type = response.headers.get("Content-Type", "?")
                chunk = next(response.iter_content(_DIAGNOSTIC_MAX_BYTES), b"")
    except (RequestException, OSError, ValueError) as exc:
        LOGGER.info(
            "Baustellen: Endpoint-Diagnose nicht möglich (%s)",
            sanitize_log_arg(str(exc)),
        )
        return
    snippet = chunk.decode("utf-8", errors="replace").strip()
    if snippet:
        LOGGER.warning(
            "Baustellen: Endpoint-Diagnose – HTTP %s, Content-Type %s, Auszug: %s",
            status,
            sanitize_log_arg(content_type),
            sanitize_log_arg(snippet),
        )


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


def _with_output_format(url: str, output_format: str) -> str:
    """Return ``url`` with its ``outputFormat`` query value set to
    ``output_format`` (appended if absent).

    Only the ``outputFormat`` token is rewritten — every other byte of the
    URL (scheme, host, ``typeName``/``srsName`` colons, …) is left
    untouched, so the host pin already applied by :func:`_resolve_data_url`
    still holds and ``_fetch_remote`` re-validates the result anyway. The
    value is percent-encoded (``/`` kept readable, as WFS endpoints expect
    for ``application/json``).
    """
    encoded = quote(output_format, safe="/")
    if re.search(r"(?i)[?&]outputFormat=", url):
        return re.sub(r"(?i)([?&]outputFormat=)[^&]*", lambda m: m.group(1) + encoded, url, count=1)
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}outputFormat={encoded}"


def _with_typename(url: str, typename: str) -> str:
    """Return ``url`` with its ``typeName``/``typeNames`` query value set to
    ``typename`` (appended if absent).

    Only the type-name token is rewritten — scheme/host/path and the other
    parameters are preserved, so the host pin from :func:`_resolve_data_url`
    still holds (``_fetch_remote`` re-validates regardless). The ``ogdwien:``
    workspace colon is kept readable.
    """
    encoded = quote(typename, safe=":")
    if re.search(r"(?i)[?&]typeNames?=", url):
        return re.sub(
            r"(?i)([?&]typeNames?=)[^&]*", lambda m: m.group(1) + encoded, url, count=1
        )
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}typeName={encoded}"


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
        except (ValueError, OSError, OverflowError):
            # ``OverflowError`` (NOT a ValueError/OSError subclass) is raised by
            # ``datetime.fromtimestamp`` for an out-of-range epoch — e.g. a
            # garbled WFS ``BEGINN``/``ENDE`` numeric field carrying ``1e20``
            # (``loads_finite`` admits any finite number). Without this catch it
            # propagated through ``_feature_to_event`` → ``_collect_events`` →
            # ``main`` and crashed the whole baustellen cache update. Mirrors the
            # ``OverflowError`` already caught in the string/dateutil branch below.
            return None
    # value is narrowed to str here by the type annotation and prior branches
    candidate = value.strip()
    if not candidate:
        return None
    # Stadt-Wien WFS emits date-only values with a bare trailing ``Z``
    # (e.g. ``2026-03-22Z``). ``dateutil`` rejects that shape, so we
    # parse the YYYY-MM-DD directly. The trailing ``Z`` here is a
    # date-shape marker — NOT a UTC tz indicator. Stadt Wien operates
    # the WFS in Europe/Vienna local time, so treating ``Z`` as UTC
    # midnight then converting to Vienna shifted the displayed
    # timestamp by 1-2 h (and could shift the *date* across DST). Parse
    # the date directly as Vienna-local midnight.
    date_only_z = re.fullmatch(r"(\d{4}-\d{2}-\d{2})Z", candidate)
    if date_only_z:
        try:
            return datetime.strptime(
                date_only_z.group(1), "%Y-%m-%d"
            ).replace(tzinfo=VIENNA_TZ)
        except ValueError:
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
    measure = (
        properties.get("BEHINDERUNGSART")
        or properties.get("VERKEHRSMASSNAHME")
        or properties.get("VERKEHRSART")
    )
    if measure:
        context["measure"] = str(measure).strip()
    status = properties.get("STATUS")
    if status:
        context["status"] = str(status).strip()
    return context


# GeoJSON geometries arrive as Point (``[lon, lat]``), LineString
# (``[[lon, lat], ...]``), Polygon (``[[[lon, lat], ...]]``) or their
# Multi* variants. The station-proximity test only needs one
# representative coordinate, so we descend to the first vertex. The depth
# is bounded so a maliciously deep nested array from a compromised
# ``data.wien.gv.at`` upstream cannot drive unbounded recursion.
_MAX_COORD_DEPTH = 8


def _first_lonlat(coordinates: Any, _depth: int = 0) -> tuple[float, float] | None:
    """Return the first ``(lon, lat)`` pair from a GeoJSON coordinate array."""
    if _depth > _MAX_COORD_DEPTH:
        return None
    if not isinstance(coordinates, list | tuple) or not coordinates:
        return None
    first = coordinates[0]
    if isinstance(first, bool):
        return None
    if isinstance(first, int | float):
        # Leaf level: a coordinate needs at least a lon/lat pair.
        if len(coordinates) < 2:
            return None
        try:
            return float(coordinates[0]), float(coordinates[1])
        except (TypeError, ValueError):
            return None
    return _first_lonlat(first, _depth + 1)


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
        point = _first_lonlat(geometry.get("coordinates"))
        if point is not None:
            lon, lat = point
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
        # Lead with the public-transport sentence so the ÖPNV impact
        # survives the feed's description truncation.
        segments.append(oepnv_lead(info))
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
    # The Baustellen GUID must stay STABLE for the same physical construction
    # site across WFS refreshes: it keys ``first_seen``, and a changing GUID
    # makes the site look brand-new on every change, resetting first_seen so it
    # perpetually dominates the first_seen-sorted feed. ``OBJECTID`` / ``ID`` are
    # ArcGIS query-time row identifiers that are NOT stable across the upstream's
    # re-indexing — observed churning 3x in two days for the *same* site
    # (identical title + start + end, three different OBJECTIDs). Derive the
    # identity from a stable open-data id when the layer offers one, otherwise
    # from the stable title; never from the volatile OBJECTID/ID row number.
    identifier = properties.get("OGD_ID") or title
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


def _fetch_layers(data_url: str, timeout: int) -> list[dict[str, Any]] | None:
    """Fetch every Baustellen feature type and return the merged events.

    For each type name the GeoJSON ``outputFormat`` is negotiated (the
    configured token first, then the common server-specific variants),
    stopping at the first that returns a parseable GeoJSON object. Returns
    ``None`` only when NO layer could be fetched (the caller then falls
    back to the bundled sample); a partial success (one of two layers)
    still returns the events it got.
    """
    merged: list[dict[str, Any]] = []
    any_success = False
    for typename in _BAUSTELLEN_TYPENAMES:
        layer_url = _with_typename(data_url, typename)
        payload = None
        for output_format in _OUTPUT_FORMAT_CANDIDATES:
            payload = _fetch_remote(_with_output_format(layer_url, output_format), timeout)
            if payload is not None:
                break
        if payload is None:
            LOGGER.warning("Baustellen: Layer %s nicht abrufbar.", typename)
            continue
        any_success = True
        merged.extend(_collect_events(payload))
    return merged if any_success else None


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
    events = _fetch_layers(data_url, timeout)
    used_fallback = False
    if events is None:
        used_fallback = True
        # Every layer was refused — capture WHY (the OGC exception body)
        # before falling back, so the operator log can pinpoint a renamed
        # typeName / unsupported version.
        _log_endpoint_diagnostic(
            _with_typename(data_url, _BAUSTELLEN_TYPENAMES[0]), timeout
        )
        payload = _load_fallback(fallback_path)
        if payload is None:
            LOGGER.error(
                "Baustellen: Live-Abruf UND Fallback fehlgeschlagen – Cache "
                "nicht aktualisiert."
            )
            return 1
        events = _collect_events(payload)
    # Keep only ÖPNV-relevant sites: at/near a rail Bahnhof OR a text that
    # mentions public transport (stop / line / bus / tram / metro). The
    # upstream feed is "verkehrswirksam" but still includes pure car-traffic
    # works, which would bury the ÖPNV signal the feed exists to carry.
    relevant = [event for event in events if is_transit_relevant(event)]
    skipped = len(events) - len(relevant)
    if skipped:
        LOGGER.info(
            "Baustellen: %d von %d Meldung(en) ohne ÖPNV-Bezug verworfen.",
            skipped,
            len(events),
        )
    # Empty payload would trigger ``DataDegradationError`` in
    # ``write_cache`` when a populated cache already exists, and the
    # uncaught error would crash the cron step. Skip the write
    # instead — the pinned previous cache stays valid and the
    # non-zero exit surfaces the issue to the cron wrapper.
    if not relevant:
        LOGGER.warning(
            "Baustellen: 0 ÖPNV-relevante Einträge nach Filter – "
            "Cache wird NICHT überschrieben, gepinnter Snapshot bleibt aktiv."
        )
        return 1
    try:
        write_cache("baustellen", relevant)
    except DataDegradationError:
        # ``write_cache`` refuses not only *empty* but also *drastically
        # smaller* payloads (< 20 % of the existing cache). The bundled
        # fallback sample holds only a couple of features, so when the live
        # WFS fetch fails against a populated production cache the write would
        # raise — and this is exactly the scenario the fallback exists for.
        # Treat it like the empty-payload guard above: keep the pinned
        # snapshot, surface a non-zero exit, never crash the cron step.
        LOGGER.warning(
            "Baustellen: Schreiben würde den Cache drastisch degradieren "
            "(%d Eintrag/Einträge) – gepinnter Snapshot bleibt aktiv.",
            len(relevant),
        )
        return 1
    if used_fallback:
        # Exit 2 = "degraded": the cache was written from the bundled
        # fallback sample, NOT a live fetch. The cron wrapper maps any
        # non-zero exit to a visible ``::warning`` so this failure mode
        # stops hiding behind an INFO "Cache aktualisiert" success line.
        LOGGER.warning(
            "Baustellen: Live-Abruf fehlgeschlagen – Cache nutzt FALLBACK-"
            "Demodaten (%d Eintrag/Einträge, kein ÖPNV-Live-Signal). "
            "WFS-Endpoint prüfen.",
            len(relevant),
        )
        return 2
    LOGGER.info("Baustellen: Cache mit %d Einträgen aktualisiert.", len(relevant))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
