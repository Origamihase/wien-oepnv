#!/usr/bin/env python3
"""Monitor delays on the S-Bahn Stammstrecke (Wien Floridsdorf ↔ Wien Meidling).

Queries direct S-Bahn connections via the VOR/VAO ReST ``/trip`` endpoint
for **both directions** independently. Per-cron tick, every observed
S-Bahn leg is recorded into a small JSON ledger
(``cache/stammstrecke/pending_trips.json``) keyed by
``(direction, line_name, scheduled_origin_dt)``. Re-observations of the
same train across multiple cron ticks *overwrite* the previous reading
— the value that eventually flows into ``data/stats/stammstrecke_
<YYYY>.csv`` is therefore the observation taken closest to the train's
actual departure, which is the most accurate one. When the train's
scheduled departure has passed, its identity key is moved to a sibling
ledger (``cache/stammstrecke/recently_finalised.json``) so any
anomalous VAO re-emission at the lookahead boundary cannot produce a
duplicate CSV row.

The CSV row's ``delay_minutes`` is the arithmetic mean of the
finalised trains for that direction *in that calendar year* — legs
without realtime signal are skipped (status unknown ≠ on-time) so the
mean reflects only verified observations. Directions are evaluated
strictly separately because merging both into a single sample dilutes
the signal — a station with a major incident in one direction often
runs normally in the opposite direction.

Migration history
-----------------

This script was originally implemented against the public ``mgate.exe``
endpoint via :mod:`pyhafas` (``OEBBProfile``). The 2026-05-09 audit
discovered that ``OEBBProfile`` is not exported by any released
``pyhafas`` version on PyPI (the import had been silently failing for
weeks, leaving ``data/stats/stammstrecke_*.csv`` empty). The script was
re-architected to use the project's existing VOR/VAO infrastructure:
the same authenticated session, quota counter, and ``fetch_content_safe``
size-/header-/SSRF-defended HTTP layer that the disruption providers
already rely on.

Design contract
---------------

- **Two-direction split**: each cron tick runs two ``/trip`` calls — one
  ``Floridsdorf → Meidling`` and one ``Meidling → Floridsdorf``. Each
  call's per-sample mean is computed independently and persisted to
  the CSV ledger; the feed builder later reads those rows to emit at
  most one event per direction.
- **Direct-connection filter**: only single-ride-leg trips are eligible
  for the per-sample mean. The VAO ``maxChange=0`` query parameter
  gives the upstream a hint, and a client-side leg-count check is the
  second layer of defence — so a multi-stop trip the API still returns
  under ``maxChange=0`` does not bleed into the signal.
- **S-Bahn product filter**: the eligible leg must carry an S-Bahn or regional train
  product label. The VAO `/trip` call uses `products=3` to pre-filter Train (1) + S-Bahn (2).
  We accept either ``leg.category in {"S", "R", "REX", "CJX"}``,
  ``leg.name`` matching ``(S|R|REX|CJX)\\d+``, or ``leg.Product[].catOut`` /
  ``Product[].line`` matching the same — covering the known
  upstream-shape variants without committing to a single one. Cityjet Express
  (``CJX``) was added 2026-05-17 after ÖBB rebranded selected REX rolling-
  stock; the corridor coverage is unchanged. Long-distance trains
  (Railjet, IC, etc.) are filtered out.
- **CSV ledger, not events**: this script is now a pure CSV appender —
  the former ``events.json`` / RSS output (``OUTPUT_PATH`` plus the event
  builders) was removed on 2026-05-09. Each tick appends one per-direction
  mean-delay row to ``data/stats/stammstrecke_<YYYY>.csv`` (and cancellation
  rows to ``data/stats/ausfaelle_<YYYY>.csv``); on an unreachable API or a
  degraded signal simply no row is recorded for that tick. Event emission —
  self-healing of stale warnings and per-episode GUID / ``first_seen``
  stability — now lives in the feed builder
  (:mod:`src.feed.stammstrecke`), not here.
- **Quota integration**: every ``/trip`` call increments the shared
  ``data/vor_request_count.json`` counter via :func:`save_request_count`
  *before* the request is sent. A run that would push the day's usage
  over :data:`MAX_REQUESTS_PER_DAY` aborts cleanly (``[]`` cache, exit
  ``0``) rather than risking a contractual breach.
- **Resilience**: the network call is wrapped in
  :class:`src.utils.circuit_breaker.CircuitBreaker` (`failure_threshold=10`,
  `recovery_timeout=3600`s — same budget shape the original HAFAS
  client used). Per-call HTTP timeout is enforced at the
  ``fetch_content_safe`` layer.
- **Atomicity**: writes go through :func:`src.utils.files.atomic_write`
  with permissive ``0o644`` permissions; a crash mid-write cannot
  leave a half-written cache file behind.
- **Timezone**: GitHub Actions runs in UTC. The scheduled / observation
  timestamps written to the CSV ledger are localised to ``Europe/Vienna``
  via :mod:`zoneinfo` and serialised as ISO 8601 strings with offset.
- **Schema**: the CSV ledger rows carry the per-direction mean delay (and
  cancellations) keyed by timestamp and direction; the canonical FeedItem
  event shape they feed into is built downstream in
  :mod:`src.feed.stammstrecke`.
- **Logging**: every diagnostic message is routed through
  :func:`src.feed.logging_safe.setup_script_logging` so log injection
  / ANSI / BiDi attacks via upstream-controlled fields are sanitised
  at the formatter layer. URLs containing the post-``VorAuth``
  ``accessId`` query parameter are NEVER logged via ``%s`` /
  ``exc_info=True`` (mirrors the canonical pattern from
  ``scripts/update_vor_cache.py``).
"""

from __future__ import annotations

import json as _json_lib
import logging
import re
import statistics
import sys
from collections.abc import Iterable, Iterator, Mapping
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Final
from zoneinfo import ZoneInfo

import requests

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.feed.logging_safe import setup_script_logging  # noqa: E402
from src.providers import vor as vor_provider  # noqa: E402
from src.utils.circuit_breaker import (  # noqa: E402
    CircuitBreaker,
    CircuitBreakerOpen,
)
from src.utils.files import atomic_write, loads_finite, read_capped_text  # noqa: E402
from src.utils.http import request_safe, session_with_retries  # noqa: E402
from src.utils.logging import sanitize_log_arg  # noqa: E402
from src.utils.stations import canonical_name, display_name  # noqa: E402
from src.utils.stats import (  # noqa: E402
    append_ausfall_row,
    append_stammstrecke_row,
)

LOGGER = logging.getLogger("update_stammstrecke_status")

# ---- Operating parameters ---------------------------------------------------

# VOR/VAO station IDs for the two Stammstrecke termini. Sourced from
# ``data/stations.json`` and pinned here so a station-directory drift
# cannot silently change which stops the script polls.
FLORIDSDORF_VOR_ID = "490033400"
MEIDLING_VOR_ID = "490101500"

# Canonical station-directory keys used to look up the user-facing labels
# via ``src.utils.stations``. These names MUST exist in
# ``data/stations.json`` (or one of the configured aliases) so the
# directory lookup succeeds. The fallback path in ``_short_target_label``
# preserves the literal value if the lookup fails.
FLORIDSDORF_CANONICAL_SEED = "Wien Floridsdorf"
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


def _short_target_label(seed_name: str) -> str:
    """Return the compact user-facing label for *seed_name*.

    Looks up the canonical station name in the project's station
    directory (``data/stations.json`` via :mod:`src.utils.stations`),
    applies ``display_name`` for project-wide overrides (e.g.
    ``Wien Mitte-Landstraße`` → ``Wien Mitte``), and strips the
    leading ``Wien `` prefix. The Stammstrecke description text
    ("in Richtung Meidling") implicitly assumes Vienna, so omitting
    the prefix produces natural German — but the canonical lookup
    still drives the suffix portion, so a future rename in the
    directory propagates automatically.

    The fallback chain — try directory, accept any
    :class:`Exception`, finally strip ``Wien `` from the seed —
    keeps the script resilient against a missing/corrupt directory
    file (fresh clone before stations sync, restricted CI runner
    without ``data/`` mounted, etc.).
    """

    try:
        canonical = canonical_name(seed_name)
    except Exception:  # pragma: no cover - defensive: directory load failure
        canonical = None

    name = display_name(canonical) if canonical else seed_name.strip()
    if name.startswith("Wien "):
        return name[len("Wien ") :]
    return name


@dataclass(frozen=True)
class _Direction:
    """A single Stammstrecke query direction.

    Carries the per-direction parameters (origin/destination VOR IDs,
    user-facing target label for the description, identity prefix for
    the deduplication key and GUID) so the main loop can iterate over
    both directions without branching on direction-specific logic.

    ``target_label`` is populated at module import time via
    :func:`_short_target_label`, which routes through the project's
    canonical station directory rather than hardcoding the display
    name.
    """

    origin_id: str
    destination_id: str
    target_label: str
    identity_prefix: str


# Direction labels: aligned with the Hbf script and the feed renderer.
#
# The northbound label was renamed from "Floridsdorf" → "Praterstern"
# in 2026-05-15: the new name is the next Stammstrecke stop after Hbf
# heading north, which is symmetric with the southbound "Meidling"
# label and accommodates short-turn trains that terminate at
# Praterstern (or even Wien Mitte) without continuing all the way
# to Floridsdorf. The label is hardcoded here rather than derived
# from a station-directory seed because the canonical seed
# "Wien Floridsdorf" no longer reflects the bucket's semantics —
# the bucket holds every Stammstrecke-northbound train regardless
# of its actual terminus.
#
# This script is no longer wired into the cron path (the Hbf
# ``/departureBoard`` script replaced it 2026-05-15 in PR #1496),
# but the constants are kept aligned so that a manual invocation
# produces ledger entries the Hbf reader can still consume.
DIRECTIONS: tuple[_Direction, ...] = (
    _Direction(
        origin_id=FLORIDSDORF_VOR_ID,
        destination_id=MEIDLING_VOR_ID,
        target_label=_short_target_label(MEIDLING_CANONICAL_SEED),
        identity_prefix="stammstrecke_delay_meidling",
    ),
    _Direction(
        origin_id=MEIDLING_VOR_ID,
        destination_id=FLORIDSDORF_VOR_ID,
        target_label="Praterstern",
        identity_prefix="stammstrecke_delay_praterstern",
    ),
)


