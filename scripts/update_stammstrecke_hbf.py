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
on the station board) is classified into the two direction labels
``"Meidling"`` (south) and ``"Praterstern"`` (north). The labels are
named after the **next major Stammstrecke stop after Hbf** in each
direction — the symmetry lets a short-turn terminating at Praterstern
(or even Wien Mitte) live in the same bucket as a long-runner that
continues to Floridsdorf, Stockerau, or Břeclav. Pre-2026-05-15 the
northbound label was ``"Floridsdorf"``; the rename is a measurement
semantic, not a routing change. The CSV migration commit (sibling of
this rename) rewrites all historical rows to the new label and the
backwards-compatibility alias :data:`LEGACY_DIRECTION_LABEL_NORTHBOUND`
keeps any externally-restored old ledger / CSV readable by the feed
renderer.

The CSV schema is otherwise unchanged — the README dashboard, feed
event renderer (``src/feed/stammstrecke.py``) and any external
analysis continue to work byte-for-byte once they pick up the new
direction value.

Two-layer classification: substring match against well-known south/north
landmark names first (fast path), then exact-terminus whitelist
fallback. Unrecognised terminuses are dropped with a deduplicated INFO
log so operators can extend the whitelist when a new line appears.

Platform-level Stammstrecke gate
--------------------------------

The Wien Hauptbahnhof track layout dedicates two platforms to the
S-Bahn Stammstrecke (``Bahnsteig 1`` for northbound trains toward
Floridsdorf, ``Bahnsteig 2`` for southbound trains toward Meidling).
Every other Hbf platform (3 through 12, including the lettered
half-platforms on longer surface tracks) serves long-distance trains
(Railjet, IC, EC, NJ), terminating-at-Hbf REX services, the Marchegger
Ostbahn (REX2 / REX5 / REX8 to Bratislava / Marchegg / Sopron), the
Pottendorfer Linie, the Westbahn, and other corridors that do NOT
traverse the Stammstrecke tunnel.

The collector therefore gates every departure on its effective track
(``rtTrack`` overrides ``track``) before doing any further filtering:
only trains scheduled on Bahnsteig 1 or 2 — or moved to one of those
platforms by a realtime announcement — are eligible for the
Stammstrecke statistic. This eliminates the substring-classification
false-positive surface where a terminus string like ``"Bratislava-
Petržalka"`` could match the northbound whitelist regardless of
whether the train uses the Stammstrecke + Břeclav corridor or the
eastward Marchegger Ostbahn corridor — only the former departs from
track 1, so only the former survives the gate.

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

Semantic break vs the pre-2026-05-15 ``/trip`` pipeline
-------------------------------------------------------

The ``/trip`` predecessor measured delay **at the train's origin
station** (Wien Floridsdorf for Meidling-bound trains, Wien Meidling
for Floridsdorf-bound trains). This script measures delay **at Wien
Hauptbahnhof** — a Stammstrecke midpoint. The two numbers are not
interchangeable for the same physical train:

* A delay that accumulates on the Stammstrecke between Floridsdorf and
  Hbf shows up in the Hbf reading but not in the origin reading.
* A delay that the train recovers on the way to Hbf (e.g., dwell-time
  shortening at intermediate stops) shrinks in the Hbf reading.

Operationally the Hbf measurement is the more representative
``Stammstrecke delay`` signal — it samples IN the Stammstrecke rather
than at its endpoints — but the README's 30-day average straddles the
migration cutover, so trend comparisons across 2026-05-15 will show a
discontinuity that is a measurement-semantic shift, not a real change
in the underlying service quality. A CHANGELOG entry exists; readers
of the dashboard who pre-date the migration should be aware.

Reuse
-----

This script imports the shared infrastructure (pending state, finalise,
ledger I/O, lock, quota charge, HTTP session) from
``scripts/update_stammstrecke_status.py`` rather than duplicating it.
The legacy script remains importable so its tests stay green during the
transition; it is no longer wired into the cron workflow.

