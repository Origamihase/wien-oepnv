"""GTFS-Realtime monitoring of the Vienna S-Bahn Stammstrecke.

The provider polls the official ÖBB GTFS-Realtime ``TripUpdates`` feed,
filters trip updates that are *currently* travelling through the
Stammstrecke corridor, and yields a single consolidated event when the
average delay across that corridor exceeds nine minutes.

Why a separate provider rather than another VOR/HAFAS query? The VOR
endpoint imposes strict per-tenant rate limits that are easy to exhaust
on a 5-minute polling cadence; ÖBB's GTFS-Realtime feed is a static
binary endpoint with caching headers that we can poll cheaply. ÖBB
publishes the feed for free under the open-data licence.

Design contract:

* Returns at most ONE :class:`FeedItem`. The feed builder consumes a
  list of items per provider; an empty list naturally drops the alert
  out of the merged feed when the corridor recovers (the "self-heal"
  property the spec calls for).
* Stateless: the provider keeps no on-disk state, so when the average
  drops back to the threshold the next run produces an empty list and
  the alert disappears with the next merged-feed write.
* Resilient: every fetch / parse step is wrapped in ``try/except`` and
  logged. Malformed protobuf, timeouts, and breaker-open errors all
  collapse to an empty result instead of crashing the cron pipeline.

Required ÖBB GTFS stop ids are resolved at runtime from
``data/gtfs/stops.txt`` (the static GTFS feed companion of the
realtime feed). Each Stammstrecke station maps to a frozen set of
``stop_id`` values — typically one for the parent station plus one
per platform.
"""

from __future__ import annotations

import logging
import os
import re
import unicodedata
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast
from collections.abc import Iterable, Sequence
from urllib.parse import urlparse

import requests

from ..feed_types import FeedItem
from ..utils.circuit_breaker import CircuitBreaker, CircuitBreakerOpen
from ..utils.http import fetch_content_safe, session_with_retries, validate_http_url
from ..utils.ids import make_guid
from ..utils.logging import sanitize_log_arg

if TYPE_CHECKING:  # pragma: no cover - typing only
    from pathlib import Path

log = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_GTFS_RT_URL",
    "STAMMSTRECKE_STATION_NAMES",
    "STAMMSTRECKE_THRESHOLD_MINUTES",
    "USER_AGENT",
    "build_event",
    "calculate_average_delay_minutes",
    "fetch_events",
    "iter_corridor_delays",
    "load_stop_id_index",
    "parse_feed_message",
]


# ÖBB publishes GTFS-RT TripUpdates at this canonical URL. ``ÖBB_GTFS_RT_URL``
# may override it but is validated against the trusted host allow-list
# below so an env override (compromised secret store / leaked CI env)
# cannot redirect the cron pipeline to an attacker host.
DEFAULT_GTFS_RT_URL = "https://realtime.oebb.at/gtfs-rt/tripUpdates"

_TRUSTED_GTFS_RT_HOSTS: frozenset[str] = frozenset({"realtime.oebb.at", "data.oebb.at"})

USER_AGENT = "Origamihase-wien-oepnv-stammstrecke/1.0 (+https://github.com/Origamihase/wien-oepnv)"

# Slowloris-defence ceiling. Mirrors ``MAX_OEBB_FETCH_TIMEOUT`` (25s)
# documented in ``src/providers/oebb.py`` — same parameter-boundary
# contract, same justification.
MAX_FETCH_TIMEOUT = 25

# Per-fetch payload cap. The ÖBB GTFS-RT feed sits at ~1-3 MiB
# (compressed) for the entire Austrian network; 8 MiB is ~3-4x
# production state and well below the 10 MiB ``MAX_PAYLOAD_SIZE``.
_MAX_PAYLOAD_BYTES = 8 * 1024 * 1024

# Threshold at which the consolidated event is emitted. The spec
# requires *strictly greater than* nine minutes — equality MUST NOT
# trigger an event so the corridor stays clean during baseline jitter.
STAMMSTRECKE_THRESHOLD_MINUTES = 9

