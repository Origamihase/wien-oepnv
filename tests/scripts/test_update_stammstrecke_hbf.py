"""Tests for ``scripts/update_stammstrecke_hbf.py``.

The Hauptbahnhof-based ``/departureBoard`` monitor was introduced on
2026-05-15 to lift the ``numF=6`` ceiling that the ``/trip``-based
predecessor (``scripts/update_stammstrecke_status.py``) inherited from
the VAO API (see ``docs/reference/trip.md:34``). These tests pin the
direction-classification logic and the response parsing against
synthetic VAO payloads.

The HTTP layer is never exercised; ``_query_departure_board`` is
mocked per test. The quota counter file is redirected to ``tmp_path``
via the same convention as the legacy-script tests.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import update_stammstrecke_hbf as script  # noqa: E402
from scripts import update_stammstrecke_status as _legacy_script  # noqa: E402
from src.providers import vor as vor_provider  # noqa: E402


# ``VIENNA_TZ`` is sourced from the legacy ``/trip`` script (which the
# Hbf monitor re-uses for its shared ledger infrastructure). Tests
# import it from its actual home rather than via a re-export from the
# Hbf script — avoiding the ``from src.utils import logging as
# utils_logging`` pattern that CodeQL flagged on PR #1496.
VIENNA_TZ = _legacy_script.VIENNA_TZ


# ---- Fixtures --------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_quota_counter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Path]:
    """Redirect the VOR daily-quota counter to ``tmp_path``."""

    count_file = tmp_path / "vor_request_count.json"
    monkeypatch.setattr(vor_provider, "REQUEST_COUNT_FILE", count_file)
    vor_provider._flush_quota_cache()
    yield count_file
    vor_provider._flush_quota_cache()


# ---- _is_sbahn_line --------------------------------------------------------


@pytest.mark.parametrize(
    "name,expected",
    [
        ("S 1", True),
        ("S1", True),
        ("S 7", True),
        ("REX 3", True),
        ("REX3", True),
        ("R 81", True),
        ("R81", True),
        # Cityjet Express (``CJX``) joined the accepted set on
        # 2026-05-17 after ÖBB rebranded selected REX rolling-stock.
        # The corridor coverage is unchanged; the line label is the
        # only thing that flips.
        ("CJX 9", True),
        ("CJX9", True),
        ("cjx 5", True),
        ("RJ 65", False),  # Railjet, not Stammstrecke
        ("IC 533", False), # InterCity
        ("EC 24", False),  # EuroCity
        ("NJ 491", False), # NightJet
        ("WB 1", False),   # Westbahn private
        ("Bus 13A", False),
        ("", False),
        ("   ", False),
    ],
)
def test_is_sbahn_line(name: str, expected: bool) -> None:
    """Line-pattern filter accepts S/R/REX/CJX and rejects everything else."""

    assert script._is_sbahn_line(name) is expected


# ---- _departure_line_name --------------------------------------------------


def test_departure_line_name_prefers_top_level_name() -> None:
    """The flat ``name`` field is the canonical line designation."""

    dep = {"name": "S 1", "Product": [{"line": "S 2"}]}  # mismatch on purpose
    assert script._departure_line_name(dep) == "S 1"


def test_departure_line_name_falls_back_to_product_line() -> None:
    """Empty top-level ``name`` falls through to ``Product[].line``."""

    dep = {"name": "", "Product": [{"line": "REX 3"}]}
    assert script._departure_line_name(dep) == "REX 3"


def test_departure_line_name_falls_back_to_product_display_number() -> None:
    """Older serialiser variant uses ``displayNumber`` in lieu of ``line``."""

    dep = {"Product": [{"displayNumber": "S 2"}]}
    assert script._departure_line_name(dep) == "S 2"


def test_departure_line_name_handles_singular_product_mapping() -> None:
    """Some VAO releases serialise ``Product`` as a single dict."""

    dep = {"Product": {"line": "S 3"}}
    assert script._departure_line_name(dep) == "S 3"


def test_departure_line_name_returns_empty_on_missing_fields() -> None:
    """No usable line designation → empty string (caller drops the entry)."""

    assert script._departure_line_name({}) == ""
    assert script._departure_line_name({"name": "  "}) == ""
    assert script._departure_line_name({"Product": []}) == ""


# ---- _departure_delay_minutes ---------------------------------------------


def test_departure_delay_minutes_positive_delay() -> None:
    """Realtime departure 7 min after scheduled yields a 7-min positive delay."""

    dep = {
        "date": "2026-05-15",
        "time": "08:00:00",
        "rtTime": "08:07:00",
    }
    assert script._departure_delay_minutes(dep) == 7.0


def test_departure_delay_minutes_negative_for_early_departure() -> None:
    """Trains departing 2 min early yield -2 (kept as meaningful signal)."""

    dep = {
        "date": "2026-05-15",
        "time": "08:00:00",
        "rtTime": "07:58:00",
    }
    assert script._departure_delay_minutes(dep) == -2.0


def test_departure_delay_minutes_truncates_seconds() -> None:
    """Sub-minute seconds in ``rtTime`` are truncated (legacy invariant).

    :func:`_parse_vao_dt` drops the seconds component to avoid branching
    on ``HH:MM`` vs ``HH:MM:SS``. A 30-second delay therefore reads as
    0.0, not 0.5 — the per-sample mean operates at minute granularity
    project-wide so this is lossless in practice.
    """

    dep = {
        "date": "2026-05-15",
        "time": "08:00:00",
        "rtTime": "08:00:30",
    }
    assert script._departure_delay_minutes(dep) == 0.0


def test_departure_delay_minutes_skips_when_rttime_missing() -> None:
    """No realtime signal → ``None`` (NOT zero — that would bias the sample)."""

    dep = {"date": "2026-05-15", "time": "08:00:00"}
    assert script._departure_delay_minutes(dep) is None


def test_departure_delay_minutes_skips_cancelled_bool() -> None:
    """Cancelled departures return ``None`` (absent != delayed)."""

    dep = {
        "date": "2026-05-15",
        "time": "08:00:00",
        "rtTime": "08:05:00",
        "cancelled": True,
    }
    assert script._departure_delay_minutes(dep) is None


def test_departure_delay_minutes_skips_cancelled_string() -> None:
    """Cancelled serialised as the literal string ``"true"`` is also dropped."""

    dep = {
        "date": "2026-05-15",
        "time": "08:00:00",
        "rtTime": "08:05:00",
        "cancelled": "true",
    }
    assert script._departure_delay_minutes(dep) is None


def test_departure_delay_minutes_skips_unparseable_schedule() -> None:
    """Malformed schedule date/time → ``None`` (NOT a crash)."""

    dep = {
        "date": "not-a-date",
        "time": "08:00:00",
        "rtTime": "08:05:00",
    }
    assert script._departure_delay_minutes(dep) is None


def test_departure_delay_minutes_skips_unparseable_rttime() -> None:
    """Malformed realtime date/time → ``None`` (NOT silently coerced to 0)."""

    dep = {
        "date": "2026-05-15",
        "time": "08:00:00",
        "rtTime": "not-a-time",
    }
    assert script._departure_delay_minutes(dep) is None


def test_departure_delay_minutes_uses_rt_date_when_present() -> None:
    """``rtDate`` overrides the scheduled date for cross-midnight delays."""

    dep = {
        "date": "2026-05-15",
        "time": "23:55:00",
        "rtDate": "2026-05-16",
        "rtTime": "00:05:00",
    }
    assert script._departure_delay_minutes(dep) == 10.0


def test_departure_delay_minutes_handles_midnight_rollover_without_rt_date() -> None:
    """``rtDate`` omitted across midnight must NOT produce a ~-24 h delay.

    Pre-fix the fallback ``rt_date = sched_date`` parked the realtime
    departure at the same calendar day as the schedule, so a train
    scheduled 23:55 running ~10 min late (rtTime ``00:05``) computed
    as actual = 2026-05-15 00:05 − scheduled = 2026-05-15 23:55 =
    −1430 min. That meaningless negative value then biases the
    per-direction mean in the stats ledger. The 12 h rollover-
    heuristic adds one day to ``actual`` so the true 10-min delay
    surfaces.
    """
    dep = {
        "date": "2026-05-15",
        "time": "23:55:00",
        # rtDate intentionally omitted; only rtTime is supplied.
        "rtTime": "00:05:00",
    }
    delay = script._departure_delay_minutes(dep)
    assert delay == 10.0


def test_departure_delay_minutes_small_early_departure_not_treated_as_rollover() -> None:
    """A small (< 12 h) early departure stays negative — only large
    backwards gaps trigger the day-bump heuristic."""
    dep = {
        "date": "2026-05-15",
        "time": "12:00:00",
        # rtDate omitted; rtTime is 2 min EARLY (not a rollover).
        "rtTime": "11:58:00",
    }
    assert script._departure_delay_minutes(dep) == -2.0


# ---- _collect_hbf_observations --------------------------------------------


def _dep(
    *,
    name: str = "S 1",
    direction: str = "Wien Meidling",
    sched_date: str = "2026-05-15",
    sched_time: str = "08:00:00",
    rt_time: str | None = "08:00:00",
    cancelled: bool = False,
    track: str | None = "1",
    rt_track: str | None = None,
) -> dict[str, Any]:
    """Build a synthetic ``/departureBoard`` Departure entry.

    Defaults to ``track="1"`` so existing tests (which pre-date the
    platform-level Stammstrecke gate) still produce observations under
    the new filter. Pass ``track=None`` for the no-platform-info case
    or ``track="5"`` to exercise the non-Stammstrecke-track drop path.
    The ``rt_track`` argument overrides ``track`` when populated,
    mirroring the VAO realtime-platform-change semantics.
    """

    entry: dict[str, Any] = {
        "name": name,
        "direction": direction,
        "date": sched_date,
        "time": sched_time,
    }
    if rt_time is not None:
        entry["rtTime"] = rt_time
    if cancelled:
        entry["cancelled"] = True
    if track is not None:
        entry["track"] = track
    if rt_track is not None:
        entry["rtTrack"] = rt_track
    return entry


def test_collect_groups_by_direction() -> None:
    """Departures split into south (Meidling) and north (Praterstern) buckets.

    The geographic primary path resolves every known terminus via the
    central station directory and compares its latitude against
    :data:`HBF_REFERENCE_LATITUDE`. Wolfsthal (lat ≈ 48.14) is south
    of Hbf, Meidling and Mödling are south, Floridsdorf is north.
    """

    departures = [
        _dep(name="S 1", direction="Wien Meidling"),     # lat 48.18 → south
        _dep(name="S 2", direction="Mödling"),           # lat 48.09 → south
        _dep(name="REX 3", direction="Wien Floridsdorf"),  # lat 48.26 → north
        _dep(name="S 7", direction="Wolfsthal"),         # lat 48.14 → south
        _dep(name="S 3", direction="Stockerau"),         # lat 48.38 → north
    ]
    by_direction, _ = script._collect_hbf_observations(departures)

    south = by_direction[script.DIRECTION_LABEL_SOUTHBOUND]
    north = by_direction[script.DIRECTION_LABEL_NORTHBOUND]

    assert {obs.name for obs in south} == {"S1", "S2", "S7"}
    assert {obs.name for obs in north} == {"REX3", "S3"}


def test_collect_skips_non_sbahn_lines() -> None:
    """Long-distance (RJ, IC, EC) and bus entries are filtered out."""

    departures = [
        _dep(name="RJ 65", direction="Graz Hbf"),
        _dep(name="IC 533", direction="Wien Floridsdorf"),
        _dep(name="EC 24", direction="Mödling"),
        _dep(name="Bus 13A", direction="Wien Meidling"),
    ]
    by_direction, _ = script._collect_hbf_observations(departures)

    assert by_direction[script.DIRECTION_LABEL_SOUTHBOUND] == []
    assert by_direction[script.DIRECTION_LABEL_NORTHBOUND] == []


def test_collect_captures_cancelled_departures_as_cancelled_observations() -> None:
    """Cancelled departures are surfaced as observations with ``cancelled=True``.

    Pre-2026-05-15 the collector silently dropped cancelled departures
    so they never appeared in any statistic. The cancellation-tracking
    rework routes them through the same pending-trip dedup machinery as
    delay observations and tags them so the finalise pass can split
    them out into the dedicated cancellation CSV.
    """

    departures = [
        _dep(name="S 1", direction="Wien Meidling", cancelled=True),
        _dep(name="S 2", direction="Wien Meidling"),
    ]
    by_direction, diag = script._collect_hbf_observations(departures)
    south = by_direction[script.DIRECTION_LABEL_SOUTHBOUND]
    by_name = {obs.name: obs for obs in south}
    assert set(by_name) == {"S1", "S2"}
    assert by_name["S1"].cancelled is True
    # Cancelled observations carry a meaningless placeholder delay so
    # the dataclass parses; the finalise pass MUST NOT fold it into a
    # delay mean. Pin the value here so a future refactor cannot quietly
    # surface a non-zero placeholder into the delay ledger.
    assert by_name["S1"].delay_minutes == 0.0
    assert by_name["S2"].cancelled is False
    assert diag.cancelled_observed == 1


def test_collect_cancelled_departure_without_rttime_still_captured() -> None:
    """A cancelled departure with no ``rtTime`` MUST still be captured.

    VAO routinely omits ``rtTime`` on cancelled trains (the train will
    never depart, so there is no realtime forecast). Pre-fix the
    rtTime gate ran BEFORE the cancellation check, so a cancelled
    departure without realtime data was double-dropped — first
    silently as "no rtTime", masking the cancellation signal entirely.
    The new pipeline checks cancellation first.
    """

    departures = [
        _dep(name="S 1", direction="Wien Meidling", cancelled=True, rt_time=None),
    ]
    by_direction, diag = script._collect_hbf_observations(departures)
    south = by_direction[script.DIRECTION_LABEL_SOUTHBOUND]
    assert len(south) == 1
    assert south[0].cancelled is True
    assert diag.cancelled_observed == 1


def test_collect_skips_missing_rttime() -> None:
    """Departures with no rtTime are dropped (status unknown != on-time)."""

    departures = [
        _dep(name="S 1", direction="Wien Meidling", rt_time=None),
        _dep(name="S 2", direction="Wien Meidling"),
    ]
    by_direction, _ = script._collect_hbf_observations(departures)
    assert {obs.name for obs in by_direction[script.DIRECTION_LABEL_SOUTHBOUND]} == {"S2"}


def test_departure_is_cancelled_accepts_bool_and_string_true() -> None:
    """Both ``True`` and the literal string ``"true"`` count as cancelled.

    VAO serialises the cancellation flag in both shapes depending on
    the response variant; both must reach the cancellation ledger.
    Other truthy spellings (``"yes"``, ``"1"``, ``1``) are deliberately
    refused — the field is a contracted boolean, and accepting fuzzy
    strings would let a hand-edited / poisoned cache forge
    cancellations to flood the dashboard.
    """

    assert script._departure_is_cancelled({"cancelled": True}) is True
    assert script._departure_is_cancelled({"cancelled": "true"}) is True
    assert script._departure_is_cancelled({"cancelled": "TRUE"}) is True
    assert script._departure_is_cancelled({"cancelled": "  true  "}) is True
    # Fuzzy spellings MUST NOT count.
    assert script._departure_is_cancelled({"cancelled": "yes"}) is False
    assert script._departure_is_cancelled({"cancelled": "1"}) is False
    assert script._departure_is_cancelled({"cancelled": 1}) is False
    assert script._departure_is_cancelled({}) is False


def test_collect_geographic_resolution_overrides_track_assignment() -> None:
    """A known terminus's latitude wins over the track-based fallback.

    Wien Floridsdorf (lat ≈ 48.256) is geographically north of Hbf, so
    the observation lands in the NORTHBOUND bucket even when the train
    departs from Bahnsteig 1 (which the fallback would otherwise
    label as SOUTHBOUND). Symmetric for Wien Meidling on Bahnsteig 2.
    """

    departures = [
        # Northern terminus on Bahnsteig 1 → geographic resolution wins.
        _dep(name="REX 3", direction="Wien Floridsdorf", track="1"),
        # Southern terminus on Bahnsteig 2 → geographic resolution wins.
        _dep(name="S 1", direction="Wien Meidling", track="2"),
    ]
    by_direction, _ = script._collect_hbf_observations(departures)
    south = by_direction[script.DIRECTION_LABEL_SOUTHBOUND]
    north = by_direction[script.DIRECTION_LABEL_NORTHBOUND]

    assert {obs.name for obs in south} == {"S1"}
    assert {obs.name for obs in north} == {"REX3"}


def test_collect_falls_back_to_track_for_unknown_terminus() -> None:
    """Termini absent from the central directory fall back to the track.

    Uses fictional terminus names guaranteed to be absent from
    ``data/stations.json``. The previous fixtures (``Břeclav`` /
    ``Bratislava-Petržalka``) were absorbed into the directory by the
    HAFAS-enrichment cron — both gained coordinates that put them on
    the geographic-resolver branch instead of the track-fallback
    branch this test exercises. With the hybrid resolver, the
    surviving track-trunk determines the direction for any terminus
    without a directory hit. No departure is dropped for an
    unrecognised terminus.
    """

    departures = [
        # Unknown terminus, track 1 → SOUTHBOUND per fallback.
        _dep(name="REX 1", direction="Fictional Test Terminus A", track="1"),
        # Unknown terminus, track 2 → NORTHBOUND per fallback.
        _dep(name="REX 2", direction="Fictional Test Terminus B", track="2"),
        # Empty direction string, track 1 → SOUTHBOUND per fallback;
        # pre-refactor this row was dropped as an "unrecognised
        # terminus" rather than reaching either bucket.
        _dep(name="S 8", direction="", track="1"),
    ]
    by_direction, _ = script._collect_hbf_observations(departures)
    south = by_direction[script.DIRECTION_LABEL_SOUTHBOUND]
    north = by_direction[script.DIRECTION_LABEL_NORTHBOUND]

    assert {obs.name for obs in south} == {"REX1", "S8"}
    assert {obs.name for obs in north} == {"REX2"}


def test_collect_diagnostics_have_no_unrecognised_field() -> None:
    """The diagnostics dataclass no longer carries the unrecognised counter.

    Pinning the removal so a future refactor cannot silently restore the
    legacy substring/whitelist surface; the hybrid resolver is
    exhaustive and there is no third "unrecognised" bucket anymore.
    """

    departures = [
        _dep(name="S 1", direction="Some Unknown Place", track="1"),
        _dep(name="S 2", direction="Another Unknown", track="2"),
    ]
    _, diag = script._collect_hbf_observations(departures)
    assert not hasattr(diag, "unrecognised_terminus")


def test_collect_handles_non_mapping_entries() -> None:
    """Non-dict entries in the Departure list are silently skipped."""

    departures: list[Any] = [
        None,
        "not-a-dict",
        42,
        _dep(name="S 1", direction="Wien Meidling"),
    ]
    by_direction, _ = script._collect_hbf_observations(departures)
    assert {obs.name for obs in by_direction[script.DIRECTION_LABEL_SOUTHBOUND]} == {"S1"}


def test_collect_computes_delay_from_rt_time() -> None:
    """The delay value reflects rtTime - scheduled, at minute granularity.

    Seconds are truncated by :func:`_parse_vao_dt` (legacy invariant: the
    per-sample mean operates at minute resolution; sub-minute seconds
    are dropped to avoid ``strptime`` branching).
    """

    departures = [
        _dep(
            name="S 1",
            direction="Wien Meidling",
            sched_time="08:00:00",
            rt_time="08:07:00",  # 7 min late
        ),
    ]
    by_direction, _ = script._collect_hbf_observations(departures)
    south = by_direction[script.DIRECTION_LABEL_SOUTHBOUND]
    assert len(south) == 1
    assert south[0].delay_minutes == 7.0


def test_collect_canonicalises_line_name() -> None:
    """Line names are normalised (whitespace removed, upper-cased)."""

    departures = [
        _dep(name="s 1", direction="Wien Meidling"),
        _dep(name=" S2 ", direction="Wien Meidling"),
        _dep(name="REX  3", direction="Wien Meidling"),
    ]
    by_direction, _ = script._collect_hbf_observations(departures)
    south = by_direction[script.DIRECTION_LABEL_SOUTHBOUND]
    assert {obs.name for obs in south} == {"S1", "S2", "REX3"}


# ---- _track_trunk + Stammstrecke-track gate ------------------------------


@pytest.mark.parametrize(
    "track_value,expected",
    [
        # Plain numerics
        ("1", "1"),
        ("2", "2"),
        ("5", "5"),
        ("12", "12"),
        # Zero-padded variants
        ("01", "1"),
        ("02", "2"),
        ("003", "3"),
        # Sub-platform suffixes
        ("1A", "1"),
        ("1B", "1"),
        ("2A", "2"),
        ("10A-B", "10"),
        # Whitespace padding
        ("  1  ", "1"),
        # Trailing modifiers
        ("1 (Tief)", "1"),
        # Non-numeric / empty / None
        ("", None),
        ("   ", None),
        ("Gleis A", None),
        ("-1", None),       # leading non-digit
        (None, None),
        (True, None),       # bool guarded (would otherwise yield "1")
        (False, None),
        (1, "1"),           # int is coerced to "1"
        (2, "2"),
    ],
)
def test_track_trunk(track_value: object, expected: str | None) -> None:
    """Track-trunk normaliser handles every VAO-documented variant."""

    assert script._track_trunk(track_value) == expected


def test_extract_track_string_prefers_rttrack() -> None:
    """``rtTrack`` overrides scheduled ``track`` when populated."""

    dep = {"track": "2", "rtTrack": "5"}
    assert script._extract_track_string(dep) == "5"


def test_extract_track_string_falls_back_to_scheduled() -> None:
    """Empty/missing ``rtTrack`` falls through to ``track``."""

    assert script._extract_track_string({"track": "1"}) == "1"
    assert script._extract_track_string({"track": "1", "rtTrack": ""}) == "1"
    assert script._extract_track_string({"track": "1", "rtTrack": None}) == "1"


def test_extract_track_string_accepts_journey_detail_variants() -> None:
    """``depTrack`` / ``rtDepTrack`` are the journey-detail spellings.

    Per the VAO Handbuch §11 example responses these alternates can
    appear instead of the station-board ``track`` / ``rtTrack`` shape.
    """

    assert script._extract_track_string({"depTrack": "1"}) == "1"
    assert (
        script._extract_track_string({"depTrack": "5", "rtDepTrack": "1"})
        == "1"
    )


def test_extract_track_string_returns_none_when_all_missing() -> None:
    """No track / rtTrack / depTrack / rtDepTrack ⇒ ``None``."""

    assert script._extract_track_string({}) is None
    assert script._extract_track_string({"track": ""}) is None
    assert script._extract_track_string({"track": None}) is None


def test_collect_keeps_stammstrecke_platform_1() -> None:
    """Departure on Bahnsteig 1 (Stammstrecke north) is kept."""

    departures = [
        _dep(name="S 1", direction="Wien Floridsdorf", track="1"),
    ]
    by_direction, diag = script._collect_hbf_observations(departures)
    assert len(by_direction[script.DIRECTION_LABEL_NORTHBOUND]) == 1
    assert diag.dropped_no_track == 0
    assert diag.dropped_non_stammstrecke_track == 0


def test_collect_keeps_stammstrecke_platform_2() -> None:
    """Departure on Bahnsteig 2 (Stammstrecke south) is kept."""

    departures = [
        _dep(name="S 2", direction="Mödling", track="2"),
    ]
    by_direction, diag = script._collect_hbf_observations(departures)
    assert len(by_direction[script.DIRECTION_LABEL_SOUTHBOUND]) == 1
    assert diag.dropped_non_stammstrecke_track == 0


def test_collect_drops_non_stammstrecke_platform() -> None:
    """A REX departing track 5 (Ostbahn) is dropped from the Stammstrecke sample."""

    departures = [
        _dep(name="REX 8", direction="Wiener Neustadt", track="5"),
    ]
    by_direction, diag = script._collect_hbf_observations(departures)
    assert all(not v for v in by_direction.values())
    assert diag.dropped_non_stammstrecke_track == 1
    assert diag.dropped_no_track == 0


def test_collect_drops_when_track_missing() -> None:
    """No ``track``/``rtTrack`` ⇒ conservative drop + counter."""

    departures = [
        _dep(name="S 1", direction="Wien Meidling", track=None),
    ]
    by_direction, diag = script._collect_hbf_observations(departures)
    assert all(not v for v in by_direction.values())
    assert diag.dropped_no_track == 1
    assert diag.dropped_non_stammstrecke_track == 0


def test_collect_rttrack_overrides_scheduled() -> None:
    """Scheduled Bahnsteig 1, but rtTrack moved the train to track 5 → drop.

    Mid-disruption platform change semantics: the realtime track is
    authoritative for the Stammstrecke gate. A train originally
    scheduled on Bahnsteig 1 that VAO announces has been moved to
    track 5 mid-tick must NOT contribute to the Stammstrecke sample
    (the platform change implies the train isn't using the
    Stammstrecke right now).
    """

    departures = [
        _dep(name="S 1", direction="Wien Floridsdorf", track="1", rt_track="5"),
    ]
    by_direction, diag = script._collect_hbf_observations(departures)
    assert all(not v for v in by_direction.values())
    assert diag.dropped_non_stammstrecke_track == 1


def test_collect_rttrack_recovers_to_stammstrecke() -> None:
    """Scheduled track 5, but rtTrack moved to Bahnsteig 2 → keep.

    The mirror image of the override-to-drop case: a platform change
    that BRINGS a train onto the Stammstrecke is honoured the same
    way. Realtime data wins.
    """

    departures = [
        _dep(name="S 2", direction="Mödling", track="5", rt_track="2"),
    ]
    by_direction, diag = script._collect_hbf_observations(departures)
    assert len(by_direction[script.DIRECTION_LABEL_SOUTHBOUND]) == 1
    assert diag.dropped_non_stammstrecke_track == 0


def test_collect_accepts_sub_platform_variants() -> None:
    """Sub-platform suffixes (``"1A"``, ``"2B"``, ``"01"``) are accepted."""

    departures = [
        _dep(name="S 1", direction="Wien Floridsdorf", track="1A"),
        _dep(name="S 2", direction="Mödling", track="2B"),
        _dep(name="S 3", direction="Wien Meidling", track="01"),
    ]
    by_direction, diag = script._collect_hbf_observations(departures)
    total = sum(len(v) for v in by_direction.values())
    assert total == 3
    assert diag.dropped_non_stammstrecke_track == 0


# ---- _query_departure_board ----------------------------------------------


def test_query_departure_board_parses_modern_flat_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Response with top-level ``Departure`` list is parsed correctly."""

    captured_url: dict[str, str] = {}

    class _FakeResponse:
        status_code = 200
        content = b'{"Departure": [{"name": "S 1"}, {"name": "S 2"}]}'

    def fake_request_safe(*args: Any, **kwargs: Any) -> _FakeResponse:
        captured_url["endpoint"] = args[1] if len(args) >= 2 else kwargs.get("url", "")
        return _FakeResponse()

    monkeypatch.setattr(script, "request_safe", fake_request_safe)
    monkeypatch.setattr(script, "_charge_one_request", lambda when: None)

    when = datetime(2026, 5, 15, 8, 0, tzinfo=VIENNA_TZ)
    result = script._query_departure_board(session=object(), when=when)
    assert len(result) == 2
    assert result[0]["name"] == "S 1"
    assert "departureBoard" in captured_url["endpoint"]


