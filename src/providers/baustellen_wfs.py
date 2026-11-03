"""Stadt-Wien Baustellen WFS: endpoint, URL/path resolution and layer checks.

Moved verbatim from ``scripts/update_baustellen_cache.py``; the script
re-exports every name, so ``scripts.update_baustellen_cache.X`` still works.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

from ..utils.env import _path_fingerprint
from ..utils.http import validate_http_url
from ..utils.logging import sanitize_log_arg
from ..utils.source_shape import MIN_RECORDS, missing_fields
from .baustellen_events import INFO_KEYS, _feature_list, _first_lonlat, _first_match, _parse_range

# ``__all__`` lists every name this module defines. Some private names are
# read only by other modules and tests; CodeQL (py/unused-global-variable)
# counts such a read through an import only when the name is listed here.
__all__ = [
    "REPO_ROOT", "LOGGER", "DEFAULT_DATA_URL", "_BAUSTELLEN_TYPENAMES", "_OUTPUT_FORMAT_CANDIDATES",
    "DEFAULT_FALLBACK_PATH", "_BAUSTELLEN_TRUSTED_HOSTS", "_validated_baustellen_data_url",
    "_resolve_data_url", "_with_output_format", "_with_typename", "_resolve_fallback_path",
    "_LAYER_MIN", "_properties", "_feature_position", "_LAYER_REQUIRED", "_usable_layer",
    "_from_snapshot",
]


# Same value as ``REPO_ROOT`` in ``scripts/update_baustellen_cache.py``.
REPO_ROOT = Path(__file__).resolve().parents[2]

# Same logger as the script: its records keep the name ``update_baustellen_cache``.
LOGGER = logging.getLogger("update_baustellen_cache")


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
DEFAULT_FALLBACK_PATH = REPO_ROOT / "data" / "samples" / "baustellen_sample.geojson"


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
    return safe


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


# ---------------- Ausfall eines Teils der Quelle ----------------
#
# Die Baustellen kommen aus zwei WFS-Layern (Linien und Punkte). Fiel einer
# aus, ging der Abruf früher mit dem anderen allein durch: Am echten Stand
# vom 2026-10-04 schrumpfte der Cache ohne Linien-Layer von 15 auf 5
# Baustellen, mit Exit-Code 0. Dasselbe bei einer Antwort, der ein Feld
# fehlt (``src/utils/source_shape.py``): ohne Datumsfelder belegten
# Baustellen neun der zehn Plätze. Jetzt kommt ein unbrauchbarer oder leerer
# Layer (wenn seine letzte gute Antwort nicht auch leer war) aus
# seiner letzten guten Antwort (``data/raw/baustellen/``), Exit-Code 3. Fehlt
# die oder fallen alle Layer aus, bleibt der Cache stehen.

_LAYER_MIN = MIN_RECORDS


def _properties(feature: dict[str, Any]) -> dict[str, Any]:
    properties = feature.get("properties")
    return properties if isinstance(properties, dict) else {}


def _feature_position(feature: dict[str, Any]) -> tuple[float, float] | None:
    geometry = feature.get("geometry")
    return _first_lonlat(geometry.get("coordinates")) if isinstance(geometry, dict) else None


_LAYER_REQUIRED = {
    "Beginn": (lambda f: _parse_range(_properties(f))[0] is not None, _LAYER_MIN),
    "Ende": (lambda f: _parse_range(_properties(f))[1] is not None, _LAYER_MIN),
    "Text": (lambda f: _first_match(_properties(f), INFO_KEYS) is not None, _LAYER_MIN),
    "Lage": (lambda f: _feature_position(f) is not None, _LAYER_MIN),
}


def _usable_layer(payload: Any, name: str) -> bool:
    """True when *payload* is a WFS answer with features in the expected shape."""
    if not isinstance(payload, dict):
        return False
    features = _feature_list(payload)
    if features is None:
        LOGGER.warning("Baustellen: Layer %s ohne Feature-Liste – Antwort unbrauchbar.", name)
        return False
    missing = missing_fields(features, _LAYER_REQUIRED)
    if missing:
        LOGGER.warning(
            "Baustellen: Layer %s – Angabe(n) %s fehlen in allen %d Einträgen – Antwort unbrauchbar.",
            name,
            ", ".join(missing),
            len(features),
        )
        return False
    return True


def _from_snapshot(snapshot: Any) -> dict[str, Any] | None:
    """A raw snapshot (``_raw_snapshot``) back in the shape of a WFS answer.

    The snapshot keeps of each geometry only its first position, which is
    all the cache uses (``_first_lonlat``).
    """
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("features"), list):
        return None
    features: list[Any] = []
    for feature in snapshot["features"]:
        if not isinstance(feature, dict):
            continue
        record = dict(feature)
        geometry = feature.get("geometry")
        if isinstance(geometry, dict) and "first_position" in geometry:
            position = geometry.get("first_position")
            record["geometry"] = {
                "type": geometry.get("type"),
                "coordinates": [position] if position is not None else None,
            }
        features.append(record)
    return {"type": "FeatureCollection", "features": features}
