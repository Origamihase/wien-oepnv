"""The yearly Baustellen ledger: a failure of the writer never raises.

An unwritable folder, a raising writer, a failing site, events that cannot
be iterated.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from src.utils import stats as stats_utils
from src.utils import stats_baustellen
from src.utils.stats_baustellen import append_baustellen_rows, known_guids
from tests._baustellen_ledger_support import (
    GUID,
    LEDGER_LOGGER,
    NOW,
    _event,
    _raise_runtime_error,
    _warnings,
)


@pytest.fixture
def stats_dir(tmp_path: Path) -> Path:
    return tmp_path / "ledger"


# --- failures ---------------------------------------------------------------


def test_unwritable_folder_does_not_raise(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("eine Datei, kein Ordner", encoding="utf-8")
    caplog.set_level(logging.WARNING)
    assert append_baustellen_rows([_event(), _event(guid="b")], now=NOW, stats_dir=blocker / "stats") == 0
    assert blocker.read_text(encoding="utf-8") == "eine Datei, kein Ordner"
    # The cause is the same for every row: one warning, not one per site.
    assert len(_warnings(caplog)) == 1
    # A path the system refuses to look at (name too long) is a file that cannot be used, not an error for the caller.
    assert known_guids(2026, stats_dir=tmp_path / ("x" * 300)) == set()
    assert "baustellen_2026.csv ist nicht lesbar" in _warnings(caplog)[-1]


def test_a_raising_writer_does_not_raise_here(stats_dir: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setattr(stats_baustellen, "_append_row", _raise_runtime_error)
    caplog.set_level(logging.WARNING, logger=LEDGER_LOGGER)
    assert append_baustellen_rows([_event(), _event(guid="b")], now=NOW, stats_dir=stats_dir) == 0
    [message] = _warnings(caplog)
    assert "2 Baustelle(n) nicht eingetragen (RuntimeError: Platte voll" in message
    assert "\n" not in message


def test_a_failing_site_costs_no_other_its_row(stats_dir: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    real = stats_baustellen.baustelle_row

    def picky(event: Any, *, seen_at: datetime) -> tuple[str, ...] | None:
        if event["guid"] == "b":
            raise ValueError("unlesbar")
        return real(event, seen_at=seen_at)

    monkeypatch.setattr(stats_baustellen, "baustelle_row", picky)
    caplog.set_level(logging.WARNING, logger=LEDGER_LOGGER)
    # Entries that are no mappings are passed over without a word.
    events: list[Any] = [None, "Burggasse", 7, _event(guid="a"), _event(guid="b"), _event(guid="c")]

    assert append_baustellen_rows(events, now=NOW, stats_dir=stats_dir) == 2

    assert known_guids(2026, stats_dir=stats_dir) == {"a", "c"}
    assert [m for m in _warnings(caplog) if "1 Baustelle(n) nicht eingetragen (ValueError: unlesbar)" in m]


def test_events_that_cannot_be_iterated_do_not_raise(stats_dir: Path, caplog: pytest.LogCaptureFixture) -> None:
    def breaking() -> Iterator[dict[str, Any]]:
        yield _event(guid="a")
        raise OSError("Quelle\nweg")

    caplog.set_level(logging.WARNING, logger=LEDGER_LOGGER)
    nothing: Any = None
    assert append_baustellen_rows(nothing, now=NOW, stats_dir=stats_dir) == 0
    assert any("Zeilen konnten nicht geschrieben werden (TypeError" in m for m in _warnings(caplog))
    # The rows written before an iterable breaks are kept and counted.
    assert append_baustellen_rows(breaking(), now=NOW, stats_dir=stats_dir) == 1
    assert known_guids(2026, stats_dir=stats_dir) == {"a"}
    assert "(OSError: Quelle" in _warnings(caplog)[-1] and all("\n" not in m for m in _warnings(caplog))


def test_rows_held_back_by_the_feed_build_are_not_written_here(stats_dir: Path) -> None:
    """Inside ``rows_held_back`` the row follows that block's rule (``src/utils/stats.py``)."""
    with stats_utils.rows_held_back() as held:
        assert append_baustellen_rows([_event(), _event()], now=NOW, stats_dir=stats_dir) == 1
    assert not stats_dir.exists()
    assert len(held) == 1
    held.write()
    assert known_guids(2026, stats_dir=stats_dir) == {GUID}