_BREAKER = CircuitBreaker(
    "stammstrecke-vor",
    failure_threshold=BREAKER_FAILURE_THRESHOLD,
    recovery_timeout=BREAKER_RECOVERY_TIMEOUT,
)


def configure_logging() -> None:
    """Install the project's :class:`SafeFormatter` for this script."""

    setup_script_logging(logging.INFO)
    # urllib3 emits one INFO line per VAO request which clutters the
    # workflow log without adding diagnostic value. Mirrors the existing
    # scripts pattern.
    logging.getLogger("urllib3").setLevel(logging.WARNING)


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


# ---- Pending-trip state persistence ---------------------------------------


# Whitespace-runs and the pipe character are both removed from the
# raw VAO ``leg.name`` field before it is used as a state key or
# persisted to the ledger. Two motivations:
#
# 1. **Format drift.** Live ledger captures show VAO emitting
#    ``"S2"`` / ``"S3"`` (no space) while older / sibling deployments
#    use the spaced form ``"S 2"``. Without normalisation the same
#    physical train observed across a format flip would split into
#    two identity keys → both finalise → the train is counted twice
#    (the exact failure mode the dedup is supposed to prevent).
# 2. **Separator hardening.** :func:`_identity_key` joins the three
#    identity fields with ``|``. A poisoned upstream value containing
#    a literal pipe could collide with a different real key. Strip
#    the character at canonicalisation time so the separator is
#    invariant.
_NAME_NORMALISE_RE: Final = re.compile(r"\s+")


def _canonical_line_name(value: object) -> str:
    """Strip whitespace runs + pipe characters, upper-case the rest.

    ``"S 2"`` / ``"S2"`` / ``" s2 "`` → ``"S2"``. Empty / whitespace-
    only / ``None`` input returns the empty string; callers reject
    such legs upstream so the value never enters the ledger.

    *value* is typed as :class:`object` so the function can be the
    single canonicalisation point regardless of whether the caller
    has a guaranteed-``str`` value (``leg.get("name") or ""``) or a
    heterogeneous ``Mapping[str, Any]`` slot (``data.get("name")``)
    — the body coerces via ``str(value) if value is not None else ""``
    to avoid the falsy-collapse of ``str(0 or "")`` returning ``""``.
    """

    cleaned = _NAME_NORMALISE_RE.sub(
        "", str(value) if value is not None else ""
    ).replace("|", "")
    return cleaned.upper()


def _identity_key(direction: str, name: str, scheduled: datetime) -> str:
    """Build the canonical state-key for one observed S-Bahn trip.

    Composed of the three fields that uniquely identify a physical
    train run in our use case: the monitored direction
    (``Meidling``/``Floridsdorf``), the canonicalised line
    designation (see :func:`_canonical_line_name`) and the
    *scheduled* origin departure timestamp.
    Two different trains of the same line cannot share a scheduled
    departure from the same station at the same second, so the tuple
    is collision-free for our scope without requiring a VAO-internal
    journey reference.

    The pipe (``|``) separator is chosen because neither the
    direction label nor the canonicalised line name contain it (the
    canonicaliser strips it). ``direction`` is one of two hardcoded
    labels so no normalisation is required there.
    """

    return f"{direction}|{_canonical_line_name(name)}|{scheduled.isoformat()}"


def _coerce_aware(value: datetime) -> datetime:
    """Force *value* to be timezone-aware in :data:`VIENNA_TZ`.

    Naive datetimes can appear when a hand-edited state file omits
    the offset; rather than rejecting the entry we localise it
    defensively so the rest of the state survives.
    """

    if value.tzinfo is None:
        return value.replace(tzinfo=VIENNA_TZ)
    return value.astimezone(VIENNA_TZ)


def _trip_to_json(trip: _PendingTrip) -> dict[str, Any]:
    """Serialise a pending trip to its JSON-ledger form.

    The ``cancelled`` field is always emitted (even when ``False``) so a
    grep over the committed ledger picks up the schema version
    explicitly — reviewers do not have to infer the boolean from its
    absence. Backwards-compat is enforced on the read side
    (:func:`_trip_from_json` defaults missing values to ``False``).
    """

    return {
        "direction": trip.direction,
        "name": trip.name,
        "scheduled": trip.scheduled.isoformat(),
        "latest_delay_minutes": trip.latest_delay_minutes,
        "last_seen_at": trip.last_seen_at.isoformat(),
        "cancelled": trip.cancelled,
    }


def _trip_from_json(data: Mapping[str, Any]) -> _PendingTrip | None:
    """Best-effort parser for a single ledger entry; ``None`` on shape error.

    The ``name`` field flows through :func:`_canonical_line_name` so an
    old ledger written before the H1 normalisation fix (which carried
    spaced names like ``"S 2"``) is migrated to the canonical form on
    next load — without that step, an entry stored as ``"S 2"`` would
    fail to match a freshly-observed ``"S2"`` on the next tick and the
    train would be tracked twice.
    """

    try:
        direction = str(data["direction"]).strip()
        name = _canonical_line_name(data.get("name"))
        scheduled = datetime.fromisoformat(str(data["scheduled"]))
        latest_delay = float(data["latest_delay_minutes"])
        last_seen = datetime.fromisoformat(str(data["last_seen_at"]))
    except (KeyError, TypeError, ValueError):
        return None
    if not direction or not name:
        return None
    # Backwards-compat: a ledger entry written before the cancellation-
    # tracking schema (commits prior to 2026-05-15) has no ``cancelled``
    # key — default to ``False`` so the entry loads as a regular
    # delay-bearing observation. Strict ``is True`` matches the upstream
    # VAO contract for the boolean and refuses ``"true"`` / ``1`` from a
    # hand-edited ledger (the writer always emits a Python bool).
    cancelled = data.get("cancelled") is True
    return _PendingTrip(
        direction=direction,
        name=name,
        scheduled=_coerce_aware(scheduled),
        latest_delay_minutes=latest_delay,
        last_seen_at=_coerce_aware(last_seen),
        cancelled=cancelled,
    )


def _load_pending_trips(path: Path) -> dict[str, _PendingTrip]:
    """Read the pending-trip ledger from *path*; corruption-tolerant.

    Returns an empty dict on missing / oversize / unparseable input;
    each diagnostic is logged at WARNING so an operator can spot the
    fresh-start fallback without the script ever blocking the cron
    pipeline.
    """

    raw = read_capped_text(
        path,
        max_bytes=PENDING_TRIPS_MAX_BYTES,
        label="pending trips",
        logger=LOGGER,
    )
    if raw is None:
        return {}
    if not raw.strip():
        return {}
    try:
        # Security: ``loads_finite`` pins parse_constant + parse_float
        # hooks (Round 1503 sibling) that reject NaN / Infinity / 1e1000
        # literals planted into a poisoned ``cache/stammstrecke/
        # pending_trips.json`` (compromised CI runner / partial flush +
        # power loss / parallel orchestrator atomic state swap). Without
        # the hooks the planted literal propagates as ``float('nan')`` /
        # ``float('inf')`` past the downstream ``_trip_from_json``
        # validators (which use ``isinstance(value, (int, float))``
        # checks that ``True`` on ``float('nan')``) and round-trip-
        # crashes the writer pin (Round 1485) on next save.
        payload = loads_finite(raw)
    except (ValueError, RecursionError) as exc:
        LOGGER.warning(
            "Pending-Trips-State korrupt (%s) — starte mit leerem Ledger.",
            sanitize_log_arg(str(exc)),
        )
        return {}
    if not isinstance(payload, Mapping):
        LOGGER.warning(
            "Pending-Trips-State hat unerwartetes Top-Level-Format — "
            "starte mit leerem Ledger."
        )
        return {}
    state: dict[str, _PendingTrip] = {}
    for value in payload.values():
        if not isinstance(value, Mapping):
            continue
        trip = _trip_from_json(value)
        if trip is None:
            continue
        canonical_key = _identity_key(trip.direction, trip.name, trip.scheduled)
        state[canonical_key] = trip
    return state


def _save_pending_trips(path: Path, state: Mapping[str, _PendingTrip]) -> bool:
    """Persist *state* atomically; best-effort.

    Returns ``True`` on success, ``False`` if the write failed (and
    was logged at WARNING). The caller is free to ignore the return
    value — losing one tick's state update means the affected trips
    keep their previous state on next load, which is the desired
    safe-fallback shape.

    Security (Trojan-Source / BiDi-Mark Drift, sibling of PRs #1434 /
    #1435 and Rounds 10–14): the file is operator-facing diagnostic
    state, committed to ``main`` by ``update-cycle.yml``'s
    ``add_options: '-A'`` auto-commit step, and reviewed via ``cat``
    / ``less`` / the GitHub web UI / IDE preview. ``ensure_ascii=True``
    escapes every non-ASCII code point as a literal ``\\uXXXX``
    sequence so a VAO-upstream-controlled ``leg.name`` carrying the
    canonical CVE-2021-42574 BiDi / zero-width / Unicode line-
    terminator / 8-bit C1 union cannot leak as raw UTF-8 bytes:
    ``_canonical_line_name`` only strips ``\\s+`` (which excludes
    U+202E and the rest of category Cf) plus ``|``, so the
    upstream-derived field reaches the dict KEY (via
    ``_identity_key``) and the inner ``name`` value verbatim.
    Mirrors the canonical fix shape pinned for the sibling
    ``data/*.json`` / ``cache/<provider>/last_run.json`` state
    writers; forensic intent is preserved
    (``_load_pending_trips`` recovers the original string from the
    literal escape via ``json.loads``). Legitimate payload content
    is exclusively ASCII (hardcoded direction labels, short S-Bahn
    / R / REX / CJX line designations, ISO-8601 timestamps), so the
    diff shape is unchanged on the happy path.

    Security (Non-Finite Literal Writer-Defence Drift, sibling of
    PRs #1487 / #1488 — coordinate + companion-writer rounds):
    ``allow_nan=False`` mirrors the canonical writer-side pin
    established for the sibling state-sink writers
    (``data/first_seen.json`` / ``data/stations_last_run.json`` /
    ``data/vor_request_count.json`` / ``data/places_quota.json`` /
    ``cache/<provider>/last_run.json``). ``_trip_to_json`` emits
    ``"latest_delay_minutes": trip.latest_delay_minutes`` directly
    from the concrete ``float`` field; a future refactor of
    :func:`_leg_departure_delay_minutes` that lets ``float('nan')``
    / ``float('inf')`` reach ``latest_delay_minutes`` (missing-data
    sentinel, third-party SDK NaN observation, derived-statistic
    division) would otherwise plant the non-standard literal —
    invalid per RFC 8259 §6 — in the committed artefact. This pin
    is also the writer-side dual of the reader-side ``loads_finite``
    hook on :func:`_load_pending_trips`: together they enforce the
    round-trip invariant that no non-finite literal can enter or
    leave the on-disk state without surfacing as a loud
    ``ValueError`` at the producing call.
    """

    payload = {key: _trip_to_json(trip) for key, trip in state.items()}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with atomic_write(
            path,
            mode="w",
            encoding="utf-8",
            permissions=0o644,
        ) as fh:
            _json_lib.dump(
                payload,
                fh,
                indent=2,
                sort_keys=True,
                ensure_ascii=True,
                allow_nan=False,
            )
            fh.write("\n")
        return True
    except OSError as exc:
        LOGGER.warning(
            "Pending-Trips-State konnte nicht geschrieben werden: %s",
            sanitize_log_arg(str(exc)),
        )
        return False


