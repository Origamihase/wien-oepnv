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


# ---- _classify_hbf_direction ----------------------------------------------


@pytest.mark.parametrize(
    "direction_str,expected",
    [
        # Southbound substring matches
        ("Wien Meidling", "Meidling"),
        ("Mödling", "Meidling"),
        ("Moedling Bahnhof", "Meidling"),
        ("Wiener Neustadt Hbf", "Meidling"),
        ("Payerbach-Reichenau", "Meidling"),
        ("Graz Hbf", "Meidling"),
        ("Klagenfurt Hbf", "Meidling"),
        ("Flughafen Wien Bahnhof", "Meidling"),
        ("Wolfsthal", "Meidling"),
        ("Wien Flughafen", "Meidling"),
        # Northbound substring matches
        ("Wien Floridsdorf", "Floridsdorf"),
        ("Praterstern", "Floridsdorf"),
        ("Stockerau", "Floridsdorf"),
        ("Hollabrunn", "Floridsdorf"),
        ("Retz", "Floridsdorf"),
        ("Břeclav", "Floridsdorf"),
        ("Breclav", "Floridsdorf"),
        ("Wolkersdorf", "Floridsdorf"),
        ("Mistelbach", "Floridsdorf"),
        ("Laa an der Thaya", "Floridsdorf"),
        ("Gänserndorf", "Floridsdorf"),
        ("Bratislava-Petržalka", "Floridsdorf"),
        # Northbound exact-terminus matches (no substring hit)
        ("Wien Mitte", "Floridsdorf"),
        ("Wien Mitte-Landstraße", "Floridsdorf"),
        ("Wien Mitte Bahnhof", "Floridsdorf"),
        # Unrecognised
        ("Wien Hauptbahnhof", None),  # terminus AT Hbf is irrelevant
        ("Wien Westbahnhof", None),   # different corridor
        ("", None),                    # empty
        ("   ", None),                 # whitespace-only
    ],
)
def test_classify_hbf_direction(direction_str: str, expected: str | None) -> None:
    """Direction classification covers the canonical termini per direction."""

    assert script.classify_hbf_direction(direction_str) == expected


def test_classify_hbf_direction_case_insensitive_substring() -> None:
    """Substring match operates case-insensitively for direction strings."""

    assert script.classify_hbf_direction("MÖDLING") == "Meidling"
    assert script.classify_hbf_direction("graz hbf") == "Meidling"
    assert script.classify_hbf_direction("FLORIDSDORF") == "Floridsdorf"


def test_classify_hbf_direction_substring_at_any_position() -> None:
    """Match anywhere in the string (e.g., ``Wien Meidling Hauptbahnhof``)."""

    assert script.classify_hbf_direction("Wien Meidling Bahnhof") == "Meidling"
    assert script.classify_hbf_direction("via Meidling nach Mödling") == "Meidling"


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
    """Line-pattern filter accepts S/R/REX and rejects everything else."""

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


# ---- _collect_hbf_observations --------------------------------------------


def _dep(
    *,
    name: str = "S 1",
    direction: str = "Wien Meidling",
    sched_date: str = "2026-05-15",
    sched_time: str = "08:00:00",
    rt_time: str | None = "08:00:00",
    cancelled: bool = False,
) -> dict[str, Any]:
    """Build a synthetic ``/departureBoard`` Departure entry."""

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
    return entry


def test_collect_groups_by_direction() -> None:
    """Departures split into south (Meidling) and north (Floridsdorf) buckets."""

    departures = [
        _dep(name="S 1", direction="Wien Meidling"),
        _dep(name="S 2", direction="Mödling"),
        _dep(name="REX 3", direction="Wien Floridsdorf"),
        _dep(name="S 7", direction="Wolfsthal"),
        _dep(name="REX 1", direction="Břeclav"),
    ]
    by_direction, unrecognised = script._collect_hbf_observations(departures)

    south = by_direction[script.DIRECTION_LABEL_SOUTHBOUND]
    north = by_direction[script.DIRECTION_LABEL_NORTHBOUND]

    assert {obs.name for obs in south} == {"S1", "S2", "S7"}
    assert {obs.name for obs in north} == {"REX3", "REX1"}
    assert unrecognised == {}


def test_collect_skips_non_sbahn_lines() -> None:
    """Long-distance (RJ, IC, EC) and bus entries are filtered out."""

    departures = [
        _dep(name="RJ 65", direction="Graz Hbf"),
        _dep(name="IC 533", direction="Wien Floridsdorf"),
        _dep(name="EC 24", direction="Mödling"),
        _dep(name="Bus 13A", direction="Wien Meidling"),
    ]
    by_direction, unrecognised = script._collect_hbf_observations(departures)

    assert by_direction[script.DIRECTION_LABEL_SOUTHBOUND] == []
    assert by_direction[script.DIRECTION_LABEL_NORTHBOUND] == []
    # Non-S/REX entries are line-filtered, so they don't count as
    # "unrecognised direction" — the direction is never evaluated.
    assert unrecognised == {}


def test_collect_skips_cancelled_departures() -> None:
    """Cancelled departures drop out before direction classification."""

    departures = [
        _dep(name="S 1", direction="Wien Meidling", cancelled=True),
        _dep(name="S 2", direction="Wien Meidling"),
    ]
    by_direction, _ = script._collect_hbf_observations(departures)
    assert {obs.name for obs in by_direction[script.DIRECTION_LABEL_SOUTHBOUND]} == {"S2"}


def test_collect_skips_missing_rttime() -> None:
    """Departures with no rtTime are dropped (status unknown != on-time)."""

    departures = [
        _dep(name="S 1", direction="Wien Meidling", rt_time=None),
        _dep(name="S 2", direction="Wien Meidling"),
    ]
    by_direction, _ = script._collect_hbf_observations(departures)
    assert {obs.name for obs in by_direction[script.DIRECTION_LABEL_SOUTHBOUND]} == {"S2"}


def test_collect_counts_unrecognised_termini() -> None:
    """Unknown termini surface in the returned counter for INFO logging."""

    departures = [
        _dep(name="S 1", direction="Some Unknown Place"),
        _dep(name="S 2", direction="Some Unknown Place"),
        _dep(name="S 3", direction="Another Unknown"),
    ]
    by_direction, unrecognised = script._collect_hbf_observations(departures)

    assert by_direction[script.DIRECTION_LABEL_SOUTHBOUND] == []
    assert by_direction[script.DIRECTION_LABEL_NORTHBOUND] == []
    assert unrecognised == {
        "Some Unknown Place": 2,
        "Another Unknown": 1,
    }


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
    """The direction labels match the historical CSV column values.

    The README dashboard, feed event renderer, and any external analysis
    keys on "Meidling" / "Floridsdorf" as the direction column values.
    Drifting this constant would silently break those consumers.
    """

    assert script.DIRECTION_LABEL_SOUTHBOUND == "Meidling"
    assert script.DIRECTION_LABEL_NORTHBOUND == "Floridsdorf"
    assert set(script.DIRECTION_LABELS) == {"Meidling", "Floridsdorf"}


def test_duration_window_covers_cron_interval_with_overlap() -> None:
    """Duration must exceed the 30-min cron interval for latest-wins overlap.

    Without overlap, a train scheduled near the end of one tick's window
    cannot be re-observed by the next tick (its scheduled time is in
    the past relative to the next query's ``time`` parameter), so the
    latest-wins re-observation breaks down.
    """

    assert script.DEPARTURE_BOARD_DURATION_MIN > 30