The :func:`_observe_legs` identity key uses ``(direction, name,
scheduled)``. Because ``scheduled`` here is the **Hbf** departure time
while the legacy script used the **origin** departure time, a legacy
pending-trip entry surviving in the ledger at migration time has a
different key shape than a freshly-observed Hbf entry for the same
physical train — the legacy entry finalises into its own CSV row
under its old timestamp once its ``scheduled`` time passes, while
the Hbf entry finalises separately. This produces at most a handful
of mixed-shape CSV rows during the one-shot transition window; the
``PENDING_TTL`` purge (6 h) bounds the residual artefact.
"""

from __future__ import annotations

import logging
import re
import statistics
import sys
from collections import defaultdict
from collections.abc import Iterable, Mapping
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Final

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
from src.utils.files import loads_finite  # noqa: E402
from src.utils.http import request_safe  # noqa: E402
from src.utils import logging as utils_logging  # noqa: E402
from src.utils.stats import (  # noqa: E402
    append_ausfall_row,
    append_stammstrecke_row,
)

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
#
# Effective coverage caveat: the 45-min window is what VAO RETURNS,
# but the :func:`_departure_delay_minutes` filter drops every entry
# without an ``rtTime`` field (see its docstring — coercing missing
# realtime to 0.0 systematically biased the legacy sample). VAO only
# populates ``rtTime`` once a train is roughly 20-25 min from its
# scheduled departure, so the *effective* coverage per tick is the
# intersection of the duration window and the rtTime forecast horizon
# — empirically ~24 min from the cron tick. The 15-min cron-interval
# overlap still holds because the next tick's rtTime horizon advances
# 30 min while the duration window also advances 30 min; no train can
# fall between two consecutive ticks' rtTime windows under normal
# cron cadence. A SKIPPED tick (cron jitter >~30 min) is the only
# shape that can lose trains, and that's a workflow-trigger issue,
# not a script-tuning issue.
DEPARTURE_BOARD_DURATION_MIN: Final = 45

# ---- Stammstrecke platform (Bahnsteig) filter -----------------------------
#
# Wien Hauptbahnhof's track layout dedicates two platforms to the S-Bahn
# Stammstrecke: ``Bahnsteig 1`` carries trains heading north toward
# Floridsdorf, ``Bahnsteig 2`` carries trains heading south toward
# Meidling. Every other platform (3 through 12, including the lettered
# half-platforms "10A"/"10B" found on longer surface tracks) serves
# long-distance services (Railjet/IC/EC/NJ), terminating-at-Hbf REX
# trains, the Marchegger Ostbahn (REX2/REX5 to Bratislava/Marchegg),
# the Pottendorfer Linie, the Westbahn, and other corridors that do
# NOT traverse the Stammstrecke tunnel.
#
# Filtering departures by their effective track therefore provides a
# deterministic, platform-level identification of trains that actually
# use the Stammstrecke at Hbf — bypassing the terminus-substring
# heuristic's false-positive surface (e.g., a train to "Marchegg" or
# "Bratislava-Petržalka" departing Hbf *eastward* via the Ostbahn
# would no longer slip into the statistic, because it would land on
# track 11 / 12 instead of 1 / 2). The substring whitelist still
# resolves the **direction** (north vs south) for the trains that
# pass the track gate; the two-layer filter is by design.
#
# Realtime override: the VAO ``rtTrack`` field overrides ``track`` when
# present. A mid-disruption platform change from Bahnsteig 2 to
# track 5 is therefore correctly excluded from the Stammstrecke
# sample even when the scheduled timetable still says Bahnsteig 2.
STAMMSTRECKE_HBF_TRACK_TRUNKS: Final[frozenset[str]] = frozenset({"1", "2"})

# Track-string normaliser. VAO emits track values in several shapes
# (Handbuch_VAO_ReST_API §13 + §11 example responses):
#
# * Plain integer: ``"1"``, ``"5"``
# * Zero-padded: ``"01"``, ``"02"`` (some legacy serialisers)
# * Sub-platform suffix: ``"1A"``, ``"1B"``, ``"2A"``, ``"10A-B"``
# * Whitespace padding: ``"  1  "``
#
# The trunk is the leading numeric component, with optional zero
# padding stripped: ``"01A"`` → ``"1"``, ``"10A-B"`` → ``"10"``.
# The match is anchored so ``"-1"`` does NOT yield trunk ``"1"`` —
# the leading ``-`` falls outside the allowed prefix and the function
# returns ``None`` (defensive: track ``-1`` is not a real platform).
_TRACK_TRUNK_RE: Final = re.compile(r"^\s*0*([0-9]+)")

# Geographic-direction → CSV-label mapping. Pinned constants so a future
# rename here cannot silently drift the README dashboard column values.
#
# Label history:
# * 2026-05-09 (legacy ``/trip`` migration): the northbound label was
#   "Floridsdorf" — the train's typical terminus on the Floridsdorf-
#   to-Meidling ``/trip`` query.
# * 2026-05-15 (this rename): the northbound label is "Praterstern".
#   Rationale: not every northbound Stammstrecke train continues all
#   the way to Floridsdorf — short-turns terminating at Praterstern
#   (and even at Wien Mitte) appear in the live feed, and the
#   southbound label "Meidling" already encodes the *next major
#   Stammstrecke stop after Hbf* rather than the eventual far-end
#   terminus. Using "Praterstern" for the symmetric northbound
#   meaning gives both directions the same shape: "Stammstrecke
#   trains heading toward <next Stammstrecke stop from Hbf>".
DIRECTION_LABEL_SOUTHBOUND: Final = "Meidling"
DIRECTION_LABEL_NORTHBOUND: Final = "Praterstern"

# Legacy CSV / ledger / first-seen labels that the 2026-05-09 pipeline
# wrote. Read paths accept either label so historical rows under the
# old name (data/stats/stammstrecke_2026.csv pre-rename + any in-flight
# pending-trip-state entries observed before the rename commit) still
# fold into the canonical "Praterstern" bucket on the next read. This
# shim is checked in :func:`src.feed.stammstrecke.compute_stammstrecke_
# events` and in :func:`scripts.update_stammstrecke_hbf.main`'s
# finalise-loop key resolver so the in-flight ledger transition is
# transparent to operators.
LEGACY_DIRECTION_LABEL_NORTHBOUND: Final = "Floridsdorf"

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
#
# Removed 2026-05-15 in the track-filter follow-up (PR #149x): both
# ``"marchegg"`` and ``"bratislava"`` previously sat in this list as
# eastward-leaning termini, but neither corresponds to a route that
# transits the Stammstrecke tunnel — Marchegg services depart Hbf via
# the Marchegger Ostbahn (eastward, never crosses Praterstern), and
# Bratislava is reachable from Hbf via either the Stammstrecke +
# Břeclav corridor (correctly captured by the ``břeclav`` /
# ``breclav`` substrings) OR the eastward Ostbahn (which doesn't
# touch the Stammstrecke at all). With the platform-level track gate
# in :data:`STAMMSTRECKE_HBF_TRACK_TRUNKS` now keeping the analysis
# strictly on Bahnsteig 1 / 2, the substring whitelist only needs to
# carry true Stammstrecke-northbound termini — leaving the ambiguous
# eastward names out is the strict-correctness win.
HBF_NORTHBOUND_SUBSTRINGS: Final[tuple[str, ...]] = (
    "floridsdorf",    # eventual Stammstrecke northern terminus
    "praterstern",    # intermediate Stammstrecke stop, terminus for some short-runs
    "stockerau",      # S2 / R-train terminus northwest
    "hollabrunn",     # REX northern terminus
    "retz",           # REX northern terminus
    "břeclav",        # long-distance north (CZ) via Stammstrecke
    "breclav",        # ASCII variant
    "wolkersdorf",    # S2 / R-train terminus north
    "mistelbach",     # S2 northern terminus
    "laa an der thaya",  # S2 northern terminus (long form)
    "laa/thaya",      # variant
    "gänserndorf",    # S1 / REX northern terminus
    "gaenserndorf",   # ASCII variant
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
        # Security: ``loads_finite`` pins parse_constant + parse_float
        # hooks (Round 1503 sibling) that reject NaN / Infinity / 1e1000
        # literals from a compromised HAFAS VAO upstream / MITM. Without
        # the hooks a planted non-finite literal in a departure-board
        # payload propagates ``float('nan')`` / ``float('inf')`` into
        # latency / delay arithmetic and round-trip-crashes the writer
        # pin (Round 1485) on pending-trip state persistence.
        payload = loads_finite(content)
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
    """Return ``"Meidling"`` (south), ``"Praterstern"`` (north) or ``None``.

    Two-layer match against the direction string (the train's terminus
    as serialised by VAO):

    1. Substring against :data:`HBF_SOUTHBOUND_SUBSTRINGS` /
       :data:`HBF_NORTHBOUND_SUBSTRINGS` — fast path, case-insensitive.
       Catches the typical "City Name [Bahnhof]" forms.
    2. Exact-string against :data:`HBF_SOUTHBOUND_TERMINI` /
       :data:`HBF_NORTHBOUND_TERMINI` — slower fallback for terminuses
       that contain none of the landmark substrings.

    Conflict resolution: when substrings from BOTH lists match — e.g., a
    descriptive direction string like ``"via Mödling nach Floridsdorf"``
    (Mödling = south, Floridsdorf = north) — the match whose latest
    occurrence sits *furthest right* in the string wins. The VAO
    ``direction`` field renders the **terminus at the end** in
    every shape observed in production; the right-most match
    therefore identifies the true terminus instead of a "via" waypoint
    that happens to belong to the opposite geographic direction.
    Pre-2026-05-15 the substring loop returned ``SOUTHBOUND`` on first
    hit, mis-classifying any such mixed string as southbound regardless
    of terminus.

    ``None`` means the terminus is unrecognised; the caller logs and
    drops the departure.
    """

    norm = direction_str.strip()
    if not norm:
        return None
    lower = norm.lower()

    south_pos = -1
    for needle in HBF_SOUTHBOUND_SUBSTRINGS:
        idx = lower.rfind(needle)
        if idx > south_pos:
            south_pos = idx
    north_pos = -1
    for needle in HBF_NORTHBOUND_SUBSTRINGS:
        idx = lower.rfind(needle)
        if idx > north_pos:
            north_pos = idx

    if south_pos >= 0 and north_pos >= 0:
        # Both matched — the terminus is whichever direction's needle
        # sits furthest right (closest to the end of the string).
        return (
            DIRECTION_LABEL_SOUTHBOUND
            if south_pos >= north_pos
            else DIRECTION_LABEL_NORTHBOUND
        )
    if south_pos >= 0:
        return DIRECTION_LABEL_SOUTHBOUND
    if north_pos >= 0:
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


def _track_trunk(track_value: object) -> str | None:
    """Return the trunk number of a VAO ``track`` field, or ``None``.

    Normalises:

    * Leading zeros: ``"01"`` → ``"1"``.
    * Sub-platform suffix: ``"1A"`` / ``"1B"`` → ``"1"``;
      ``"10A-B"`` → ``"10"``.
    * Whitespace padding: ``"  1  "`` → ``"1"``.

    Returns ``None`` for ``None`` / empty / non-leading-digit inputs
    (e.g., a VAO response that omits ``track`` entirely or reports a
    non-numeric value like ``"Gleis A"``).
    """

    if track_value is None:
        return None
    if isinstance(track_value, bool):
        # ``int(True) == 1`` so a stray boolean in the response would
        # otherwise produce trunk ``"1"`` and falsely whitelist a
        # bogus departure as Stammstrecke. Strict refusal is the
        # safer default — VAO's documented ``track`` shape is a
        # string, never a boolean.
        return None
    text = track_value if isinstance(track_value, str) else str(track_value)
    match = _TRACK_TRUNK_RE.match(text)
    if match is None:
        return None
    return match.group(1)


def _extract_track_string(dep: Mapping[str, Any]) -> str | None:
    """Return the effective track string for *dep* (rtTrack preferred).

    Field-name priority mirrors the VAO Handbuch §11 + §13 inventory:

    * ``rtTrack`` — realtime track at the station-board / trip departure.
    * ``rtDepTrack`` — realtime departure-track in journey-detail shape.
    * ``track`` — scheduled station-board / trip track.
    * ``depTrack`` — scheduled departure-track in journey-detail shape.

    Realtime fields override scheduled fields so an unscheduled
    platform change during a disruption is respected. Returns
    ``None`` when none of the candidates carries a non-empty string.
    """

    for key in ("rtTrack", "rtDepTrack", "track", "depTrack"):
        value = dep.get(key)
        if value is None:
            continue
        text = value if isinstance(value, str) else str(value)
        if text.strip():
            return text
    return None


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


def _departure_is_cancelled(dep: Mapping[str, Any]) -> bool:
    """Return ``True`` when *dep* is flagged as cancelled.

    VAO serialises the cancellation flag both as a JSON boolean and as
    the literal string ``"true"`` depending on the response variant;
    both shapes are observed in production payloads and both must
    surface as cancellations in the statistics ledger. Other truthy
    spellings (``"yes"``, ``"1"``, …) are deliberately refused — the
    field is a contracted boolean, and accepting fuzzy strings would
    open the door to false positives from a hand-edited / poisoned
    cache.
    """

    cancelled = dep.get("cancelled")
    if cancelled is True:
        return True
    if isinstance(cancelled, str) and cancelled.strip().lower() == "true":
        return True
    return False


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

    if _departure_is_cancelled(dep):
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

    direction: str  # label: "Meidling" or "Praterstern" (legacy: "Floridsdorf")
    name: str       # canonicalised line designation (e.g., "S1", "REX3")
    scheduled: datetime
    delay_minutes: float


@dataclass(frozen=True)
class _CollectionDiagnostics:
    """Per-tick filter diagnostics for operator logging.

    The collection step drops every Departure that fails one of four
    independent gates (cancelled / non-S-Bahn-line / no rtTime /
    unrecognised terminus / non-Stammstrecke track). Each drop reason
    surfaces as a separate counter so the operator log can pin the
    dominant failure mode after a tick.

    Fields:

    * ``unrecognised_terminus`` — distinct terminus strings that
      failed direction classification (``{terminus: occurrence_count}``).
      The :func:`_log_unrecognised_terminuses` helper renders the
      top-N entries.
    * ``dropped_no_track`` — count of departures dropped because the
      VAO response did not populate ``track``/``rtTrack`` at all.
      A persistently-high value points to an upstream serialiser drift
      (VAO releasing departures without platform info for the queried
      station).
    * ``dropped_non_stammstrecke_track`` — count of departures whose
      effective track normalised to a trunk OTHER than ``1`` or ``2``
      (i.e., not a Stammstrecke platform). High values are healthy
      — they reflect long-distance Railjet / IC / EC / NJ services
      and eastward REX / S60 / S80 / Westbahn departures that pass
      through Hbf but never touch the Stammstrecke tunnel.
    * ``cancelled_observed`` — count of Stammstrecke departures whose
      ``cancelled`` flag was ``True`` and which were forwarded to the
      pending-trip ledger for cancellation finalisation. Pre-fix the
      collector silently dropped these, masking real outages from the
      statistics dashboard; the counter is the observability hook so
      operators can spot a sudden spike without reading the CSV.
    """

    unrecognised_terminus: dict[str, int]
    dropped_no_track: int
    dropped_non_stammstrecke_track: int
    cancelled_observed: int = 0


def _collect_hbf_observations(
    departures: Iterable[Any],
) -> tuple[dict[str, list[_SbahnLegObservation]], _CollectionDiagnostics]:
    """Filter + direction-classify a ``/departureBoard`` Departure list.

    Returns a 2-tuple:

    * ``{direction_label: [observations]}`` ready to feed to
      :func:`_observe_legs` per-direction. Keys are always present
      (empty list when no departures matched that direction).
    * :class:`_CollectionDiagnostics` — per-failure-mode drop counters
      and the unrecognised-terminus histogram. The caller logs them at
      INFO so operators can spot platform-info drift / new termini /
      Stammstrecke-foreign route patterns without scraping DEBUG output.

    Filter pipeline (any failure drops the entry):

    1. Departure entry is a :class:`Mapping`.
    2. Line designation matches the S / R / REX pattern (``S1``,
       ``REX 3``, ``R 81`` — :func:`_is_sbahn_line`). Long-distance
       products (RJ, IC, EC, NJ, ICE, CAT, WB) are rejected here.
    3. **Effective track is Bahnsteig 1 or 2.** The deterministic
       Stammstrecke gate: only the Wien Hbf platforms dedicated to the
       S-Bahn Stammstrecke (north / south) pass. Realtime track
       (``rtTrack`` / ``rtDepTrack``) overrides scheduled track when
       present so a mid-disruption platform change is respected.
       Long-distance services on tracks 3-12 and Marchegg / Bratislava
       / Sopron-bound REX trains on the Ostbahn platforms are all
       rejected here, regardless of how their terminus string maps
       under the substring whitelist.
    4. Scheduled timestamp parses to a :class:`datetime`.
    5. **Cancellation captured** — a cancelled departure is emitted
       as an observation with ``cancelled=True`` and a placeholder
       ``delay_minutes=0.0``. The pending-trip ledger then routes the
       train to ``data/stats/ausfaelle_<YYYY>.csv`` at finalise time
       instead of folding it into the delay mean (which would
       systematically bias the mean toward zero — a cancelled train
       is *absent*, not *on-time*). Pre-fix the collector silently
       dropped cancelled departures, hiding real outages from the
       statistics dashboard.
    6. Non-cancelled departures need a realtime signal (``rtTime``
       populated) — the conservative legacy rule, preserved verbatim.
    7. Terminus resolves to ``Meidling`` or ``Praterstern`` via
       :func:`classify_hbf_direction`.

    Compatible with :func:`_observe_legs` via the
    :class:`_SbahnLegObservation` shape (``name``, ``scheduled``,
    ``delay_minutes``, ``cancelled``); the ``direction`` field of the
    observation is implicit in which list it lives in.
    """

    by_direction: dict[str, list[_SbahnLegObservation]] = {
        label: [] for label in DIRECTION_LABELS
    }
    unrecognised: dict[str, int] = defaultdict(int)
    dropped_no_track = 0
    dropped_non_stammstrecke_track = 0
    cancelled_observed = 0

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

        # Platform-level Stammstrecke gate. Applied EARLY (before the
        # rtTime / direction filters) so the diagnostic counters
        # attribute every drop to its primary cause: a long-distance
        # train on track 7 is counted as ``non-Stammstrecke track``
        # rather than masked behind a downstream filter.
        track_text = _extract_track_string(dep)
        if track_text is None:
            dropped_no_track += 1
            continue
        track_trunk = _track_trunk(track_text)
        if track_trunk is None or track_trunk not in STAMMSTRECKE_HBF_TRACK_TRUNKS:
            dropped_non_stammstrecke_track += 1
            continue

        sched_date = dep.get("date")
        sched_time = dep.get("time")
        scheduled = _parse_vao_dt(sched_date, sched_time)
        if scheduled is None:
            continue

        # Cancellation check BEFORE the rtTime gate: a cancelled
        # departure typically has no ``rtTime`` (the train will never
        # depart, so there is nothing to forecast), so the conservative
        # rtTime filter would otherwise discard the cancellation signal
        # alongside it. The direction classifier still runs so the
        # cancelled train lands in the correct bucket.
        cancelled = _departure_is_cancelled(dep)
        if not cancelled:
            delay_value = _departure_delay_minutes(dep)
            if delay_value is None:
                continue
        else:
            delay_value = 0.0

        direction_str = str(dep.get("direction") or "").strip()
        direction = classify_hbf_direction(direction_str)
        if direction is None:
            unrecognised[direction_str] += 1
            continue

        observation = _SbahnLegObservation(
            name=name,
            scheduled=scheduled,
            delay_minutes=delay_value,
            cancelled=cancelled,
        )
        by_direction[direction].append(observation)
        if cancelled:
            cancelled_observed += 1

    return (
        by_direction,
        _CollectionDiagnostics(
            unrecognised_terminus=dict(unrecognised),
            dropped_no_track=dropped_no_track,
            dropped_non_stammstrecke_track=dropped_non_stammstrecke_track,
            cancelled_observed=cancelled_observed,
        ),
    )


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
            utils_logging.sanitize_log_arg(rendered),
        )


def _log_track_drops(
    dropped_no_track: int, dropped_non_stammstrecke_track: int
) -> None:
    """Surface track-filter drop counts at INFO when non-zero.

    Two independent signals:

    * ``dropped_no_track`` — VAO response omitted ``track``/``rtTrack``
      entirely for these departures. A persistently-high value is
      a serialiser-drift alarm: Wien Hbf is a major station and
      should always carry platform info. Operators investigate.
    * ``dropped_non_stammstrecke_track`` — effective track resolved to
      a trunk other than ``1``/``2``. A *healthy* count: it reflects
      the long-distance + Ostbahn + Westbahn + S60/S80 departures
      that legitimately don't use the Stammstrecke and are being
      correctly excluded by the platform gate.
    """

    if not dropped_no_track and not dropped_non_stammstrecke_track:
        return
    LOGGER.info(
        "Stammstrecke (Hbf): Track-Filter — %d Abfahrt(en) ohne Bahnsteig-"
        "Info verworfen, %d Abfahrt(en) auf Nicht-Stammstrecke-Bahnsteig "
        "(Gleis != 1/2) verworfen.",
        dropped_no_track,
        dropped_non_stammstrecke_track,
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
            utils_logging.sanitize_log_arg(str(exc)),
        )
        return "quota_exceeded"
    except requests.HTTPError as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        LOGGER.warning(
            "Stammstrecke (Hbf): /departureBoard fehlgeschlagen: HTTP %s.",
            utils_logging.sanitize_log_arg(str(status) if status is not None else "?"),
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

    by_direction, diagnostics = _collect_hbf_observations(departures)
    _log_unrecognised_terminuses(diagnostics.unrecognised_terminus)
    _log_track_drops(
        diagnostics.dropped_no_track,
        diagnostics.dropped_non_stammstrecke_track,
    )

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
        "Stammstrecke (Hbf): %d Abfahrten gesamt — %s=%d, "
        "%s=%d (Ledger-Updates), %d Ausfall-Beobachtung(en).",
        len(departures),
        DIRECTION_LABEL_SOUTHBOUND,
        written_per_dir.get(DIRECTION_LABEL_SOUTHBOUND, 0),
        DIRECTION_LABEL_NORTHBOUND,
        written_per_dir.get(DIRECTION_LABEL_NORTHBOUND, 0),
        diagnostics.cancelled_observed,
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
        #
        # Backwards-compat: for the northbound direction we also drain
        # any pending entries that survived the 2026-05-15 rename
        # under the legacy ``Floridsdorf`` direction value. The CSV
        # row is still written under the canonical ``Praterstern``
        # label so the dashboard sees a single direction-stream.
        csv_rows_written = 0
        ausfaelle_rows_written = 0
        for direction in DIRECTION_LABELS:
            finalised = _finalize_departed(
                state,
                direction=direction,
                now=when,
                recently_finalised=recently_finalised,
            )
            if direction == DIRECTION_LABEL_NORTHBOUND:
                legacy_finalised = _finalize_departed(
                    state,
                    direction=LEGACY_DIRECTION_LABEL_NORTHBOUND,
                    now=when,
                    recently_finalised=recently_finalised,
                )
                if legacy_finalised:
                    LOGGER.info(
                        "Stammstrecke (Hbf): %d Pending-Eintrag/Einträge mit "
                        "Legacy-Richtungs-Label '%s' finalisiert "
                        "(umgebucht auf '%s' im CSV).",
                        len(legacy_finalised),
                        LEGACY_DIRECTION_LABEL_NORTHBOUND,
                        DIRECTION_LABEL_NORTHBOUND,
                    )
                    finalised = list(finalised) + list(legacy_finalised)
            if not finalised:
                continue
            # Split finalised trains: cancellations go to a dedicated
            # ledger (one CSV row per cancelled train so each shows up
            # as a discrete event in the dashboard count), non-
            # cancelled observations contribute to the per-tick mean-
            # delay aggregate (one CSV row per direction per year).
            # Both ledgers are appended atomically by ``src.utils.stats``;
            # an IO failure on one side does not block the other.
            cancelled_trips = [t for t in finalised if t.cancelled]
            observed_trips = [t for t in finalised if not t.cancelled]
            for trip in cancelled_trips:
                LOGGER.info(
                    "Stammstrecke (Hbf): Richtung %s — Zug %s (geplant %s) "
                    "als Ausfall finalisiert.",
                    direction,
                    utils_logging.sanitize_log_arg(trip.name),
                    utils_logging.sanitize_log_arg(trip.scheduled.isoformat()),
                )
                append_ausfall_row(
                    timestamp=trip.scheduled,
                    direction=direction,
                    line=trip.name,
                )
                ausfaelle_rows_written += 1
            if not observed_trips:
                continue
            by_year: dict[int, list[Any]] = {}
            for trip in observed_trips:
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
        "Stammstrecke (Hbf): %d Beobachtungs-Tick(s), %d Delay-CSV-Zeile(n) "
        "+ %d Ausfall-CSV-Zeile(n) geschrieben (Erfolg=%d, Fehler=%d, "
        "Pending=%d offen, Finalisiert=%d).",
        1,
        csv_rows_written,
        ausfaelle_rows_written,
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
    "LEGACY_DIRECTION_LABEL_NORTHBOUND",
    "STAMMSTRECKE_HBF_TRACK_TRUNKS",
    "classify_hbf_direction",
    "main",
]