# Canonical Stammstrecke station names. The order matches the official
# ÖBB northbound→southbound corridor sequence (Floridsdorf ↔ Meidling).
STAMMSTRECKE_STATION_NAMES: tuple[str, ...] = (
    "Wien Floridsdorf",
    "Wien Handelskai",
    "Wien Traisengasse",
    "Wien Praterstern",
    "Wien Mitte",
    "Wien Rennweg",
    "Wien Quartier Belvedere",
    "Wien Hauptbahnhof",
    "Wien Matzleinsdorfer Platz",
    "Wien Meidling",
)

_STAMMSTRECKE_LABEL = "S-Bahn Stammstrecke"
_BREAKER = CircuitBreaker(
    "gtfs_stammstrecke",
    failure_threshold=5,
    recovery_timeout=300.0,
)


@dataclass(frozen=True)
class CorridorDelay:
    """Per-trip delay snapshot used to compute the corridor average."""

    trip_id: str
    delay_seconds: int
    stop_ids: frozenset[str]


@dataclass(frozen=True)
class StammstreckeStateSnapshot:
    """Aggregated outcome of a single GTFS-RT poll."""

    average_delay_minutes: float
    active_trips: int
    sampled_delays: tuple[CorridorDelay, ...] = field(default_factory=tuple)


def _validated_gtfs_rt_url(raw: str) -> str | None:
    safe = validate_http_url(raw)
    if not safe:
        return None
    host = (urlparse(safe).hostname or "").lower()
    if host not in _TRUSTED_GTFS_RT_HOSTS:
        return None
    return safe


def _resolve_endpoint() -> str:
    raw = os.getenv("OEBB_GTFS_RT_URL", "").strip()
    if not raw:
        return DEFAULT_GTFS_RT_URL
    safe = _validated_gtfs_rt_url(raw)
    if safe is None:
        log.warning(
            "OEBB_GTFS_RT_URL %s ist kein bekannter ÖBB-GTFS-RT-Host; verwende Standard.",
            sanitize_log_arg(raw),
        )
        return DEFAULT_GTFS_RT_URL
    return safe


# ---------------------------------------------------------------------------
# Stop id resolution
# ---------------------------------------------------------------------------


def _normalize_station_name(name: str) -> str:
    """Return *name* in a comparison-friendly form.

    Casefolding + accent stripping + collapsed whitespace + dropped
    Bahnhof/Bf/Hbf suffixes — matches the project convention used by
    ``scripts/update_station_directory.py:_normalize_location_keys``.
    Compound forms (``Hauptbahnhof``, ``Westbahnhof``, ``Südbahnhof``)
    collapse to the same canonical key so the corridor mapping treats
    "Wien Hauptbahnhof" / "Wien Hbf" / "Wien Hauptbahnhof Bf" as one
    station.
    """
    text = unicodedata.normalize("NFKD", name)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace("ß", "ss").casefold()
    # Strip compound *bahnhof* forms before the standalone variants —
    # otherwise "hauptbahnhof" stays intact (the trailing ``\b`` of
    # ``bahnhof`` does not match between two word chars).
    text = re.sub(
        r"(?:haupt|west|ost|nord|sued|sud|s-|s)?bahnhof",
        "",
        text,
    )
    text = re.sub(r"\b(?:bahnhst|bhf|hbf|bf)\b", "", text)
    text = text.replace("-", " ").replace("/", " ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s{2,}", " ", text).strip()


