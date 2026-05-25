"""Tests for ``_find_identity_field_conflicts``.

Pin the 2026-05-16 Innenstadt-U-Bahn DIVA-drift regression: two
stations sharing the same ``wl_diva`` (or any of ``vor_id`` /
``bst_id`` / ``bst_code``) must trip a blocking ``IdentityFieldConflict``
even when they have different names and slightly different
coordinates.

Distinct from ``test_stations_validation_cross_id.py`` which exercises
alias-vs-identity collisions; this module exercises identity-vs-identity.
"""
from __future__ import annotations

import json
from pathlib import Path

from src.utils.stations_validation import validate_stations


def _make_entry(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "name": "Default",
        "latitude": 48.2,
        "longitude": 16.3,
        "in_vienna": True,
        "pendler": False,
        "source": "wl",
        "aliases": ["Default"],
    }
    base.update(overrides)
    return base


def test_wl_diva_duplicate_flagged_even_with_distinct_names(tmp_path: Path) -> None:
    """The Innenstadt-U-Bahn failure mode: ``Herrengasse`` (google_places)
    and ``Wien Herrengasse (WL)`` (wl) sharing DIVA 60200506 must be
    detected even though the names normalise differently and the
    coordinates differ by ~12 m (below the duplicate-coord bucket
    boundary of 5 decimal places)."""
    path = tmp_path / "stations.json"
    entries = [
        _make_entry(
            name="Herrengasse",
            wl_diva="60200506",
            source="google_places",
            latitude=48.209648,
            longitude=16.366380,
            aliases=["Herrengasse"],
        ),
        _make_entry(
            name="Wien Herrengasse (WL)",
            wl_diva="60200506",
            source="wl",
            latitude=48.209750,
            longitude=16.365304,
            aliases=["Wien Herrengasse (WL)"],
        ),
    ]
    path.write_text(json.dumps(entries), encoding="utf-8")

    report = validate_stations(path)

    # Sanity: the geographic-duplicates and cross-station-id checks
    # both miss this case under the conditions the regression
    # was discovered with.
    assert report.duplicates == ()
    assert report.cross_station_id_issues == ()

    # The new check fires.
    assert len(report.identity_field_conflicts) == 1
    conflict = report.identity_field_conflicts[0]
    assert conflict.field == "wl_diva"
    assert conflict.value == "60200506"
    assert set(conflict.names) == {"Herrengasse", "Wien Herrengasse (WL)"}
    assert report.has_issues


def test_vor_id_duplicate_flagged(tmp_path: Path) -> None:
    path = tmp_path / "stations.json"
    entries = [
        _make_entry(
            name="Station A",
            vor_id="430420200",
            bst_id="200",
            bst_code="A1",
            source="oebb,osm",
            aliases=["A"],
        ),
        _make_entry(
            name="Station B",
            vor_id="430420200",
            bst_id="201",
            bst_code="A2",
            source="oebb,osm",
            aliases=["B"],
        ),
    ]
    path.write_text(json.dumps(entries), encoding="utf-8")

    report = validate_stations(path)

    assert any(
        c.field == "vor_id" and c.value == "430420200"
        for c in report.identity_field_conflicts
    )


def test_bst_id_and_bst_code_duplicates_flagged(tmp_path: Path) -> None:
    """Two stations colliding on both ``bst_id`` and ``bst_code`` produce
    two distinct conflicts — one per field."""
    path = tmp_path / "stations.json"
    entries = [
        _make_entry(
            name="A",
            bst_id="42",
            bst_code="Aw",
            source="oebb,osm",
            aliases=["A"],
        ),
        _make_entry(
            name="B",
            bst_id="42",
            bst_code="Aw",
            source="oebb,osm",
            aliases=["B"],
        ),
    ]
    path.write_text(json.dumps(entries), encoding="utf-8")

    report = validate_stations(path)
    fields = {c.field for c in report.identity_field_conflicts}
    assert fields == {"bst_id", "bst_code"}


