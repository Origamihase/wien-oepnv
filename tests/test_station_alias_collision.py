import json
import logging
from pathlib import Path

import pytest

from src.utils import stations


def _records_for(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    data: list[dict[str, object]],
) -> list[logging.LogRecord]:
    temp_file = tmp_path / "stations.json"
    temp_file.write_text(json.dumps(data), encoding="utf-8")

    monkeypatch.setattr(stations, "_STATIONS_PATH", temp_file)
    stations._station_entries.cache_clear()
    stations._station_lookup.cache_clear()
    try:
        with caplog.at_level(logging.DEBUG, logger="src.utils.stations"):
            stations._station_lookup()
    finally:
        stations._station_entries.cache_clear()
        stations._station_lookup.cache_clear()
    return [r for r in caplog.records if "Duplicate station alias" in r.getMessage()]


def test_station_alias_collision_is_logged_at_debug(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The collision is still reported — just not as a WARNING.

    Previously WARNING. Measured on the live directory (audit 2026-09-12,
    Befund 6): 111 WARNING lines per process, four times per build, for 22
    contested keys — enough to bury a real warning. Every one of them was
    answer-neutral: across all 44 (key, loser, winner) triples the two sides
    agreed on ``in_vienna``, the verdict ``station_info`` actually feeds.

    Demoting is only defensible because the *guarding* moved rather than
    vanished: ``stations_validation._find_alias_collision_issues`` now fails
    a collision whose claimants disagree, which a log line never could.
    """
    records = _records_for(
        tmp_path,
        caplog,
        monkeypatch,
        [
            {"name": "First Station", "aliases": ["Collision"]},
            {"name": "Second Station", "aliases": ["Collision"]},
        ],
    )

    assert records, "die Kollision muss weiterhin protokolliert werden"
    assert all(r.levelno == logging.DEBUG for r in records), [
        r.levelname for r in records
    ]
    joined = " ".join(r.getMessage() for r in records)
    assert "First Station" in joined and "Second Station" in joined


def test_one_contested_key_costs_one_log_line(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cosmetic alias variants must not restate the same fact.

    The live noise came from the alias lists carrying up to seven spellings
    of one name — ``Bhf. Hütteldorf``, ``Bahnhof Bhf. Hütteldorf``,
    ``Bhf. Hütteldorf Bahnhof`` — each colliding separately on the SAME
    normalized key.
    """
    records = _records_for(
        tmp_path,
        caplog,
        monkeypatch,
        [
            {"name": "First Station", "aliases": ["Collision"]},
            {
                "name": "Second Station",
                # Five surfaces, one normalized key.
                "aliases": [
                    "Collision",
                    "collision",
                    "Bahnhof Collision",
                    "Collision Bahnhof",
                    "Bf Collision",
                ],
            },
        ],
    )

    assert len(records) == 1, [r.getMessage() for r in records]


def test_short_bst_codes_with_umlaut_remain_distinct() -> None:
    """Short ÖBB Stellencodes ``Sue`` and ``Su`` must not collide.

    Regression for the umlaut-fold over-aggression: the legacy ``ue→u``
    substitution applied to every token, so ``Sue`` (Wien Süßenbrunn) and
    ``Su`` (Stockerau) both collapsed to ``su`` and one shadowed the
    other in :func:`_station_lookup`. Skipping the fold for tokens of
    length ≤ 3 keeps short identifier-like codes distinct while still
    folding longer ASCII transliterations like ``Mueller``.
    """
    sue_info = stations.station_info("Sue")
    assert sue_info is not None
    assert sue_info.name == "Wien Süßenbrunn"

    su_info = stations.station_info("Su")
    assert su_info is not None
    assert su_info.name == "Stockerau"

    # Long-token fold still works
    assert stations._normalize_token("Mueller") == "muller"
    assert stations._normalize_token("Müller") == "muller"
