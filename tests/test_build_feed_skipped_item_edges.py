"""``build_feed.main()``: what the review of 2026-11-10 found around the skipped item.

* A collected entry that is no mapping leaves the run like every other item
  the chain fails on; the health report names it with the placeholders.
* A ledger row that cannot be written is a warning; the build goes on and
  skips nothing.
* Items that cannot be copied are built as before the change.
* The single-item trials check every collected item, also one the feed's
  ten places would never show.
* When the run without the failing item fails too, the first exception ends
  the build; feed, state file and ledger stay as they are.
* The health report and the state file of a build that skipped an item hold
  nothing the failed run counted; that goes for the number of new items too,
  which ``main()`` counts next to the chain.

Helpers and the pattern of every ``main()`` run come from
``tests/test_build_feed_skipped_item.py``; the unit tests of
``build_isolating`` for the same cases are in
``tests/test_build_isolation_edges.py``.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest

import src.build_feed as bf
from src.feed_types import FeedItem
from tests._conftest.build_feed_bindings import setattr_all
from tests.test_build_feed_skipped_item import (
    FAILING,
    OTHERS,
    SECTION,
    SOURCE_TEXT,
    Run,
    _build,
    _earlier_state,
    _fail_on,
    _is_failing,
    _ledger_rows,
    _prepare,
    _wl,
)

ALL_FIVE = ["guid-1", "guid-2", FAILING, "guid-4", "guid-5"]
# Stands for text of the source inside the message of a second exception.
SECOND_TEXT = "wording of another notice 0815"
NOT_COPYABLE = "Meldungen nicht kopierbar – der Feed wird ohne Einzelprüfung gebaut."
FIRST_RUN_FAILED = re.compile(r"Feed-Kette gescheitert \((?P<place>[^()]+)\) – suche die Meldung, an der es liegt\.")
SECOND_RUN_FAILED = re.compile(
    r"Auch ohne 1 einzeln scheiternde Meldung\(en\) scheitert der Bau \((?P<place>[^()]+)\)"
    r" – er endet mit dem ersten Fehler\."
)
# A lone surrogate: the CSV writer of the ledger cannot encode it as UTF-8.
UNWRITABLE_PLACE = "Neubau\ud83dgasse"
HOUR = 3600.0


def _five(now: datetime) -> list[FeedItem]:
    return [_wl(number, now) for number in range(1, 6)]


def _build_warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    """Every line of the logger ``build_feed`` at level WARNING, in order."""
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == "build_feed" and record.levelno == logging.WARNING
    ]


def _fail_second_run(monkeypatch: pytest.MonkeyPatch, step: str) -> list[BaseException]:
    """Make the run over the four items that are left fail too, with a ``ValueError``.

    ``first step``: ``_drop_test_messages``, before the chain counts anything.
    ``rendering``: ``_make_rss``, after the whole chain. A single-item trial
    never meets it.
    """
    raised: list[BaseException] = []
    name = {"first step": "_drop_test_messages", "rendering": "_make_rss"}[step]
    real = getattr(bf, name)

    def step_over_four(items: list[FeedItem], *args: Any, **kwargs: Any) -> Any:
        if len(items) == len(OTHERS):
            raised.append(ValueError(SECOND_TEXT))
            raise raised[-1]
        return real(items, *args, **kwargs)

    monkeypatch.setattr(bf, name, step_over_four)
    return raised


def _new_item_counts(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Every result of ``_count_new_items`` in ``main()``, in order: the runs and the single-item trials."""
    counts: list[int] = []
    real = bf._count_new_items

    def count(items: list[FeedItem], state: dict[str, Any]) -> int:
        counts.append(real(items, state))
        return counts[-1]

    monkeypatch.setattr(bf, "_count_new_items", count)
    return counts


# --- an entry that is no mapping ------------------------------------------------------


