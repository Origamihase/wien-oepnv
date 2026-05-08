"""GTFS-Realtime monitoring of the Vienna S-Bahn Stammstrecke (cached).

This module is the *read* half of the standard cache-driven provider
architecture used by every other ÖBB / WL / VOR / Baustellen source in
the project. The matching *write* half lives in
``scripts/update_gtfs_cache.py`` — that script polls the official ÖBB
GTFS-Realtime ``TripUpdates`` feed every 30 minutes (via the GitHub
Actions workflow ``update-gtfs-cache.yml``), aggregates the average
delay across the Stammstrecke corridor, and persists the result as a
small JSON document at ``cache/gtfs_stammstrecke/events.json``. This
module simply reads that document and yields zero or one
:class:`FeedItem` for the merged feed builder.

Cache document shape::

    {
      "events": [
        {
          "guid": "...",
          "first_seen": "2026-05-08T10:00:00+02:00",
          "updated":    "2026-05-08T10:30:00+02:00",
          "average_delay_minutes": 12,
          "active_trips": 7
        }
      ],
      "metadata": {"last_run": "...", "version": 1}
    }

State semantics:

* ``"events"`` is a list. An *active* delay event is the FIRST element;
  any other shape (empty list, no ``"events"`` key, missing file)
  collapses to a clean state and the provider yields nothing.
* ``"first_seen"`` is the original timestamp at which the corridor
  delay first crossed the 9-minute threshold. The update script
  preserves this across refreshes for as long as the threshold stays
  exceeded, so the rendered ``[Seit DD.MM.YYYY]`` line in the
  description matches the user's lived experience of "since when".
* ``"updated"`` is the most recent refresh timestamp; the provider
  surfaces it as ``pubDate`` so the merged feed records freshness.

Why a separate cache file shape (object, not list)?  The other
providers serialise pre-rendered ``FeedItem`` lists with no per-event
state.  The Stammstrecke threshold contract requires preserving
``first_seen`` across runs, which is a stateful piece of metadata
that belongs in the cache file itself, not next to it.  The custom
shape keeps the state colocated with the events it describes.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from ..feed_types import FeedItem
from ..utils.files import read_capped_json
from ..utils.ids import make_guid
from ..utils.logging import sanitize_log_arg

if TYPE_CHECKING:  # pragma: no cover - typing only
    pass

log = logging.getLogger(__name__)

__all__ = [
    "CACHE_RELATIVE_PATH",
    "DEFAULT_CACHE_PATH",
    "STAMMSTRECKE_CATEGORY",
    "STAMMSTRECKE_LABEL",
    "STAMMSTRECKE_LINK",
    "STAMMSTRECKE_SOURCE",
    "STAMMSTRECKE_STATION_NAMES",
    "STAMMSTRECKE_THRESHOLD_MINUTES",
    "build_event_from_state",
    "fetch_events",
    "load_cache_document",
]

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

STAMMSTRECKE_LABEL = "S-Bahn Stammstrecke"
STAMMSTRECKE_CATEGORY = "Störung"
STAMMSTRECKE_SOURCE = "ÖBB GTFS-Realtime"
STAMMSTRECKE_LINK = "https://www.oebb.at/de/fahrplan/stoerungen-baustellen"

CACHE_RELATIVE_PATH = Path("cache") / "gtfs_stammstrecke" / "events.json"
DEFAULT_CACHE_PATH = Path(__file__).resolve().parents[2] / CACHE_RELATIVE_PATH

_VIENNA_TZ = ZoneInfo("Europe/Vienna")


def load_cache_document(path: Path | None = None) -> dict[str, Any] | None:
    """Return the parsed cache document at *path* or ``None``.

    A defensive read that survives a missing / unparseable / oversized
    cache file by returning ``None`` so the caller falls through to an
    empty result.  The size cap defends against a planted-huge file in
    a corrupted cache directory; mirrors the canonical defence pattern
    used by ``read_capped_json`` callers throughout the project.
    """
    target = path if path is not None else DEFAULT_CACHE_PATH
    payload = read_capped_json(target, label="GTFS Stammstrecke Cache", logger=log)
    if not isinstance(payload, dict):
        return None
    return payload


def _parse_iso_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_VIENNA_TZ)
    return parsed


def _coerce_int_minutes(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value == value:
        return int(round(value))
    return None


def _coerce_int_count(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, float) and value == value:
        return max(0, int(round(value)))
    return None


def build_event_from_state(state: dict[str, Any]) -> FeedItem | None:
    """Render the consolidated alert :class:`FeedItem` from *state*.

    *state* is the first entry of the cache document's ``"events"``
    list.  Returns ``None`` when the state lacks the minimum keys
    needed to render a meaningful alert (``first_seen`` and either
    ``average_delay_minutes`` or ``rounded_minutes``).
    """
    minutes = _coerce_int_minutes(state.get("average_delay_minutes"))
    if minutes is None:
        minutes = _coerce_int_minutes(state.get("rounded_minutes"))
    if minutes is None or minutes <= STAMMSTRECKE_THRESHOLD_MINUTES:
        return None

    first_seen = _parse_iso_datetime(state.get("first_seen"))
    if first_seen is None:
        return None

    updated = _parse_iso_datetime(state.get("updated")) or first_seen
    active_trips = _coerce_int_count(state.get("active_trips")) or 0

    seit = first_seen.astimezone(_VIENNA_TZ).strftime("%d.%m.%Y")
    title = f"{STAMMSTRECKE_LABEL}: Derzeit durchschnittlich {minutes} Minuten Verspätung"

    if active_trips > 0:
        body = (
            f"Aktuell sind <b>{active_trips} Züge</b> auf der Wiener "
            "S-Bahn-Stammstrecke (<b>Wien Floridsdorf</b> ↔ <b>Wien Meidling</b>) mit "
            f"einer durchschnittlichen Verspätung von <b>{minutes} Minuten</b> unterwegs."
        )
    else:
        body = (
            "Auf der Wiener S-Bahn-Stammstrecke (<b>Wien Floridsdorf</b> ↔ "
            "<b>Wien Meidling</b>) ist eine durchschnittliche Verspätung von "
            f"<b>{minutes} Minuten</b> erfasst."
        )
    description = (
        f"[Seit {seit}]<br/><br/>{body}<br/>Datenquelle: ÖBB GTFS-Realtime."
    )

    explicit_guid = state.get("guid")
    if isinstance(explicit_guid, str) and explicit_guid.strip():
        guid = explicit_guid
    else:
        guid = make_guid(
            "gtfs_stammstrecke",
            "stammstrecke_delay",
            first_seen.astimezone(_VIENNA_TZ).isoformat(),
        )

    item = FeedItem(
        title=title,
        link=STAMMSTRECKE_LINK,
        description=description,
        guid=guid,
        source=STAMMSTRECKE_SOURCE,
        category=STAMMSTRECKE_CATEGORY,
        pubDate=updated,
    )
    return item


def fetch_events(*, cache_path: Path | None = None) -> list[FeedItem]:
    """Public provider entry point.

    Reads the cache document at ``cache/gtfs_stammstrecke/events.json``
    (or *cache_path* when supplied) and returns at most one
    :class:`FeedItem`.  The list is empty when the cache is missing,
    the events list is empty, or the persisted state is below the
    threshold — the merged feed naturally drops any prior alert in
    that case (the "self-heal" property the spec calls for).
    """
    document = load_cache_document(cache_path)
    if document is None:
        return []

    events_raw = document.get("events")
    if not isinstance(events_raw, list) or not events_raw:
        return []

    state = events_raw[0]
    if not isinstance(state, dict):
        log.warning(
            "GTFS-RT Stammstrecke cache event entry is not an object: %s",
            sanitize_log_arg(type(state).__name__),
        )
        return []

    item = build_event_from_state(state)
    if item is None:
        return []
    return [item]