def _purge_stale_entries(
    state: dict[str, _PendingTrip],
    *,
    cutoff: datetime,
) -> int:
    """Drop entries whose ``last_seen_at`` is strictly before *cutoff*.

    Returns the number of entries removed. Mutates *state* in place.
    """

    stale = [key for key, trip in state.items() if trip.last_seen_at < cutoff]
    for key in stale:
        del state[key]
    return len(stale)


def _load_recently_finalised(path: Path) -> dict[str, datetime]:
    """Read the recently-finalised companion ledger from *path*.

    Schema: ``{identity_key: iso-8601-timestamp}``. Returns an empty
    dict on missing / oversize / unparseable input — the WARNING log
    line distinguishes the silent fresh-start from a healthy first
    run.
    """

    raw = read_capped_text(
        path,
        max_bytes=RECENTLY_FINALISED_MAX_BYTES,
        label="recently finalised",
        logger=LOGGER,
    )
    if raw is None:
        return {}
    if not raw.strip():
        return {}
    try:
        # Security: same parse_constant + parse_float hook pin as the
        # pending-trips ledger above — both sidecars share the same
        # disk-tainted threat model (Round 1503 sibling). A poisoned
        # ``cache/stammstrecke/recently_finalised.json`` would otherwise
        # land ``float('nan')`` in the timestamp field, breaking the
        # subsequent ``datetime.fromisoformat`` shape check at the
        # ``str`` instance test below but still leaking through the
        # writer-pin round-trip the next time the ledger is rewritten.
        payload = loads_finite(raw)
    except (ValueError, RecursionError) as exc:
        LOGGER.warning(
            "Recently-Finalised-Ledger korrupt (%s) — starte mit leerem Set.",
            sanitize_log_arg(str(exc)),
        )
        return {}
    if not isinstance(payload, Mapping):
        LOGGER.warning(
            "Recently-Finalised-Ledger hat unerwartetes Top-Level-Format — "
            "starte mit leerem Set."
        )
        return {}
    out: dict[str, datetime] = {}
    for key, value in payload.items():
        if not isinstance(value, str):
            continue
        try:
            ts = datetime.fromisoformat(value)
        except ValueError:
            continue
        out[str(key)] = _coerce_aware(ts)
    return out


def _save_recently_finalised(
    path: Path, finalised: Mapping[str, datetime]
) -> bool:
    """Persist the recently-finalised companion ledger atomically.

    Security: same Trojan-Source / BiDi-Mark threat model as
    :func:`_save_pending_trips` — the keys are built via
    :func:`_identity_key` which interpolates a VAO-upstream-controlled
    leg-name segment, so a hostile / pathological upstream payload
    surfaces here through the same path. ``ensure_ascii=True`` escapes
    every non-ASCII code point so no raw byte in the canonical attack
    union reaches the committed
    ``cache/stammstrecke/recently_finalised.json``.

    Security (Non-Finite Literal Writer-Defence Drift, defence-in-
    depth sibling pin of :func:`_save_pending_trips`):
    ``allow_nan=False`` is set in lockstep with the canonical writer
    pin even though today's payload is all-string
    (``{key: ts.isoformat()}`` — ``isoformat()`` always returns a
    finite-byte string). The pin protects against a future schema
    widening that adds a numeric field (re-emission count, age-in-
    seconds for cleanup tooling, observed-delay arithmetic) — the
    sibling pin keeps the writer-shape contract uniform across the
    two-file ledger pair so a future refactor cannot regress one
    half of the round-trip invariant.
    """

    payload = {key: ts.isoformat() for key, ts in finalised.items()}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with atomic_write(
            path,
            mode="w",
            encoding="utf-8",
            permissions=0o644,
        ) as fh:
            _json_lib.dump(
                payload,
                fh,
                indent=2,
                sort_keys=True,
                ensure_ascii=True,
                allow_nan=False,
            )
            fh.write("\n")
        return True
    except OSError as exc:
        LOGGER.warning(
            "Recently-Finalised-Ledger konnte nicht geschrieben werden: %s",
            sanitize_log_arg(str(exc)),
        )
        return False


def _purge_finalised_entries(
    finalised: dict[str, datetime],
    *,
    cutoff: datetime,
) -> int:
    """Drop finalisation records older than *cutoff*; in-place.

    Once the TTL has elapsed, re-emission of a long-finalised train is
    indistinguishable from a fresh observation — falling out of the
    suppression set is the conservative choice (versus retaining
    forever, which would also count as state-file rot).
    """

    stale = [key for key, ts in finalised.items() if ts < cutoff]
    for key in stale:
        del finalised[key]
    return len(stale)


@contextmanager
def _ledger_lock(lock_path: Path) -> Iterator[None]:
    """OS-level advisory lock around the load-modify-save ledger sequence.

    Uses :mod:`fcntl` on POSIX runners (the production target). On
    platforms without ``fcntl`` (Windows dev boxes) the lock degrades
    to a no-op — same risk model as the pre-lock code, and the
    project's production runners are all Linux.

    The lock file is opened in append mode so the first caller
    creates it without truncating any existing content (we only use
    its file descriptor; the file body is irrelevant). A WARNING is
    logged on lock-acquire failures so an operator sees the
    degradation explicitly.
    """

    try:
        import fcntl  # POSIX-only; fcntl is in the stdlib on Linux.
    except ImportError:  # pragma: no cover - non-POSIX dev machines.
        yield
        return

    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = lock_path.open("a", encoding="utf-8")
    except OSError as exc:
        LOGGER.warning(
            "Konnte Ledger-Lock-Datei nicht öffnen (%s) — fahre ohne Lock fort.",
            sanitize_log_arg(str(exc)),
        )
        yield
        return
    try:
        try:
            fcntl.flock(fd.fileno(), fcntl.LOCK_EX)
        except OSError as exc:
            LOGGER.warning(
                "Ledger-Lock konnte nicht erworben werden (%s) — fahre ohne "
                "Lock fort.",
                sanitize_log_arg(str(exc)),
            )
            yield
            return
        try:
            yield
        finally:
            try:
                fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
            except OSError as exc:
                LOGGER.debug(
                    "Ledger-Lock konnte nicht freigegeben werden: %s",
                    sanitize_log_arg(str(exc)),
                )
    finally:
        try:
            fd.close()
        except OSError as exc:
            LOGGER.debug(
                "Ledger-Lock-Datei konnte nicht geschlossen werden: %s",
                sanitize_log_arg(str(exc)),
            )


# ---- VAO ``/trip`` request + parse ----------------------------------------


class _QuotaExceeded(RuntimeError):
    """Raised when the VAO daily quota is exhausted before a request."""


def _build_session(stack: ExitStack) -> requests.Session:
    """Create a :class:`requests.Session` with VOR auth + retries.

    The session is registered with *stack* so the orchestrator's
    ``with ExitStack()`` block manages teardown deterministically (close
    + connection-pool drain) regardless of which direction's call
    raises. Mirrors the pattern used by ``scripts/update_vor_cache.py``.
    """

    session = stack.enter_context(
        session_with_retries(
            vor_provider.VOR_USER_AGENT,
            **vor_provider.VOR_RETRY_OPTIONS,
        )
    )
    vor_provider.apply_authentication(session)
    return session


def _charge_one_request(now: datetime) -> None:
    """Reserve one VAO request slot or raise :class:`_QuotaExceeded`.

    Threading: the ``_QUOTA_LOCK`` from :mod:`src.providers.vor` is
    held across the read-then-increment so two parallel script
    invocations cannot race past the cap. The Stammstrecke cron does
    not run in parallel (concurrency group ``external-api-fetch``),
    but the lock keeps the contract identical to every other VOR
    consumer for free.
    """

    with vor_provider._QUOTA_LOCK:
        _, current_usage = vor_provider.load_request_count()
        if current_usage >= vor_provider.MAX_REQUESTS_PER_DAY:
            raise _QuotaExceeded(
                f"VAO daily quota exhausted ({current_usage}/"
                f"{vor_provider.MAX_REQUESTS_PER_DAY})"
            )
        vor_provider.save_request_count(now)


