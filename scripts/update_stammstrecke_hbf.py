#!/usr/bin/env python3
"""Stammstrecke delay monitor via VAO ``/departureBoard`` at Wien Hauptbahnhof.

Architectural successor to ``scripts/update_stammstrecke_status.py``,
written 2026-05-15 to lift the ``numF=6`` capture ceiling that the
``/trip`` endpoint imposes.

Motivation
----------

The pre-2026-05-15 pipeline queried ``/trip`` from Floridsdorf → Meidling
and Meidling → Floridsdorf separately. ``numF`` is contractually capped
at 6 (``docs/reference/trip.md:34``). Empirical analysis of
``cache/stammstrecke/recently_finalised.json`` showed that the response
consistently returned 6 trains spanning ~21 of 30 minutes, with the last
9 minutes of every 30-min window falling outside both the current
tick's and the next tick's coverage windows. At Vienna S-Bahn peak
density (~3.5-min spacing) that 9-min gap routinely contains 2-3
trains that were never observed — bypassed by the cap.

``/departureBoard`` has only a SOFT ``maxJourneys`` limit (``docs/
reference/departureboard.md:22``); we omit the parameter entirely so
VAO returns every departure in the configured duration window. Querying
at Wien Hauptbahnhof — geographically central on the Stammstrecke —
catches every train that passes through the corridor, including the
ones that originate / terminate at intermediate stations and never
appeared in the Floridsdorf-to-Meidling ``/trip`` view.

Quota
-----

The poll is ONE ``/departureBoard`` call per cron tick. At 48
cron ticks/day that's 48 VAO requests/day — half of the
contractual ``MAX_REQUESTS_PER_DAY = 100`` Start-tier limit. The
~50% saving over the two-direction ``/trip`` polls gives manual
``workflow_dispatch`` operators a substantial extra budget for
out-of-band runs.

Direction labelling
-------------------

Each departure's ``direction`` field (the train's terminus as displayed
on the station board) is classified into the same ``"Meidling"`` /
``"Floridsdorf"`` direction labels that the pre-2026-05-15 ``/trip``
pipeline emitted, so:

* ``data/stats/stammstrecke_<YYYY>.csv`` rows keep the exact same
  schema and direction-column values — the README dashboard, feed
  event renderer (``src/feed/stammstrecke.py``) and any external
  analysis continue to work byte-for-byte.
* The pending-trip ledger (``cache/stammstrecke/pending_trips.json``)
  and the recently-finalised companion ledger remain compatible.

Two-layer classification: substring match against well-known south/north
landmark names first (fast path), then exact-terminus whitelist
fallback. Unrecognised terminuses are dropped with a deduplicated INFO
log so operators can extend the whitelist when a new line appears.

Latest-wins re-observation
--------------------------

A train scheduled 40 minutes in the future is observed at this tick
with whatever rtTime VAO can forecast at the moment. The NEXT tick
re-observes the same train (still ~10 minutes in the future) — the
``_observe_legs`` ledger overwrites the older entry with the newer
delay reading, so the value that eventually flows into the CSV is the
closest-to-departure (therefore most accurate) one. The pending-trip
ledger only finalises a train when its scheduled time has passed
(``scheduled <= now``); long-horizon observations contribute to the
CSV row of the cron tick that catches the actual departure, never the
one that first saw the future train.

Reuse
-----

This script imports the shared infrastructure (pending state, finalise,
ledger I/O, lock, quota charge, HTTP session) from
``scripts/update_stammstrecke_status.py`` rather than duplicating it.
The legacy script remains importable so its tests stay green during the
transition; it is no longer wired into the cron workflow.
"""

from __future__ import annotations

import json as _json_lib
import logging
import re
import statistics
import sys
from collections import defaultdict
from collections.abc import Iterable, Iterator, Mapping
from contextlib import ExitStack
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
from src.utils.http import request_safe  # noqa: E402
from src.utils.logging import sanitize_log_arg  # noqa: E402
from src.utils.stats import append_stammstrecke_row  # noqa: E402

