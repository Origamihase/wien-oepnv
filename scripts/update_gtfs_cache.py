#!/usr/bin/env python3
"""Refresh the persistent S-Bahn Stammstrecke cache.

This is the *write* half of the cache-driven Stammstrecke provider.
It polls the official ÖBB GTFS-Realtime ``TripUpdates`` feed (every
30 minutes via the ``update-gtfs-cache.yml`` GitHub Actions workflow),
filters trip updates that are travelling through the Wien Floridsdorf
↔ Wien Meidling corridor, computes the average corridor delay, and
persists the result at ``cache/gtfs_stammstrecke/events.json`` for
the read-side provider in ``src/providers/gtfs_stammstrecke.py``.

State semantics:

* When the average delay > 9 min:
    * if the cache already records an active delay event, the
      original ``first_seen`` timestamp is preserved (so the rendered
      ``[Seit DD.MM.YYYY]`` line stays anchored to the start of the
      disruption);
    * the ``updated`` timestamp is bumped to "now", and the new
      rounded average / active-trip count are written.
* When the average delay ≤ 9 min:
    * the events list is emptied so the read-side provider yields
      nothing on the next feed build (the "self-heal" property).
    * the file itself is kept in place (with metadata) so the
      mtime / git diff reliably surfaces every refresh — this matches
      the heartbeat semantics of the WL / ÖBB / VOR cache files.

Resilience: every fetch / parse step collapses to "no new data"
rather than crashing the cron pipeline. A network failure or
malformed payload leaves the prior cache file intact (no overwrite),
so the read-side provider continues to surface whatever the previous
successful run recorded — until the next successful refresh either
confirms the active alert or clears it.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.gtfs import read_gtfs_stops  # noqa: E402  (import after path setup)
from src.providers.gtfs_stammstrecke import (  # noqa: E402
    CACHE_RELATIVE_PATH,
    STAMMSTRECKE_STATION_NAMES,
    STAMMSTRECKE_THRESHOLD_MINUTES,
)
from src.utils.circuit_breaker import CircuitBreaker, CircuitBreakerOpen  # noqa: E402
from src.utils.files import read_capped_json, write_json_atomic  # noqa: E402
from src.utils.http import (  # noqa: E402
    fetch_content_safe,
    session_with_retries,
    validate_http_url,
)
from src.utils.ids import make_guid  # noqa: E402
from src.utils.logging import sanitize_log_arg  # noqa: E402

LOGGER = logging.getLogger("update_gtfs_cache")

# ÖBB publishes GTFS-RT TripUpdates at this canonical URL. ``OEBB_GTFS_RT_URL``
# may override it but is validated against the trusted host allow-list
# so an env override (compromised secret store / leaked CI env) cannot
# redirect the cron pipeline to an attacker host.
DEFAULT_GTFS_RT_URL = "https://realtime.oebb.at/gtfs-rt/tripUpdates"
_TRUSTED_GTFS_RT_HOSTS: frozenset[str] = frozenset({"realtime.oebb.at", "data.oebb.at"})

USER_AGENT = "Origamihase-wien-oepnv-stammstrecke/2.0 (+https://github.com/Origamihase/wien-oepnv)"

# Slowloris-defence ceiling. Mirrors ``MAX_OEBB_FETCH_TIMEOUT`` (25s)
# in ``src/providers/oebb.py`` — same parameter-boundary contract.
MAX_FETCH_TIMEOUT = 25
DEFAULT_FETCH_TIMEOUT = 25

# Per-fetch payload cap. The ÖBB GTFS-RT feed sits at ~1-3 MiB
# (compressed); 8 MiB is ~3-4x production state and well below the
# 10 MiB ``MAX_PAYLOAD_SIZE``.
_MAX_PAYLOAD_BYTES = 8 * 1024 * 1024

# Cache-document version pinned in ``metadata.version``. Bump only on a
# breaking schema change; downstream readers tolerate missing keys but
# do not migrate old shapes automatically.
CACHE_DOCUMENT_VERSION = 1

_VIENNA_TZ = ZoneInfo("Europe/Vienna")

_BREAKER = CircuitBreaker(
    "gtfs_stammstrecke_update",
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

    @property
    def rounded_minutes(self) -> int:
        return int(round(self.average_delay_minutes))


def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("urllib3").setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# URL / endpoint resolution
# ---------------------------------------------------------------------------


def _validated_gtfs_rt_url(raw: str) -> str | None:
    safe = validate_http_url(raw)
    if not safe:
        return None
    host = (urlparse(safe).hostname or "").lower()
    if host not in _TRUSTED_GTFS_RT_HOSTS:
        return None
    return safe


def resolve_endpoint(raw: str | None = None) -> str:
    candidate = (raw if raw is not None else os.getenv("OEBB_GTFS_RT_URL", "")).strip()
    if not candidate:
        return DEFAULT_GTFS_RT_URL
    safe = _validated_gtfs_rt_url(candidate)
    if safe is None:
        LOGGER.warning(
            "OEBB_GTFS_RT_URL %s ist kein bekannter ÖBB-GTFS-RT-Host; verwende Standard.",
            sanitize_log_arg(candidate),
        )
        return DEFAULT_GTFS_RT_URL
    return safe


# ---------------------------------------------------------------------------
# Stop id resolution
# ---------------------------------------------------------------------------


def normalize_station_name(name: str) -> str:
    """Return *name* in a comparison-friendly form.

    Casefolding + accent stripping + collapsed whitespace + dropped
    Bahnhof/Bf/Hbf suffixes — matches the project convention used by
    ``scripts/update_station_directory.py``.
    """
    text = unicodedata.normalize("NFKD", name)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace("ß", "ss").casefold()
    # Strip compound *bahnhof* forms before the standalone variants.
    text = re.sub(r"(?:haupt|west|ost|nord|sued|sud|s-|s)?bahnhof", "", text)
    text = re.sub(r"\b(?:bahnhst|bhf|hbf|bf)\b", "", text)
    text = text.replace("-", " ").replace("/", " ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s{2,}", " ", text).strip()


def _read_gtfs_stops(path: Path) -> dict[str, object]:
    """Defensive wrapper around :func:`scripts.gtfs.read_gtfs_stops`."""
    try:
        return cast(dict[str, object], read_gtfs_stops(path))
    except (OSError, ValueError) as exc:
        LOGGER.warning(
            "Could not read GTFS stops file %s: %s",
            sanitize_log_arg(str(path)),
            sanitize_log_arg(str(exc)),
        )
        return {}


def load_stop_id_index(
    stops_path: Path | None = None,
    *,
    station_names: Sequence[str] = STAMMSTRECKE_STATION_NAMES,
) -> dict[str, frozenset[str]]:
    """Map each Stammstrecke station name to its known GTFS ``stop_id``s."""
    if stops_path is None:
        stops_path = REPO_ROOT / "data" / "gtfs" / "stops.txt"

    stops = _read_gtfs_stops(stops_path)
    canonical = {name: normalize_station_name(name) for name in station_names}
    result: dict[str, set[str]] = {name: set() for name in station_names}

    if not stops:
        return {name: frozenset(values) for name, values in result.items()}

    for stop_id, stop in stops.items():
        stop_name = getattr(stop, "stop_name", None)
        if not isinstance(stop_name, str) or not stop_name.strip():
            continue
        normalised = normalize_station_name(stop_name)
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
    """Decode a GTFS-Realtime ``FeedMessage`` byte string."""
    try:
        from google.transit import gtfs_realtime_pb2
    except ImportError as exc:
        raise ValueError("gtfs-realtime-bindings package is unavailable") from exc

    feed = gtfs_realtime_pb2.FeedMessage()
    try:
        feed.ParseFromString(blob)
    except Exception as exc:
        raise ValueError(
            f"Could not parse GTFS-RT FeedMessage: {type(exc).__name__}"
        ) from exc
    return feed


def _coerce_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value == value:
        return int(value)
    return None


def _select_delay_seconds(stop_time_update: object) -> int | None:
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
        LOGGER.warning(
            "Unexpected error while iterating GTFS-RT entities: %s: %s",
            type(exc).__name__,
            sanitize_log_arg(str(exc)),
        )
        return ()
    return out


def iter_corridor_delays(
    feed: object,
    corridor_stop_ids: Iterable[str],
) -> list[CorridorDelay]:
    """Return per-trip delay snapshots for trips touching the corridor."""
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
            LOGGER.warning(
                "Unexpected error reading stop_time_update list: %s: %s",
                type(exc).__name__,
                sanitize_log_arg(str(exc)),
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


def calculate_average_delay_minutes(delays: Sequence[CorridorDelay]) -> float:
    """Return the mean delay across *delays* in minutes."""
    if not delays:
        return 0.0
    seconds = [max(0, d.delay_seconds) for d in delays]
    if not seconds:
        return 0.0
    return (sum(seconds) / len(seconds)) / 60.0


def evaluate_corridor(
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


# ---------------------------------------------------------------------------
# Network fetch
# ---------------------------------------------------------------------------


def fetch_blob(url: str, timeout: int) -> bytes | None:
    """Fetch the GTFS-RT binary payload through ``fetch_content_safe``."""
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
            LOGGER.warning(
                "GTFS-RT Stammstrecke fetch fehlgeschlagen: %s: %s",
                type(exc).__name__,
                sanitize_log_arg(str(exc)),
            )
            return None


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------


def _now_local() -> datetime:
    return datetime.now(tz=_VIENNA_TZ)


def load_existing_state(cache_path: Path) -> dict[str, Any] | None:
    """Read the existing cache document at *cache_path* or ``None``."""
    payload = read_capped_json(
        cache_path, label="GTFS Stammstrecke Cache", logger=LOGGER,
    )
    if not isinstance(payload, dict):
        return None
    return payload


def _existing_active_event(document: dict[str, Any] | None) -> dict[str, Any] | None:
    if not document:
        return None
    events = document.get("events")
    if not isinstance(events, list) or not events:
        return None
    head = events[0]
    if isinstance(head, dict):
        return head
    return None


def compute_next_state(
    snapshot: StammstreckeStateSnapshot,
    existing_document: dict[str, Any] | None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return the next cache document for *snapshot*.

    Implements the threshold + first-seen state machine documented at
    the module top.  *now* is injectable for deterministic tests.
    """
    current = (now or _now_local()).astimezone(_VIENNA_TZ)
    current_iso = current.isoformat()

    metadata = {
        "last_run": current_iso,
        "version": CACHE_DOCUMENT_VERSION,
        "average_delay_minutes": round(snapshot.average_delay_minutes, 2),
        "active_trips": snapshot.active_trips,
    }

    if snapshot.average_delay_minutes <= STAMMSTRECKE_THRESHOLD_MINUTES:
        return {"events": [], "metadata": metadata}

    rounded = snapshot.rounded_minutes
    existing = _existing_active_event(existing_document)
    first_seen_iso = current_iso
    guid = make_guid(
        "gtfs_stammstrecke",
        "stammstrecke_delay",
        first_seen_iso,
    )
    if existing is not None:
        candidate_first_seen = existing.get("first_seen")
        if isinstance(candidate_first_seen, str) and candidate_first_seen.strip():
            first_seen_iso = candidate_first_seen
        existing_guid = existing.get("guid")
        if isinstance(existing_guid, str) and existing_guid.strip():
            guid = existing_guid
        else:
            guid = make_guid(
                "gtfs_stammstrecke",
                "stammstrecke_delay",
                first_seen_iso,
            )

    event = {
        "guid": guid,
        "first_seen": first_seen_iso,
        "updated": current_iso,
        "pubDate": current_iso,
        "average_delay_minutes": rounded,
        "average_delay_minutes_raw": round(snapshot.average_delay_minutes, 2),
        "active_trips": snapshot.active_trips,
    }
    return {"events": [event], "metadata": metadata}


