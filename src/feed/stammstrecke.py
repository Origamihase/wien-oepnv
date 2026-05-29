"""CSV-driven Stammstrecke feed-event generator.

Replaces the old ``cache/stammstrecke/events.json``-based pipeline
2026-05-09: previously the cron script computed thresholds + first-seen
inline and wrote a JSON event list that the feed provider read
verbatim. The new flow is:

* :mod:`scripts.update_stammstrecke_status` writes ONLY the
  observation row to ``data/stats/stammstrecke_<YYYY>.csv``. No JSON
  cache, no threshold logic in the cron script.
* This module reads the CSV and rebuilds feed events on every feed
  build: a sliding 1-hour ``feed_window`` decides whether a direction
  is currently above the 9-minute threshold, and a wider
  ``episode_lookback`` decides when the current episode started
  (``first_seen``).

The CSV is a single-source-of-truth ledger that the README dashboard
already consumes for the 30-day snapshot — same data, different
window, no duplicate persistence.

Operational notes:

* The function is best-effort: every I/O failure under the CSV reader
  is swallowed at WARNING level (see :func:`src.utils.stats.read_recent
  _stammstrecke_observations`); callers see an empty list rather than
  an exception.
* Direction labels match the writer's convention (``Meidling`` /
  ``Praterstern``, with legacy ``Floridsdorf`` rows transparently
  re-bucketed via :data:`DIRECTIONS_BY_LABEL`); a direction not in
  the lookup table is silently dropped — protects against a future
  writer that adds a new direction without updating the renderer here.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from statistics import mean
from typing import Any, Final
from zoneinfo import ZoneInfo

from src.utils.ids import make_guid
from src.utils.stats import (
    StammstreckeObservation,
    read_recent_stammstrecke_observations,
)

LOGGER = logging.getLogger("feed.stammstrecke")

VIENNA_TZ: Final = ZoneInfo("Europe/Vienna")

# Threshold and event-shape constants. Single source of truth — the
# cron script no longer carries its own copy of these (it writes raw
# observations only) and the feed provider here owns the entire
# rendering contract.
DELAY_THRESHOLD_MINUTES: Final[float] = 9.0
# Minimum number of *consecutive* in-window observations (adjacent in
# time order) that must each carry a delay strictly greater than
# ``DELAY_THRESHOLD_MINUTES`` before a direction's episode is allowed to
# start. Two back-to-back cron samples above the threshold approximate a
# sustained ≥30-minute exceedance (given the ~30-minute observation
# cadence); a lone outlier sample — or two high samples separated by a
# sub-threshold dip — must not fire on its own.
TRIGGER_CONSECUTIVE_COUNT: Final[int] = 2
EVENT_SOURCE: Final = "VOR/VAO"
EVENT_CATEGORY: Final = "Störung"
EVENT_TITLE: Final = "S-Bahn Stammstrecke Verspätungen"
EVENT_LINK: Final = (
    "https://www.wienerlinien.at/web/wienerlinien/oeffis-stoerungen-strecke"
)

# Window lengths.
#
# ``FEED_WINDOW`` — only observations within the last hour decide
# whether a direction is *currently* above threshold. This matches the
# 2026-05-09 design directive ("Die Meldung im RSS-Feed soll ganz
# aktuelle Meldungen der letzten Stunde anzeigen") and follows the
# observation cadence (one row per direction per 30 minutes → window
# typically holds 2 rows). The trigger gate requires
# ``TRIGGER_CONSECUTIVE_COUNT`` (2) *consecutive* observations in the
# given direction — adjacent in time order — each with a delay strictly
# greater than the threshold within the window (defensive: neither a
# single high outlier nor two high samples straddling a below-threshold
# dip can push the direction past the threshold on their own); the value
# rendered in the feed item description is the *mean* of all in-window
# observations (more intuitive for end users).
#
# ``EPISODE_LOOKBACK`` — when computing ``first_seen`` for the current
# above-threshold episode, we walk back further: the earliest
# contiguous threshold-exceeding observation in this window becomes
# the episode start. Six hours catches typical morning / evening
# rush-hour episodes without dragging yesterday's resolved disruption
# into today's episode start.
#
# ``EPISODE_GAP_TOLERANCE`` — observations are nominally every 30
# minutes, but a single missed cron run (concurrency-group preemption,
# CI runner cold start) leaves a ~60-minute gap. We tolerate up to one
# missed observation when walking back through the episode; a longer
# gap terminates the episode (the disruption resolved between the
# samples we have).
FEED_WINDOW: Final = timedelta(hours=1)
EPISODE_LOOKBACK: Final = timedelta(hours=6)
EPISODE_GAP_TOLERANCE: Final = timedelta(minutes=70)


@dataclass(frozen=True)
class _Direction:
    """Renderer-side metadata per monitored direction."""

    target_label: str
    identity_prefix: str


# Direction registry. Mirrors the cron script's ``DIRECTIONS`` tuple
# (``scripts/update_stammstrecke_hbf.py``) — the CSV's ``direction``
# column carries the ``target_label`` verbatim, so the label here MUST
# match the writer's value byte-for-byte.
#
# 2026-05-15 rename: the northbound label was changed from
# ``"Floridsdorf"`` to ``"Praterstern"`` to make both direction
# buckets name the **next Stammstrecke stop after Hbf** symmetrically
# (south = Meidling, north = Praterstern). The CSV migration commit
# rewrites historical rows, but the legacy alias in
# :data:`DIRECTIONS_BY_LABEL` below keeps any externally-restored
# old CSV / backup readable without losing the northbound observations.
DIRECTIONS: Final[tuple[_Direction, ...]] = (
    _Direction(target_label="Meidling", identity_prefix="stammstrecke_delay_meidling"),
    _Direction(
        target_label="Praterstern", identity_prefix="stammstrecke_delay_praterstern"
    ),
)

# Legacy direction label kept in the lookup table only — never the
# canonical write label. The Hbf script (and any future writer) emits
# the post-rename ``"Praterstern"`` value; this alias exists so a
# historical CSV row that still says ``"Floridsdorf"`` (rare after the
# CSV migration commit, but possible from an external backup or a
# resurrected legacy-script run that skipped the rename) folds into
# the same direction object instead of being silently dropped.
_LEGACY_DIRECTION_ALIAS: Final[dict[str, str]] = {"Floridsdorf": "Praterstern"}

DIRECTIONS_BY_LABEL: Final[dict[str, _Direction]] = {
    **{direction.target_label: direction for direction in DIRECTIONS},
    **{
        legacy_label: next(
            d for d in DIRECTIONS if d.target_label == canonical_label
        )
        for legacy_label, canonical_label in _LEGACY_DIRECTION_ALIAS.items()
    },
}


def _format_minutes(value: float) -> str:
    """Render *value* as ``9`` / ``9.5`` (no trailing zeros)."""
    rounded = round(value, 1)
    return f"{rounded:g}"


def _episode_start(
    *,
    direction_obs: list[StammstreckeObservation],
    now: datetime,
) -> datetime | None:
    """Find the start timestamp of the current above-threshold episode.

    Considers only the above-threshold rows in *direction_obs* (the
    caller passes the per-direction subset). Rows whose delay is at or
    below :data:`DELAY_THRESHOLD_MINUTES` are filtered out up front and
    are NOT treated as episode boundaries — see the note below. The
    surviving rows are walked from the most recent backwards; the
    episode terminates at the first gap *between consecutive
    above-threshold rows* that exceeds :data:`EPISODE_GAP_TOLERANCE`,
    and the timestamp of the oldest row reached before that gap is
    returned. Returns ``None`` when no row exceeds the threshold.

    Short sub-threshold dips are bridged, not terminating. Because the
    below-threshold rows are dropped before the walk, a brief dip
    between two above-threshold samples does not end the episode — it
    only widens the gap the two surviving rows straddle. The episode
    ends across a dip solely when that widened gap exceeds
    :data:`EPISODE_GAP_TOLERANCE` (i.e. the dip, a genuine data gap, or
    the two combined, outlasted one tolerated missed observation). This
    keeps the ``[Seit DD.MM.YYYY]`` label and the item GUID stable
    across the momentary recoveries that punctuate a sustained
    disruption rather than resetting them on every transient dip. The
    trigger decision in :func:`compute_stammstrecke_events` is
    independent of this function — bridging here changes only the
    displayed episode-start date and the GUID, never whether an event
    fires.
    """
    above_threshold = [
        obs for obs in direction_obs if obs.delay_minutes > DELAY_THRESHOLD_MINUTES
    ]
    if not above_threshold:
        return None
    above_threshold.sort(key=lambda obs: obs.timestamp, reverse=True)
    episode_start = above_threshold[0].timestamp
    previous = above_threshold[0].timestamp
    for obs in above_threshold[1:]:
        if previous - obs.timestamp > EPISODE_GAP_TOLERANCE:
            break
        episode_start = obs.timestamp
        previous = obs.timestamp
    return episode_start


def _has_consecutive_exceedance(
    observations: list[StammstreckeObservation],
    *,
    required: int = TRIGGER_CONSECUTIVE_COUNT,
) -> bool:
    """Return ``True`` when *required* time-adjacent observations each exceed the threshold.

    Walks *observations* in ascending-timestamp order, tracking the
    length of the current run of consecutive samples whose delay is
    strictly greater than :data:`DELAY_THRESHOLD_MINUTES`, and returns
    as soon as a run reaches *required*. A sample at or below the
    threshold resets the run to zero, so two high samples that straddle
    a sub-threshold dip do NOT satisfy the gate. This enforces the
    "two *consecutive* trains over 9 minutes" episode-start rule rather
    than the weaker "any two trains over 9 minutes anywhere in the
    window" count.
    """
    run = 0
    for obs in sorted(observations, key=lambda o: o.timestamp):
        if obs.delay_minutes > DELAY_THRESHOLD_MINUTES:
            run += 1
            if run >= required:
                return True
        else:
            run = 0
    return False


def _build_event(
    *,
    direction: _Direction,
    avg_delay_minutes: float,
    now: datetime,
    episode_start: datetime,
) -> dict[str, Any]:
    """Construct the FeedItem dict for *direction*'s current episode."""
    iso_now = now.isoformat()
    iso_first_seen = episode_start.isoformat()
    description = (
        f"Durchschnittliche Verspätung von "
        f"{_format_minutes(avg_delay_minutes)} Minuten "
        f"in Richtung {direction.target_label} "
        f"[Seit {episode_start.strftime('%d.%m.%Y')}]"
    )
    identity = f"{direction.identity_prefix}|{iso_first_seen}"
    guid = make_guid(direction.identity_prefix, iso_first_seen)
    return {
        "source": EVENT_SOURCE,
        "category": EVENT_CATEGORY,
        "title": EVENT_TITLE,
        "description": description,
        "link": EVENT_LINK,
        "guid": guid,
        "pubDate": iso_now,
        "starts_at": iso_first_seen,
        "ends_at": None,
        "first_seen": iso_first_seen,
        "_identity": identity,
    }