def _read_gtfs_stops(path: Path) -> dict[str, object]:
    """Locally-imported wrapper for ``scripts.gtfs.read_gtfs_stops``.

    The GTFS reader lives under ``scripts/`` to keep test-only utilities
    out of the runtime ``src/`` tree. We import it lazily so importing
    this module never touches disk and never crashes if the script
    happens to be missing in a packaging context.
    """
    # Import lazily inside the function: scripts/gtfs.py is a
    # script-package module, not part of ``src/``. Importing it at
    # module load time would couple the import order of this provider
    # to the script-package layout.
    try:
        from scripts.gtfs import read_gtfs_stops
    except ImportError:
        try:
            import sys as _sys
            from pathlib import Path as _Path

            scripts_dir = _Path(__file__).resolve().parents[2] / "scripts"
            if str(scripts_dir.parent) not in _sys.path:
                _sys.path.insert(0, str(scripts_dir.parent))
            from scripts.gtfs import read_gtfs_stops
        except (ImportError, OSError) as exc:  # pragma: no cover - defensive
            log.warning("Could not load scripts.gtfs.read_gtfs_stops: %s", exc)
            return {}
    try:
        return cast(dict[str, object], read_gtfs_stops(path))
    except (OSError, ValueError) as exc:
        log.warning(
            "Could not read GTFS stops file %s: %s",
            sanitize_log_arg(str(path)),
            exc,
        )
        return {}


def load_stop_id_index(
    stops_path: Path | None = None,
    *,
    station_names: Sequence[str] = STAMMSTRECKE_STATION_NAMES,
) -> dict[str, frozenset[str]]:
    """Map each Stammstrecke station name to its known GTFS ``stop_id``s.

    Resolution algorithm:

    1. Read ``data/gtfs/stops.txt`` (operator-supplied static GTFS
       stops file).
    2. Normalise every ``stop_name`` plus the canonical Stammstrecke
       names.
    3. A normalised stop name *equals* or *starts with* the canonical
       Stammstrecke key → record the stop_id.

    Returns a dict containing every requested name even when no stop
    matched (the value is then an empty frozenset). Callers can union
    the values into a single corridor stop-id set.
    """
    from pathlib import Path as _PathConcrete  # local alias

    if stops_path is None:
        stops_path = _PathConcrete(__file__).resolve().parents[2] / "data" / "gtfs" / "stops.txt"

    stops = _read_gtfs_stops(stops_path)
    canonical = {name: _normalize_station_name(name) for name in station_names}
    result: dict[str, set[str]] = {name: set() for name in station_names}

    if not stops:
        return {name: frozenset(values) for name, values in result.items()}

    for stop_id, stop in stops.items():
        stop_name = getattr(stop, "stop_name", None)
        if not isinstance(stop_name, str) or not stop_name.strip():
            continue
        normalised = _normalize_station_name(stop_name)
        if not normalised:
            continue
        for station_name, key in canonical.items():
            if not key:
                continue
            if normalised == key or normalised.startswith(key + " "):
                result[station_name].add(str(stop_id))
                break

    return {name: frozenset(values) for name, values in result.items()}


def _flatten_stop_ids(stop_index: dict[str, frozenset[str]]) -> frozenset[str]:
    flat: set[str] = set()
    for ids in stop_index.values():
        flat.update(ids)
    return frozenset(flat)


# ---------------------------------------------------------------------------
# Protobuf parsing
# ---------------------------------------------------------------------------


def parse_feed_message(blob: bytes) -> object:
    """Decode a GTFS-Realtime ``FeedMessage`` byte string.

    The function returns the parsed ``FeedMessage`` (typed as
    ``object`` to keep the gtfs-realtime-bindings dependency optional
    at type-check time) or raises :class:`ValueError` on any decoding
    error so the caller can route through a single failure handler.
    """
    try:
        # Local import keeps the dependency optional for environments
        # that exercise the static fixtures only.
        from google.transit import gtfs_realtime_pb2
    except ImportError as exc:
        raise ValueError("gtfs-realtime-bindings package is unavailable") from exc

    feed = gtfs_realtime_pb2.FeedMessage()
    try:
        feed.ParseFromString(blob)
    except Exception as exc:
        raise ValueError(f"Could not parse GTFS-RT FeedMessage: {exc}") from exc
    return feed


def _coerce_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value == value:  # NaN check
        return int(value)
    return None


def _select_delay_seconds(stop_time_update: object) -> int | None:
    """Return the most relevant delay in seconds for one stop visit.

    GTFS-RT exposes ``arrival.delay`` and ``departure.delay``. We pick
    the larger absolute value — the spec interprets "delay on the
    corridor" as the worst-case effect at the platform.
    """
    candidates: list[int] = []
    for attr in ("arrival", "departure"):
        sub = getattr(stop_time_update, attr, None)
        if sub is None:
            continue
        delay = _coerce_int(getattr(sub, "delay", None))
        if delay is None:
            continue
        candidates.append(delay)
    if not candidates:
        return None
    return max(candidates, key=abs)