# Reuse pending-state + ledger infrastructure from the legacy /trip-
# based monitor. The private prefix is intentional in that script;
# importing across scripts is the transitional shape until the legacy
# script is removed in a follow-up PR.
from scripts.update_stammstrecke_status import (  # noqa: E402
    BREAKER_FAILURE_THRESHOLD,
    BREAKER_RECOVERY_TIMEOUT,
    DELAY_THRESHOLD_MINUTES,
    MAX_QUERY_TIMEOUT,
    PENDING_TRIPS_LOCK_PATH,
    PENDING_TRIPS_PATH,
    PENDING_TTL,
    QUERY_TIMEOUT,
    RECENTLY_FINALISED_PATH,
    VIENNA_TZ,
    _QuotaExceeded,
    _build_session,
    _canonical_line_name,
    _charge_one_request,
    _finalize_departed,
    _ledger_lock,
    _load_pending_trips,
    _load_recently_finalised,
    _now_vienna,
    _observe_legs,
    _parse_vao_dt,
    _purge_finalised_entries,
    _purge_stale_entries,
    _S_BAHN_LINE_RE,
    _SbahnLegObservation,
    _save_pending_trips,
    _save_recently_finalised,
)

LOGGER = logging.getLogger("update_stammstrecke_hbf")


# ---- Operating parameters --------------------------------------------------

# VOR/VAO station ID for Wien Hauptbahnhof (HBF). Pinned here to avoid a
# station-directory drift quietly changing which stop we poll. Sourced
# from ``data/stations.json``: the canonical entry for ``Wien
# Hauptbahnhof`` carries ``vor_id = "490134900"``.
HAUPTBAHNHOF_VOR_ID: Final = "490134900"

# /departureBoard duration window, in minutes.
#
# Sized at the 30-min cron interval plus a 15-min safety overlap so every
# train scheduled in the poll window appears in BOTH this tick and the
# next one — the pending-trip ledger then keeps the latest (closer-to-
# departure, therefore more accurate) realtime reading via the
# latest-wins overwrite in :func:`_observe_legs`.
#
# Why not 60 min: VAO returns ALL departures in the requested window, so
# a longer window inflates JSON payload + parse cost + in-memory state
# without giving us a more accurate observation (the train was already
# covered in the prior window). 45 min is the 50%-overlap sweet spot.
DEPARTURE_BOARD_DURATION_MIN: Final = 45

# Geographic-direction → CSV-label mapping. Pinned constants so a future
# rename here cannot silently drift the README dashboard column values
# (which are pinned to "Meidling" / "Floridsdorf" since the 2026-05-09
# Stammstrecke migration).
DIRECTION_LABEL_SOUTHBOUND: Final = "Meidling"
DIRECTION_LABEL_NORTHBOUND: Final = "Floridsdorf"

DIRECTION_LABELS: Final[tuple[str, ...]] = (
    DIRECTION_LABEL_SOUTHBOUND,
    DIRECTION_LABEL_NORTHBOUND,
)


# ---- Direction classification at Wien Hauptbahnhof ------------------------
#
# A Stammstrecke train's geographic direction is determined by its
# terminus (the ``direction`` field in the ``/departureBoard`` response).
# The Stammstrecke runs north↔south through Vienna:
#
#   Wien Floridsdorf — Praterstern — Wien Mitte — Rennweg — Quartier
#   Belvedere — Wien Hauptbahnhof — Wien Meidling
#
# Trains heading SOUTH from Hbf next stop at Wien Meidling (and continue
# further south to Mödling, Wiener Neustadt, Graz, etc.) → labelled
# ``"Meidling"`` for CSV-column compatibility with the pre-2026-05-15
# ``/trip``-based pipeline.
#
# Trains heading NORTH from Hbf reach Wien Mitte after two intermediate
# stops (QB → Rennweg) and continue to Praterstern → Floridsdorf and
# beyond to Stockerau, Hollabrunn, Břeclav, etc. → labelled
# ``"Floridsdorf"``.

