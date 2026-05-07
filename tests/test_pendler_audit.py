"""Tests for ``src.utils.pendler_audit``.

The audit module cross-references ``data/pendler_candidates.json`` with
``data/stations.json`` and produces a Markdown coverage report. Tests
exercise the pure functions exhaustively (loading, normalisation,
auditing, markdown rendering, stale-day capping) so the module reaches
100% line + branch coverage.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from src.utils.pendler_audit import (
    MAX_STALE_DAYS_CAP,
    AuditEntry,
    AuditReport,
    PriorityCoverage,
    audit_pendler_candidates,
    cap_stale_days,
    load_candidates,
    load_pendler_station_keys,
    render_markdown,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def _candidates_payload(*entries: dict[str, object]) -> dict[str, object]:
    return {"candidates": list(entries)}


def _station(
    name: str,
    *,
    pendler: bool = True,
    bst_id: str | None = "100",
    aliases: list[str] | None = None,
    in_vienna: bool = False,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "name": name,
        "pendler": pendler,
        "in_vienna": in_vienna,
        "aliases": aliases or [],
    }
    if bst_id is not None:
        payload["bst_id"] = bst_id
    return payload


# ---------------------------------------------------------------------------
# load_candidates
# ---------------------------------------------------------------------------


def test_load_candidates_happy_path(tmp_path: Path) -> None:
    path = tmp_path / "candidates.json"
    _write_json(
        path,
        _candidates_payload(
            {"name": "Pfaffstätten", "priority": 1, "added": "2026-05-05"},
            {
                "name": "Guntramsdorf Südbahn",
                "alternative_names": ["Guntramsdorf"],
                "priority": 2,
                "added": "2026-05-05",
                "line": "S-Bahn Südbahn",
            },
        ),
    )

    candidates = load_candidates(path)

    assert [c.name for c in candidates] == ["Pfaffstätten", "Guntramsdorf Südbahn"]
    assert candidates[1].alternative_names == ("Guntramsdorf",)
    assert candidates[1].priority == 2
    assert candidates[1].added == date(2026, 5, 5)
    assert candidates[1].line == "S-Bahn Südbahn"
    # Without explicit priority: defaults to None, never crashes.
    assert candidates[0].line is None
    assert candidates[0].alternative_names == ()


def test_load_candidates_missing_file_returns_empty(tmp_path: Path) -> None:
    assert load_candidates(tmp_path / "missing.json") == ()


def test_load_candidates_invalid_json_returns_empty(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("{not-json", encoding="utf-8")
    assert load_candidates(path) == ()


def test_load_candidates_root_not_object_returns_empty(tmp_path: Path) -> None:
    path = tmp_path / "list.json"
    _write_json(path, [{"name": "X"}])
    assert load_candidates(path) == ()


def test_load_candidates_candidates_key_missing(tmp_path: Path) -> None:
    path = tmp_path / "no-candidates.json"
    _write_json(path, {"description": "irrelevant"})
    assert load_candidates(path) == ()


def test_load_candidates_candidates_key_not_list(tmp_path: Path) -> None:
    path = tmp_path / "bad-candidates.json"
    _write_json(path, {"candidates": "oops"})
    assert load_candidates(path) == ()


def test_load_candidates_skips_non_dict_or_unnamed_entries(tmp_path: Path) -> None:
    path = tmp_path / "mixed.json"
    _write_json(
        path,
        {
            "candidates": [
                "string-entry",
                {"name": ""},
                {"name": "   "},
                {"name": "Valid Station"},
                {"name": 42},
            ]
        },
    )
    candidates = load_candidates(path)
    assert [c.name for c in candidates] == ["Valid Station"]


def test_load_candidates_alternatives_filter_non_strings(tmp_path: Path) -> None:
    path = tmp_path / "alts.json"
    _write_json(
        path,
        _candidates_payload(
            {
                "name": "Foo",
                "alternative_names": ["Bar", 7, "", "  ", "Baz"],
            }
        ),
    )
    candidates = load_candidates(path)
    assert candidates[0].alternative_names == ("Bar", "Baz")


def test_load_candidates_priority_out_of_range_drops_to_none(tmp_path: Path) -> None:
    path = tmp_path / "prio.json"
    _write_json(
        path,
        _candidates_payload(
            {"name": "A", "priority": 9},
            {"name": "B", "priority": "high"},
            {"name": "C", "priority": 1},
        ),
    )
    candidates = load_candidates(path)
    assert [c.priority for c in candidates] == [None, None, 1]


def test_load_candidates_invalid_added_field_ignored(tmp_path: Path) -> None:
    path = tmp_path / "added.json"
    _write_json(
        path,
        _candidates_payload(
            {"name": "A", "added": "not-a-date"},
            {"name": "B", "added": 42},
            {"name": "C", "added": "2026-05-05"},
        ),
    )
    candidates = load_candidates(path)
    assert [c.added for c in candidates] == [None, None, date(2026, 5, 5)]


# ---------------------------------------------------------------------------
# load_pendler_station_keys
# ---------------------------------------------------------------------------


def test_load_pendler_station_keys_collects_normalised_names(tmp_path: Path) -> None:
    path = tmp_path / "stations.json"
    _write_json(
        path,
        {
            "stations": [
                _station(
                    "Pfaffstätten",
                    aliases=["Pfaffstaetten Bahnhof"],
                ),
                _station("Wien Mitte", pendler=False, in_vienna=True),
                _station("Klosterneuburg-Kierling", aliases=["Klosterneuburg Kierling"]),
                _station("UnattachedNoBst", bst_id=None),
            ]
        },
    )

    index = load_pendler_station_keys(path)

    # Vienna station is excluded (pendler=False)
    assert "wien mitte" not in index
    # Pendler station without bst_id is excluded
    assert "unattachednobst" not in index
    # Pendler stations with bst_id contribute their normalised name + aliases
    assert "pfaffstatten" in index
    # Aliases also produce keys
    assert "klosterneuburg kierling" in index


def test_load_pendler_station_keys_missing_file_returns_empty(tmp_path: Path) -> None:
    assert load_pendler_station_keys(tmp_path / "missing.json") == {}


def test_load_pendler_station_keys_invalid_json_returns_empty(tmp_path: Path) -> None:
    path = tmp_path / "broken.json"
    path.write_text("{not-json", encoding="utf-8")
    assert load_pendler_station_keys(path) == {}


def test_load_pendler_station_keys_root_not_object(tmp_path: Path) -> None:
    path = tmp_path / "wrong.json"
    _write_json(path, [_station("X")])
    assert load_pendler_station_keys(path) == {}


def test_load_pendler_station_keys_stations_key_not_list(tmp_path: Path) -> None:
    path = tmp_path / "wrong2.json"
    _write_json(path, {"stations": "oops"})
    assert load_pendler_station_keys(path) == {}


def test_load_pendler_station_keys_skips_non_dict_entries(tmp_path: Path) -> None:
    path = tmp_path / "mixed.json"
    _write_json(
        path,
        {
            "stations": [
                "string-entry",
                {"name": "Valid", "pendler": True, "bst_id": "1"},
            ]
        },
    )
    index = load_pendler_station_keys(path)
    assert "valid" in index


def test_load_pendler_station_keys_skips_blank_names(tmp_path: Path) -> None:
    path = tmp_path / "blanks.json"
    _write_json(
        path,
        {
            "stations": [
                {"name": "", "pendler": True, "bst_id": "1"},
                {"name": "  ", "pendler": True, "bst_id": "2"},
                {"pendler": True, "bst_id": "3"},
                {"name": 7, "pendler": True, "bst_id": "4"},
                {"name": "Real", "pendler": True, "bst_id": "5"},
            ]
        },
    )
    index = load_pendler_station_keys(path)
    assert list(index) == ["real"]


def test_load_pendler_station_keys_skips_blank_bst_ids(tmp_path: Path) -> None:
    path = tmp_path / "no-bst.json"
    _write_json(
        path,
        {
            "stations": [
                {"name": "NoBst", "pendler": True, "bst_id": ""},
                {"name": "Whitespace", "pendler": True, "bst_id": "   "},
                {"name": "Numeric", "pendler": True, "bst_id": 42},
                {"name": "OK", "pendler": True, "bst_id": "100"},
            ]
        },
    )
    index = load_pendler_station_keys(path)
    assert "ok" in index
    assert "nobst" not in index
    assert "whitespace" not in index
    assert "numeric" not in index


# ---------------------------------------------------------------------------
# cap_stale_days (Sentinel)
# ---------------------------------------------------------------------------


def test_cap_stale_days_uses_min_cap() -> None:
    assert cap_stale_days(30) == 30
    assert cap_stale_days(MAX_STALE_DAYS_CAP) == MAX_STALE_DAYS_CAP
    # Above cap → clamped (Sentinel min() pattern).
    assert cap_stale_days(MAX_STALE_DAYS_CAP * 2) == MAX_STALE_DAYS_CAP


def test_cap_stale_days_floors_at_zero() -> None:
    assert cap_stale_days(-1) == 0
    assert cap_stale_days(0) == 0


# ---------------------------------------------------------------------------
# audit_pendler_candidates
# ---------------------------------------------------------------------------


def test_audit_marks_adopted_when_name_matches_pendler_station() -> None:
    candidates = load_candidates_from_payload(
        {"name": "Pfaffstätten", "priority": 1, "added": "2026-05-05"}
    )
    station_index = {"pfaffstatten": "Pfaffstätten"}
    report = audit_pendler_candidates(
        candidates,
        station_index,
        reference_date=date(2026, 5, 6),
        max_stale_days=365,
    )
    assert report.total == 1
    assert report.adopted == 1
    assert report.orphans == 0
    assert report.entries[0].adopted is True
    assert report.entries[0].matched_station == "Pfaffstätten"


def test_audit_marks_orphan_when_no_station_match() -> None:
    candidates = load_candidates_from_payload(
        {"name": "Phantom Bahnhof", "priority": 2, "added": "2025-04-01"}
    )
    report = audit_pendler_candidates(
        candidates,
        station_index={},
        reference_date=date(2026, 5, 7),
        max_stale_days=365,
    )
    assert report.adopted == 0
    assert report.orphans == 1
    entry = report.entries[0]
    assert entry.adopted is False
    assert entry.matched_station is None
    # 401 days old > 365 → stale.
    assert entry.stale is True
    assert entry.age_days == 401


def test_audit_orphan_without_added_is_not_marked_stale() -> None:
    candidates = load_candidates_from_payload({"name": "Unknown", "priority": 3})
    report = audit_pendler_candidates(
        candidates,
        station_index={},
        reference_date=date(2026, 5, 7),
        max_stale_days=365,
    )
    entry = report.entries[0]
    assert entry.adopted is False
    assert entry.stale is False
    assert entry.age_days is None


def test_audit_skips_names_that_normalise_to_empty() -> None:
    """Garbage names (e.g. only punctuation) don't crash matching.

    A candidate whose primary name normalises to an empty string forces
    the matcher to fall through to the alternative names — this exercises
    the empty-key continue branch.
    """
    candidates = load_candidates_from_payload(
        {
            "name": "---",  # normalises to empty string
            "alternative_names": ["Real Station"],
            "priority": 1,
            "added": "2026-05-05",
        }
    )
    station_index = {"real station": "Real Station"}
    report = audit_pendler_candidates(
        candidates,
        station_index,
        reference_date=date(2026, 5, 7),
        max_stale_days=365,
    )
    assert report.adopted == 1
    assert report.entries[0].matched_station == "Real Station"


def test_audit_alternative_names_count_for_match() -> None:
    candidates = load_candidates_from_payload(
        {
            "name": "Guntramsdorf Südbahn",
            "alternative_names": ["Guntramsdorf"],
            "priority": 1,
            "added": "2026-05-05",
        }
    )
    # Station appears under the alternative spelling only.
    station_index = {"guntramsdorf": "Guntramsdorf"}

    report = audit_pendler_candidates(
        candidates,
        station_index,
        reference_date=date(2026, 5, 7),
        max_stale_days=365,
    )
    assert report.adopted == 1
    assert report.entries[0].matched_station == "Guntramsdorf"


def test_audit_priority_coverage_groups_by_priority() -> None:
    candidates = load_candidates_from_payload(
        {"name": "P1Adopted", "priority": 1, "added": "2026-05-05"},
        {"name": "P1Orphan", "priority": 1, "added": "2026-05-05"},
        {"name": "P2Adopted", "priority": 2, "added": "2026-05-05"},
        {"name": "Untyped", "added": "2026-05-05"},
    )
    station_index = {"p1adopted": "P1Adopted", "p2adopted": "P2Adopted"}

    report = audit_pendler_candidates(
        candidates,
        station_index,
        reference_date=date(2026, 5, 7),
        max_stale_days=365,
    )

    coverage_by_prio = {pc.priority: pc for pc in report.priority_coverage}
    assert coverage_by_prio[1].adopted == 1
    assert coverage_by_prio[1].total == 2
    assert coverage_by_prio[2].adopted == 1
    assert coverage_by_prio[2].total == 1
    # ``None`` priority is bucketed as "unprioritised".
    assert coverage_by_prio[None].total == 1
    assert coverage_by_prio[None].adopted == 0


def test_audit_caps_max_stale_days_via_sentinel_min() -> None:
    """The Sentinel ``min()`` cap clamps the staleness horizon downward.

    The cap defends against pathological config (a multi-decade horizon
    would silently disable staleness flagging because nothing is ever
    older than 1_000_000 days). After clamping to MAX_STALE_DAYS_CAP,
    the staleness signal stays meaningful.
    """
    candidates = load_candidates_from_payload(
        {"name": "Phantom", "priority": 3, "added": "2024-01-01"}
    )
    report = audit_pendler_candidates(
        candidates,
        station_index={},
        reference_date=date(2026, 5, 7),
        max_stale_days=1_000_000,
    )
    # The reported horizon is the post-cap value, never the raw input.
    assert report.max_stale_days == MAX_STALE_DAYS_CAP


def test_audit_stale_candidate_beyond_cap_remains_flagged() -> None:
    """An orphan older than ``MAX_STALE_DAYS_CAP`` is still flagged stale.

    Capping the horizon to MAX_STALE_DAYS_CAP doesn't *disable* stale
    detection for ancient entries — anything older than the cap still
    triggers the flag.
    """
    very_old_added = date(2010, 1, 1)
    candidates = load_candidates_from_payload(
        {"name": "Ancient", "priority": 1, "added": very_old_added.isoformat()}
    )
    report = audit_pendler_candidates(
        candidates,
        station_index={},
        reference_date=date(2026, 5, 7),
        max_stale_days=1_000_000,  # capped to MAX_STALE_DAYS_CAP
    )
    # Age is way beyond the cap → stale=True.
    assert report.entries[0].age_days is not None
    assert report.entries[0].age_days > MAX_STALE_DAYS_CAP
    assert report.entries[0].stale is True


def test_audit_stale_horizon_respected() -> None:
    candidates = load_candidates_from_payload(
        {"name": "Recent", "priority": 1, "added": "2026-04-01"},
        {"name": "Old", "priority": 1, "added": "2024-01-01"},
    )
    report = audit_pendler_candidates(
        candidates,
        station_index={},
        reference_date=date(2026, 5, 7),
        max_stale_days=180,
    )
    by_name = {entry.name: entry for entry in report.entries}
    assert by_name["Recent"].stale is False
    assert by_name["Old"].stale is True


# ---------------------------------------------------------------------------
# render_markdown
# ---------------------------------------------------------------------------


def test_render_markdown_full_coverage_report() -> None:
    candidates = load_candidates_from_payload(
        {"name": "P1A", "priority": 1, "added": "2026-05-05"},
        {"name": "P1B", "priority": 1, "added": "2024-01-01"},
        {"name": "P2A", "priority": 2, "added": "2026-05-05"},
    )
    station_index = {"p1a": "P1A"}
    report = audit_pendler_candidates(
        candidates,
        station_index,
        reference_date=date(2026, 5, 7),
        max_stale_days=180,
    )

    md = render_markdown(report, reference_date=date(2026, 5, 7))

    assert "# Pendler Candidates Audit" in md
    assert "Reference date: 2026-05-07" in md
    assert "Total candidates: 3" in md
    # Adopted candidate appears in the adopted table.
    assert "P1A" in md
    # Orphan with old `added` is flagged stale.
    assert "P1B" in md
    assert "stale" in md.lower()
    # Coverage section present.
    assert "Coverage by priority" in md


def test_render_markdown_all_adopted_no_orphan_section() -> None:
    candidates = load_candidates_from_payload(
        {"name": "Only", "priority": 1, "added": "2026-05-05"}
    )
    station_index = {"only": "Only"}
    report = audit_pendler_candidates(
        candidates,
        station_index,
        reference_date=date(2026, 5, 7),
        max_stale_days=365,
    )
    md = render_markdown(report, reference_date=date(2026, 5, 7))
    assert "No outstanding orphan candidates" in md


def test_render_markdown_no_candidates() -> None:
    report = audit_pendler_candidates(
        candidates=(),
        station_index={},
        reference_date=date(2026, 5, 7),
        max_stale_days=365,
    )
    md = render_markdown(report, reference_date=date(2026, 5, 7))
    assert "No candidates configured" in md
    # Empty-report path is intentionally compact: it skips the summary
    # block since there is nothing to summarise.
    assert "Reference date: 2026-05-07" in md


def test_render_markdown_escapes_pipe_in_names() -> None:
    candidates = load_candidates_from_payload(
        {"name": "Pipe|Injection", "priority": 1, "added": "2026-05-05"}
    )
    report = audit_pendler_candidates(
        candidates,
        station_index={},
        reference_date=date(2026, 5, 7),
        max_stale_days=365,
    )
    md = render_markdown(report, reference_date=date(2026, 5, 7))
    # Pipe must be escaped so the markdown table doesn't break.
    assert "Pipe\\|Injection" in md
    assert "| Pipe|Injection |" not in md


# ---------------------------------------------------------------------------
# Public dataclass guarantees
# ---------------------------------------------------------------------------


def test_audit_entry_is_immutable() -> None:
    entry = AuditEntry(
        name="X",
        priority=1,
        added=date(2026, 5, 5),
        adopted=True,
        matched_station="X",
        stale=False,
        age_days=2,
    )
    with pytest.raises((AttributeError, TypeError)):
        entry.adopted = False  # type: ignore[misc]


def test_priority_coverage_rate_handles_zero_division() -> None:
    coverage = PriorityCoverage(priority=1, adopted=0, total=0)
    assert coverage.adoption_rate == 0.0


def test_priority_coverage_rate_normal_case() -> None:
    coverage = PriorityCoverage(priority=2, adopted=3, total=5)
    assert coverage.adoption_rate == pytest.approx(0.6)


def test_audit_report_has_orphans_signal() -> None:
    candidates = load_candidates_from_payload(
        {"name": "Phantom", "priority": 1, "added": "2026-05-05"}
    )
    report_with_orphans = audit_pendler_candidates(
        candidates,
        station_index={},
        reference_date=date(2026, 5, 7),
        max_stale_days=180,
    )
    report_clean = audit_pendler_candidates(
        candidates,
        station_index={"phantom": "Phantom"},
        reference_date=date(2026, 5, 7),
        max_stale_days=180,
    )
    assert report_with_orphans.has_orphans is True
    assert report_clean.has_orphans is False


def test_audit_report_has_stale_orphans_signal() -> None:
    candidates = load_candidates_from_payload(
        {"name": "Old", "priority": 1, "added": "2024-01-01"},
        {"name": "Fresh", "priority": 1, "added": "2026-04-01"},
    )
    report = audit_pendler_candidates(
        candidates,
        station_index={},
        reference_date=date(2026, 5, 7),
        max_stale_days=180,
    )
    assert report.stale_orphans == 1
    assert report.has_stale_orphans is True


def test_audit_report_iter_orphans_filters_correctly() -> None:
    candidates = load_candidates_from_payload(
        {"name": "Adopted", "priority": 1, "added": "2026-05-05"},
        {"name": "Phantom", "priority": 1, "added": "2026-05-05"},
    )
    report = audit_pendler_candidates(
        candidates,
        station_index={"adopted": "Adopted"},
        reference_date=date(2026, 5, 7),
        max_stale_days=365,
    )
    orphan_names = [entry.name for entry in report.iter_orphans()]
    assert orphan_names == ["Phantom"]


def test_audit_report_dataclass_fields_match_payload() -> None:
    """``AuditReport`` exposes total, adopted, orphans, stale and entries."""
    candidates = load_candidates_from_payload(
        {"name": "X", "priority": 1, "added": "2026-05-05"}
    )
    report = audit_pendler_candidates(
        candidates,
        station_index={"x": "X"},
        reference_date=date(2026, 5, 7),
        max_stale_days=365,
    )
    assert isinstance(report, AuditReport)
    assert report.total == 1
    assert report.adopted == 1
    assert report.orphans == 0
    assert report.stale_orphans == 0
    assert len(report.entries) == 1


# ---------------------------------------------------------------------------
# helper used by several tests
# ---------------------------------------------------------------------------


def load_candidates_from_payload(*entries: dict[str, object]) -> tuple:  # type: ignore[type-arg]
    """Build a tuple of ``Candidate`` objects without touching the filesystem."""
    from src.utils.pendler_audit import _coerce_candidate

    coerced = []
    for entry in entries:
        candidate = _coerce_candidate(entry)
        if candidate is not None:
            coerced.append(candidate)
    return tuple(coerced)