def _iter_trip_updates(feed: object) -> Iterable[object]:
    entities = getattr(feed, "entity", None)
    if entities is None:
        return ()
    out: list[object] = []
    try:
        for entity in entities:
            trip_update = getattr(entity, "trip_update", None)
            if trip_update is None:
                continue
            if not getattr(entity, "HasField", lambda _name: True)("trip_update"):
                continue
            out.append(trip_update)
    except Exception as exc:  # pragma: no cover - defensive logging path
        log.warning(
            "Unexpected error while iterating GTFS-RT entities: %s: %s",
            type(exc).__name__,
            exc,
        )
        return ()
    return out


def iter_corridor_delays(
    feed: object,
    corridor_stop_ids: Iterable[str],
) -> list[CorridorDelay]:
    """Return per-trip delay snapshots for trips touching the corridor.

    A trip "touches the corridor" when at least one of its
    ``stop_time_update`` entries names a stop_id from
    *corridor_stop_ids*. The reported delay for that trip is the
    maximum (by absolute value) of the per-stop delays observed at the
    corridor stops only — that is the metric an end-user perceives
    when riding through the Stammstrecke.
    """
    corridor = frozenset(str(s) for s in corridor_stop_ids if s)
    if not corridor:
        return []

    out: list[CorridorDelay] = []
    for trip_update in _iter_trip_updates(feed):
        trip = getattr(trip_update, "trip", None)
        trip_id = getattr(trip, "trip_id", "") if trip is not None else ""
        delays_in_corridor: list[int] = []
        touched: set[str] = set()
        try:
            stop_time_updates = list(getattr(trip_update, "stop_time_update", []))
        except Exception as exc:  # pragma: no cover - defensive
            log.warning(
                "Unexpected error reading stop_time_update list: %s: %s",
                type(exc).__name__,
                exc,
            )
            continue
        for stu in stop_time_updates:
            stop_id = getattr(stu, "stop_id", "")
            if not isinstance(stop_id, str) or stop_id not in corridor:
                continue
            touched.add(stop_id)
            delay = _select_delay_seconds(stu)
            if delay is not None:
                delays_in_corridor.append(delay)
        if not touched or not delays_in_corridor:
            continue
        worst = max(delays_in_corridor, key=abs)
        out.append(
            CorridorDelay(
                trip_id=str(trip_id) if isinstance(trip_id, str) else "",
                delay_seconds=worst,
                stop_ids=frozenset(touched),
            )
        )
    return out


def calculate_average_delay_minutes(
    delays: Sequence[CorridorDelay],
) -> float:
    """Return the mean delay across *delays* in minutes.

    Negative delays (early trips) are clamped at zero so a single
    early arrival cannot mask a real downstream delay. Returns ``0.0``
    when the input list is empty (the empty-corridor heal path).
    """
    if not delays:
        return 0.0
    seconds = [max(0, d.delay_seconds) for d in delays]
    if not seconds:
        return 0.0
    return (sum(seconds) / len(seconds)) / 60.0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_event(snapshot: StammstreckeStateSnapshot, link: str) -> FeedItem:
    """Render the consolidated corridor alert as a :class:`FeedItem`."""
    rounded = int(round(snapshot.average_delay_minutes))
    title = f"{_STAMMSTRECKE_LABEL}: Derzeit durchschnittlich {rounded} Minuten Verspätung"
    description = (
        f"Aktuell sind {snapshot.active_trips} Züge auf der Wiener "
        f"S-Bahn-Stammstrecke (Wien Floridsdorf ↔ Wien Meidling) mit "
        f"einer durchschnittlichen Verspätung von {rounded} Minuten "
        "unterwegs. Datenquelle: ÖBB GTFS-Realtime."
    )
    guid = make_guid(
        "gtfs_stammstrecke",
        "corridor_average_delay",
        str(rounded),
    )
    return FeedItem(
        title=title,
        link=link,
        description=description,
        guid=guid,
        source="gtfs_stammstrecke",
        category=_STAMMSTRECKE_LABEL,
    )


