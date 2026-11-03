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

Hybrid direction classification: the destination terminus is resolved
through the central station directory (:func:`src.utils.stations.station_info`)
and labelled by the terminus latitude relative to Wien Hauptbahnhof
(geographic primary path). Departures whose terminus is unknown to the
directory — or known but missing coordinates — fall back to the
deterministic Bahnsteig-1/2 mapping enforced by the track-trunk gate.
No trains are dropped for an unrecognised terminus; the track gate
already restricts the sample to the two Stammstrecke platforms, so
every survivor belongs to one of the two corridor directions by
construction.

Platform-level Stammstrecke gate
--------------------------------

The Wien Hauptbahnhof track layout dedicates two platforms to the
S-Bahn Stammstrecke (``Bahnsteig 1`` for southbound trains toward
Meidling, ``Bahnsteig 2`` for northbound trains toward Floridsdorf).
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
Stammstrecke statistic. This eliminates the false-positive surface
where a terminus string like ``"Bratislava-Petržalka"`` could be
mistaken for a Stammstrecke + Břeclav corridor service: only those
trains actually departing from Bahnsteig 1 or 2 survive the gate,
regardless of which corridor their terminus name might suggest.

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

import re
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any, Final

import requests

from src.providers import vor as vor_provider
from src.stammstrecke.core import MAX_QUERY_TIMEOUT, QUERY_TIMEOUT
from src.utils.files import loads_finite
from src.utils.http import request_safe

# ``__all__`` lists every name this module defines. Some private names are
# read only by other modules and tests; CodeQL (py/unused-global-variable)
# counts such a read through an import only when the name is listed here.
__all__ = [
    "HAUPTBAHNHOF_VOR_ID", "DEPARTURE_BOARD_DURATION_MIN", "IMPLAUSIBLE_DELAY",
    "STAMMSTRECKE_HBF_TRACK_TRUNKS", "_TRACK_TRUNK_RE", "DIRECTION_LABEL_SOUTHBOUND",
    "DIRECTION_LABEL_NORTHBOUND", "LEGACY_DIRECTION_LABEL_NORTHBOUND", "DIRECTION_LABELS",
    "HBF_REFERENCE_LATITUDE", "_query_departure_board",
]


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

# A real-time reading at least this far from the plan is no delay reading
# (see :func:`_departure_delay_minutes`).
IMPLAUSIBLE_DELAY: Final = timedelta(hours=12)

# ---- Stammstrecke platform (Bahnsteig) filter -----------------------------
#
# Wien Hauptbahnhof's track layout dedicates two platforms to the S-Bahn
# Stammstrecke: ``Bahnsteig 1`` carries trains heading south toward
# Meidling, ``Bahnsteig 2`` carries trains heading north toward
# Floridsdorf. Every other platform (3 through 12, including the
# lettered half-platforms "10A"/"10B" found on longer surface tracks)
# serves long-distance services (Railjet/IC/EC/NJ), terminating-at-Hbf
# REX trains, the Marchegger Ostbahn (REX2/REX5 to Bratislava/Marchegg),
# the Pottendorfer Linie, the Westbahn, and other corridors that do
# NOT traverse the Stammstrecke tunnel.
#
# Filtering departures by their effective track therefore provides a
# deterministic, platform-level identification of trains that actually
# use the Stammstrecke at Hbf — bypassing the false-positive surface
# (e.g., a train to "Marchegg" or "Bratislava-Petržalka" departing Hbf
# *eastward* via the Ostbahn would no longer slip into the statistic,
# because it would land on track 11 / 12 instead of 1 / 2). The
# track-trunk also serves as the deterministic fallback when
# direction resolution via :func:`station_info` cannot place the
# terminus on the geographic axis.
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
# ``"Praterstern"``.
#
# Resolution is hybrid:
#
#   1. **Primary geographic path** — the ``direction`` string is looked
#      up in the central station directory (:func:`station_info`) and
#      the terminus latitude is compared against
#      :data:`HBF_REFERENCE_LATITUDE`. Northern destinations
#      → ``"Praterstern"``, southern → ``"Meidling"``.
#   2. **Track-based fallback** — when the terminus is unknown to the
#      directory (or known but missing coordinates) the track-trunk
#      assignment alone determines the direction. The track-trunk
#      gate above guarantees ``track_trunk`` is ``"1"`` or ``"2"``,
#      so the fallback is exhaustive and no departure is dropped for
#      an unrecognised terminus.

# Reference latitude for the geographic direction split — sourced from
# the Wien Hauptbahnhof entry in ``data/stations.json``. A terminus
# whose latitude is strictly greater counts as northbound, strictly
# less as southbound. The exact-equality boundary falls through to the
# track-based fallback (immaterial in practice: only a terminus
# co-located with Hbf would tie, and a Hbf-terminating departure does
# not continue across the Stammstrecke).
HBF_REFERENCE_LATITUDE: Final = 48.1851


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

    The VAO daily quota is charged by the CALLER (before the circuit
    breaker is consulted), not here: a quota breach is a local budget
    signal and must not count as an upstream failure toward the breaker.
    See the charge at the call site in the tick loop.
    """

    safe_timeout = max(1, min(timeout, MAX_QUERY_TIMEOUT))

    params: dict[str, str] = {
        "id": HAUPTBAHNHOF_VOR_ID,
        "date": when.strftime("%Y-%m-%d"),
        "time": when.strftime("%H:%M"),
        "duration": str(duration_min),
        # Filter on product classes Train (1) + S-Bahn (2). The
        # post-filter in :func:`_is_sbahn_line` narrows further to
        # S/R/REX/CJX line patterns, but the upstream filter saves us
        # a ton of long-distance (RJ, IC, EC, NJ) entries in the
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
            f"VAO /departureBoard returned unparseable JSON: {type(exc).__name__}: {exc}"
        ) from exc

    if not isinstance(payload, dict):
        raise TypeError(
            f"VAO /departureBoard returned non-dict payload: "
            f"{type(payload).__name__}"
        )

    # VAO sometimes returns HTTP 200 with an error envelope —
    # ``{"errorCode": "H730", "errorText": "..."}`` (quota / authentication
    # errors are typical examples). Pre-fix the function silently
    # returned ``[]`` for any payload without a ``Departure`` field,
    # so an operator log showed a clean "0 departures" run while the
    # real upstream state was an authentication failure. Surface the
    # error explicitly so the caller's diagnostic branch logs it and
    # the cron tick reports an error rather than success.
    error_code = payload.get("errorCode")
    if isinstance(error_code, str) and error_code:
        error_text = payload.get("errorText")
        text_part = f" ({error_text!r})" if isinstance(error_text, str) else ""
        raise ValueError(
            f"VAO /departureBoard returned errorCode "
            f"{error_code!r}{text_part}"
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
