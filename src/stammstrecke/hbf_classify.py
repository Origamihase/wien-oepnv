"""Departure-field helpers and the Hbf direction classifier.

Moved verbatim from ``scripts/update_stammstrecke_hbf.py``; the design
notes are the docstring of :mod:`src.stammstrecke.hbf_board`.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from src.stammstrecke.core import _S_BAHN_LINE_RE
from src.stammstrecke.hbf_board import (
    DIRECTION_LABEL_NORTHBOUND,
    DIRECTION_LABEL_SOUTHBOUND,
    DIRECTION_LABELS,
    HBF_REFERENCE_LATITUDE,
    IMPLAUSIBLE_DELAY,
    STAMMSTRECKE_HBF_TRACK_TRUNKS,
    _TRACK_TRUNK_RE,
)
from src.stammstrecke.ledger import _canonical_line_name
from src.stammstrecke.legs import (
    _elapsed_minutes,
    _parse_vao_dt,
    _SbahnLegObservation,
)
from src.utils.stations import station_info

LOGGER = logging.getLogger("update_stammstrecke_hbf")


def _is_sbahn_line(name: str) -> bool:
    """Return ``True`` if *name* matches the S/R/REX/CJX line-code pattern.

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

    rt_date_explicit = dep.get("rtDate") or dep.get("rtDepDate")
    rt_date = rt_date_explicit or sched_date
    actual = _parse_vao_dt(rt_date, rt_time)
    if actual is None:
        return None

    # Midnight-rollover heuristic: when VAO omits ``rtDate`` (we fell
    # back to ``sched_date``) AND the resulting ``actual`` lies more
    # than 12 h BEFORE ``scheduled``, the realtime departure has
    # crossed midnight relative to the schedule (e.g. scheduled
    # 23:55, rtTime "00:05" → actual computed as same-day 00:05 yields
    # a meaningless ≈-23:50 h "delay"). Bump ``actual`` by one day so
    # the recorded delay reflects the true wall-clock difference. The
    # 12 h threshold separates a legitimate (small-magnitude) early
    # departure from a midnight wrap.
    if rt_date_explicit is None and (scheduled - actual) > timedelta(hours=12):
        actual = actual + timedelta(days=1)
    # Symmetric wrap: a leg scheduled just AFTER midnight (e.g. 00:05)
    # that departs a few minutes early (rtTime "23:54", i.e. the previous
    # day) yields a same-day 23:54 ``actual`` and a bogus ≈ +23h49m
    # "delay". Pull ``actual`` back one day so the recorded delay is the
    # true small negative value (≈ −11 min) instead of ~+1429 min.
    elif rt_date_explicit is None and (actual - scheduled) > timedelta(hours=12):
        actual = actual - timedelta(days=1)

    # A real-time reading twelve hours or more from the plan is not this
    # train's delay; the same twelve hours the wrap above uses to tell a
    # small early or late departure from another day. On 08.06.2026 the
    # 20:00 board gave three Praterstern trains (S2 20:15, REX3 20:20,
    # S3 20:27) a real time about 20 hours after the plan (1160, 1199 and
    # 1196 minutes), with the next day as ``rtDate``; their tick row read
    # 395.33 minutes, the year's maximum on the website, and lifted the
    # 20h and Monday means. The largest reading besides them was 85
    # minutes. Such an entry counts as one without a real-time reading:
    # the train keeps the last plausible reading of an earlier tick.
    if abs(actual - scheduled) >= IMPLAUSIBLE_DELAY:
        return None

    return _elapsed_minutes(scheduled, actual)