# Substring fragments that unambiguously indicate the geographic direction
# *from Wien Hauptbahnhof*. Strings are lowercased for case-insensitive
# matching against ``direction.lower()``.
#
# Hauptbahnhof itself is excluded — a train terminating AT Hbf does not
# cross the Stammstrecke any further and is irrelevant to the corridor
# signal.
HBF_SOUTHBOUND_SUBSTRINGS: Final[tuple[str, ...]] = (
    "meidling",       # next Stammstrecke stop southbound
    "mödling",        # next major node after Meidling (S1, S2, S3 terminus)
    "moedling",       # ASCII variant
    "wiener neustadt",  # S1, REX terminus
    "payerbach",      # REX southbound terminus
    "semmering",      # REX southbound terminus
    "mürzzuschlag",   # REX southbound terminus
    "muerzzuschlag",  # ASCII variant
    "bruck/mur",      # REX terminus / waypoint
    "bruck an der mur",
    "graz",           # long-distance south
    "klagenfurt",     # long-distance south-west
    "villach",        # long-distance south-west
    "lienz",          # long-distance south-west
    "flughafen wien", # S7 southern arm via Stammstrecke
    "wien flughafen", # variant capitalisation
    "wolfsthal",      # S7 eastern terminus (after airport)
    "baden",          # CAT / S-Bahn southern terminus
    "pottendorf",     # S2 alternate southern terminus
    "wampersdorf",    # S2 alternate southern terminus
)

# Substring fragments for the *northbound* geographic direction at Hbf.
HBF_NORTHBOUND_SUBSTRINGS: Final[tuple[str, ...]] = (
    "floridsdorf",    # eventual Stammstrecke northern terminus
    "praterstern",    # intermediate Stammstrecke stop, terminus for some short-runs
    "stockerau",      # S2 / R-train terminus northwest
    "hollabrunn",     # REX northern terminus
    "retz",           # REX northern terminus
    "břeclav",        # long-distance north (CZ)
    "breclav",        # ASCII variant
    "wolkersdorf",    # S2 / R-train terminus north
    "mistelbach",     # S2 northern terminus
    "laa an der thaya",  # S2 northern terminus (long form)
    "laa/thaya",      # variant
    "gänserndorf",    # S1 / REX northern terminus
    "gaenserndorf",   # ASCII variant
    "marchegg",       # REX eastern terminus
    "bratislava",     # long-distance northeast (SK)
    # "Wien Mitte" intentionally NOT a substring — too short and would
    # match any train mentioning "Mitte" in a free-form direction string.
    # The exact-terminus whitelist below covers the rare Mitte-
    # terminating short-run.
)

# Exact-terminus whitelists for departures not covered by substring
# matching. Add new entries here when an unknown terminus appears
# repeatedly in operator logs and we can verify (via an external
# timetable lookup) that it is on the Stammstrecke axis.
HBF_SOUTHBOUND_TERMINI: Final[frozenset[str]] = frozenset({
    # Empty initially — extend from the
    # ``Unbekannter Endpunkt am Hbf`` log entries on first deployment.
})

HBF_NORTHBOUND_TERMINI: Final[frozenset[str]] = frozenset({
    "Wien Mitte",
    "Wien Mitte-Landstraße",
    "Wien Mitte Bahnhof",
})


# Cap on the rendered ``Unbekannter Endpunkt am Hbf`` diagnostic per
# run. Bounds a planted-upstream amplification where every departure
# carries a different unique direction string and the INFO log balloons.
_UNRECOGNISED_DIAG_MAX_LEN: Final = 80


# ---- Departure-board request + parse --------------------------------------


_BREAKER = CircuitBreaker(
    "stammstrecke-hbf-vor",
    failure_threshold=BREAKER_FAILURE_THRESHOLD,
    recovery_timeout=BREAKER_RECOVERY_TIMEOUT,
)