def test_query_departure_board_parses_nested_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Response with ``DepartureBoard`` wrapper is parsed correctly."""

    class _FakeResponse:
        status_code = 200
        content = (
            b'{"DepartureBoard": {"Departure": '
            b'[{"name": "REX 3"}, {"name": "S 1"}]}}'
        )

    monkeypatch.setattr(
        script,
        "request_safe",
        lambda *args, **kwargs: _FakeResponse(),
    )
    monkeypatch.setattr(script, "_charge_one_request", lambda when: None)

    when = datetime(2026, 5, 15, 8, 0, tzinfo=VIENNA_TZ)
    result = script._query_departure_board(session=object(), when=when)
    assert len(result) == 2
    assert result[0]["name"] == "REX 3"


def test_query_departure_board_handles_single_departure_object(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Single-element response serialised as bare object is normalised."""

    class _FakeResponse:
        status_code = 200
        content = b'{"Departure": {"name": "S 1"}}'

    monkeypatch.setattr(
        script,
        "request_safe",
        lambda *args, **kwargs: _FakeResponse(),
    )
    monkeypatch.setattr(script, "_charge_one_request", lambda when: None)

    when = datetime(2026, 5, 15, 8, 0, tzinfo=VIENNA_TZ)
    result = script._query_departure_board(session=object(), when=when)
    assert result == [{"name": "S 1"}]


