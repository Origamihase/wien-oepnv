"""VAO leg parsing and the latest-wins observe/finalise transitions.

Moved verbatim from ``scripts/update_stammstrecke_status.py``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from src.stammstrecke.core import (
    LOGGER,
    VIENNA_TZ,
    _PendingTrip,
    _S_BAHN_LINE_RE,
)
from src.stammstrecke.ledger import _canonical_line_name, _identity_key
from src.utils.logging import sanitize_log_arg

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
    # ``or None`` collapses an EMPTY-string rtDate/rtDepDate to ``None``:
    # ``"" or "" == ""`` is falsy but not ``None``, which would make the
    # ``rt_date_explicit is None`` heuristic gate below evaluate False and
    # wrongly skip the midnight-rollover correction (a 23:55 leg with
    # rtTime "00:05" then yields a bogus ≈ −23h50m delay).
    rt_date_explicit = origin.get("rtDate") or origin.get("rtDepDate") or None
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

    return _elapsed_minutes(scheduled, actual)


def _elapsed_minutes(scheduled: datetime, actual: datetime) -> float:
    """Minutes from *scheduled* to *actual*, both VAO times in Vienna.

    VAO gives local wall-clock times. Subtracting two datetimes in the same
    zone subtracts their wall clocks, so a train due 01:55 that left ten
    minutes late on the morning the clocks go forward (28.03.2027, its
    real time reading 03:05) counted as 70 minutes late. The minutes are
    therefore taken between the two moments in UTC. A time in the hour
    that repeats when summer time ends (02:00 to 03:00 on 25.10.2026) says
    neither which of the two hours it means, so there the wall clocks
    are subtracted as before, which is right whenever both times fall in
    the same hour of the two.
    """
    if _ambiguous_local(scheduled) or _ambiguous_local(actual):
        return (actual - scheduled).total_seconds() / 60.0
    return (actual.astimezone(UTC) - scheduled.astimezone(UTC)).total_seconds() / 60.0


def _ambiguous_local(when: datetime) -> bool:
    """Whether *when*'s wall clock in Vienna repeats or is skipped by a clock change."""
    local = when.astimezone(VIENNA_TZ).replace(tzinfo=None)
    return (
        local.replace(tzinfo=VIENNA_TZ, fold=0).utcoffset()
        != local.replace(tzinfo=VIENNA_TZ, fold=1).utcoffset()
    )


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