def _query_trips(
    session: requests.Session,
    direction: _Direction,
    *,
    when: datetime,
    timeout: int = QUERY_TIMEOUT,
) -> list[Mapping[str, Any]]:
    """Call ``/trip`` once for *direction* and return the parsed Trip list.

    Validates: HTTP status (handled by ``fetch_content_safe`` raising
    ``RequestException`` on 4xx/5xx), JSON shape (``dict`` with optional
    ``Trip`` list), and per-trip shape (``dict``). Anything else raises
    so the caller's per-direction error isolation runs.

    The wall-clock lookup window is ``date=today, time=now``; the VAO
    server returns the next ``MAX_TRIPS_PER_QUERY`` connections after
    that timestamp. ``rtMode=SERVER_DEFAULT`` enables realtime delays
    so ``Origin.rtTime`` is populated when available.
    """

    safe_timeout = max(1, min(timeout, MAX_QUERY_TIMEOUT))

    # Param-naming notes for the VAO ``/trip`` endpoint (sixth iteration
    # of the 2026-05-09 triage; see PR #1391's commit log for the
    # full chain). After the workflow-config fix exposed the accessId,
    # VAO's response advanced from ``"Missing value for required param
    # accessId"`` to ``"location missing or invalid (LOCATION)"`` —
    # confirming auth works but the ``originId=extId::<id>`` form
    # (Gemini's hypothesis from the manual) is NOT what the live
    # endpoint accepts. We revert to the form documented in the
    # ``trip.md`` curl example AND used by the working
    # ``_fetch_departure_board_for_station`` in
    # ``src/providers/vor.py``: bare numeric station IDs passed to
    # ``originId``/``destId`` directly.
    params: dict[str, str] = {
        "originId": direction.origin_id,
        "destId": direction.destination_id,
        "date": when.strftime("%Y-%m-%d"),
        "time": when.strftime("%H:%M"),
        "numF": str(MAX_TRIPS_PER_QUERY),
        # Force direct connections — the only Stammstrecke-relevant
        # signal is the per-S-Bahn-leg delay, and a transfer would
        # dilute the sample with the (irrelevant) waiting time.
        "maxChange": "0",
        # Enable server-side realtime data so ``Origin.rtTime`` is
        # populated when available.
        "rtMode": "SERVER_DEFAULT",
        # Filter on product classes Train (1) + S-Bahn (2)
        "products": "3",
    }

    endpoint = f"{vor_provider.VOR_BASE_URL}trip"

    # ``request_safe(raise_for_status=False)`` rather than the
    # ``fetch_content_safe`` wrapper: the wrapper hardcodes
    # ``raise_for_status=True``, which means the request_safe ``except``
    # block runs ``r.close()`` on the response BEFORE the body is read
    # into ``r._content`` (see ``src/utils/http.py`` lines ~2068-2073).
    # The diagnostic helpers downstream (``_decode_error_body``,
    # ``_describe_error_body_keys``) would then see ``response.content
    # == b""`` and report ``body_keys=[EMPTY_BODY]`` — even though the
    # ``Content-Length`` header confirms the body has bytes (``cl=163``
    # in the 2026-05-09 cron run). With ``raise_for_status=False`` the
    # body is read into ``_content`` before any exception path can
    # close the stream. We then check status manually and synthesise
    # the same ``HTTPError`` the wrapper would have raised, but with a
    # response whose ``.content`` is fully populated.
    response = request_safe(
        session,
        endpoint,
        method="GET",
        raise_for_status=False,
        params=params,
        # Content negotiation via Accept header instead of the
        # ``format=json`` query parameter. The trip.md curl example
        # uses this exact pattern, and removing the query parameter
        # eliminates one variable in the iterative HTTP 400 triage.
        headers={"Accept": "application/json"},
        timeout=safe_timeout,
        allowed_content_types=("application/json",),
    )
    if response.status_code >= 400:
        raise requests.HTTPError(
            f"VAO /trip returned HTTP {response.status_code}",
            response=response,
        )
    content = response.content

    try:
        # Security: ``loads_finite`` pins parse_constant + parse_float
        # hooks (Round 1503 sibling). A compromised HAFAS VAO upstream /
        # MITM serving NaN / Infinity / 1e1000 literals in a trip
        # payload would otherwise propagate ``float('nan')`` /
        # ``float('inf')`` into delay-minute arithmetic and round-trip-
        # crash the writer pin (Round 1485) on pending-trip persistence.
        payload = loads_finite(content)
    except (ValueError, RecursionError) as exc:
        # Drift defence (JSON Depth-Bomb Round 5): a depth-bomb body
        # passes the size cap (it can be only a few KiB on the wire) but
        # blows the recursion limit on parse, and ``RecursionError`` is
        # NOT a subclass of ``ValueError``. Re-raise as ``ValueError`` so
        # the caller's per-direction error-isolation branch runs without
        # propagating the BaseException-rooted recursion failure further.
        raise ValueError(
            f"VAO /trip returned unparseable JSON: {type(exc).__name__}"
        ) from exc
    if not isinstance(payload, dict):
        raise TypeError(
            f"VAO /trip returned non-dict payload: {type(payload).__name__}"
        )
    raw_trips = payload.get("Trip")
    if raw_trips is None:
        return []
    if isinstance(raw_trips, Mapping):
        # Some HAFAS-style JSON serialisers collapse single-element lists
        # to the bare object. Normalise to list-of-dict.
        return [raw_trips]
    if not isinstance(raw_trips, list):
        raise TypeError(
            f"VAO /trip Trip field has unexpected type: {type(raw_trips).__name__}"
        )
    return [t for t in raw_trips if isinstance(t, Mapping)]


def _is_sbahn_leg(leg: object) -> bool:
    """Return ``True`` when *leg* represents a Vienna S-Bahn or regional rail product.

    The filter targets regional rail: the Vienna S-Bahn product
    family (``S 1``, ``S 2``, ``S 7``, ``S 80`` …) as well as Regional
    (``R``), Regional Express (``REX``), and Cityjet Express (``CJX``)
    trains are accepted. InterCity (``IC``), Railjet (``RJ``), and any
    non-rail products are rejected.

    Checks (any single signal is sufficient):

    * ``leg.category in {"S", "R", "REX", "CJX"}`` — VAO's preferred field;
    * ``leg.name`` matching ``^\\s*(S|REX|R|CJX)\\s*\\d+\\s*$`` — fallback for
      older VAO peers that only set the human-readable label;
    * ``leg.Product[].catOut in {"S", "R", "REX", "CJX"}`` or
      ``Product[].line`` matching ``^\\s*(S|REX|R|CJX)\\s*\\d+\\s*$`` — the
      JSON-RPC nested form some VAO releases use.

    The previous-generation matcher also accepted ``"SB"`` as category;
    the 2026-05-09 Senior-API-Integration audit removed it because
    ``SB`` is ambiguous in the German-speaking ÖV space (it can denote
    *Schnellbahn* — a synonym for S-Bahn — but also *Schnellbus* in
    some VAO/ÖBB regional dialects, and there is no SB service on the
    Stammstrecke). ``CJX`` was added 2026-05-17 once ÖBB rebranded
    selected REX rolling-stock as Cityjet Express; the line still
    serves Stammstrecke-axis corridors (CJX 9 to Payerbach-Reichenau,
    CJX 5 to Wiener Neustadt) and must not be dropped from the sample
    just because of the new product label.

    Accepts ``object`` (rather than ``Mapping``) so the defensive
    ``isinstance(leg, Mapping)`` gate is reachable at type-check time
    — a non-mapping payload (a planted ``None`` / ``str`` / ``list``
    that slipped past upstream JSON parsing) returns ``False`` cleanly
    instead of triggering an unreachable-statement diagnostic.
    """

    if not isinstance(leg, Mapping):
        return False

    category = (str(leg.get("category") or "")).strip().upper()
    if category in {"S", "R", "REX", "CJX"}:
        return True

    name = str(leg.get("name") or "").strip()
    if _S_BAHN_LINE_RE.match(name):
        return True

    products = leg.get("Product")
    if isinstance(products, list):
        candidates: list[Mapping[str, Any]] = [
            p for p in products if isinstance(p, Mapping)
        ]
    elif isinstance(products, Mapping):
        candidates = [products]
    else:
        candidates = []

    for product in candidates:
        cat_out = str(product.get("catOut") or "").strip().upper()
        if cat_out in {"S", "R", "REX", "CJX"}:
            return True
        line = str(product.get("line") or "").strip()
        if _S_BAHN_LINE_RE.match(line):
            return True

    return False


def _parse_vao_dt(date_str: Any, time_str: Any) -> datetime | None:
    """Parse a VAO ``date``/``time`` pair into a Vienna-localised datetime.

    Accepts ``date`` in ``YYYY-MM-DD`` and ``time`` in either
    ``HH:MM:SS`` or ``HH:MM``. Returns ``None`` on any parse failure so
    callers can skip the leg without crashing the whole run.

    A naive ``datetime`` is force-localised to Europe/Vienna with
    ``fold=0`` (matches :func:`src.providers.vor._parse_dt`'s convention
    for the once-a-year DST ambiguity).
    """

    date_txt = str(date_str or "").strip()
    if not date_txt:
        return None
    time_txt = str(time_str or "").strip()
    if time_txt:
        # VAO returns either ``HH:MM:SS`` or ``HH:MM`` — accept both by
        # truncating to ``HH:MM`` (the per-sample mean arithmetic
        # below operates on minutes, so dropping seconds is lossless
        # in practice and avoids strptime branching).
        time_txt = time_txt[:5]
    else:
        time_txt = "00:00"
    try:
        naive = datetime.strptime(f"{date_txt} {time_txt}", "%Y-%m-%d %H:%M")
    except ValueError:
        return None
    return naive.replace(tzinfo=VIENNA_TZ, fold=0)