@pytest.mark.parametrize("entry", ["a line of text", None, 17, ["a", "b"]])
def test_an_entry_that_is_no_mapping_leaves_the_run_and_is_reported(
    entry: object, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The chain itself fails on such an entry (``entry.get`` in its first step); nothing is patched."""
    collected = _five(datetime.now(UTC))
    collected.insert(2, cast(FeedItem, entry))

    run = _build(monkeypatch, tmp_path / "run", collected)

    assert run.code == 0
    assert run.guids() == ALL_FIVE
    assert run.guids("feed.en.xml") == ALL_FIVE
    entries = run.health["skipped_items"]
    assert [(skipped["source"], skipped["title"]) for skipped in entries] == [("unbekannt", "(ohne Titel)")]
    assert re.fullmatch(r"AttributeError in src/feed_build/filters\.py:\d+", entries[0]["error"]), entries[0]["error"]
    assert run.health["run"]["status"] == "success"
    assert (run.health["run"]["raw_item_count"], run.health["run"]["final_item_count"]) == (6, 5)
    markdown = run.health_markdown.splitlines()
    assert markdown[markdown.index(SECTION) + 2].startswith("- unbekannt: \\(ohne Titel\\) (AttributeError in src/")
    # One row per item of the published feed.
    rows = _ledger_rows()
    assert len(rows) == 5, rows
    assert sorted(run.state) == ALL_FIVE


def test_more_than_half_of_the_entries_being_no_mapping_fails_the_build(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    now = datetime.now(UTC)
    collected = [_wl(1, now), _wl(2, now), cast(FeedItem, "a line of text"), cast(FeedItem, None), cast(FeedItem, 17)]
    run = _prepare(monkeypatch, tmp_path / "run", collected)

    with pytest.raises(AttributeError):
        bf.main()

    assert run.health["run"]["status"] == "error"
    assert run.health["skipped_items"] == []
    assert not (run.directory / "feed.xml").exists()
    assert not (run.directory / "state.json").exists()
    assert _ledger_rows() == []


# --- a ledger row that cannot be written ----------------------------------------------------


def test_a_ledger_row_that_cannot_be_written_does_not_fail_the_build(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """As before the change: a warning, the feed with every item, no skipped item."""
    collected = _five(datetime.now(UTC))
    collected[2]["description"] = f"{collected[2]['description']} | Haltestelle: {UNWRITABLE_PLACE}, Burggasse"

    with caplog.at_level(logging.DEBUG):
        run = _build(monkeypatch, tmp_path / "run", collected)

    assert run.code == 0
    assert run.guids() == ALL_FIVE
    assert run.health["skipped_items"] == []
    assert run.health["run"]["status"] == "success"
    assert _build_warnings(caplog) == []
    # The rows of the four other items are in the ledger.
    rows = _ledger_rows()
    assert len(rows) == 4, rows
    assert all(",Wiener Linien," in row for row in rows)
    failed = [record for record in caplog.records if record.getMessage().startswith("Stats append fehlgeschlagen")]
    assert [(record.name, record.levelno) for record in failed] == [("utils.stats", logging.WARNING)]
    assert sorted(run.state) == ALL_FIVE


# --- items that cannot be copied -------------------------------------------------------------


def test_items_that_cannot_be_copied_are_built_as_before_the_change(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    now = datetime.now(UTC)

    def collect(report: object) -> list[FeedItem]:
        items = _five(now)
        cast(dict[str, Any], items[1])["_handle"] = threading.Lock()
        return items

    run = _prepare(monkeypatch, tmp_path / "run", [])
    monkeypatch.setattr(bf, "_invoke_collect_items", collect)

    with caplog.at_level(logging.DEBUG):
        run.code = bf.main()

    assert run.code == 0
    assert run.guids() == ALL_FIVE
    assert run.guids("feed.en.xml") == ALL_FIVE
    assert run.health["skipped_items"] == []
    assert run.health["run"]["status"] == "success"
    assert _build_warnings(caplog) == [NOT_COPYABLE]
    assert len(_ledger_rows()) == 5
    assert sorted(run.state) == ALL_FIVE


# --- the trials check every collected item ------------------------------------------------------


def test_an_item_the_feed_would_not_show_is_reported_once_the_first_run_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Thirty items for ten places: the rendering fails on the second and on the 25th."""
    now = datetime.now(UTC)
    _fail_on(monkeypatch, "rendering", lambda item: item.get("guid") in ("guid-2", "guid-25"))

    run = _build(monkeypatch, tmp_path / "run", [_wl(number, now) for number in range(1, 31)])

    assert run.code == 0
    assert run.guids() == ["guid-1", *(f"guid-{number}" for number in range(3, 12))]
    assert [entry["title"] for entry in run.health["skipped_items"]] == [
        "2A: Rettungseinsatz Ort2", "25A: Rettungseinsatz Ort25",
    ]
    assert (run.health["run"]["raw_item_count"], run.health["run"]["final_item_count"]) == (30, 28)
    assert len(_ledger_rows()) == 10


def test_an_item_the_feed_would_not_show_does_not_start_the_trials(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The normal run is unchanged: it renders ten items and never meets the 25th."""
    now = datetime.now(UTC)
    raised = _fail_on(monkeypatch, "rendering", lambda item: item.get("guid") == "guid-25")

    with caplog.at_level(logging.DEBUG):
        run = _build(monkeypatch, tmp_path / "run", [_wl(number, now) for number in range(1, 31)])

    assert run.code == 0
    assert run.guids() == [f"guid-{number}" for number in range(1, 11)]
    assert run.health["skipped_items"] == []
    assert raised == []
    assert _build_warnings(caplog) == []


# --- the run without the failing item fails too ---------------------------------------------


def test_a_second_run_that_fails_ends_the_build_with_the_first_exception(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    now = datetime.now(UTC)
    first = _fail_on(monkeypatch, "chain", _is_failing)
    second = _fail_second_run(monkeypatch, "rendering")
    run = _prepare(monkeypatch, tmp_path / "run", _five(now), _earlier_state(now))
    for name in ("feed.xml", "feed.en.xml"):
        (run.directory / name).write_text(f"published {name}", encoding="utf-8")
    state_before = (run.directory / "state.json").read_bytes()

    with caplog.at_level(logging.DEBUG), pytest.raises(RuntimeError) as failure:
        bf.main()

    # Raised: the full run, the trial of the failing item, then the run over the four.
    assert failure.value is first[0]
    assert (len(first), len(second)) == (2, 1)
    assert failure.value.__suppress_context__ is True
    for name in ("feed.xml", "feed.en.xml"):
        assert (run.directory / name).read_text(encoding="utf-8") == f"published {name}"
    assert (run.directory / "state.json").read_bytes() == state_before
    assert _ledger_rows() == []

    warnings = _build_warnings(caplog)
    assert len(warnings) == 2, warnings
    first_line, second_line = FIRST_RUN_FAILED.fullmatch(warnings[0]), SECOND_RUN_FAILED.fullmatch(warnings[1])
    assert first_line is not None and second_line is not None, warnings
    assert re.fullmatch(r"RuntimeError in src/build_feed\.py:\d+", first_line["place"]), warnings[0]
    assert re.fullmatch(r"ValueError in src/build_feed\.py:\d+", second_line["place"]), warnings[1]
    assert first_line["place"].rpartition(":")[2] != second_line["place"].rpartition(":")[2]
    assert all(text not in line for line in warnings for text in (SOURCE_TEXT, SECOND_TEXT))

    assert run.health["run"]["status"] == "error"
    assert run.health["run"]["feed_path"] is None
    assert run.health["skipped_items"] == []
    assert SECTION not in run.health_markdown
    assert any(f"RuntimeError: {SOURCE_TEXT}" in error for error in run.health["errors"])
    assert all(SECOND_TEXT not in error for error in run.health["errors"])


def test_the_counters_of_the_failed_first_run_are_cleared_before_the_second_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The first run counted five items after the age filter; the second fails before it counts."""
    now = datetime.now(UTC)
    first = _fail_on(monkeypatch, "chain", _is_failing)
    _fail_second_run(monkeypatch, "first step")
    run = _prepare(monkeypatch, tmp_path / "run", _five(now))

    with pytest.raises(RuntimeError) as failure:
        bf.main()

    assert failure.value is first[0]
    assert run.health["metrics"] == {
        "raw_items": 5, "filtered_items": 0, "deduped_items": 0, "new_items": 0, "duplicate_count": 0,
    }


def test_the_new_item_count_of_the_failed_first_run_is_cleared_before_the_second_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The first run fails in the rendering, after ``main()`` counted its new items; the second fails before it counts.

    The earlier state knows ``guid-1`` and the failing item: three of the five are new.
    """
    now = datetime.now(UTC)
    counts = _new_item_counts(monkeypatch)
    first = _fail_on(monkeypatch, "rendering", _is_failing)
    second = _fail_second_run(monkeypatch, "first step")
    run = _prepare(monkeypatch, tmp_path / "run", _five(now), _earlier_state(now))

    with pytest.raises(RuntimeError) as failure:
        bf.main()

    assert failure.value is first[0]
    assert len(second) == 1
    # The run over the five did count its three new items ...
    assert counts[0] == 3
    # ... and the report of the failed build shows none of them.
    assert run.health["run"]["status"] == "error"
    assert run.health["metrics"] == {
        "raw_items": 5, "filtered_items": 0, "deduped_items": 0, "new_items": 0, "duplicate_count": 0,
    }
    assert "| Neue Items seit letztem State | 0 |" in run.health_markdown.splitlines()


# --- nothing of the failed run in the report of a build that skipped an item ----------------


def test_a_skipped_build_reports_no_duration_and_no_dropped_id_of_the_failed_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A full second run assigns every field of ``FeedAssemblyStats`` anew.

    The failed run is made recognisable: its age filter and its duplicate
    step each take an hour on the build's clock, and its age filter reports
    an entry of the state file as run out.
    """
    now = datetime.now(UTC)
    collected = _five(now)
    _fail_on(monkeypatch, "rendering", _is_failing)
    ahead = [0.0]
    # One clock for the chain, the rendering and ``main()``.
    setattr_all(monkeypatch, bf, "perf_counter", lambda: time.perf_counter() + ahead[0])
    real_filter, real_dedupe = bf._drop_old_items, bf._dedupe_items

    def age_filter(items: list[FeedItem], *args: Any) -> tuple[list[FeedItem], set[str]]:
        kept, dropped = real_filter(items, *args)
        if len(items) == len(collected):
            ahead[0] += HOUR
            dropped = dropped | {"guid-gone"}
        return kept, dropped

    def dedupe(items: list[FeedItem]) -> list[FeedItem]:
        if len(items) == len(collected):
            ahead[0] += HOUR
        return real_dedupe(items)

    monkeypatch.setattr(bf, "_drop_old_items", age_filter)
    monkeypatch.setattr(bf, "_dedupe_items", dedupe)

    run: Run = _build(monkeypatch, tmp_path / "run", collected, _earlier_state(now))

    assert run.code == 0
    assert sorted(run.guids()) == OTHERS
    assert [entry["title"] for entry in run.health["skipped_items"]] == ["3A: Rettungseinsatz Ort3"]
    durations = run.health["durations"]
    # The failed run did take its two hours on that clock ...
    assert ahead[0] == 2 * HOUR
    assert durations["total"] >= 2 * HOUR
    # ... and the report shows the steps of the run whose feed was written.
    assert 0 <= durations["filter"] < HOUR
    assert 0 <= durations["dedupe"] < HOUR
    assert 0 <= durations["rss"] < HOUR
    # ``guid-gone`` ran out in the failed run only: its entry stays in the state file.
    assert sorted(run.state) == sorted([*OTHERS, FAILING, "guid-expired", "guid-gone"])
    assert run.health["metrics"] == {
        "raw_items": 5, "filtered_items": 4, "deduped_items": 4, "new_items": 3, "duplicate_count": 0,
    }


def test_a_skipped_build_reports_the_new_items_of_the_run_whose_feed_was_written(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The earlier state knows ``guid-1`` only: four new items with the failing one, three without it."""
    now = datetime.now(UTC)
    counts = _new_item_counts(monkeypatch)
    _fail_on(monkeypatch, "rendering", _is_failing)
    state = {key: entry for key, entry in _earlier_state(now).items() if key != FAILING}

    run = _build(monkeypatch, tmp_path / "run", _five(now), state)

    assert run.code == 0
    assert sorted(run.guids()) == OTHERS
    assert [entry["title"] for entry in run.health["skipped_items"]] == ["3A: Rettungseinsatz Ort3"]
    # The failed run counted the failing item as new, the run without it ``guid-2``, ``guid-4`` and ``guid-5``.
    assert (counts[0], counts[-1]) == (4, 3)
    assert run.health["metrics"] == {
        "raw_items": 5, "filtered_items": 4, "deduped_items": 4, "new_items": 3, "duplicate_count": 0,
    }
    assert "| Neue Items seit letztem State | 3 |" in run.health_markdown.splitlines()
    # The skipped item, new to the failed run, has no entry in the state file.
    assert sorted(run.state) == sorted([*OTHERS, "guid-expired", "guid-gone"])