def configure_logging() -> None:
    """Install the project's :class:`SafeFormatter` for this script."""

    setup_script_logging(logging.INFO)
    # urllib3 emits one INFO line per VAO request which clutters the
    # workflow log without adding diagnostic value. Mirrors the existing
    # legacy-script pattern.
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def _query_departure_board(
    session: requests.Session,
    *,
    when: datetime,
    duration_min: int = DEPARTURE_BOARD_DURATION_MIN,
    timeout: int = QUERY_TIMEOUT,
) -> list[Mapping[str, Any]]:
    """Call ``/departureBoard`` once at Wien Hauptbahnhof.

    Returns the parsed ``Departure`` list (possibly empty). Validates
    HTTP status, JSON shape, and the response wrapper (the VAO
    serialiser can place the list at the top level under ``Departure``
    OR nest it under ``DepartureBoard.Departure``; both shapes are
    accepted).

    Charges the VAO daily quota counter exactly once via
    :func:`_charge_one_request` before sending. A
    :class:`_QuotaExceeded` raised here aborts the cron tick before any
    network I/O — operators see the budget breach in the workflow log
    without a half-completed request.
    """

    safe_timeout = max(1, min(timeout, MAX_QUERY_TIMEOUT))

    params: dict[str, str] = {
        "id": HAUPTBAHNHOF_VOR_ID,
        "date": when.strftime("%Y-%m-%d"),
        "time": when.strftime("%H:%M"),
        "duration": str(duration_min),
        # Filter on product classes Train (1) + S-Bahn (2). The
        # post-filter in :func:`_is_sbahn_line` narrows further to
        # S/R/REX line patterns, but the upstream filter saves us a
        # ton of long-distance (RJ, IC, EC, NJ) entries in the
        # response payload.
        "products": "3",
        # Enable server-side realtime data so ``rtTime`` is populated
        # when available.
        "rtMode": "SERVER_DEFAULT",
        # The ``maxJourneys`` parameter is INTENTIONALLY OMITTED — it
        # is a soft limit (``docs/reference/departureboard.md:22``)
        # and omitting it lets VAO return every departure in the
        # window, which is the point of the migration from the
        # hard-capped ``/trip`` endpoint.
    }

    endpoint = f"{vor_provider.VOR_BASE_URL}departureBoard"

    _charge_one_request(when)

    # Mirror the legacy script's response-handling pattern (read body
    # before raising on HTTP error) so the downstream diagnostic
    # helpers can inspect ``response.content`` even on 4xx/5xx.
    response = request_safe(
        session,
        endpoint,
        method="GET",
        raise_for_status=False,
        params=params,
        headers={"Accept": "application/json"},
        timeout=safe_timeout,
        allowed_content_types=("application/json",),
    )
    if response.status_code >= 400:
        raise requests.HTTPError(
            f"VAO /departureBoard returned HTTP {response.status_code}",
            response=response,
        )

    content = response.content

    try:
        payload = _json_lib.loads(content)
    except (ValueError, RecursionError) as exc:
        # Drift defence (JSON Depth-Bomb): a depth-bomb body passes the
        # size cap but blows the recursion limit on parse. Re-raise as
        # ``ValueError`` so the caller's error-isolation branch runs
        # without propagating the BaseException-rooted failure.
        raise ValueError(
            f"VAO /departureBoard returned unparseable JSON: {type(exc).__name__}"
        ) from exc

    if not isinstance(payload, dict):
        raise TypeError(
            f"VAO /departureBoard returned non-dict payload: "
            f"{type(payload).__name__}"
        )

    # Response shape variants seen in the wild (audit docs 2026-02):
    #   * ``{"Departure": [...]}`` — modern flat shape
    #   * ``{"DepartureBoard": {"Departure": [...]}}`` — nested shape
    # Accept either.
    raw_deps: Any
    if "DepartureBoard" in payload:
        board = payload["DepartureBoard"]
        if isinstance(board, Mapping):
            raw_deps = board.get("Departure")
        else:
            raw_deps = None
    else:
        raw_deps = payload.get("Departure")

    if raw_deps is None:
        return []
    if isinstance(raw_deps, Mapping):
        # Single-element responses are sometimes serialised as the bare
        # object rather than a one-element list.
        return [raw_deps]
    if not isinstance(raw_deps, list):
        raise TypeError(
            f"VAO /departureBoard Departure field has unexpected type: "
            f"{type(raw_deps).__name__}"
        )
    return [d for d in raw_deps if isinstance(d, Mapping)]