def _leg_departure_delay_minutes(leg: Mapping[str, Any]) -> float | None:
    """Return the leg's departure delay in fractional minutes, or None.

    Computes ``Origin.rtTime - Origin.time`` (resp. ``rtDepTime -
    depTime`` on legacy peers) in minutes:

    * **rtTime missing** — returns ``None`` (status *unknown*). VAO
      omits ``rtTime`` both when realtime confirms on-time AND when
      no realtime signal is available for the leg; without a way to
      tell the two cases apart, coercing missing rtTime to ``0.0``
      systematically biased the sample downward (the 2026-05 ledger
      ran at ~88% exact zeros and 0.2 min mean over 30 days, masking
      real delays). Treating the field as "no signal" excludes the
      leg from the sample so the statistic reflects only legs whose
      status was genuinely observed.
    * **On-time (rtTime == time)** — yields exactly ``0.0`` minutes,
      an explicit on-time signal from the upstream.
    * **Cancelled** — returns ``None`` (no signal; cancelled trains
      are not "delayed", they are "absent").
    * **Schedule unparseable** — returns ``None`` (the leg cannot
      contribute a meaningful delay value).
    * **Realtime field present but unparseable** — returns ``None``
      (a malformed ``rtTime`` is treated like a missing schedule
      rather than silently coerced to zero).

    Negative delays (early departure) are possible at the timetable
    level and contribute negative values, which is still meaningful
    — keep them.
    """

    origin = leg.get("Origin")
    if not isinstance(origin, Mapping):
        return None
    if origin.get("cancelled") is True or leg.get("cancelled") is True:
        return None

    sched_date = origin.get("date") or origin.get("depDate")
    sched_time = origin.get("time") or origin.get("depTime")
    scheduled = _parse_vao_dt(sched_date, sched_time)
    if scheduled is None:
        return None

    rt_time = origin.get("rtTime") or origin.get("rtDepTime")
    if not rt_time:
        return None

    # ``rt_date_explicit`` captures whether VAO ITSELF supplied a real-
    # time date (vs the ``sched_date`` fallback). The midnight-rollover
    # heuristic below is only applied when it did NOT — otherwise the
    # explicit date is authoritative and a same-day early departure is a
    # legitimate small-magnitude negative delay we must record verbatim.
    rt_date_explicit = origin.get("rtDate") or origin.get("rtDepDate")
    rt_date = rt_date_explicit or sched_date
    actual = _parse_vao_dt(rt_date, rt_time)
    if actual is None:
        return None

    # Midnight-rollover heuristic ported from the sibling
    # :func:`update_stammstrecke_hbf._departure_delay_minutes`. Without
    # an explicit ``rtDate``, ``actual`` defaults to ``sched_date``; a
    # scheduled-at-23:55 leg with ``rtTime="00:05"`` therefore yields a
    # same-day 00:05 ``actual`` and a meaningless ≈ −23h50m "delay"
    # which then propagates verbatim into ``latest_delay_minutes`` and
    # the downstream Stammstrecke aggregator. Bumping ``actual`` by one
    # day when the computed delay is < −12 h reflects the true wall-
    # clock difference. 12 h cleanly separates a legitimate small
    # negative early departure from a midnight wrap.
    if rt_date_explicit is None and (scheduled - actual) > timedelta(hours=12):
        actual = actual + timedelta(days=1)
    # Symmetric wrap: a leg scheduled just AFTER midnight (e.g. 00:05)
    # departing a few minutes early (rtTime "23:54", i.e. the previous
    # day) would otherwise yield a bogus ≈ +23h49m delay; pull ``actual``
    # back one day so it becomes the true small negative value.
    elif rt_date_explicit is None and (actual - scheduled) > timedelta(hours=12):
        actual = actual - timedelta(days=1)

    return (actual - scheduled).total_seconds() / 60.0


@dataclass(frozen=True)
class _SbahnLegObservation:
    """One filtered, parsed S-Bahn leg observation.

    Produced by :func:`_collect_sbahn_leg_observations` and consumed
    by :func:`_observe_legs` (state update + latest-wins dedup) and
    :func:`_collect_sbahn_delays_minutes` (legacy thin wrapper for
    tests that only care about the delay sequence).

    ``cancelled`` is ``True`` when the upstream marked the leg as
    cancelled. For cancelled legs ``delay_minutes`` is a meaningless
    placeholder (``0.0``) — the finalise pass downstream of
    :func:`_observe_legs` routes cancelled trips to the dedicated
    cancellation ledger instead of folding them into the delay mean.
    Default ``False`` keeps the dataclass backwards-compatible with
    tests that construct observations positionally.
    """

    name: str
    scheduled: datetime
    delay_minutes: float
    cancelled: bool = False


def _leg_is_cancelled(leg: Mapping[str, Any]) -> bool:
    """Return ``True`` when the upstream flagged *leg* as cancelled.

    Mirrors the cancellation branch inside
    :func:`_leg_departure_delay_minutes`: either the leg itself or its
    ``Origin`` substructure may carry the boolean. Both VAO
    serialisations are observed in production payloads. Pure boolean
    contract — refuses ``"true"`` / ``1`` / etc. so a hand-edited
    cache cannot trick the collector by spelling the flag as a string.
    """

    origin = leg.get("Origin")
    if isinstance(origin, Mapping) and origin.get("cancelled") is True:
        return True
    return leg.get("cancelled") is True


def _collect_sbahn_leg_observations(
    trips: Iterable[Mapping[str, Any]],
) -> list[_SbahnLegObservation]:
    """Extract per-leg observations from a ``/trip`` response.

    Filters:

    * **Direct only** — exactly one ride leg in ``LegList.Leg``.
      Walk-only segments before/after the ride are tolerated; multi-ride
      trips (changes) are rejected because the change-waiting time would
      dilute the sample.
    * **S-Bahn only** — the single ride leg must pass
      :func:`_is_sbahn_leg`.
    * **Identifiable** — the leg must carry a non-empty ``name``
      (line designation) and a parseable scheduled departure
      timestamp; both feed the state-key for cross-tick dedup.
    * **Cancellation captured** — a cancelled leg is emitted as an
      observation with ``cancelled=True`` and a placeholder
      ``delay_minutes=0.0``. Downstream :func:`_observe_legs` carries
      the flag through the pending-trip ledger so the finalise pass
      can route the train to the dedicated cancellation CSV. Pre-fix
      behaviour silently dropped these legs, masking real outages
      from the statistics dashboard.
    * **Missing realtime excluded** — a non-cancelled leg without
      ``rtTime`` returns ``None`` from
      :func:`_leg_departure_delay_minutes` (status unknown, not
      implicitly on-time) and is dropped here. Cancellations are
      checked BEFORE the delay-minutes branch so a cancelled leg
      that also lacks ``rtTime`` (the canonical shape) is still
      captured.
    """

    observations: list[_SbahnLegObservation] = []
    for trip in trips:
        leg_list = trip.get("LegList")
        if not isinstance(leg_list, Mapping):
            continue
        raw_legs = leg_list.get("Leg")
        if isinstance(raw_legs, Mapping):
            # Single-leg trips are sometimes serialised as a bare object.
            legs: list[Mapping[str, Any]] = [raw_legs]
        elif isinstance(raw_legs, list):
            legs = [item for item in raw_legs if isinstance(item, Mapping)]
        else:
            continue

        ride_legs = [
            leg
            for leg in legs
            if str(leg.get("type") or "").strip().upper() != "WALK"
        ]
        if len(ride_legs) != 1:
            continue
        leg = ride_legs[0]
        if not _is_sbahn_leg(leg):
            continue

        # Canonicalise the line designation at extraction time so the
        # identity key + persisted ``_PendingTrip.name`` are stable
        # against VAO format drift (``"S 2"`` ↔ ``"S2"``) and
        # separator collisions (a ``|`` inside the raw name).
        name = _canonical_line_name(leg.get("name") or "")
        if not name:
            continue
        origin = leg.get("Origin")
        if not isinstance(origin, Mapping):
            continue
        scheduled = _parse_vao_dt(
            origin.get("date") or origin.get("depDate"),
            origin.get("time") or origin.get("depTime"),
        )
        if scheduled is None:
            continue

        if _leg_is_cancelled(leg):
            observations.append(
                _SbahnLegObservation(
                    name=name,
                    scheduled=scheduled,
                    delay_minutes=0.0,
                    cancelled=True,
                )
            )
            continue

        delay = _leg_departure_delay_minutes(leg)
        if delay is None:
            continue
        observations.append(
            _SbahnLegObservation(
                name=name,
                scheduled=scheduled,
                delay_minutes=delay,
            )
        )
    return observations


def _collect_sbahn_delays_minutes(
    trips: Iterable[Mapping[str, Any]],
) -> list[float]:
    """Backward-compatible thin wrapper around
    :func:`_collect_sbahn_leg_observations`.

    Returns just the per-leg delays — the original signature retained
    so leg-filtering regression tests continue to assert on a simple
    sequence of floats without coupling to the observation record
    shape. Cancelled observations carry a meaningless placeholder
    ``delay_minutes=0.0`` (set so the dataclass still parses), so they
    are filtered out here to preserve the pre-cancellation-tracking
    semantics of "delays-only sequence".
    """

    return [
        obs.delay_minutes
        for obs in _collect_sbahn_leg_observations(trips)
        if not obs.cancelled
    ]


def _observe_legs(
    state: dict[str, _PendingTrip],
    observations: Iterable[_SbahnLegObservation],
    *,
    direction: str,
    now: datetime,
    recently_finalised: Mapping[str, datetime] | None = None,
) -> int:
    """Insert / overwrite *observations* in *state* with latest-wins semantics.

    Each observation produces or updates a state entry keyed by
    :func:`_identity_key`. Re-observation of the same train (same
    ``direction``, ``name``, ``scheduled``) overwrites
    ``latest_delay_minutes`` and ``last_seen_at`` so the value that
    eventually flows into the CSV row is the most recent reading —
    typically the one taken closest to the train's actual departure,
    which is most accurate.

    *recently_finalised* (M4 defence): keys present in this mapping
    were already finalised on a prior tick. If VAO unexpectedly
    returns one of them again (anomalous upstream behaviour at the
    lookahead boundary), the observation is skipped — without this
    gate, the rediscovered train would re-enter the ledger, get
    finalised again on the next tick, and produce a second CSV row
    for the same physical train. Pass ``None`` to disable the gate
    (default for legacy unit tests).

    Returns the number of state entries written this call.
    """

    suppressed: Mapping[str, datetime] = recently_finalised or {}
    written = 0
    for obs in observations:
        key = _identity_key(direction, obs.name, obs.scheduled)
        if key in suppressed:
            LOGGER.info(
                "Stammstrecke: bereits finalisierter Zug erneut beobachtet "
                "(%s, scheduled=%s) — Beobachtung verworfen.",
                sanitize_log_arg(direction),
                sanitize_log_arg(obs.scheduled.isoformat()),
            )
            continue
        state[key] = _PendingTrip(
            direction=direction,
            name=obs.name,
            scheduled=obs.scheduled,
            latest_delay_minutes=obs.delay_minutes,
            last_seen_at=now,
            cancelled=obs.cancelled,
        )
        written += 1
    return written