@dataclass(frozen=True)
class _CollectionDiagnostics:
    """Per-tick filter diagnostics for operator logging.

    The collection step drops every Departure that fails one of three
    independent gates (non-S-Bahn-line / non-Stammstrecke track /
    missing rtTime for non-cancelled trains). Each drop reason
    surfaces as a separate counter so the operator log can pin the
    dominant failure mode after a tick. Direction-classification no
    longer produces a drop reason — the hybrid geographic-plus-track
    resolver in :func:`_collect_hbf_observations` is exhaustive.

    Fields:

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

    dropped_no_track: int
    dropped_non_stammstrecke_track: int
    cancelled_observed: int = 0
    #: S-Bahn departures on a Stammstrecke platform (or cancelled without
    #: one), counted before the realtime gate: a train without ``rtTime``
    #: is still a train on the board. Feeds ``poll_status.json``.
    on_stammstrecke: int = 0


def _collect_hbf_observations(
    departures: Iterable[Any],
) -> tuple[dict[str, list[_SbahnLegObservation]], _CollectionDiagnostics]:
    """Filter + direction-classify a ``/departureBoard`` Departure list.

    Returns a 2-tuple:

    * ``{direction_label: [observations]}`` ready to feed to
      :func:`_observe_legs` per-direction. Keys are always present
      (empty list when no departures matched that direction).
    * :class:`_CollectionDiagnostics` — per-failure-mode drop counters
      plus the cancellation-capture counter. The caller logs them at
      INFO so operators can spot platform-info drift and
      Stammstrecke-foreign route patterns without scraping DEBUG output.

    Filter pipeline (any failure drops the entry):

    1. Departure entry is a :class:`Mapping`.
    2. Line designation matches the S / R / REX pattern (``S1``,
       ``REX 3``, ``R 81`` — :func:`_is_sbahn_line`). Long-distance
       products (RJ, IC, EC, NJ, ICE, CAT, WB) are rejected here.
    3. **Effective track is Bahnsteig 1 or 2.** The deterministic
       Stammstrecke gate: only the Wien Hbf platforms dedicated to the
       S-Bahn Stammstrecke pass. Realtime track (``rtTrack`` /
       ``rtDepTrack``) overrides scheduled track when present so a
       mid-disruption platform change is respected. Long-distance
       services on tracks 3-12 and Marchegg / Bratislava / Sopron-bound
       REX trains on the Ostbahn platforms are all rejected here.
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
    7. **Direction resolution (hybrid)** — the ``direction`` string is
       looked up in the central station directory
       (:func:`station_info`); when a known terminus carries a
       latitude, the geographic comparison against
       :data:`HBF_REFERENCE_LATITUDE` labels the observation
       (``"Praterstern"`` for northern destinations, ``"Meidling"``
       for southern). An unknown terminus, or one without
       coordinates, falls back to the track-trunk it survived: trunk
       ``"1"`` → ``"Meidling"``, trunk ``"2"`` → ``"Praterstern"``.
       The fallback is exhaustive because the track-trunk gate
       already constrained ``track_trunk`` to ``{"1", "2"}``, so no
       train is dropped here.

    Compatible with :func:`_observe_legs` via the
    :class:`_SbahnLegObservation` shape (``name``, ``scheduled``,
    ``delay_minutes``, ``cancelled``); the ``direction`` field of the
    observation is implicit in which list it lives in.
    """

    by_direction: dict[str, list[_SbahnLegObservation]] = {
        label: [] for label in DIRECTION_LABELS
    }
    dropped_no_track = 0
    dropped_non_stammstrecke_track = 0
    cancelled_observed = 0
    on_stammstrecke = 0

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

        # Compute cancellation up front so the track-gate below can let
        # cancelled departures through without ``rtTrack`` — same
        # rationale as the cancellation-before-rtTime-gate documented
        # below. A cancelled S-Bahn train at Hbf often has its
        # ``rtTrack`` removed by VAO (the train will never arrive at
        # any platform), so the strict track gate would otherwise
        # silently undercount outages in
        # ``data/stats/ausfaelle_<YYYY>.csv``.
        cancelled = _departure_is_cancelled(dep)

        # Platform-level Stammstrecke gate. Applied EARLY (before the
        # rtTime / direction resolution) so the diagnostic counters
        # attribute every drop to its primary cause: a long-distance
        # train on track 7 is counted as ``non-Stammstrecke track``
        # rather than masked behind a downstream filter.
        track_text = _extract_track_string(dep)
        track_trunk: str | None
        if track_text is None:
            # Cancelled-no-track: keep so terminus-based direction
            # resolution can still classify the outage. Non-cancelled
            # departures with no track are genuinely unclassifiable
            # and still drop here.
            if not cancelled:
                dropped_no_track += 1
                continue
            track_trunk = None
        else:
            track_trunk = _track_trunk(track_text)
            if track_trunk is None or track_trunk not in STAMMSTRECKE_HBF_TRACK_TRUNKS:
                dropped_non_stammstrecke_track += 1
                continue
        on_stammstrecke += 1

        sched_date = dep.get("date")
        sched_time = dep.get("time")
        scheduled = _parse_vao_dt(sched_date, sched_time)
        if scheduled is None:
            continue

        # Cancellation already decided above; the cancellation path
        # uses ``delay_minutes=0.0`` as a placeholder while the
        # observation is routed to the Ausfaelle ledger.
        if not cancelled:
            delay_value = _departure_delay_minutes(dep)
            if delay_value is None:
                continue
        else:
            delay_value = 0.0

        # Hybrid direction resolution: directory lookup + geographic
        # latitude split, with the surviving track trunk as the
        # deterministic fallback. The track-trunk gate above already
        # restricted ``track_trunk`` to ``{"1", "2"}``, so the
        # fallback is exhaustive and no observation is dropped for an
        # unrecognised terminus.
        direction_str = str(dep.get("direction") or "").strip()
        info = station_info(direction_str)
        direction: str | None = None
        if info is not None and info.latitude is not None:
            if info.latitude > HBF_REFERENCE_LATITUDE:
                direction = DIRECTION_LABEL_NORTHBOUND
            elif info.latitude < HBF_REFERENCE_LATITUDE:
                direction = DIRECTION_LABEL_SOUTHBOUND
        if direction is None:
            if track_trunk == "1":
                direction = DIRECTION_LABEL_SOUTHBOUND
            elif track_trunk == "2":
                direction = DIRECTION_LABEL_NORTHBOUND
        if direction is None:
            # Reachable on the cancelled-no-track path when terminus
            # lookup also failed (unknown destination or no recorded
            # latitude). Drop these genuinely unclassifiable
            # cancellations rather than misroute them into an
            # arbitrary direction bucket. For the normal
            # track-known path the trunk-fallback above is
            # exhaustive (``track_trunk`` ∈ ``{"1", "2"}``), so this
            # guard never fires there.
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
            dropped_no_track=dropped_no_track,
            dropped_non_stammstrecke_track=dropped_non_stammstrecke_track,
            cancelled_observed=cancelled_observed,
            on_stammstrecke=on_stammstrecke,
        ),
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


def _count_readable_departures(departures: Iterable[Any]) -> int:
    """Departures that carry every field the monitor reads.

    A line name, a parseable scheduled date and time, and a platform (a
    cancelled train may lack the platform). Independent of the S-Bahn and
    platform gates: a Railjet on platform 7 is a perfectly readable
    departure. When a board lists departures and *none* is readable, the
    answer is incomplete (:func:`src.utils.stammstrecke_poll.answer_is_incomplete`)
    — a train-less board and a board whose fields went missing must not
    look the same.
    """

    readable = 0
    for dep in departures:
        if not isinstance(dep, Mapping):
            continue
        if not _departure_line_name(dep):
            continue
        if _parse_vao_dt(dep.get("date"), dep.get("time")) is None:
            continue
        if _extract_track_string(dep) is None and not _departure_is_cancelled(dep):
            continue
        readable += 1
    return readable


# A VAO entry named by its train number ("S28809", "CJX2932") instead of its
# line. From 19.05. to 07.07.2026 the departure board listed trains twice
# in the same answer: under the line ("S2") and under the number, at the
# same minute in the same direction (113 of the 122 number entries). The
# identity key holds the name, so both were finalised: six trains stood
# twice in the cancellation ledger, three that ran (only their number entry
# said cancelled) stood there once, and running pairs counted twice in the
# mean delay of their tick.
_TRAIN_NUMBER_NAME_RE = re.compile(r"^(?:S|REX|R|CJX)\d{4,}$")


def _without_train_number_twins(trips: Sequence[Any]) -> list[Any]:
    """*trips* without a train-number entry whose train a line entry already names.

    One departure per minute and direction leaves Wien Hbf on the
    Stammstrecke, so a train-number entry at the minute of a line entry is
    the same train: the line entry stands for it, cancelled or running. A
    train-number entry without such a twin stays (it counts under "ohne
    Linienangabe" in the statistics).
    """
    lined = {
        trip.scheduled
        for trip in trips
        if not _TRAIN_NUMBER_NAME_RE.match(trip.name.strip())
    }
    return [
        trip
        for trip in trips
        if not (_TRAIN_NUMBER_NAME_RE.match(trip.name.strip()) and trip.scheduled in lined)
    ]