def classify_hbf_direction(direction_str: str) -> str | None:
    """Return ``"Meidling"`` (south), ``"Floridsdorf"`` (north) or ``None``.

    Two-layer match against the direction string (the train's terminus
    as serialised by VAO):

    1. Substring against :data:`HBF_SOUTHBOUND_SUBSTRINGS` /
       :data:`HBF_NORTHBOUND_SUBSTRINGS` — fast path, case-insensitive.
       Catches the typical "City Name [Bahnhof]" forms.
    2. Exact-string against :data:`HBF_SOUTHBOUND_TERMINI` /
       :data:`HBF_NORTHBOUND_TERMINI` — slower fallback for terminuses
       that contain none of the landmark substrings.

    ``None`` means the terminus is unrecognised; the caller logs and
    drops the departure.
    """

    norm = direction_str.strip()
    if not norm:
        return None
    lower = norm.lower()
    for needle in HBF_SOUTHBOUND_SUBSTRINGS:
        if needle in lower:
            return DIRECTION_LABEL_SOUTHBOUND
    for needle in HBF_NORTHBOUND_SUBSTRINGS:
        if needle in lower:
            return DIRECTION_LABEL_NORTHBOUND
    if norm in HBF_SOUTHBOUND_TERMINI:
        return DIRECTION_LABEL_SOUTHBOUND
    if norm in HBF_NORTHBOUND_TERMINI:
        return DIRECTION_LABEL_NORTHBOUND
    return None


def _is_sbahn_line(name: str) -> bool:
    """Return ``True`` if *name* matches the S/R/REX line-code pattern.

    Mirrors the legacy ``_is_sbahn_leg`` line-name check but operates
    on a bare string (the departure's ``name`` field) rather than a
    full leg object. The regex is the same project-wide
    :data:`_S_BAHN_LINE_RE` so any future widening propagates.
    """

    return bool(_S_BAHN_LINE_RE.match(name.strip()))


def _departure_line_name(dep: Mapping[str, Any]) -> str:
    """Extract the line designation from a ``/departureBoard`` entry.

    VAO emits the line in several spots depending on serialiser
    version:

    * Top-level ``name`` field (e.g., ``"S 1"`` or ``"REX 3"``);
    * Nested ``Product[].line`` or ``Product[].displayNumber`` or
      ``Product[].name``.

    Returns the first non-empty hit, or empty string when none of the
    fields are populated.
    """

    top = str(dep.get("name") or "").strip()
    if top:
        return top
    products = dep.get("Product")
    if isinstance(products, list):
        candidates = [p for p in products if isinstance(p, Mapping)]
    elif isinstance(products, Mapping):
        candidates = [products]
    else:
        candidates = []
    for product in candidates:
        for key in ("line", "displayNumber", "name"):
            v = str(product.get(key) or "").strip()
            if v:
                return v
    return ""


def _departure_delay_minutes(dep: Mapping[str, Any]) -> float | None:
    """Return the departure delay in fractional minutes, or ``None``.

    Same conservative-skip rules as the legacy
    ``_leg_departure_delay_minutes`` but reads from the DEPARTURE-level
    fields rather than from a nested ``Leg.Origin`` substructure:

    * Cancelled departures return ``None`` (cancelled trains are
      ``absent``, not ``delayed`` — counting them as delay-zero
      systematically biases the sample downward).
    * Missing ``rtTime`` returns ``None`` (no realtime signal — VAO
      omits ``rtTime`` both for on-time AND for genuinely-unknown
      trains, so coercing missing data to ``0.0`` would the
      88%-zeros bias documented in the legacy script's docstring).
    * Unparseable schedule or realtime fields return ``None``
      (malformed entries are dropped, not silently coerced).
    """

    cancelled = dep.get("cancelled")
    if cancelled is True:
        return None
    if isinstance(cancelled, str) and cancelled.strip().lower() == "true":
        return None

    sched_date = dep.get("date")
    sched_time = dep.get("time")
    scheduled = _parse_vao_dt(sched_date, sched_time)
    if scheduled is None:
        return None

    rt_time = dep.get("rtTime") or dep.get("rtDepTime")
    if not rt_time:
        return None

    rt_date = dep.get("rtDate") or dep.get("rtDepDate") or sched_date
    actual = _parse_vao_dt(rt_date, rt_time)
    if actual is None:
        return None

    return (actual - scheduled).total_seconds() / 60.0