def _finalize_departed(
    state: dict[str, _PendingTrip],
    *,
    direction: str,
    now: datetime,
    recently_finalised: dict[str, datetime] | None = None,
) -> list[_PendingTrip]:
    """Pop departed trips for *direction* and return them as full records.

    A trip is "departed" when ``scheduled <= now`` — the latest
    reading we ever recorded for that train is then committed to the
    caller (which writes it into the CSV row) and the entry is
    removed from *state* so the same train cannot contribute to a
    later cron tick. Sort order is ascending scheduled time so the
    resulting CSV row's mean is computed over a deterministic sample.

    The returned list contains the full :class:`_PendingTrip` records
    (not just the delay floats) so the caller can scope the CSV row
    timestamp to the actual scheduled departure time — important at
    the New-Year boundary, where the cron tick wall clock and the
    train's scheduled year can differ.

    When *recently_finalised* is supplied (the canonical production
    path), each popped key is registered there with ``now`` as its
    finalisation timestamp so a future re-observation can be
    suppressed by :func:`_observe_legs`.

    Mutates *state* (and, when supplied, *recently_finalised*) in place.
    """

    # ``key not in recently_finalised`` guard: if the previous tick
    # successfully wrote the recently-finalised ledger but then CRASHED
    # before ``_save_pending_trips`` rewrote ``pending_trips.json``, the
    # next load sees both files out of sync — the entry is already
    # finalised on disk but still present in ``state``. Pre-fix we
    # finalised it a SECOND time, producing a duplicate CSV row (and a
    # second tally in the daily ``ausfaelle_*.csv``). The
    # ``_observe_legs`` re-emission guard at the read path only protects
    # against API-side re-emissions; it has no effect on entries already
    # loaded from the un-updated ``pending_trips.json`` after a crash
    # between the two ``atomic_write`` calls. Skipping here is the
    # complete fix and matches the spirit of the docstring's "exactly
    # once per trip" contract.
    finalize_keys = [
        key
        for key, trip in state.items()
        if (
            trip.direction == direction
            and trip.scheduled <= now
            and (recently_finalised is None or key not in recently_finalised)
        )
    ]
    finalize_keys.sort(key=lambda k: state[k].scheduled)
    finalised: list[_PendingTrip] = []
    for key in finalize_keys:
        finalised.append(state[key])
        del state[key]
        if recently_finalised is not None:
            recently_finalised[key] = now
    return finalised


# ---- Cache field-preservation validators ---------------------------------

# XML 1.0 control characters that have no readability value in a preserved
# cache field. Mirrors ``src/build_feed.py:_CONTROL_RE`` so the per-field
# shape validators reject the same control-character set the canonical
# 2026-05-09: the events.json cache has been retired. Feed events are
# now derived from the CSV ledger by :mod:`src.feed.stammstrecke`, so
# this script no longer carries any first-seen / identity / event
# building logic. The previous helpers
# (``_is_valid_preserved_*``, ``_read_existing_first_seen``,
# ``_resolve_first_seen``, ``_build_event``, ``_write_cache``) plus
# the ``OUTPUT_PATH`` constant were removed in the same pass.

# Hard cap on the ``errorCode`` string length. Real VAO codes are
# ~4-8 chars (``H890``, ``H892``, ``H730``, ``SVC_LOC_INVALID``); the
# 64-char ceiling is generous headroom while preventing a planted
# upstream from poisoning a structured log record with a multi-KiB
# blob via the otherwise-trusted error-body extraction path.
_ERROR_CODE_MAX_LEN: Final = 64

# Hard cap on the JSON body we will load into memory before scanning
# for ``errorCode``. The legitimate VAO error envelope is <1 KiB; 16
# KiB is ~16x headroom for any plausible future evolution while still
# preventing a planted upstream from amplifying the diagnostic-logging
# branch into a memory-pressure DoS. ``response.content`` would
# otherwise materialise the entire (potentially huge) error body.
_ERROR_BODY_MAX_BYTES: Final = 16 * 1024

# Strict character whitelist for the rendered ``errorCode``. Real VAO
# codes are ``[A-Za-z][A-Za-z0-9_]*`` shape (``H890``, ``H892``,
# ``H730``, ``SVC_LOC_INVALID``, ``API_GEN``…). The 2026-05-09 cron
# run revealed that the upstream sometimes echoes the supplied
# ``accessId`` into the ``errorCode`` field on bad-request responses
# (``HTTP 400`` with the accessId verbatim in the body — GitHub
# Actions then masked the log line as ``errorCode=***``, defeating
# the purpose of the diagnostic). The strict regex bails to
# ``"<malformed>"`` whenever the upstream value is not a canonical
# short code, so the diagnostic CANNOT inadvertently surface
# user-supplied or upstream-echoed secrets even when GitHub Actions'
# secret-masker is unavailable.
_VAO_ERROR_CODE_RE: Final = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,31}$")

# Hard cap on the rendered body-keys diagnostic. Real VAO error
# envelopes carry ~3-5 top-level keys (``errorCode``, ``errorText``,
# ``Message``, ``serverVersion``, ``planRtTs``); 256 chars is generous
# headroom while bounding the worst case where a planted upstream
# returns thousands of single-letter top-level keys.
_BODY_KEYS_MAX_LEN: Final = 256


# Sentinel strings for the diagnostic logging branch.
#
# Why brackets-and-uppercase-words:
# The 2026-05-09 cron run revealed that the project's ``SafeFormatter``
# (or one of its delegated maskers) treats lowercase alphanumeric
# tokens like ``unknown`` as "potential secret"-shaped and replaces
# them with ``***``. The original ``<unknown>`` sentinel therefore
# became ``***`` in the live log line, defeating the purpose of the
# diagnostic. Switching to ``[BRACKET_TAG]``-shape tokens (uppercase,
# underscores, hard delimiters) keeps the sentinels safely outside
# any token-shape heuristic the masker applies.
_DIAG_NO_RESPONSE: Final = "[NO_RESPONSE]"
_DIAG_MISSING: Final = "[MISSING]"
_DIAG_BAD_SHAPE: Final = "[BAD_SHAPE]"
_DIAG_EMPTY_BODY: Final = "[EMPTY_BODY]"
_DIAG_NO_KEYS: Final = "[NO_KEYS]"
_DIAG_REDACTED_KEY: Final = "[REDACTED]"


def _extract_http_status(exc: requests.HTTPError) -> str:
    """Return the HTTP status code from *exc* as a stringy diagnostic.

    Falls back to :data:`_DIAG_NO_RESPONSE` when the exception carries
    no response object (network-level failure, redirect-loop break,
    etc.).
    """
    response = getattr(exc, "response", None)
    if response is None:
        return _DIAG_NO_RESPONSE
    status = getattr(response, "status_code", None)
    if not isinstance(status, int):
        return _DIAG_MISSING
    return str(status)


def _extract_response_header(exc: requests.HTTPError, name: str) -> str:
    """Return the value of the *name* header from *exc.response*, or a sentinel.

    The headers we surface (``Content-Type``, ``Content-Length``,
    ``Server``, ``WWW-Authenticate``) are server-set and do NOT carry
    user-controlled content — logging them is leak-free. The value is
    truncated at :data:`_BODY_KEYS_MAX_LEN` to bound a planted-huge-
    header poisoning shape (an upstream that returns a multi-KiB
    ``Server`` header would otherwise pollute the structured log
    record).
    """
    response = getattr(exc, "response", None)
    if response is None:
        return _DIAG_NO_RESPONSE
    headers = getattr(response, "headers", None)
    if headers is None:
        return _DIAG_MISSING
    value = headers.get(name)
    if not isinstance(value, str):
        return _DIAG_MISSING
    stripped = value.strip()
    if not stripped:
        return _DIAG_MISSING
    if len(stripped) > _BODY_KEYS_MAX_LEN:
        return stripped[:_BODY_KEYS_MAX_LEN] + "…"
    return stripped


def _decode_error_body(exc: requests.HTTPError) -> Mapping[str, Any] | None:
    """Decode the response body as a top-level JSON mapping, or return ``None``.

    Bounded by :data:`_ERROR_BODY_MAX_BYTES` to prevent a planted
    upstream from amplifying the diagnostic-logging branch into a
    memory-pressure DoS.
    """
    response = getattr(exc, "response", None)
    if response is None:
        return None
    raw = getattr(response, "content", None)
    if not isinstance(raw, (bytes, bytearray)):
        return None
    if len(raw) == 0 or len(raw) > _ERROR_BODY_MAX_BYTES:
        return None
    try:
        # Security: ``loads_finite`` pins parse_constant + parse_float
        # hooks (Round 1503 sibling). VAO error envelopes carry an
        # ``errorCode`` field that the caller strict-matches against a
        # short alphabet, but the broader envelope can carry additional
        # numeric fields under a future schema — pin the canonical
        # defence here too so a hostile error response does not seed
        # non-finite floats into the diagnostic-logging surface.
        body = loads_finite(raw)
    except (ValueError, RecursionError, UnicodeDecodeError):
        return None
    if not isinstance(body, Mapping):
        return None
    return body


def _extract_vao_error_code(exc: requests.HTTPError) -> str:
    """Return the ``errorCode`` from a VAO error-envelope JSON body.

    Strict-match against :data:`_VAO_ERROR_CODE_RE` (canonical short
    HAFAS code shape) — a value that does not match (e.g. a free-form
    error message that happens to echo upstream-controlled content,
    including the supplied ``accessId``) collapses to
    :data:`_DIAG_BAD_SHAPE` rather than risking a secret leak through
    the diagnostic line.

    Falls back to :data:`_DIAG_MISSING` for every other failure mode
    (no response, body too large, body not JSON, body not a mapping,
    errorCode field absent or non-stringy).
    """
    body = _decode_error_body(exc)
    if body is None:
        return _DIAG_MISSING
    raw_code = body.get("errorCode") or body.get("error")
    if not isinstance(raw_code, str):
        return _DIAG_MISSING
    code = raw_code.strip()
    if not code:
        return _DIAG_MISSING
    if not _VAO_ERROR_CODE_RE.match(code):
        # Defensive bail: the upstream value is NOT a canonical short
        # code — most plausibly a verbose error message whose contents
        # cannot be safely surfaced. The body-keys diagnostic from
        # :func:`_describe_error_body_keys` will still expose enough
        # structural shape information to triage the failure.
        return _DIAG_BAD_SHAPE
    return code[:_ERROR_CODE_MAX_LEN]