def test_query_departure_board_returns_empty_when_field_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Response without ``Departure`` field yields an empty list (no error)."""

    class _FakeResponse:
        status_code = 200
        content = b'{}'

    monkeypatch.setattr(
        script,
        "request_safe",
        lambda *args, **kwargs: _FakeResponse(),
    )
    monkeypatch.setattr(script, "_charge_one_request", lambda when: None)

    when = datetime(2026, 5, 15, 8, 0, tzinfo=VIENNA_TZ)
    result = script._query_departure_board(session=object(), when=when)
    assert result == []


def test_query_departure_board_raises_on_http_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """4xx/5xx responses produce a :class:`requests.HTTPError`."""

    import requests

    class _FakeResponse:
        status_code = 500
        content = b'{"errorCode": "API_GEN"}'

    monkeypatch.setattr(
        script,
        "request_safe",
        lambda *args, **kwargs: _FakeResponse(),
    )
    monkeypatch.setattr(script, "_charge_one_request", lambda when: None)

    when = datetime(2026, 5, 15, 8, 0, tzinfo=VIENNA_TZ)
    with pytest.raises(requests.HTTPError):
        script._query_departure_board(session=object(), when=when)


def test_query_departure_board_raises_on_unparseable_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Malformed JSON body raises :class:`ValueError` (not RecursionError)."""

    class _FakeResponse:
        status_code = 200
        content = b'{ not valid json'

    monkeypatch.setattr(
        script,
        "request_safe",
        lambda *args, **kwargs: _FakeResponse(),
    )
    monkeypatch.setattr(script, "_charge_one_request", lambda when: None)

    when = datetime(2026, 5, 15, 8, 0, tzinfo=VIENNA_TZ)
    with pytest.raises(ValueError):
        script._query_departure_board(session=object(), when=when)