@dataclass(frozen=True)
class _HbfDepartureObservation:
    """One filtered + direction-classified departure at Wien Hauptbahnhof.

    Field shapes mirror :class:`_SbahnLegObservation` so that the
    existing :func:`_observe_legs` consumer (which duck-types on
    ``.name`` / ``.scheduled`` / ``.delay_minutes``) accepts these
    instances unchanged. The extra ``direction`` attribute is read by
    the caller (which groups observations into per-direction lists
    before invoking ``_observe_legs``).
    """

    direction: str  # historical label: "Meidling" or "Floridsdorf"
    name: str       # canonicalised line designation (e.g., "S1", "REX3")
    scheduled: datetime
    delay_minutes: float


def _collect_hbf_observations(
    departures: Iterable[Mapping[str, Any]],
) -> tuple[dict[str, list[_SbahnLegObservation]], dict[str, int]]:
    """Filter + direction-classify a ``/departureBoard`` Departure list.

    Returns a 2-tuple:

    * ``{direction_label: [observations]}`` ready to feed to
      :func:`_observe_legs` per-direction. Keys are always present
      (empty list when no departures matched that direction).
    * ``{unrecognised_terminus: occurrence_count}`` — distinct terminus
      strings that failed direction classification. The caller logs the
      top-N entries at INFO so operators can extend the whitelists.

    Filters (any failure drops the entry):

    * Cancelled departures (no useful delay signal).
    * Lines that are not S / R / REX (non-Stammstrecke products).
    * Missing realtime signal (``rtTime`` absent) — treated as
      ``unknown``, same conservative rule as the legacy script.
    * Unrecognised terminus — direction cannot be classified.

    Compatible with :func:`_observe_legs` via the
    :class:`_SbahnLegObservation` shape (``name``, ``scheduled``,
    ``delay_minutes``); the ``direction`` field of the observation is
    implicit in which list it lives in.
    """

    by_direction: dict[str, list[_SbahnLegObservation]] = {
        label: [] for label in DIRECTION_LABELS
    }
    unrecognised: dict[str, int] = defaultdict(int)

    for dep in departures:
        if not isinstance(dep, Mapping):
            continue

        raw_line = _departure_line_name(dep)
        if not raw_line:
            continue
        if not _is_sbahn_line(raw_line):
            continue
        name = _canonical_line_name(raw_line)
        if not name:
            continue

        sched_date = dep.get("date")
        sched_time = dep.get("time")
        scheduled = _parse_vao_dt(sched_date, sched_time)
        if scheduled is None:
            continue

        delay = _departure_delay_minutes(dep)
        if delay is None:
            continue

        direction_str = str(dep.get("direction") or "").strip()
        direction = classify_hbf_direction(direction_str)
        if direction is None:
            unrecognised[direction_str] += 1
            continue

        observation = _SbahnLegObservation(
            name=name,
            scheduled=scheduled,
            delay_minutes=delay,
        )
        by_direction[direction].append(observation)

    return by_direction, dict(unrecognised)


def _log_unrecognised_terminuses(unrecognised: Mapping[str, int]) -> None:
    """Surface unrecognised termini at INFO with a per-terminus count.

    Operators read this log to extend
    :data:`HBF_SOUTHBOUND_SUBSTRINGS` / etc. when a new line appears.

    Bounded:
    * Sorted by count (descending) so the most-frequent terminus is
      surfaced first.
    * Each rendered direction-string is capped at
      :data:`_UNRECOGNISED_DIAG_MAX_LEN` chars; longer values are
      truncated with an ellipsis to bound a planted-upstream
      amplification shape.
    """

    if not unrecognised:
        return
    LOGGER.info(
        "Stammstrecke (Hbf): %d Abfahrt(en) mit unbekanntem Endpunkt "
        "verworfen — Top-Termini folgen.",
        sum(unrecognised.values()),
    )
    for terminus, count in sorted(
        unrecognised.items(), key=lambda kv: kv[1], reverse=True
    ):
        rendered = terminus or "<leer>"
        if len(rendered) > _UNRECOGNISED_DIAG_MAX_LEN:
            rendered = rendered[: _UNRECOGNISED_DIAG_MAX_LEN] + "…"
        LOGGER.info(
            "  • %d× Endpunkt='%s'",
            count,
            sanitize_log_arg(rendered),
        )