def test_eva_nr_duplicate_flagged(tmp_path: Path) -> None:
    """Two records sharing the UIC ``eva_nr`` but with different
    ``bst_code`` / ``bst_id`` — the Handelskai pattern, where an
    ``oebb_geonetz`` Betriebsstelle record duplicated the canonical
    ``Wien Handelskai`` Verkehrsstation — must be flagged. The pair slips
    past the other four identifier keys because those differ."""
    path = tmp_path / "stations.json"
    entries = [
        _make_entry(
            name="Handelskai",
            eva_nr="8101934",
            bst_code="Nw  H2",
            bst_id="1586",
            source="oebb_geonetz,osm",
            aliases=["Handelskai"],
            latitude=48.242143,
            longitude=16.385980,
        ),
        _make_entry(
            name="Wien Handelskai",
            eva_nr="8101934",
            bst_code="Hak",
            bst_id="779",
            wl_diva="60201705",
            source="hafas,oebb,oebb_geonetz,osm,wl",
            aliases=["Wien Handelskai"],
            latitude=48.241418,
            longitude=16.384869,
        ),
    ]
    path.write_text(json.dumps(entries), encoding="utf-8")

    report = validate_stations(path)
    eva_conflicts = [
        c for c in report.identity_field_conflicts if c.field == "eva_nr"
    ]
    assert len(eva_conflicts) == 1
    assert eva_conflicts[0].value == "8101934"
    assert set(eva_conflicts[0].names) == {"Handelskai", "Wien Handelskai"}
    assert report.has_issues


def test_unique_identifiers_produce_no_conflicts(tmp_path: Path) -> None:
    path = tmp_path / "stations.json"
    entries = [
        _make_entry(name="A", wl_diva="60200001", aliases=["A"]),
        _make_entry(name="B", wl_diva="60200002", aliases=["B"]),
        _make_entry(name="C", wl_diva="60200003", aliases=["C"]),
    ]
    path.write_text(json.dumps(entries), encoding="utf-8")

    report = validate_stations(path)
    assert report.identity_field_conflicts == ()


def test_none_and_whitespace_values_ignored(tmp_path: Path) -> None:
    """``wl_diva = None`` or `` `` must not falsely fire the conflict
    check — only structurally meaningful values count."""
    path = tmp_path / "stations.json"
    entries = [
        _make_entry(name="A", wl_diva=None, aliases=["A"]),
        _make_entry(name="B", wl_diva=None, aliases=["B"]),
        _make_entry(name="C", wl_diva="   ", aliases=["C"]),
        _make_entry(name="D", wl_diva="   ", aliases=["D"]),
    ]
    path.write_text(json.dumps(entries), encoding="utf-8")

    report = validate_stations(path)
    assert report.identity_field_conflicts == ()


def test_three_way_collision_yields_single_conflict(tmp_path: Path) -> None:
    path = tmp_path / "stations.json"
    entries = [
        _make_entry(name=f"S{i}", wl_diva="60200506", aliases=[f"S{i}"])
        for i in range(3)
    ]
    path.write_text(json.dumps(entries), encoding="utf-8")

    report = validate_stations(path)
    assert len(report.identity_field_conflicts) == 1
    assert len(report.identity_field_conflicts[0].identifiers) == 3


def test_real_data_has_no_identity_conflicts() -> None:
    """Regression test against the committed ``data/stations.json``.

    The 2026-05-16 heal (PR #1539) merged the ten ``google_places`` <->
    ``wl`` DIVA-sharing stubs into their masters and added the
    ``wl_diva`` index in ``scripts/update_wl_stations.py:merge_into_
    stations`` so they cannot reappear via cron. This test pins the
    invariant.
    """
    report = validate_stations(Path("data/stations.json"))
    assert report.identity_field_conflicts == (), (
        f"Identity-field conflicts reappeared in data/stations.json: "
        f"{report.identity_field_conflicts}"
    )


def test_has_issues_flips_on_identity_conflict(tmp_path: Path) -> None:
    """``ValidationReport.has_issues`` must include identity_field_conflicts."""
    path = tmp_path / "stations.json"
    entries = [
        _make_entry(name="A", wl_diva="60200506", aliases=["A"]),
        _make_entry(name="B", wl_diva="60200506", aliases=["B"]),
    ]
    path.write_text(json.dumps(entries), encoding="utf-8")

    report = validate_stations(path)
    assert report.identity_field_conflicts != ()
    assert report.has_issues is True


def test_markdown_renders_identity_conflict_section(tmp_path: Path) -> None:
    path = tmp_path / "stations.json"
    # Distinct coordinates avoid Geographic-duplicates noise so the
    # rendered Markdown isolates the identity-conflict section.
    entries = [
        _make_entry(name="A", wl_diva="60200506", aliases=["A"], latitude=48.20),
        _make_entry(name="B", wl_diva="60200506", aliases=["B"], latitude=48.21),
    ]
    path.write_text(json.dumps(entries), encoding="utf-8")

    report = validate_stations(path)
    rendered = report.to_markdown()

    assert "## Identity-Field-Konflikte" in rendered
    # ``_safe_md`` HTML-escapes ``_`` to ``\_`` per CommonMark protocol.
    assert "wl\\_diva" in rendered
    assert "60200506" in rendered
