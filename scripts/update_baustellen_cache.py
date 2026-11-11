#!/usr/bin/env python3
"""Refresh the cache with construction work information for Vienna."""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

from requests.exceptions import RequestException

REPO_ROOT = Path(__file__).resolve().parents[1]
# Only the repository root goes on the path: every project module is
# imported once, as ``src.…`` (docs/development.md, "Skripte im Überblick").
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.feed.logging_safe import setup_script_logging  # noqa: E402
from src.providers.baustellen import is_transit_relevant  # noqa: E402
from src.utils import raw_capture  # noqa: E402
from src.utils.cache import DataDegradationError, cache_modified_at, write_cache  # noqa: E402
from src.utils.env import _path_fingerprint  # noqa: E402
from src.utils.files import loads_finite, read_capped_json  # noqa: E402
from src.utils.http import fetch_content_safe, session_with_retries, validate_http_url  # noqa: E402
from src.utils.logging import sanitize_log_arg  # noqa: E402
from src.utils.stats_baustellen import append_baustellen_rows  # noqa: E402
# ``as`` re-exports explicitly: the feature parsing and the WFS helpers
# live in ``src/providers/``; tests and callers read them from here.
from src.providers.baustellen_events import (  # noqa: E402
    DEFAULT_INFO_URL as DEFAULT_INFO_URL,
    VIENNA_TZ as VIENNA_TZ,
    TITLE_KEYS as TITLE_KEYS,
    STREET_KEYS as STREET_KEYS,
    FROM_KEYS as FROM_KEYS,
    TO_KEYS as TO_KEYS,
    INFO_KEYS as INFO_KEYS,
    START_KEYS as START_KEYS,
    END_KEYS as END_KEYS,
    ConstructionEvent as ConstructionEvent,
    _iter_features as _iter_features,
    _feature_list as _feature_list,
    _first_match as _first_match,
    _parse_datetime as _parse_datetime,
    _parse_range as _parse_range,
    _UTC_DATE_RE as _UTC_DATE_RE,
    _start_is_utc_date as _start_is_utc_date,
    _first_day as _first_day,
    _normalize_datetime as _normalize_datetime,
    _build_context as _build_context,
    _MAX_COORD_DEPTH as _MAX_COORD_DEPTH,
    _first_lonlat as _first_lonlat,
    _build_location as _build_location,
    _format_description as _format_description,
    _UPSTREAM_TITLE_CAP as _UPSTREAM_TITLE_CAP,
    _mark_upstream_truncation as _mark_upstream_truncation,
    _feature_to_event as _feature_to_event,
    _RAW_VOLATILE_PROPERTIES as _RAW_VOLATILE_PROPERTIES,
    _raw_snapshot as _raw_snapshot,
    _raw_sort_key as _raw_sort_key,
    _collect_events as _collect_events,
)
from src.providers.baustellen_wfs import (  # noqa: E402
    DEFAULT_DATA_URL as DEFAULT_DATA_URL,
    _BAUSTELLEN_TYPENAMES as _BAUSTELLEN_TYPENAMES,
    _OUTPUT_FORMAT_CANDIDATES as _OUTPUT_FORMAT_CANDIDATES,
    DEFAULT_FALLBACK_PATH as DEFAULT_FALLBACK_PATH,
    _BAUSTELLEN_TRUSTED_HOSTS as _BAUSTELLEN_TRUSTED_HOSTS,
    _validated_baustellen_data_url as _validated_baustellen_data_url,
    _resolve_data_url as _resolve_data_url,
    _with_output_format as _with_output_format,
    _with_typename as _with_typename,
    _resolve_fallback_path as _resolve_fallback_path,
    _LAYER_MIN as _LAYER_MIN,
    _properties as _properties,
    _feature_position as _feature_position,
    _LAYER_REQUIRED as _LAYER_REQUIRED,
    _usable_layer as _usable_layer,
    _from_snapshot as _from_snapshot,
)

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


# Upper bound for the read-only diagnostic snippet logged when the WFS
# refuses every outputFormat. An OGC ``ServiceExceptionReport`` naming the
# offending parameter fits comfortably in 2 KiB; the cap keeps a hostile /
# oversized upstream body from being buffered into the log.
_DIAGNOSTIC_MAX_BYTES = 2048
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
# ``src/feed/config.py``.
DEFAULT_BAUSTELLEN_TIMEOUT = 20
MAX_BAUSTELLEN_TIMEOUT = DEFAULT_BAUSTELLEN_TIMEOUT


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