def compute_stammstrecke_events(
    *,
    now: datetime | None = None,
    feed_window: timedelta = FEED_WINDOW,
    episode_lookback: timedelta = EPISODE_LOOKBACK,
) -> list[dict[str, Any]]:
    """Read the CSV ledger, fold it into 0..N feed events.

    *now* defaults to :func:`datetime.now` in Europe/Vienna. The
    function emits at most one event per direction in
    :data:`DIRECTIONS` and returns an empty list when no direction has
    :data:`TRIGGER_CONSECUTIVE_COUNT` *consecutive* observations within
    *feed_window* whose delay strictly exceeds
    :data:`DELAY_THRESHOLD_MINUTES` (two adjacent samples over the
    threshold — a lone outlier, or two high samples straddling a
    sub-threshold dip, does not fire). The displayed value in the feed
    item is the *mean* of all in-window observations — see the module-
    level note on the trigger / display split.

    Used by :func:`src.feed.providers.read_cache_stammstrecke` as the
    canonical entry point.
    """
    current = now if now is not None else datetime.now(VIENNA_TZ)
    observations = read_recent_stammstrecke_observations(
        now=current, window=episode_lookback
    )
    if not observations:
        return []
    # Bucket observations by their *canonical* direction label so a CSV
    # row that still carries the legacy ``"Floridsdorf"`` value (from a
    # backup restore, a partial deploy that skipped the migration
    # commit, or a hand-edited row) folds into the same trigger
    # evaluation as the post-rename ``"Praterstern"`` rows. Without
    # this canonicalisation the loop below — which iterates over
    # :data:`DIRECTIONS` and looks up ``direction.target_label`` —
    # would silently ignore the legacy-label observations because
    # the ``by_direction`` key would be ``"Floridsdorf"`` while the
    # registered direction's ``target_label`` is ``"Praterstern"``.
    by_direction: defaultdict[str, list[StammstreckeObservation]] = defaultdict(list)
    for obs in observations:
        canonical = DIRECTIONS_BY_LABEL.get(obs.direction)
        if canonical is None:
            # Unknown / unrecognised direction value — silently dropped.
            # Protects against a future writer that emits a direction
            # outside the registry without updating this consumer.
            continue
        by_direction[canonical.target_label].append(obs)
    feed_window_start = current - feed_window
    events: list[dict[str, Any]] = []
    # Process directions in registry order so two simultaneous events
    # surface in a stable order across feed builds.
    for direction in DIRECTIONS:
        direction_obs = by_direction.get(direction.target_label)
        if not direction_obs:
            continue
        recent = [obs for obs in direction_obs if obs.timestamp >= feed_window_start]
        if not recent:
            continue
        # Trigger gate: an episode may only start when
        # ``TRIGGER_CONSECUTIVE_COUNT`` (2) *consecutive* in-window
        # observations each carry a delay strictly greater than the
        # threshold — two adjacent cron samples over 9 minutes. A lone
        # outlier, or two high samples straddling a below-threshold dip,
        # does not fire. The displayed value is the *mean* of all
        # in-window observations (easier for end users to interpret).
        # See module docstring "Window lengths".
        if not _has_consecutive_exceedance(recent):
            continue
        avg_delay = mean([obs.delay_minutes for obs in recent])
        episode_start = _episode_start(direction_obs=direction_obs, now=current)
        if episode_start is None:
            # Degenerate case: ``_episode_start`` returned None despite
            # the threshold gate firing. Should be unreachable (the recent
            # subset feeds the same threshold gate), but rather than
            # raise we fall back to the earliest row in *recent* —
            # which is at most ``feed_window`` old.
            episode_start = min(obs.timestamp for obs in recent)
        events.append(
            _build_event(
                direction=direction,
                avg_delay_minutes=avg_delay,
                now=current,
                episode_start=episode_start,
            )
        )
    return events


__all__ = [
    "DELAY_THRESHOLD_MINUTES",
    "DIRECTIONS",
    "DIRECTIONS_BY_LABEL",
    "EPISODE_GAP_TOLERANCE",
    "EPISODE_LOOKBACK",
    "EVENT_CATEGORY",
    "EVENT_LINK",
    "EVENT_SOURCE",
    "EVENT_TITLE",
    "FEED_WINDOW",
    "TRIGGER_CONSECUTIVE_COUNT",
    "compute_stammstrecke_events",
]
