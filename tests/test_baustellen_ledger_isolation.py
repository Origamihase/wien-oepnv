"""The yearly Baustellen ledger: the tests never write to the real ``data/stats``."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.utils import stats as stats_utils
from src.utils import stats_baustellen
from src.utils.stats_baustellen import append_baustellen_rows, known_guids
from tests._baustellen_ledger_support import (
    GUID,
    NOW,
    _event,
    _real_ledgers,
)


# --- test isolation ---------------------------------------------------------


def test_default_folder_is_read_at_call_time_and_isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The autouse fixture ``isolate_stats_writes`` replaces ``src.utils.stats.DEFAULT_STATS_DIR``;
    # a module that bound the constant at import time would still write to the real ``data/stats``.
    assert stats_utils.DEFAULT_STATS_DIR == tmp_path / "stats"
    assert not hasattr(stats_baustellen, "DEFAULT_STATS_DIR")
    before = _real_ledgers()

    assert append_baustellen_rows([_event()], now=NOW) == 1

    assert known_guids(2026) == {GUID}
    assert [p.name for p in (tmp_path / "stats").iterdir()] == ["baustellen_2026.csv"]
    assert _real_ledgers() == before
    # A later change of the constant is followed as well.
    monkeypatch.setattr(stats_utils, "DEFAULT_STATS_DIR", tmp_path / "anderswo")
    assert append_baustellen_rows([_event()], now=NOW) == 1
    assert (tmp_path / "anderswo" / "baustellen_2026.csv").is_file()