_fallback_layers: list[str] = []


def _fetch_layers(data_url: str, timeout: int) -> list[dict[str, Any]] | None:
    """Fetch every Baustellen feature type and return the merged events.

    For each type name the GeoJSON ``outputFormat`` is negotiated (the
    configured token first, then the common server-specific variants),
    stopping at the first that returns a parseable GeoJSON object. A layer
    without a usable answer comes from its last good one (see above).
    Returns ``None`` when a layer has neither, or when no layer answered:
    the caller then keeps the cache.
    """
    _fallback_layers.clear()
    merged: list[dict[str, Any]] = []
    for typename in _BAUSTELLEN_TYPENAMES:
        layer = typename.rsplit(":", 1)[-1]
        layer_url = _with_typename(data_url, typename)
        payload = None
        for output_format in _OUTPUT_FORMAT_CANDIDATES:
            payload = _fetch_remote(_with_output_format(layer_url, output_format), timeout)
            if payload is not None:
                break
        live = payload if payload is not None and _usable_layer(payload, layer) else None
        last = _from_snapshot(raw_capture.read_snapshot("baustellen", layer))
        if last is not None and not _usable_layer(last, layer):
            last = None
        if live is not None and (
            _feature_list(live) or last is None or len(_feature_list(last) or []) < _LAYER_MIN
        ):
            # An empty layer counts only when its last good answer was
            # (nearly) empty too: both layers always list long-running sites.
            raw_capture.write_snapshot("baustellen", layer, _raw_snapshot(live))
            used = live
        elif last is not None:
            LOGGER.warning(
                "Baustellen: Layer %s %s – letzte gute Antwort verwendet.",
                layer,
                "leer" if live is not None else "nicht abrufbar",
            )
            _fallback_layers.append(layer)
            used = last
        else:
            LOGGER.warning("Baustellen: Layer %s nicht abrufbar.", typename)
            return None
        merged.extend(_collect_events(used))
    if len(_fallback_layers) == len(_BAUSTELLEN_TYPENAMES):
        return None
    return merged


def _cache_exists() -> bool:
    return cache_modified_at("baustellen") is not None


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
    raw_capture.reset_drops("baustellen")
    events = _fetch_layers(data_url, timeout)
    used_fallback = False
    if events is None and _cache_exists():
        # The bundled demo sample is for a checkout without a cache. Written
        # over a real cache it replaced every real construction site with two
        # from 2025 whenever the cache held ten or fewer (the 20 % guard of
        # ``write_cache`` let 2 of 10 through).
        LOGGER.warning(
            "Baustellen: Live-Abruf fehlgeschlagen – bestehender Cache bleibt stehen."
        )
        return 1
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
    if not used_fallback and not events:
        # The city delivered no site at all: both layers always list
        # long-running works, so this is an outage, not a quiet day. Only a
        # selection the transit filter emptied may empty the cache (below).
        LOGGER.warning(
            "Baustellen: Stadt Wien lieferte 0 Baustellen – bestehender Cache bleibt stehen."
        )
        return 1
    # Keep only sites whose text names an effect on public transport (a stop
    # moved or closed, a line diverted or shortened, replacement buses). The
    # upstream feed is "verkehrswirksam" but mostly carries car-traffic works,
    # which would take the few feed places from real disruptions. Rule and
    # evidence: ``src/providers/baustellen.py``.
    relevant = []
    for event in events:
        if is_transit_relevant(event):
            relevant.append(event)
        else:
            raw_capture.note_drop("baustellen", "ohne ÖPNV-Bezug", event.get("title"))
    if not used_fallback and not _fallback_layers:
        # The drops of the bundled demo sample or of a last good answer say
        # nothing about this fetch.
        raw_capture.write_drops("baustellen")
    skipped = len(events) - len(relevant)
    if skipped:
        LOGGER.info(
            "Baustellen: %d von %d Meldung(en) ohne ÖPNV-Bezug verworfen.",
            skipped,
            len(events),
        )
    # A live answer was judged per layer above (``_usable_layer``, minimum
    # record count, last good answer) and holds at least one site. What is
    # left after the transit filter is a selection, and it can honestly be
    # small or empty: on many days no
    # site in Vienna moves a stop. So a live write may shrink the cache, down
    # to nothing; the 20 % guard of ``write_cache`` would otherwise keep sites
    # that no longer qualify (or have ended) and fail every tick. The bundled
    # demo sample keeps the guard and the old refusal of an empty write.
    if used_fallback and not relevant:
        LOGGER.warning(
            "Baustellen: 0 ÖPNV-relevante Einträge nach Filter – "
            "Cache wird NICHT überschrieben, gepinnter Snapshot bleibt aktiv."
        )
        return 1
    try:
        write_cache("baustellen", relevant, allow_shrink=not used_fallback)
    except DataDegradationError:
        # Only reachable for the demo sample: ``write_cache`` refuses a payload
        # below 20 % of a populated cache. Keep the pinned snapshot, surface a
        # non-zero exit, never crash the cron step.
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
    # Yearly Baustellen ledger (owner's decision of 2026-11-10): every site
    # the city delivered that passed the transit rule above gets one row per
    # year in ``data/stats/baustellen_<year>.csv``, with its text as it stood
    # at that first sighting. The writer leaves out a site whose last day
    # lies before 1 January of the running year (the city can still deliver
    # a site on the day after its end): the cache above holds what the city
    # sent, the ledger what was under way in the year. Written here and not in
    # the feed build, because the feed drops a site that a running WL notice
    # already reports (twins rule, 2026-10-09). Reached for the city's own
    # data only: the bundled demo sample has returned above, while a layer
    # taken from its last good answer (exit 3 below) still holds real sites,
    # each entered once by its guid. The writer never raises and returns
    # nothing this flow uses; no branch of its own, ``main`` is at the C901
    # limit.
    append_baustellen_rows(relevant)
    if _fallback_layers:
        # Exit 3 = cache written, but a layer came from its last good answer.
        LOGGER.warning(
            "Baustellen: Cache mit %d Einträgen aktualisiert; %s aus der letzten guten Antwort.",
            len(relevant),
            ", ".join(_fallback_layers),
        )
        return 3
    LOGGER.info("Baustellen: Cache mit %d Einträgen aktualisiert.", len(relevant))
    return 0


