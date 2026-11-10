"""``rows_held_back``: ledger rows wait until the caller writes them.

The feed build renders inside such a block (``src/feed_build/isolation.py``),
so a rendering that fails or only tries one item enters nothing into
``data/stats/stoerungen_YYYY.csv``.

``HeldRows.write`` is best-effort per row, like every writer of the module:
a row that cannot be written is a warning and the next row is written. The
build calls it outside the handler that used to catch such a failure.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

import pytest

from src.utils import stats
from src.utils.stats import append_disruption_row, rows_held_back

WHEN = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
# A lone surrogate: the file is written as UTF-8, which cannot encode it.
UNWRITABLE_PLACE = "Neubau\ud83dgasse"


def _append(stats_dir: Path, place: str) -> bool:
    return append_disruption_row(timestamp=WHEN, provider="Wiener Linien", location_name=place, stats_dir=stats_dir)


def _rows(stats_dir: Path) -> list[str]:
    path = stats_dir / "stoerungen_2026.csv"
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def test_outside_a_block_a_row_is_written_at_once(tmp_path: Path) -> None:
    assert _append(tmp_path, "Karlsplatz") is True
    assert _rows(tmp_path) == [
        "timestamp,weekday,hour,provider,location_name",
        "2026-11-10T13:00:00+01:00,Di,13,Wiener Linien,Karlsplatz",
    ]


def test_rows_of_a_block_are_written_by_the_caller_in_their_order(tmp_path: Path) -> None:
    with rows_held_back() as held:
        assert _append(tmp_path, "Karlsplatz") is True
        assert _append(tmp_path, "Praterstern") is True
        assert _rows(tmp_path) == []
        assert len(held) == 2
    assert _rows(tmp_path) == []

    held.write()

    assert [row.rsplit(",", 1)[1] for row in _rows(tmp_path)] == ["location_name", "Karlsplatz", "Praterstern"]
    assert len(held) == 0
    held.write()
    assert len(_rows(tmp_path)) == 3


def test_rows_that_are_not_written_are_dropped(tmp_path: Path) -> None:
    with rows_held_back():
        _append(tmp_path, "Karlsplatz")
    _append(tmp_path, "Praterstern")
    assert [row.rsplit(",", 1)[1] for row in _rows(tmp_path)] == ["location_name", "Praterstern"]


def test_a_block_that_raises_leaves_the_writer_as_it_was(tmp_path: Path) -> None:
    try:
        with rows_held_back():
            _append(tmp_path, "Karlsplatz")
            raise RuntimeError("rendering failed")
    except RuntimeError:
        pass
    assert _rows(tmp_path) == []
    _append(tmp_path, "Praterstern")
    assert len(_rows(tmp_path)) == 2


def test_an_inner_block_keeps_its_own_rows(tmp_path: Path) -> None:
    with rows_held_back() as outer:
        _append(tmp_path, "Karlsplatz")
        with rows_held_back() as inner:
            _append(tmp_path, "Praterstern")
        _append(tmp_path, "Floridsdorf")
    assert (len(outer), len(inner)) == (2, 1)
    outer.write()
    assert [row.rsplit(",", 1)[1] for row in _rows(tmp_path)] == ["location_name", "Karlsplatz", "Floridsdorf"]


# --- a row that cannot be written ---------------------------------------------------


def _places(stats_dir: Path) -> list[str]:
    return [row.rsplit(",", 1)[1] for row in _rows(stats_dir)]


def _append_failures(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.getMessage().startswith("Stats append fehlgeschlagen für ")]


@pytest.mark.parametrize("failure", [ValueError("row refused"), OSError("disk full"), UnicodeError("no encoding")])
def test_a_row_whose_writing_raises_is_a_warning_and_the_next_row_is_written(
    failure: Exception, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    with rows_held_back() as held:
        for place in ("Karlsplatz", "Praterstern", "Floridsdorf"):
            assert _append(tmp_path, place) is True
    real_append = stats._append_row

    def append(path: Path, header: tuple[str, ...], row: tuple[str, ...]) -> bool:
        if row[-1] == "Praterstern":
            raise failure
        return real_append(path, header, row)

    monkeypatch.setattr(stats, "_append_row", append)

    with caplog.at_level(logging.DEBUG):
        held.write()

    assert _places(tmp_path) == ["location_name", "Karlsplatz", "Floridsdorf"]
    records = _append_failures(caplog)
    assert [(record.name, record.levelno) for record in records] == [(stats.LOGGER.name, logging.WARNING)]
    assert str(tmp_path / "stoerungen_2026.csv") in records[0].getMessage()
    assert str(failure) in records[0].getMessage()
    # The row is not kept for another try.
    assert len(held) == 0
    held.write()
    assert _places(tmp_path) == ["location_name", "Karlsplatz", "Floridsdorf"]
    assert len(_append_failures(caplog)) == 1


def test_a_row_the_file_cannot_encode_is_a_warning_and_the_next_row_is_written(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The real writer: ``UnicodeEncodeError`` is no ``OSError``, so ``_append_row`` lets it through."""
    with rows_held_back() as held:
        for place in ("Karlsplatz", UNWRITABLE_PLACE, "Floridsdorf"):
            assert _append(tmp_path, place) is True

    with caplog.at_level(logging.DEBUG):
        held.write()

    assert _places(tmp_path) == ["location_name", "Karlsplatz", "Floridsdorf"]
    records = _append_failures(caplog)
    assert [(record.name, record.levelno) for record in records] == [(stats.LOGGER.name, logging.WARNING)]
    assert len(held) == 0


def test_every_row_failing_writes_nothing_and_raises_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    with rows_held_back() as held:
        _append(tmp_path, "Karlsplatz")
        _append(tmp_path, "Praterstern")

    def append(path: Path, header: tuple[str, ...], row: tuple[str, ...]) -> bool:
        raise RuntimeError("no row today")

    monkeypatch.setattr(stats, "_append_row", append)

    with caplog.at_level(logging.DEBUG):
        held.write()

    assert _rows(tmp_path) == []
    assert len(_append_failures(caplog)) == 2