def _describe_error_body_keys(exc: requests.HTTPError) -> str:
    """Return a comma-separated list of top-level keys from the JSON body.

    The keys are alphabetised so the diagnostic is stable across
    runs, capped at :data:`_BODY_KEYS_MAX_LEN` chars to bound a
    planted-huge-key-set body, and filtered through
    :func:`_VAO_ERROR_CODE_RE` so a top-level key whose name itself
    smuggles upstream-controlled content (an unusual but possible
    shape) does not slip into the log line. Keys that do NOT match
    the canonical HAFAS field-name pattern are rendered as
    :data:`_DIAG_REDACTED_KEY` so the diagnostic still indicates
    "the body has N fields" without exposing what they are.

    Falls back to :data:`_DIAG_EMPTY_BODY` for non-JSON / non-mapping
    / oversize payloads, mirroring :func:`_extract_vao_error_code`.
    """
    body = _decode_error_body(exc)
    if body is None:
        return _DIAG_EMPTY_BODY
    keys: list[str] = []
    # ``Mapping[str, Any]`` from ``_decode_error_body`` guarantees the
    # iteration yields ``str`` keys; we only need to gate against the
    # canonical-shape regex.
    for key in body.keys():
        stripped = key.strip()
        if _VAO_ERROR_CODE_RE.match(stripped):
            keys.append(stripped)
        else:
            keys.append(_DIAG_REDACTED_KEY)
    keys.sort()
    rendered = ",".join(keys) or _DIAG_NO_KEYS
    if len(rendered) > _BODY_KEYS_MAX_LEN:
        return rendered[:_BODY_KEYS_MAX_LEN] + "…"
    return rendered


# Hard cap on the rendered ``errorText`` diagnostic. Real VAO error
# texts are <300 chars (1-2 sentences); 256 char ceiling is generous
# headroom while bounding a planted-huge-text amplification shape.
_ERROR_TEXT_MAX_LEN: Final = 256


def _extract_vao_error_code_length(exc: requests.HTTPError) -> str:
    """Return the length of the ``errorCode`` field as a stringy diagnostic.

    The 2026-05-09 cron run revealed that VAO occasionally puts an
    accessId-shaped value into ``errorCode``; the strict regex in
    :func:`_extract_vao_error_code` accepts it (alphanumeric, within
    the 32-char ceiling) and the project's SafeFormatter then masks
    the literal value as ``***`` — defeating the diagnostic.

    Logging the LENGTH is a leak-free signal: a 4-8 char value is a
    canonical short code (``H890``, ``API_GEN``); a 16+ char value is
    almost certainly an accessId-shaped token. The operator can then
    distinguish "VAO returned its own short code" vs "VAO is echoing
    our token back" without seeing the value itself.

    Falls back to :data:`_DIAG_EMPTY_BODY` / :data:`_DIAG_MISSING`
    for the same reasons as :func:`_extract_vao_error_code`.
    """
    body = _decode_error_body(exc)
    if body is None:
        return _DIAG_EMPTY_BODY
    raw_code = body.get("errorCode") or body.get("error")
    if not isinstance(raw_code, str):
        return _DIAG_MISSING
    return str(len(raw_code))


def _extract_vao_error_text(exc: requests.HTTPError) -> str:
    """Return the ``errorText`` field, normalised and truncated.

    Unlike :func:`_extract_vao_error_code`, the errorText field is
    intended to be human-readable diagnostic prose. A typical value is
    e.g. ``"Invalid origin location"``, ``"Authentication failed"``,
    ``"No journey found between A and B"``. When VAO echoes a
    parameter value into the message, the project's SafeFormatter
    + GitHub Actions secret-masker replace the secret with ``***``
    while preserving the surrounding text — so an operator sees
    ``"Invalid accessId: ***"`` rather than ``"Invalid accessId:
    <ACCESSID>"``.

    Control bytes / line-terminators / whitespace runs are
    normalised via the project's :func:`sanitize_log_arg` (applied
    at the call site) on top of the length cap here.

    Falls back to :data:`_DIAG_EMPTY_BODY` / :data:`_DIAG_MISSING`
    for the same reasons as :func:`_extract_vao_error_code`.
    """
    body = _decode_error_body(exc)
    if body is None:
        return _DIAG_EMPTY_BODY
    raw_text = body.get("errorText") or body.get("errorMsg")
    if not isinstance(raw_text, str):
        return _DIAG_MISSING
    text = raw_text.strip()
    if not text:
        return _DIAG_MISSING
    if len(text) > _ERROR_TEXT_MAX_LEN:
        return text[:_ERROR_TEXT_MAX_LEN] + "…"
    return text


def _extract_vao_internal_error_text(exc: requests.HTTPError) -> str:
    """Return the ``internalErrorText`` / ``internalErrorTextOut`` field.

    The 2026-05-09 cron run revealed that VAO's error envelope
    includes a richer ``internalError*`` family of fields that
    typically explains the "what went wrong" at the validator level
    (e.g. ``"Stop ID 'extId::490033400' could not be resolved"``).
    These fields are server-set diagnostic prose, mirroring the
    ``errorText`` shape — same masker behaviour, same length cap.

    Falls back to :data:`_DIAG_EMPTY_BODY` / :data:`_DIAG_MISSING`
    for the same reasons as :func:`_extract_vao_error_text`.
    """
    body = _decode_error_body(exc)
    if body is None:
        return _DIAG_EMPTY_BODY
    raw_text = (
        body.get("internalErrorText")
        or body.get("internalErrorTextOut")
    )
    if not isinstance(raw_text, str):
        return _DIAG_MISSING
    text = raw_text.strip()
    if not text:
        return _DIAG_MISSING
    if len(text) > _ERROR_TEXT_MAX_LEN:
        return text[:_ERROR_TEXT_MAX_LEN] + "…"
    return text


def _extract_vao_internal_error_code(exc: requests.HTTPError) -> str:
    """Return the ``internalErrorCode`` field's length-only fingerprint.

    VAO's ``internalErrorCode`` is a structured short-code (matches
    the same canonical regex as ``errorCode``) but the SafeFormatter
    might mask it for the same false-positive reason. Render the
    LENGTH (zero-leak) so the operator can correlate it with VAO
    documentation tables.
    """
    body = _decode_error_body(exc)
    if body is None:
        return _DIAG_EMPTY_BODY
    raw_code = body.get("internalErrorCode")
    if not isinstance(raw_code, str):
        return _DIAG_MISSING
    code = raw_code.strip()
    if not code:
        return _DIAG_MISSING
    if not _VAO_ERROR_CODE_RE.match(code):
        return _DIAG_BAD_SHAPE
    return code[:_ERROR_CODE_MAX_LEN]


def _extract_vao_request_id(exc: requests.HTTPError) -> str:
    """Return the VAO ``requestId`` from the error body, or a sentinel.

    VAO assigns each request a server-side trace ID (typically a UUID
    or a short hex token) that VAO support can use to look up the
    failure on their end. The value is server-set and does not echo
    user-controlled content — safe to log fully (with the same
    canonical-shape regex guard as :func:`_extract_vao_error_code` to
    defend against an upstream that ever ships free-form text in this
    field).
    """
    body = _decode_error_body(exc)
    if body is None:
        return _DIAG_EMPTY_BODY
    raw_id = body.get("requestId") or body.get("request_id") or body.get("id")
    if not isinstance(raw_id, str):
        return _DIAG_MISSING
    rid = raw_id.strip()
    if not rid:
        return _DIAG_MISSING
    # Allow hex / UUID-like shapes too (digits, hyphens, underscores).
    if not re.match(r"^[A-Za-z0-9_\-]{1,64}$", rid):
        return _DIAG_BAD_SHAPE
    return rid


def _process_direction(
    session: requests.Session,
    direction: _Direction,
    state: dict[str, _PendingTrip],
    *,
    when: datetime,
    recently_finalised: Mapping[str, datetime] | None = None,
) -> str:
    """Query ``direction`` once and observe its legs into *state*.

    Returns one of:

    * ``"ok"`` — query succeeded and zero-or-more legs were observed
      into the pending-trip ledger (the CSV row is *not* written
      here — :func:`main` does that once finalisation runs across
      all directions);
    * ``"error"`` — VAO/parse raised an exception (already logged);
    * ``"quota_exceeded"`` — the daily quota cap hit before the call;
      caller treats the same as ``"error"`` for self-healing purposes.

    The ``CircuitBreakerOpen`` case is *not* handled here — the caller
    catches it so it can break out of the per-direction loop instead
    of consuming further breaker-protected slots.

    2026-05-11: split CSV-write off into :func:`main` so the cron
    tick can observe both directions before deciding which rows to
    finalise. The latest-observation-wins ledger (see
    :data:`PENDING_TRIPS_PATH`) records every seen leg by
    ``(direction, line_name, scheduled)`` so a train re-observed
    across multiple cron ticks contributes exactly one CSV row —
    fixing the lookahead-overlap double-counting in which the same
    physical train inflated the persisted mean of every tick that
    saw it.
    """

    LOGGER.info(
        "Stammstrecke: Abfrage Richtung %s (%s → %s) um %s.",
        direction.target_label,
        direction.origin_id,
        direction.destination_id,
        when.isoformat(),
    )
    try:
        # Charge the VAO daily quota BEFORE consulting the breaker: a quota
        # breach is a LOCAL budget signal, not an upstream-health failure, so
        # it must not count toward the breaker's failure threshold (which would
        # otherwise OPEN the breaker for BREAKER_RECOVERY_TIMEOUT after ~10
        # quota-blocked ticks and skip the first ticks after the midnight reset).
        # ``_QuotaExceeded`` raised here is caught below.
        _charge_one_request(when)
        trips = _BREAKER.call(_query_trips, session, direction, when=when)
    except CircuitBreakerOpen:
        # Re-raise so main() can break out of the loop without re-trying
        # the next direction (the breaker would short-circuit it anyway).
        raise
    except _QuotaExceeded as exc:
        LOGGER.warning(
            "Stammstrecke: Tageslimit erreicht — Richtung %s übersprungen (%s).",
            direction.target_label,
            sanitize_log_arg(str(exc)),
        )
        return "quota_exceeded"
    except requests.HTTPError as exc:
        # Diagnostic-rich branch for non-2xx responses. The previous
        # PR (#1389) made the body readable, revealing the canonical
        # VAO error envelope shape ``{dialectVersion, errorCode,
        # errorText, requestId, serverVersion}``. The 2026-05-09 cron
        # then showed ``errorCode=***`` — VAO echoes the supplied
        # accessId into the ``errorCode`` field, which the project's
        # SafeFormatter / GitHub Actions secret-masker then redacts
        # entirely.
        #
        # This iteration adds three more leak-free diagnostics:
        # * ``code_len`` — the length of ``errorCode`` (4-8 chars =
        #   canonical short code; 16+ chars = accessId-shaped token);
        # * ``err_text`` — the human-readable ``errorText`` field
        #   with the secret-masking applied at the ``sanitize_log_arg``
        #   layer (preserves surrounding text like "Invalid origin
        #   location: ***");
        # * ``req_id`` — the VAO server-side trace ID for support
        #   triage.
        status_code = _extract_http_status(exc)
        error_code = _extract_vao_error_code(exc)
        code_len = _extract_vao_error_code_length(exc)
        error_text = _extract_vao_error_text(exc)
        internal_code = _extract_vao_internal_error_code(exc)
        internal_text = _extract_vao_internal_error_text(exc)
        request_id = _extract_vao_request_id(exc)
        body_keys = _describe_error_body_keys(exc)
        content_type = _extract_response_header(exc, "Content-Type")
        content_length = _extract_response_header(exc, "Content-Length")
        server = _extract_response_header(exc, "Server")
        www_auth = _extract_response_header(exc, "WWW-Authenticate")
        LOGGER.warning(
            "Stammstrecke: Abfrage Richtung %s fehlgeschlagen: HTTP %s "
            "(errorCode=%s, code_len=%s, err_text=%s, "
            "int_code=%s, int_text=%s, req_id=%s, "
            "body_keys=%s, ct=%s, cl=%s, server=%s, www_auth=%s).",
            direction.target_label,
            status_code,
            sanitize_log_arg(error_code),
            sanitize_log_arg(code_len),
            sanitize_log_arg(error_text),
            sanitize_log_arg(internal_code),
            sanitize_log_arg(internal_text),
            sanitize_log_arg(request_id),
            sanitize_log_arg(body_keys),
            sanitize_log_arg(content_type),
            sanitize_log_arg(content_length),
            sanitize_log_arg(server),
            sanitize_log_arg(www_auth),
        )
        return "error"
    except Exception as exc:
        # Security: never log the full exception via ``%s`` / ``exc_info``
        # — ``VorAuth`` injected the ``accessId`` into the prepared
        # request URL, and ``RequestException`` may carry that URL in
        # its message. Logging the class name only suppresses the leak
        # while preserving the failure-mode diagnostic.
        LOGGER.warning(
            "Stammstrecke: Abfrage Richtung %s fehlgeschlagen: %s.",
            direction.target_label,
            type(exc).__name__,
        )
        return "error"

    observations = _collect_sbahn_leg_observations(trips)
    written = _observe_legs(
        state,
        observations,
        direction=direction.target_label,
        now=when,
        recently_finalised=recently_finalised,
    )
    LOGGER.info(
        "Stammstrecke: Richtung %s — %d S-Bahn-Legs aus %d Trips beobachtet "
        "(Ledger-Updates: %d, Anti-Doppelzähl-Skips: %d).",
        direction.target_label,
        len(observations),
        len(trips),
        written,
        len(observations) - written,
    )
    return "ok"