def _fetch_blob(url: str, timeout: int) -> bytes | None:
    """Fetch the GTFS-RT binary payload through ``request_safe``.

    Returns ``None`` on any network or content-type error. The caller
    propagates that into an empty event list — the cron pipeline must
    not crash on transient upstream issues.
    """
    with session_with_retries(USER_AGENT) as session:
        try:
            return fetch_content_safe(
                session,
                url,
                max_bytes=_MAX_PAYLOAD_BYTES,
                timeout=timeout,
                allowed_content_types=(
                    "application/octet-stream",
                    "application/x-protobuf",
                    "application/protobuf",
                ),
            )
        except (requests.RequestException, ValueError) as exc:
            log.warning(
                "GTFS-RT Stammstrecke fetch fehlgeschlagen: %s: %s",
                type(exc).__name__,
                sanitize_log_arg(str(exc)),
            )
            return None


def _evaluate_corridor(
    feed: object,
    corridor_stop_ids: frozenset[str],
) -> StammstreckeStateSnapshot:
    delays = iter_corridor_delays(feed, corridor_stop_ids)
    average = calculate_average_delay_minutes(delays)
    return StammstreckeStateSnapshot(
        average_delay_minutes=average,
        active_trips=len(delays),
        sampled_delays=tuple(delays),
    )


def fetch_events(
    timeout: int = 25,
    *,
    stops_path: Path | None = None,
    url: str | None = None,
) -> list[FeedItem]:
    """Public provider entry point.

    Returns a list with at most one :class:`FeedItem`. An empty list
    means the average corridor delay is at or below the threshold (or
    the upstream feed was unavailable) — either way the merged feed
    naturally drops any prior alert.
    """
    if timeout > MAX_FETCH_TIMEOUT:
        timeout = MAX_FETCH_TIMEOUT

    endpoint = url or _resolve_endpoint()

    try:
        stop_index = load_stop_id_index(stops_path)
    except (OSError, ValueError) as exc:
        log.warning(
            "Cannot resolve Stammstrecke stop ids: %s: %s",
            type(exc).__name__,
            exc,
        )
        return []

    corridor = _flatten_stop_ids(stop_index)
    if not corridor:
        log.info(
            "GTFS-RT Stammstrecke: no stop_id mapping resolved from GTFS stops.txt; skipping (no corridor coverage available)",
        )
        return []

    try:
        blob = _BREAKER.call(_fetch_blob, endpoint, timeout)
    except CircuitBreakerOpen:
        log.warning(
            "GTFS-RT Stammstrecke breaker is OPEN; skipping fetch this run",
        )
        return []
    except Exception as exc:  # pragma: no cover - defensive logging path
        log.error(
            "Unexpected error fetching GTFS-RT Stammstrecke: %s: %s",
            type(exc).__name__,
            sanitize_log_arg(str(exc)),
        )
        return []

    if not blob:
        return []

    try:
        feed = parse_feed_message(blob)
    except ValueError as exc:
        log.warning(
            "GTFS-RT Stammstrecke konnte FeedMessage nicht parsen: %s",
            sanitize_log_arg(str(exc)),
        )
        return []
    except Exception as exc:  # pragma: no cover - defensive logging path
        log.error(
            "Unexpected error parsing GTFS-RT FeedMessage: %s: %s",
            type(exc).__name__,
            sanitize_log_arg(str(exc)),
        )
        return []

    snapshot = _evaluate_corridor(feed, corridor)
    log.info(
        "GTFS-RT Stammstrecke: %d aktive Züge, Durchschnittsverspätung %.2f min",
        snapshot.active_trips,
        snapshot.average_delay_minutes,
    )

    if snapshot.average_delay_minutes <= STAMMSTRECKE_THRESHOLD_MINUTES:
        return []

    link = "https://www.oebb.at/de/fahrplan/stoerungen-baustellen"
    return [build_event(snapshot, link)]
