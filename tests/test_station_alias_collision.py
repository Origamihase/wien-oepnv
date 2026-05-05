import json
import logging
from pathlib import Path

import pytest

from src.utils import stations


def test_station_alias_collision_logs_warning(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = [
        {
            "name": "First Station",
            "aliases": ["Collision"],
        },
        {
            "name": "Second Station",
            "aliases": ["Collision"],
        },
    ]
    temp_file = tmp_path / "stations.json"
    temp_file.write_text(json.dumps(data), encoding="utf-8")

    monkeypatch.setattr(stations, "_STATIONS_PATH", temp_file)
    stations._station_entries.cache_clear()
    stations._station_lookup.cache_clear()
    try:
        with caplog.at_level(logging.WARNING):
            stations._station_lookup()
    finally:
        stations._station_entries.cache_clear()
        stations._station_lookup.cache_clear()

    warnings = [record.getMessage() for record in caplog.records if record.levelno == logging.WARNING]
    assert any("Duplicate station alias" in message for message in warnings)
    assert any("First Station" in message and "Second Station" in message for message in warnings)


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