def main() -> int:
    """Entry point. Returns ``0`` on success (incl. partial), ``1`` on full failure.

    The script never raises an unhandled exception out of ``main`` — the
    cron pipeline relies on a clean exit so other cache updates run on
    schedule even when this provider is degraded.

    The script writes only to the CSV ledger
    ``data/stats/stammstrecke_<YYYY>.csv``; the feed builder reads from
    that ledger directly (see :mod:`src.feed.stammstrecke`). A
    CircuitBreakerOpen short-circuit or all-directions-failed run
    appends nothing — the feed naturally degrades to "no Stammstrecke
    entry" because the most-recent observations roll out of the
    1-hour feed window without replacement.
    """

    configure_logging()

    when = _now_vienna()
    successes = 0
    errors = 0

    # OS-level lock around the entire load-modify-save cycle: cron
    # ticks are concurrency-grouped, but ``workflow_dispatch`` /
    # manual triggers can bypass that gate. Without the lock, two
    # concurrent runs would each load the same baseline ledger and
    # the slower writer's observations would be silently dropped at
    # save time.
    with _ledger_lock(PENDING_TRIPS_LOCK_PATH):
        # Pending-trip ledger: latest-observation-wins dedup across
        # cron ticks. Load up front, observe both directions, then
        # finalise departed trains. Load failures / corruption start
        # the script with an empty ledger (logged at WARNING by the
        # loader) so a one-off bad state file never blocks the cron.
        state = _load_pending_trips(PENDING_TRIPS_PATH)
        recently_finalised = _load_recently_finalised(RECENTLY_FINALISED_PATH)

        cutoff = when - PENDING_TTL
        purged_pending = _purge_stale_entries(state, cutoff=cutoff)
        purged_finalised = _purge_finalised_entries(
            recently_finalised, cutoff=cutoff
        )
        if purged_pending or purged_finalised:
            LOGGER.info(
                "Stammstrecke: %d veraltete Pending-Einträge, %d veraltete "
                "Finalisiert-Einträge entfernt (TTL %s).",
                purged_pending,
                purged_finalised,
                PENDING_TTL,
            )

        with ExitStack() as stack:
            try:
                session = _build_session(stack)
            except Exception as exc:  # pragma: no cover - defensive
                LOGGER.error(
                    "Stammstrecke: VOR-Session konnte nicht erstellt werden: %s.",
                    type(exc).__name__,
                )
                return 1

            for direction in DIRECTIONS:
                try:
                    status = _process_direction(
                        session,
                        direction,
                        state,
                        when=when,
                        recently_finalised=recently_finalised,
                    )
                except CircuitBreakerOpen:
                    LOGGER.warning(
                        "Stammstrecke: Circuit breaker offen (%d "
                        "aufeinanderfolgende Fehler) — weitere "
                        "Richtungen werden übersprungen.",
                        _BREAKER.consecutive_failures,
                    )
                    break

                if status in ("error", "quota_exceeded"):
                    errors += 1
                else:
                    successes += 1

        # Finalisation pass: every train whose scheduled departure
        # is in the past (relative to this tick) is committed to the
        # CSV with its latest observed delay and removed from the
        # pending ledger; its identity key flows into the
        # recently-finalised ledger so any VAO re-emission at the
        # lookahead boundary cannot produce a second CSV row.
        #
        # Trains are grouped by their scheduled year so a cron tick
        # straddling the New-Year boundary (e.g. tick at 00:05 on
        # Jan 1 finalising a train scheduled at 23:55 on Dec 31)
        # writes the row into the calendar year of the actual
        # departure — preventing the year-wide dashboard counter
        # from quietly losing the observation.
        csv_rows_written = 0
        ausfaelle_rows_written = 0
        for direction in DIRECTIONS:
            finalised = _finalize_departed(
                state,
                direction=direction.target_label,
                now=when,
                recently_finalised=recently_finalised,
            )
            if not finalised:
                continue
            # Durably record the suppression set BEFORE writing any CSV row for
            # this direction. ``_finalize_departed`` has already popped these
            # trips from ``state`` (in memory) and registered their keys in
            # ``recently_finalised``; persisting it now closes the crash window
            # between a CSV append below and the post-loop ledger save. On a
            # crash in that window the still-on-disk pending entry is re-observed
            # next tick but skipped by the now-durable ``recently_finalised``
            # guard instead of being double-finalised (a duplicate row would
            # inflate the mean delay). Trade-off: a crash here means a missing
            # row (under-count), which is preferable to a duplicate.
            _save_recently_finalised(RECENTLY_FINALISED_PATH, recently_finalised)
            # Split finalised trains into cancellations (each gets one
            # ``ausfaelle_<YYYY>.csv`` row keyed on the train's
            # scheduled departure) and regular delay observations (one
            # per-direction-per-year aggregate row with the mean delay).
            # The split is performed AFTER ``_finalize_departed`` so
            # cancelled trains still pass through the recently-finalised
            # dedup guard — a VAO re-emission of a cancelled train at
            # the lookahead boundary cannot double-count it.
            cancelled_trips = [t for t in finalised if t.cancelled]
            observed_trips = [t for t in finalised if not t.cancelled]
            for trip in cancelled_trips:
                LOGGER.info(
                    "Stammstrecke: Richtung %s — Zug %s (geplant %s) "
                    "als Ausfall finalisiert.",
                    direction.target_label,
                    sanitize_log_arg(trip.name),
                    sanitize_log_arg(trip.scheduled.isoformat()),
                )
                append_ausfall_row(
                    timestamp=trip.scheduled,
                    direction=direction.target_label,
                    line=trip.name,
                )
                ausfaelle_rows_written += 1
            if not observed_trips:
                continue
            by_year: dict[int, list[_PendingTrip]] = {}
            for trip in observed_trips:
                by_year.setdefault(trip.scheduled.year, []).append(trip)
            for year in sorted(by_year):
                year_trips = by_year[year]
                mean_minutes = float(
                    statistics.mean(t.latest_delay_minutes for t in year_trips)
                )
                # Use the latest scheduled time in the year-group as
                # the row timestamp — anchors the row to the actual
                # departure window rather than the (potentially
                # next-year) cron wall clock.
                row_timestamp = max(t.scheduled for t in year_trips)
                LOGGER.info(
                    "Stammstrecke: Richtung %s, Jahr %d — %d Zug/Züge "
                    "finalisiert, ⌀ %.2f Minuten (Schwelle %d).",
                    direction.target_label,
                    year,
                    len(year_trips),
                    mean_minutes,
                    DELAY_THRESHOLD_MINUTES,
                )
                append_stammstrecke_row(
                    timestamp=row_timestamp,
                    direction=direction.target_label,
                    delay_minutes=mean_minutes,
                )
                csv_rows_written += 1

        # Persist both ledgers AFTER finalisation. Save recently_finalised
        # FIRST so that, on a crash between the two writes, the
        # suppression set is already durable — the pending entry will
        # be re-observed on the next tick but skipped by the
        # recently_finalised guard instead of being double-finalised.
        _save_recently_finalised(RECENTLY_FINALISED_PATH, recently_finalised)
        _save_pending_trips(PENDING_TRIPS_PATH, state)

    LOGGER.info(
        "Stammstrecke: %d Beobachtung(en), %d Delay-CSV-Zeile(n) + "
        "%d Ausfall-CSV-Zeile(n) geschrieben (Erfolg=%d, Fehler=%d, "
        "Pending=%d offen, Finalisiert=%d).",
        successes,
        csv_rows_written,
        ausfaelle_rows_written,
        successes,
        errors,
        len(state),
        len(recently_finalised),
    )

    # Exit 1 only if every direction failed AND at least one was attempted —
    # a CircuitBreakerOpen-only run (errors=0, successes=0) is intentional
    # short-circuiting and exits 0.
    if successes == 0 and errors > 0:
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