def write_state(cache_path: Path, document: dict[str, Any]) -> None:
    """Persist the cache document atomically via :func:`write_json_atomic`."""
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(cache_path, document, permissions=0o644, indent=2)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def _resolve_cache_path(raw: str | None = None) -> Path:
    if raw and raw.strip():
        return Path(raw).resolve()
    return REPO_ROOT / CACHE_RELATIVE_PATH


def _resolve_timeout(raw: str | None = None) -> int:
    candidate = raw if raw is not None else os.getenv("OEBB_GTFS_RT_TIMEOUT", "")
    text = candidate.strip()
    if not text:
        return DEFAULT_FETCH_TIMEOUT
    try:
        value = int(text)
    except ValueError:
        LOGGER.warning(
            "Ungültiger OEBB_GTFS_RT_TIMEOUT-Wert %s — verwende Standard.",
            sanitize_log_arg(text),
        )
        return DEFAULT_FETCH_TIMEOUT
    return min(max(value, 1), MAX_FETCH_TIMEOUT)


def run_update(
    *,
    cache_path: Path | None = None,
    stops_path: Path | None = None,
    url: str | None = None,
    timeout: int | None = None,
    now: datetime | None = None,
) -> int:
    """Execute one refresh cycle. Returns process exit code."""
    cache = cache_path if cache_path is not None else _resolve_cache_path()
    endpoint = url if url is not None else resolve_endpoint()
    fetch_timeout = (
        min(max(timeout, 1), MAX_FETCH_TIMEOUT)
        if timeout is not None
        else _resolve_timeout()
    )

    try:
        stop_index = load_stop_id_index(stops_path)
    except (OSError, ValueError) as exc:
        LOGGER.warning(
            "Cannot resolve Stammstrecke stop ids: %s: %s",
            type(exc).__name__,
            sanitize_log_arg(str(exc)),
        )
        return 1

    corridor = _flatten_stop_ids(stop_index)
    if not corridor:
        LOGGER.info(
            "GTFS-RT Stammstrecke: kein stop_id-Mapping aufgelöst — überspringe Refresh.",
        )
        return 1

    try:
        blob = _BREAKER.call(fetch_blob, endpoint, fetch_timeout)
    except CircuitBreakerOpen:
        LOGGER.warning(
            "GTFS-RT Stammstrecke breaker ist OFFEN; überspringe Fetch.",
        )
        return 1
    except Exception as exc:  # pragma: no cover - defensive logging path
        LOGGER.error(
            "Unerwarteter Fehler beim Fetch von GTFS-RT Stammstrecke: %s: %s",
            type(exc).__name__,
            sanitize_log_arg(str(exc)),
        )
        return 1

    if not blob:
        LOGGER.warning(
            "GTFS-RT Stammstrecke: leeres Payload — Cache bleibt unverändert.",
        )
        return 1

    try:
        feed = parse_feed_message(blob)
    except ValueError as exc:
        LOGGER.warning(
            "GTFS-RT Stammstrecke konnte FeedMessage nicht parsen: %s",
            sanitize_log_arg(str(exc)),
        )
        return 1

    snapshot = evaluate_corridor(feed, corridor)
    LOGGER.info(
        "GTFS-RT Stammstrecke: %d aktive Züge, Durchschnittsverspätung %.2f min",
        snapshot.active_trips,
        snapshot.average_delay_minutes,
    )

    existing = load_existing_state(cache)
    document = compute_next_state(snapshot, existing, now=now)
    write_state(cache, document)

    if snapshot.average_delay_minutes > STAMMSTRECKE_THRESHOLD_MINUTES:
        LOGGER.info(
            "GTFS-RT Stammstrecke: aktive Verspätung gespeichert (%d min).",
            snapshot.rounded_minutes,
        )
    else:
        LOGGER.info(
            "GTFS-RT Stammstrecke: keine Schwellwert-Überschreitung — Events geleert.",
        )
    return 0


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cache-path",
        default=None,
        help="Override the cache file location (defaults to cache/gtfs_stammstrecke/events.json).",
    )
    parser.add_argument(
        "--stops-path",
        default=None,
        help="Override the GTFS stops.txt file used for corridor stop_id lookup.",
    )
    parser.add_argument(
        "--url",
        default=None,
        help="Override the GTFS-RT TripUpdates endpoint (must be on the trusted host list).",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=None,
        help="Override the per-request timeout (clamped to 1..25 seconds).",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    configure_logging()
    args = _parse_args(argv)
    cache_path = _resolve_cache_path(args.cache_path)
    stops_path = Path(args.stops_path).resolve() if args.stops_path else None
    return run_update(
        cache_path=cache_path,
        stops_path=stops_path,
        url=args.url,
        timeout=args.timeout,
    )


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
