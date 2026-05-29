"""Regression tests for the ``_load_existing_station_entries`` classifier.

The classifier decides whether an existing station entry came from the
ÖBB ``Verzeichnis der Verkehrsstationen`` Excel workbook (and therefore
needs to round-trip through the upcoming Excel re-pull) or is a manual /
synthetic entry that must survive the rebuild verbatim.

Pre-fix the classifier used a naive substring check ``"oebb" in source``
which mis-matched the canonical ``oebb`` token against any source value
that *contained* the substring — most notably ``oebb_geonetz`` (the
GeoNetz EVA/IFOPT enrichment provenance token added in PR β,
2026-05-21). Synthetic entries whose ``source`` listed ``oebb_geonetz``
but never the bare ``oebb`` token (``Wien Hauptbahnhof`` / ``Wien
Kaiserebersdorf``, both with ``bst_id`` in the 900xxx synthetic range
that the live ÖBB workbook does not carry) were therefore bucketed as
ÖBB-Excel entries, landed in ``mapping`` instead of
``manual_stations``, and dropped out of ``data/stations.json`` on every
weekly cron tick because the Excel re-pull couldn't re-emit a
``bst_id`` it had never seen.

The fix at ``scripts/update_station_directory.py:_load_existing_station_entries``
switches to a token-based check that splits the comma-separated source
field and looks for the exact ``oebb`` token. The cases pinned below
cover the substring trap, the legitimate ÖBB-with-extras case, the
empty-source backward-compat path, and the list-source variant.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.update_station_directory import _load_existing_station_entries


def _write_stations(path: Path, entries: list[dict[str, object]]) -> None:
    path.write_text(
        json.dumps({"stations": entries}, ensure_ascii=False),
        encoding="utf-8",
    )


def test_synthetic_entry_with_oebb_geonetz_provenance_is_manual(tmp_path: Path) -> None:
    """``oebb_geonetz`` substring must NOT classify the entry as ÖBB.

    Pre-fix the substring check at ``_load_existing_station_entries`` mis-
    matched ``oebb_geonetz`` against the bare ``oebb`` token. The synthetic
    Wien Hauptbahnhof entry (the canonical regression vector — see the
    post-mortem in commit ``484c1f6``) therefore vanished from the
    rebuilt directory on every cron tick because the Excel re-pull
    couldn't re-emit its 900xxx synthetic ``bst_id``.
    """
    path = tmp_path / "stations.json"
    _write_stations(
        path,
        [
            {
                "bst_id": 900100,
                "bst_code": "900100",
                "name": "Wien Hauptbahnhof",
                "source": "google_places,oebb_geonetz,vor,wl",
                "in_vienna": True,
                "pendler": False,
            }
        ],
    )

    mapping, manual = _load_existing_station_entries(path)

    assert "900100" not in mapping, (
        "Wien Hauptbahnhof (synthetic) must NOT land in the ÖBB-Excel "
        "mapping — its 900100 bst_id is not in the live workbook so the "
        "Excel re-pull cannot re-emit it; dropping it from the manual list "
        "is the exact bug the token-based classifier fixes."
    )
    assert any(e.get("name") == "Wien Hauptbahnhof" for e in manual), (
        "Wien Hauptbahnhof (synthetic) must land in the manual_stations "
        "list so the wrapper can append it verbatim to the rebuilt set."
    )


def test_synthetic_kaiserebersdorf_with_oebb_geonetz_is_manual(tmp_path: Path) -> None:
    """``Wien Kaiserebersdorf`` — sibling synthetic entry to Hauptbahnhof.

    Same substring trap as :func:`test_synthetic_entry_with_oebb_geonetz_provenance_is_manual`
    but with ``source=oebb_geonetz,vor`` (no leading provider names). Pins
    that the token-based classifier rejects the substring match at the
    very start of the source string too.
    """
    path = tmp_path / "stations.json"
    _write_stations(
        path,
        [
            {
                "bst_id": 900105,
                "bst_code": "900105",
                "name": "Wien Kaiserebersdorf",
                "source": "oebb_geonetz,vor",
                "in_vienna": True,
                "pendler": False,
            }
        ],
    )

    mapping, manual = _load_existing_station_entries(path)

    assert "900105" not in mapping
    assert any(e.get("name") == "Wien Kaiserebersdorf" for e in manual)


def test_real_oebb_entry_with_extras_stays_in_mapping(tmp_path: Path) -> None:
    """A real ÖBB-Excel entry that also picked up enrichment tokens.

    A legitimate ÖBB entry like Wiener Neustadt Hbf carries
    ``source=oebb,oebb_geonetz,osm`` — the bare ``oebb`` token IS present
    (alongside enrichment provenance). The classifier must keep this in
    ``mapping`` so the Excel re-pull re-emits it; the bare ``oebb``
    token is the canonical "this came from the Excel" signal.
    """
    path = tmp_path / "stations.json"
    _write_stations(
        path,
        [
            {
                "bst_id": 1499,
                "bst_code": "WrNs",
                "name": "Wiener Neustadt Hauptbahnhof",
                "source": "oebb,oebb_geonetz,osm",
                "in_vienna": False,
                "pendler": True,
            }
        ],
    )

    mapping, manual = _load_existing_station_entries(path)

    assert "1499" in mapping, (
        "A real ÖBB-Excel entry (bare ``oebb`` token present in the source "
        "set) must stay in the mapping so the next Excel re-pull keeps it."
    )
    assert not any(e.get("name") == "Wiener Neustadt Hauptbahnhof" for e in manual)


def test_real_oebb_entry_without_bare_oebb_token_stays_in_mapping(tmp_path: Path) -> None:
    """A real workbook station that lost its bare ``oebb`` source token → mapping.

    Regression for the duplicate-``bst_id`` bug. ``Siebenhirten`` is a
    real ÖBB Betriebsstelle (``bst_id=1371``) the Excel workbook
    re-emits, but its committed ``source`` is ``"oebb_geonetz,osm"`` —
    the bare ``oebb`` token was lost across enrichment cycles. The
    pre-fix token check classified it ``manual``, so the rebuild
    appended it verbatim *alongside* the fresh Excel extract's own
    ``bst_id=1371`` row → two entries sharing one ``bst_id`` → a
    ``validate_stations`` identity-conflict failure. Because ``1371`` is
    NOT in the synthetic VOR range, it must land in ``mapping`` so the
    fresh extract merges into it instead of duplicating it.
    """
    path = tmp_path / "stations.json"
    _write_stations(
        path,
        [
            {
                "bst_id": 1371,
                "bst_code": "Mb  H2H",
                "name": "Siebenhirten",
                "source": "oebb_geonetz,osm",
                "eva_nr": "8101523",
            }
        ],
    )

    mapping, manual = _load_existing_station_entries(path)

    assert "1371" in mapping, (
        "A real ÖBB workbook station (non-synthetic bst_id) whose source "
        "lost the bare ``oebb`` token must stay in mapping so the Excel "
        "re-pull merges into it rather than emitting a duplicate."
    )
    assert not any(e.get("name") == "Siebenhirten" for e in manual)


def test_bare_oebb_source_string_stays_in_mapping(tmp_path: Path) -> None:
    """The pre-PR-β canonical ÖBB source — ``source="oebb"`` exactly.

    The existing fast-path at ``elif source != "oebb"`` already handles
    this; the test pins that the token-based replacement preserves that
    path so legacy entries don't silently flip to manual.
    """
    path = tmp_path / "stations.json"
    _write_stations(
        path,
        [
            {
                "bst_id": 1234,
                "bst_code": "ABCD",
                "name": "Legacy ÖBB Entry",
                "source": "oebb",
            }
        ],
    )

    mapping, manual = _load_existing_station_entries(path)

    assert "1234" in mapping
    assert manual == []


def test_empty_source_with_bst_code_treats_as_oebb(tmp_path: Path) -> None:
    """Empty ``source`` + present ``bst_code`` → ÖBB (backward-compat).

    Entries written before the ``as_dict`` source-default fix lacked a
    ``source`` field entirely. If they carry the typical ÖBB Excel
    fields (``bst_id`` + ``bst_code``), treat them as ÖBB so the next
    Excel pull does not create a duplicate (see PR #1203 cron-failure
    post-mortem). The token-based classifier preserves this branch.
    """
    path = tmp_path / "stations.json"
    _write_stations(
        path,
        [
            {
                "bst_id": 5678,
                "bst_code": "XYZ1",
                "name": "Pre-source-default Entry",
                "source": "",
            }
        ],
    )

    mapping, manual = _load_existing_station_entries(path)

    assert "5678" in mapping
    assert manual == []


def test_empty_source_without_bst_code_is_manual(tmp_path: Path) -> None:
    """Empty ``source`` + missing ``bst_code`` → manual (backward-compat)."""
    path = tmp_path / "stations.json"
    _write_stations(
        path,
        [
            {
                "bst_id": 5678,
                "name": "Backward-compat manual entry",
                "source": "",
            }
        ],
    )

    mapping, manual = _load_existing_station_entries(path)

    assert "5678" not in mapping
    assert any(e.get("name") == "Backward-compat manual entry" for e in manual)


def test_non_oebb_source_only_is_manual(tmp_path: Path) -> None:
    """``source=wl,vor`` (no ÖBB token at all) → manual.

    The historical else-branch already covered this. The token-based
    rewrite must keep it because WL-only / VOR-only entries are
    real-world (any DIVA-carrying haltestelle, every VOR-derived stop).
    """
    path = tmp_path / "stations.json"
    _write_stations(
        path,
        [
            {
                "bst_id": 7890,
                "name": "WL-only Entry",
                "source": "wl,vor",
            }
        ],
    )

    mapping, manual = _load_existing_station_entries(path)

    assert "7890" not in mapping
    assert any(e.get("name") == "WL-only Entry" for e in manual)


def test_list_source_with_oebb_token_stays_in_mapping(tmp_path: Path) -> None:
    """The legacy list-shape source (``["oebb", "wl"]``) — list path.

    A small number of pre-PR-β entries carried the ``source`` as a
    JSON list rather than a comma-separated string. The classifier
    preserves the legacy element-wise membership check at
    ``isinstance(source, list) and "oebb" in source``.
    """
    path = tmp_path / "stations.json"
    _write_stations(
        path,
        [
            {
                "bst_id": 4321,
                "bst_code": "ABCD",
                "name": "Legacy list-source ÖBB",
                "source": ["oebb", "wl"],
            }
        ],
    )

    mapping, manual = _load_existing_station_entries(path)

    assert "4321" in mapping
    assert manual == []


def test_real_stations_json_keeps_wien_hauptbahnhof_as_manual() -> None:
    """End-to-end pin against the committed ``data/stations.json``.

    The committed file carries the two synthetic Wien-area entries the
    regression bit on (``Wien Hauptbahnhof`` = 900100, ``Wien
    Kaiserebersdorf`` = 900105). Pin that the classifier puts them in
    ``manual_stations`` so the wrapper's ``final_stations.extend(
    manual_stations)`` re-emits them after the Excel re-pull.
    """
    stations_path = Path("data/stations.json")
    if not stations_path.exists():  # pragma: no cover - fresh clone
        pytest.skip("data/stations.json not present in this checkout")

    mapping, manual = _load_existing_station_entries(stations_path)

    assert "900100" not in mapping, (
        "Regression: Wien Hauptbahnhof (synthetic 900100) leaked into the "
        "ÖBB-Excel mapping — see commit 484c1f6 post-mortem for the "
        "cascade this triggers."
    )
    assert "900105" not in mapping, (
        "Regression: Wien Kaiserebersdorf (synthetic 900105) leaked into "
        "the ÖBB-Excel mapping."
    )

    manual_names = {e.get("name") for e in manual}
    assert "Wien Hauptbahnhof" in manual_names
    assert "Wien Kaiserebersdorf" in manual_names


def test_real_stations_json_keeps_siebenhirten_in_mapping() -> None:
    """End-to-end pin: committed Siebenhirten (real bst_id 1371) → mapping.

    Sibling to :func:`test_real_stations_json_keeps_wien_hauptbahnhof_as_manual`
    for the opposite failure mode. ``Siebenhirten`` carries
    ``source="oebb_geonetz,osm"`` (no bare ``oebb`` token) but a real,
    non-synthetic ``bst_id`` the live workbook re-emits. It must classify
    into ``mapping`` — leaking it into ``manual_stations`` ships a
    duplicate ``bst_id=1371`` that fails ``validate_stations`` in CI.
    """
    stations_path = Path("data/stations.json")
    if not stations_path.exists():  # pragma: no cover - fresh clone
        pytest.skip("data/stations.json not present in this checkout")

    mapping, manual = _load_existing_station_entries(stations_path)

    assert "1371" in mapping, (
        "Regression: Siebenhirten (real bst_id 1371, source without a bare "
        "``oebb`` token) must round-trip through the ÖBB-Excel mapping, not "
        "leak into manual_stations and duplicate against the fresh extract."
    )
    assert not any(e.get("name") == "Siebenhirten" for e in manual)