# ---- Sanity / pinning -----------------------------------------------------


def test_hauptbahnhof_id_matches_stations_directory() -> None:
    """The pinned Hbf VOR ID matches what's in ``data/stations.json``.

    Drift between this constant and the directory entry would mean the
    script polls a stale ID — this test catches a future directory
    rename or ID rewrite.
    """

    import json

    stations_path = REPO_ROOT / "data" / "stations.json"
    if not stations_path.exists():  # pragma: no cover - fresh clone
        pytest.skip("stations.json not present in this checkout")
    with stations_path.open(encoding="utf-8") as fh:
        directory = json.load(fh)
    stations = directory.get("stations", directory) if isinstance(directory, dict) else directory

    hits = [s for s in stations if s.get("name") == "Wien Hauptbahnhof"]
    assert hits, "Wien Hauptbahnhof missing from stations.json"
    assert hits[0].get("vor_id") == script.HAUPTBAHNHOF_VOR_ID


def test_direction_labels_match_csv_convention() -> None:
    """The direction labels match the CSV column values.

    The README dashboard, feed event renderer, and any external analysis
    key on "Meidling" / "Praterstern" as the direction column values
    since the 2026-05-15 rename (the legacy ``Floridsdorf`` value is
    accepted as an alias by the feed renderer's DIRECTIONS_BY_LABEL
    lookup but never produced by the canonical write path). Drifting
    these constants would silently break those consumers.
    """

    assert script.DIRECTION_LABEL_SOUTHBOUND == "Meidling"
    assert script.DIRECTION_LABEL_NORTHBOUND == "Praterstern"
    assert script.LEGACY_DIRECTION_LABEL_NORTHBOUND == "Floridsdorf"
    assert set(script.DIRECTION_LABELS) == {"Meidling", "Praterstern"}


def test_duration_window_covers_cron_interval_with_overlap() -> None:
    """Duration must exceed the 30-min cron interval for latest-wins overlap.

    Without overlap, a train scheduled near the end of one tick's window
    cannot be re-observed by the next tick (its scheduled time is in
    the past relative to the next query's ``time`` parameter), so the
    latest-wins re-observation breaks down.
    """

    assert script.DEPARTURE_BOARD_DURATION_MIN > 30
