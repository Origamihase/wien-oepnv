"""Shared building blocks of the Stammstrecke monitors.

Operating parameters, ledger paths, the pending-trip record, the HTTP
and breaker policy, the S-Bahn line pattern and the Vienna clock. Moved
verbatim from ``scripts/update_stammstrecke_status.py``; both
Stammstrecke scripts import them from here.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Final
from zoneinfo import ZoneInfo

# ``__all__`` lists every name this module defines. Some private names are
# read only by other modules and tests; CodeQL (py/unused-global-variable)
# counts such a read through an import only when the name is listed here.
__all__ = [
    "REPO_ROOT", "LOGGER", "FLORIDSDORF_VOR_ID", "MEIDLING_VOR_ID", "MEIDLING_CANONICAL_SEED",
    "DELAY_THRESHOLD_MINUTES", "MAX_TRIPS_PER_QUERY", "PENDING_TRIPS_PATH",
    "PENDING_TRIPS_MAX_BYTES", "PENDING_TTL", "RECENTLY_FINALISED_PATH",
    "RECENTLY_FINALISED_MAX_BYTES", "PENDING_TRIPS_LOCK_PATH", "_PendingTrip", "QUERY_TIMEOUT",
    "MAX_QUERY_TIMEOUT", "BREAKER_FAILURE_THRESHOLD", "BREAKER_RECOVERY_TIMEOUT", "_S_BAHN_LINE_RE",
    "VIENNA_TZ", "_now_vienna", "_format_minutes",
]


REPO_ROOT = Path(__file__).resolve().parents[2]

LOGGER = logging.getLogger("update_stammstrecke_status")


# ---- Operating parameters ---------------------------------------------------

# VOR/VAO station IDs for the two Stammstrecke termini. Sourced from
# ``data/stations.json`` and pinned here so a station-directory drift
# cannot silently change which stops the script polls.
FLORIDSDORF_VOR_ID = "490033400"
MEIDLING_VOR_ID = "490101500"

# Canonical station-directory key used to look up the user-facing label
# via ``src.utils.stations``. The name MUST exist in ``data/stations.json``
# (or one of the configured aliases) so the directory lookup succeeds. The
# fallback path in ``_short_target_label`` preserves the literal value if
# the lookup fails.
MEIDLING_CANONICAL_SEED = "Wien Meidling"

# Threshold above which the per-sample mean delay of a direction
# generates a feed entry. The user-facing semantics are "more than 9
# minutes" — a value of exactly 9 minutes does NOT trigger the event.
DELAY_THRESHOLD_MINUTES = 9

# Number of trips to fetch per direction in a single ``/trip`` call.
# Pinned to ``6`` — the VAO contractual maximum (``numF`` accepts 1..6,
# see ``docs/reference/trip.md``). The 30-minute cron tick combined
# with ``maxChange=0`` typically yields 4-6 S-Bahn legs after the
# product filter; pinning at the API ceiling maximises the sample's
# size without inflating the per-day quota cost (the VAO response
# size is identical between numF=5 and numF=6).
MAX_TRIPS_PER_QUERY = 6

# ---- Pending-trip state (latest-observation dedup) -------------------------
#
# The cron lookahead (``numF=6``) returns 60-180 min of upcoming
# departures while the cron itself ticks every ~30 min. Without
# deduplication the same physical S-Bahn train would land in the
# sample mean of several consecutive cron rows — inflating both the
# persisted ``delay_minutes`` value and the threshold counter every
# time the train was re-observed.
#
# Design: every observed leg is identified by
# ``(direction, line_name, scheduled_origin_dt)`` and stored in a
# tiny JSON ledger (:data:`PENDING_TRIPS_PATH`). The ledger keeps the
# *latest* delay reading per train — re-observations overwrite older
# ones, so the value that ultimately gets persisted reflects the
# observation closest to the train's actual departure (10 min before
# departure beats 40 min before departure for accuracy).
#
# A train is "finalised" when its scheduled departure has passed
# (``scheduled <= now``); the latest reading then flows into the CSV
# row for the cron tick that catches the departure boundary, and the
# state entry is removed. Each train contributes to exactly one CSV
# row over its entire observation history — there is no double
# counting regardless of how many cron ticks saw the train.
PENDING_TRIPS_PATH: Final = REPO_ROOT / "cache" / "stammstrecke" / "pending_trips.json"

# Defense-in-depth size cap on the pending-trip ledger. A healthy
# ledger holds at most ~2 directions × ~12 trains/hour × ~3 hours
# = ~72 entries (~150 bytes each) ≈ 11 KiB; 1 MiB is ~90× headroom
# while bounding the memory cost of a planted / corrupted state file.
PENDING_TRIPS_MAX_BYTES: Final = 1 * 1024 * 1024

# State TTL — entries last touched more than this long ago are
# discarded. Wider than the longest plausible cron-tick gap (a
# missed IFTTT trigger + manual catch-up rarely exceeds 2 h) so a
# delayed but eventually-fired tick still finalises the entries it
# would have processed earlier.
PENDING_TTL: Final = timedelta(hours=6)

# Companion ledger: identity keys of trains that have already been
# finalised + the timestamp of finalisation. The cron observation
# pass consults this ledger and skips legs whose key is present, so
# anomalous VAO behaviour (re-emitting a just-finalised train at the
# lookahead boundary) cannot produce a duplicate CSV row. Entries
# inherit the same TTL as the pending ledger — once a finalised
# train ages past it, any further re-emission is treated as a
# new observation, which is the conservative choice for stale state.
RECENTLY_FINALISED_PATH: Final = (
    REPO_ROOT / "cache" / "stammstrecke" / "recently_finalised.json"
)
RECENTLY_FINALISED_MAX_BYTES: Final = 1 * 1024 * 1024

# OS-level advisory lock around the load → modify → save sequence on
# both ledger files. The cron workflow uses a concurrency group so
# two scheduled runs cannot overlap, but ``workflow_dispatch`` /
# manual triggers can bypass that gate. The lock turns concurrent
# runs from "last-write-wins, loser's observations lost" into
# "loser blocks briefly, then runs to completion against the
# now-updated ledger" — at the cost of one syscall per cron tick.
PENDING_TRIPS_LOCK_PATH: Final = (
    REPO_ROOT / "cache" / "stammstrecke" / "pending_trips.lock"
)


@dataclass(frozen=True)
class _PendingTrip:
    """One observed but not-yet-finalised S-Bahn trip.

    Fields mirror the JSON schema persisted to
    :data:`PENDING_TRIPS_PATH`. ``scheduled`` and ``last_seen_at`` are
    always timezone-aware (Europe/Vienna) — the loader normalises
    naive timestamps defensively rather than dropping the entry.

    ``cancelled`` flags a train that the upstream reported as
    cancelled (``Origin.cancelled`` / ``leg.cancelled`` / departure-
    level ``cancelled``). For cancelled trips the
    ``latest_delay_minutes`` field is meaningless (set to ``0.0`` as
    a placeholder by the collectors) — the finalise pass routes the
    trip to ``data/stats/ausfaelle_YYYY.csv`` instead of the delay
    ledger, so the placeholder never reaches the CSV. The field
    defaults to ``False`` so a ledger entry written by a pre-
    cancellation-tracking script version loads cleanly under the
    new schema (``_trip_from_json`` does the equivalent migration
    via ``data.get("cancelled", False)``).
    """

    direction: str
    name: str
    scheduled: datetime
    latest_delay_minutes: float
    last_seen_at: datetime
    cancelled: bool = False


# Per-call HTTP budget (seconds). Enforced at the ``fetch_content_safe``
# layer (``src/utils/http.py``) which forwards the kwarg verbatim to
# ``requests.Session.get``. Bound between 1 and ``MAX_QUERY_TIMEOUT`` so
# a future env-driven misconfiguration cannot disable the timeout.
QUERY_TIMEOUT = 20
MAX_QUERY_TIMEOUT = 30

# Circuit-breaker policy. Aligned with the existing VAO Start budget of
# 100 requests/day — the per-direction-level breaker stays OPEN for one
# hour after 10 consecutive failures, capping outage-mode traffic at 10
# attempts/hour. Normal operation produces 4 calls/h (2 directions × 2
# fires/h via the ``*/30`` cron), well below the breaker ceiling.
BREAKER_FAILURE_THRESHOLD = 10
BREAKER_RECOVERY_TIMEOUT = 3600.0

# Pattern that identifies an S-Bahn, R, REX, or CJX line label (``S 1``, ``S 7``,
# ``REX 3``, ``R 81``, ``CJX 9`` …). Used as the secondary signal when the VAO
# ``category`` field is missing or non-canonical; primary signal is
# ``category in {"S", "R", "REX", "CJX"}`` /
# ``Product.catOut in {"S", "R", "REX", "CJX"}``.
_S_BAHN_LINE_RE = re.compile(r"^\s*(S|REX|R|CJX)\s*\d+\s*$", re.IGNORECASE)

VIENNA_TZ = ZoneInfo("Europe/Vienna")

# 2026-05-09: ``OUTPUT_PATH`` (``cache/stammstrecke/events.json``) and
# the per-event metadata constants (``EVENT_SOURCE``, ``EVENT_CATEGORY``,
# ``EVENT_TITLE``, ``EVENT_LINK``) used to live here — the script
# wrote a JSON cache that the feed provider read verbatim. The new
# pipeline derives feed events from the CSV ledger at feed-build time
# (see :mod:`src.feed.stammstrecke`), so this script is a pure CSV
# appender now and the rendering constants belong to the renderer.


def _now_vienna() -> datetime:
    """Return the current time, anchored to Europe/Vienna."""

    return datetime.now(tz=VIENNA_TZ)


def _format_minutes(value: float) -> str:
    """Format *value* as a German-readable minute count.

    ``round(x, 1)`` followed by ``:g`` strips trailing ``.0`` so a
    whole-minute value renders as ``"12"`` rather than ``"12.0"``,
    while a fractional value keeps its single decimal (``"12.5"``).
    """

    rounded = round(value, 1)
    return f"{rounded:g}"