# ---- Main flow ------------------------------------------------------------


def _process_tick(
    session: requests.Session,
    state: dict[Any, Any],
    *,
    when: datetime,
    recently_finalised: Mapping[str, datetime] | None = None,
) -> str:
    """Single ``/departureBoard`` poll, classify, and observe.

    Returns one of:

    * ``"ok"`` — query succeeded and observations were folded into
      the pending state;
    * ``"error"`` — VAO transport / parse error (already logged);
    * ``"quota_exceeded"`` — daily quota cap hit before the call.

    ``CircuitBreakerOpen`` is re-raised so :func:`main` can short-
    circuit the rest of the tick.
    """

    LOGGER.info(
        "Stammstrecke (Hbf): /departureBoard für %s (id=%s, duration=%d).",
        when.isoformat(),
        HAUPTBAHNHOF_VOR_ID,
        DEPARTURE_BOARD_DURATION_MIN,
    )

    try:
        departures = _BREAKER.call(_query_departure_board, session, when=when)
    except CircuitBreakerOpen:
        raise
    except _QuotaExceeded as exc:
        LOGGER.warning(
            "Stammstrecke (Hbf): Tageslimit erreicht — Abfrage übersprungen (%s).",
            sanitize_log_arg(str(exc)),
        )
        return "quota_exceeded"
    except requests.HTTPError as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        LOGGER.warning(
            "Stammstrecke (Hbf): /departureBoard fehlgeschlagen: HTTP %s.",
            sanitize_log_arg(str(status) if status is not None else "?"),
        )
        return "error"
    except Exception as exc:
        # Security: ``VorAuth`` injects the ``accessId`` into the URL
        # before the prepared request leaves the session, so a
        # ``RequestException`` may carry that URL in its message —
        # logging the exception type alone is leak-safe.
        LOGGER.warning(
            "Stammstrecke (Hbf): /departureBoard fehlgeschlagen: %s.",
            type(exc).__name__,
        )
        return "error"

    by_direction, unrecognised = _collect_hbf_observations(departures)
    _log_unrecognised_terminuses(unrecognised)

    written_per_dir: dict[str, int] = {}
    for direction in DIRECTION_LABELS:
        observations = by_direction.get(direction, [])
        if not observations:
            written_per_dir[direction] = 0
            continue
        written_per_dir[direction] = _observe_legs(
            state,
            observations,
            direction=direction,
            now=when,
            recently_finalised=recently_finalised,
        )

    LOGGER.info(
        "Stammstrecke (Hbf): %d Abfahrten gesamt — Meidling=%d, "
        "Floridsdorf=%d (Ledger-Updates).",
        len(departures),
        written_per_dir.get(DIRECTION_LABEL_SOUTHBOUND, 0),
        written_per_dir.get(DIRECTION_LABEL_NORTHBOUND, 0),
    )
    return "ok"