# Names other files and tests read through this script; the code lives in
# src/ (700-line limit, 2026-11-02). Listing them marks the imports as
# deliberate re-exports.
__all__ = [
    "REPO_ROOT",
    "MAX_JSON_FILE_BYTES",
    "LOGGER",
    "_DIAGNOSTIC_MAX_BYTES",
    "USER_AGENT",
    "DEFAULT_BAUSTELLEN_TIMEOUT",
    "MAX_BAUSTELLEN_TIMEOUT",
    "configure_logging",
    "_load_json_from_content",
    "_fetch_remote",
    "_log_endpoint_diagnostic",
    "_load_fallback",
    "_fallback_layers",
    "_fetch_layers",
    "_cache_exists",
    "main",
    "setup_script_logging",
    "is_transit_relevant",
    "raw_capture",
    "DataDegradationError",
    "cache_modified_at",
    "write_cache",
    "_path_fingerprint",
    "loads_finite",
    "read_capped_json",
    "fetch_content_safe",
    "session_with_retries",
    "validate_http_url",
    "sanitize_log_arg",
    "append_baustellen_rows",
    "_feature_list",
    "_raw_snapshot",
    "_collect_events",
    "_BAUSTELLEN_TYPENAMES",
    "_OUTPUT_FORMAT_CANDIDATES",
    "_resolve_data_url",
    "_with_output_format",
    "_with_typename",
    "_resolve_fallback_path",
    "_LAYER_MIN",
    "_usable_layer",
    "_from_snapshot",
    "DEFAULT_INFO_URL",
    "VIENNA_TZ",
    "TITLE_KEYS",
    "STREET_KEYS",
    "FROM_KEYS",
    "TO_KEYS",
    "INFO_KEYS",
    "START_KEYS",
    "END_KEYS",
    "ConstructionEvent",
    "_iter_features",
    "_first_match",
    "_parse_datetime",
    "_parse_range",
    "_UTC_DATE_RE",
    "_start_is_utc_date",
    "_first_day",
    "_normalize_datetime",
    "_build_context",
    "_MAX_COORD_DEPTH",
    "_first_lonlat",
    "_build_location",
    "_format_description",
    "_UPSTREAM_TITLE_CAP",
    "_mark_upstream_truncation",
    "_feature_to_event",
    "_RAW_VOLATILE_PROPERTIES",
    "_raw_sort_key",
    "DEFAULT_DATA_URL",
    "DEFAULT_FALLBACK_PATH",
    "_BAUSTELLEN_TRUSTED_HOSTS",
    "_validated_baustellen_data_url",
    "_properties",
    "_feature_position",
    "_LAYER_REQUIRED",
]


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