def main() -> int:
    """Entry point. Returns ``0`` on success (incl. degraded), ``1`` on full failure.

    The script never raises an unhandled exception out of ``main`` — the
    cron pipeline relies on a clean exit so other cache updates run on
    schedule even when this provider is degraded. A
    ``CircuitBreakerOpen`` short-circuit appends nothing to the CSV;
    the feed naturally degrades to "no Stammstrecke entry" because the
    most-recent observations roll out of the 1-hour feed window
    without replacement.
    """

    configure_logging()

    when = _now_vienna()

    with _ledger_lock(PENDING_TRIPS_LOCK_PATH):
        state = _load_pending_trips(PENDING_TRIPS_PATH)
        recently_finalised = _load_recently_finalised(RECENTLY_FINALISED_PATH)

        cutoff = when - PENDING_TTL
        purged_pending = _purge_stale_entries(state, cutoff=cutoff)
        purged_finalised = _purge_finalised_entries(
            recently_finalised, cutoff=cutoff
        )
        if purged_pending or purged_finalised:
            LOGGER.info(
                "Stammstrecke (Hbf): %d veraltete Pending-Einträge, %d "
                "veraltete Finalisiert-Einträge entfernt (TTL %s).",
                purged_pending,
                purged_finalised,
                PENDING_TTL,
            )

        successes = 0
        errors = 0

        with ExitStack() as stack:
            try:
                session = _build_session(stack)
            except Exception as exc:  # pragma: no cover - defensive
                LOGGER.error(
                    "Stammstrecke (Hbf): VOR-Session konnte nicht erstellt werden: %s.",
                    type(exc).__name__,
                )
                return 1

            try:
                status = _process_tick(
                    session,
                    state,
                    when=when,
                    recently_finalised=recently_finalised,
                )
            except CircuitBreakerOpen:
                LOGGER.warning(
                    "Stammstrecke (Hbf): Circuit breaker offen (%d "
                    "aufeinanderfolgende Fehler) — Tick übersprungen.",
                    _BREAKER.consecutive_failures,
                )
                status = "error"

            if status in ("error", "quota_exceeded"):
                errors += 1
            else:
                successes += 1

        # Finalisation pass per direction. Trains scheduled <= now are
        # popped from the pending state, their latest observation is
        # written to the CSV, and their identity key is registered in
        # ``recently_finalised`` so a VAO re-emission at the lookahead
        # boundary cannot produce a duplicate CSV row.
        csv_rows_written = 0
        for direction in DIRECTION_LABELS:
            finalised = _finalize_departed(
                state,
                direction=direction,
                now=when,
                recently_finalised=recently_finalised,
            )
            if not finalised:
                continue
            by_year: dict[int, list[Any]] = {}
            for trip in finalised:
                by_year.setdefault(trip.scheduled.year, []).append(trip)
            for year in sorted(by_year):
                year_trips = by_year[year]
                mean_minutes = float(
                    statistics.mean(t.latest_delay_minutes for t in year_trips)
                )
                row_timestamp = max(t.scheduled for t in year_trips)
                LOGGER.info(
                    "Stammstrecke (Hbf): Richtung %s, Jahr %d — %d Zug/Züge "
                    "finalisiert, ⌀ %.2f Minuten (Schwelle %d).",
                    direction,
                    year,
                    len(year_trips),
                    mean_minutes,
                    DELAY_THRESHOLD_MINUTES,
                )
                append_stammstrecke_row(
                    timestamp=row_timestamp,
                    direction=direction,
                    delay_minutes=mean_minutes,
                )
                csv_rows_written += 1

        # Persist both ledgers AFTER finalisation. Save
        # recently_finalised FIRST so that on a crash between the two
        # writes, the suppression set is durable — the pending entry
        # will be re-observed on the next tick but skipped by the
        # ``recently_finalised`` guard instead of being double-
        # finalised.
        _save_recently_finalised(RECENTLY_FINALISED_PATH, recently_finalised)
        _save_pending_trips(PENDING_TRIPS_PATH, state)

    LOGGER.info(
        "Stammstrecke (Hbf): %d Beobachtungs-Tick(s), %d CSV-Zeile(n) "
        "geschrieben (Erfolg=%d, Fehler=%d, Pending=%d offen, "
        "Finalisiert=%d).",
        1,
        csv_rows_written,
        successes,
        errors,
        len(state),
        len(recently_finalised),
    )

    if successes == 0 and errors > 0:
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())


__all__ = [
    "DEPARTURE_BOARD_DURATION_MIN",
    "DIRECTION_LABELS",
    "DIRECTION_LABEL_NORTHBOUND",
    "DIRECTION_LABEL_SOUTHBOUND",
    "HAUPTBAHNHOF_VOR_ID",
    "HBF_NORTHBOUND_SUBSTRINGS",
    "HBF_NORTHBOUND_TERMINI",
    "HBF_SOUTHBOUND_SUBSTRINGS",
    "HBF_SOUTHBOUND_TERMINI",
    "classify_hbf_direction",
    "main",
]
