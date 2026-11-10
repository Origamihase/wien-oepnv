"""``build_isolating``: what the review of 2026-11-10 found besides the one failing item.

* A collected entry that is no mapping is left out and reported with the
  placeholders; reading its fields does not end the build.
* The ledger rows of the returned run are written as the very last step: a
  build that ends with an exception, wherever it is raised, enters nothing.
* Items that cannot be copied are built in one plain run, as before the
  change, without single-item trials.
* A build that ends says in a warning line why: where the first run failed,
  how many items fail alone, where the run without them failed. No line
  carries the message of an exception.

The tests use the stand-in chain of ``tests/test_build_isolation.py``. Here
its rendering enters every item into a statistics ledger before it renders,
as ``_make_rss`` does. ``tests/test_build_feed_skipped_item_edges.py`` covers
the same through ``build_feed.main()``.
"""

from __future__ import annotations

import logging
import threading
import traceback
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest

from src.feed.config import InvalidPathError, validate_path
from src.feed_types import FeedItem
from src.utils.stats import append_disruption_row
from tests.test_build_isolation import (
    CHAIN_LOG,
    REPO_ROOT,
    SOURCE_TEXT,
    Chain,
    Recorder,
    Rule,
    State,
    _guids,
    _items,
    _never,
    _skip_records,
    on,
)

WHEN = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
# Stands for text of the source inside the message of a second exception.
SECOND_TEXT = "wording of another notice 0815"
OUTSIDE = Path("/outside-the-repository/notice-0815/feed.xml")
FIRST_RUN_FAILED = "Feed-Kette gescheitert ({place}) – suche die Meldung, an der es liegt."
FAILING_ALONE = "{failing} von {collected} Meldungen scheitern einzeln – der Bau endet mit dem Fehler."
SECOND_RUN_FAILED = (
    "Auch ohne {failing} einzeln scheiternde Meldung(en) scheitert der Bau ({place}) – er endet mit dem ersten Fehler."
)
NOT_COPYABLE = "Meldungen nicht kopierbar – der Feed wird ohne Einzelprüfung gebaut."


class LedgerChain(Chain):
    """The stand-in chain; its rendering enters every item into a ledger first."""

    def __init__(self, stats_dir: Path, *, assemble_fails: Rule = _never, render_fails: Rule = _never) -> None:
        super().__init__(assemble_fails=assemble_fails, render_fails=render_fails)
        self.stats_dir = stats_dir

    def render(self, items: list[FeedItem], state: State) -> str:
        for guid in _guids(items):
            append_disruption_row(timestamp=WHEN, provider="Quelle", location_name=guid, stats_dir=self.stats_dir)
        return super().render(items, state)


class UncopiedChain(LedgerChain):
    """The same chain without the copy ``Chain`` keeps of what a step received."""

    def __init__(self, stats_dir: Path, *, assemble_fails: Rule = _never, render_fails: Rule = _never) -> None:
        super().__init__(stats_dir, assemble_fails=assemble_fails, render_fails=render_fails)
        self.received: list[tuple[str, int, list[int]]] = []

    def _enter(self, step: str, items: list[FeedItem], state: State) -> list[str]:
        self.received.append((step, id(items), [id(item) for item in items]))
        self.steps.append(f"{step}:{len(items)}")
        CHAIN_LOG.warning("%s over %d items", step, len(items))
        return _guids(items)


class RefusingReport:
    """A run report that cannot take an entry."""

    def add_skipped_item(self, source: str, title: str, error: str) -> None:
        raise RuntimeError("the report takes no entry")


def _ledger(stats_dir: Path) -> list[str]:
    """The place names of the ledger rows, in the order they were written."""
    path = stats_dir / "stoerungen_2026.csv"
    rows = path.read_text(encoding="utf-8").splitlines()[1:] if path.exists() else []
    return [row.rsplit(",", 1)[1] for row in rows]


def _build_warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    """Every line of the logger ``build_feed`` at level WARNING, in order."""
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == "build_feed" and record.levelno == logging.WARNING
    ]


def _place_inside_src() -> str:
    """Where ``validate_path`` raises for ``OUTSIDE``: ``InvalidPathError in src/feed/config.py:<line>``."""
    with pytest.raises(InvalidPathError) as direct:
        validate_path(OUTSIDE, "OUT_PATH")
    frame = traceback.extract_tb(direct.value.__traceback__)[-1]
    return f"InvalidPathError in {Path(frame.filename).resolve().relative_to(REPO_ROOT).as_posix()}:{frame.lineno}"


def _with_lock(*guids: str) -> list[FeedItem]:
    """Items of which the second holds a value ``copy.deepcopy`` refuses."""
    items = _items(*guids)
    cast(dict[str, Any], items[1])["handle"] = threading.Lock()
    return items


# --- an entry that is no mapping ------------------------------------------------------


@pytest.mark.parametrize("entry", ["a line of text", None, 17, ["a", "b"]])
def test_an_entry_that_is_no_mapping_is_left_out_and_reported_with_the_placeholders(
    entry: object, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The stand-in reads ``entry.get`` first, as the real chain does: ``AttributeError``."""
    chain, report = LedgerChain(tmp_path), Recorder()
    collected = _items("a", "b", "d", "e")
    collected.insert(2, cast(FeedItem, entry))

    with caplog.at_level(logging.DEBUG):
        ordered, rss, _duration = chain.run(collected, {}, report)

    assert _guids(ordered) == ["a", "b", "d", "e"]
    assert rss == "rss:a,b,d,e"
    assert report.skipped == [("unbekannt", "(ohne Titel)", "AttributeError")]
    assert [record.getMessage() for record in _skip_records(caplog)] == [
        "Meldung übersprungen, der Feed wurde ohne sie gebaut (AttributeError): unbekannt – (ohne Titel)"
    ]
    # One row per item of the returned feed; the single-item trials entered none.
    assert _ledger(tmp_path) == ["a", "b", "d", "e"]


def test_more_than_half_of_the_entries_being_no_mapping_ends_the_build(tmp_path: Path) -> None:
    chain, report = LedgerChain(tmp_path), Recorder()
    collected = [*_items("a", "b"), cast(FeedItem, "a line of text"), cast(FeedItem, None), cast(FeedItem, 17)]

    with pytest.raises(AttributeError):
        chain.run(collected, {}, report)

    assert report.skipped == []
    assert _ledger(tmp_path) == []


# --- the ledger is written last -----------------------------------------------------------


@pytest.mark.parametrize("failing_step", ["assemble", "render"])
def test_the_ledger_holds_the_rows_of_the_returned_run_only(failing_step: str, tmp_path: Path) -> None:
    rule = on("c")
    chain = LedgerChain(tmp_path, assemble_fails=rule) if failing_step == "assemble" else LedgerChain(tmp_path, render_fails=rule)

    ordered, _rss, _duration = chain.run(_items("a", "b", "c", "d", "e"), {}, Recorder())

    # The rendering ran over five, over each of the five alone and over four.
    expected_renderings = {"assemble": 4 + 1, "render": 1 + 5 + 1}[failing_step]
    assert len([call for call in chain.calls if call.step == "render"]) == expected_renderings
    assert _ledger(tmp_path) == _guids(ordered) == ["a", "b", "d", "e"]


def test_a_run_without_failure_enters_its_rows_once(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    chain = LedgerChain(tmp_path)

    with caplog.at_level(logging.DEBUG):
        chain.run(_items("a", "b", "c"), {}, Recorder())

    assert _ledger(tmp_path) == ["a", "b", "c"]
    assert _build_warnings(caplog) == []


def test_a_report_that_raises_leaves_the_ledger_empty(tmp_path: Path) -> None:
    """Nothing is entered before the last step that can fail is over."""
    chain = LedgerChain(tmp_path, assemble_fails=on("c"))

    with pytest.raises(RuntimeError, match="the report takes no entry"):
        chain.run(_items("a", "b", "c", "d", "e"), {}, RefusingReport())

    # The run over the four was complete.
    assert chain.steps[-2:] == ["assemble:4", "render:4"]
    assert _ledger(tmp_path) == []


@pytest.mark.parametrize("failing", [("a", "b", "d"), ("a", "b", "c", "d")])
def test_a_build_that_ends_in_the_rendering_enters_nothing(failing: tuple[str, ...], tmp_path: Path) -> None:
    chain = LedgerChain(tmp_path, render_fails=on(*failing))

    with pytest.raises(ValueError):
        chain.run(_items("a", "b", "c", "d"), {}, Recorder())

    # Rows were appended in the failed run and in every trial.
    assert len([call for call in chain.calls if call.step == "render"]) == 5
    assert _ledger(tmp_path) == []


# --- items that cannot be copied --------------------------------------------------------


def test_items_that_cannot_be_copied_are_built_in_one_plain_run(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    chain, report = UncopiedChain(tmp_path), Recorder()
    collected = _with_lock("a", "b", "c")
    as_collected = [id(item) for item in collected]
    state: State = {}

    with caplog.at_level(logging.DEBUG):
        ordered, rss, duration = chain.run(collected, state, report)

    assert chain.steps == ["assemble:3", "render:3"]
    # The chain got the caller's list and the caller's items, as before the change.
    assert chain.received[0] == ("assemble", id(collected), as_collected)
    assert [id(item) for item in ordered] == as_collected
    assert rss == "rss:a,b,c"
    assert duration >= 0
    assert report.skipped == []
    assert sorted(state) == sorted([f"{step}:{guid}" for step in ("assembled", "rendered") for guid in "abc"])
    assert _build_warnings(caplog) == [NOT_COPYABLE]
    assert _ledger(tmp_path) == ["a", "b", "c"]


@pytest.mark.parametrize("failing_step", ["assemble", "render"])
def test_a_failure_with_items_that_cannot_be_copied_ends_the_build_without_trials(
    failing_step: str, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    rule = on("c")
    chain = UncopiedChain(tmp_path, assemble_fails=rule) if failing_step == "assemble" else UncopiedChain(tmp_path, render_fails=rule)
    report = Recorder()

    with caplog.at_level(logging.DEBUG), pytest.raises(ValueError) as raised:
        chain.run(_with_lock("a", "b", "c", "d", "e"), {}, report)

    assert raised.value is chain.raised[0]
    assert chain.steps == {"assemble": ["assemble:5"], "render": ["assemble:5", "render:5"]}[failing_step]
    assert report.skipped == []
    assert _build_warnings(caplog) == [NOT_COPYABLE]
    assert _ledger(tmp_path) == []


# --- the warning lines of a build that met a failure ----------------------------------------


def test_a_failed_first_run_is_logged_before_the_search_for_the_item(caplog: pytest.LogCaptureFixture) -> None:
    chain = Chain(assemble_fails=on("c"))

    with caplog.at_level(logging.DEBUG):
        chain.run(_items("a", "b", "c", "d", "e"), {}, Recorder())

    # The stand-in raises inside tests/: the place is the class alone.
    assert _build_warnings(caplog) == [FIRST_RUN_FAILED.format(place="ValueError")]
    lines = [record.getMessage() for record in caplog.records if record.name in ("build_feed", CHAIN_LOG.name)]
    assert lines[:3] == ["assemble over 5 items", FIRST_RUN_FAILED.format(place="ValueError"), "assemble over 4 items"]
    assert all(SOURCE_TEXT not in record.getMessage() for record in caplog.records)
    assert all(record.exc_info is None for record in caplog.records)


def test_a_first_failure_inside_src_is_logged_with_file_and_line(caplog: pytest.LogCaptureFixture) -> None:
    def rule(guids: list[str]) -> BaseException | None:
        if "c" in guids:
            validate_path(OUTSIDE, "OUT_PATH")
        return None

    place = _place_inside_src()
    assert place.startswith("InvalidPathError in src/feed/config.py:")

    with caplog.at_level(logging.DEBUG):
        Chain(assemble_fails=rule).run(_items("a", "b", "c", "d", "e"), {}, Recorder())

    assert _build_warnings(caplog) == [FIRST_RUN_FAILED.format(place=place)]
    assert all("notice-0815" not in record.getMessage() for record in caplog.records)


def _pair_rule(guids: list[str]) -> BaseException | None:
    """Fails only while ``b`` and ``d`` are both among the items."""
    return ValueError(SOURCE_TEXT) if {"b", "d"} <= set(guids) else None


@pytest.mark.parametrize(
    ("collected", "rule", "failing"),
    [
        (("a", "b", "c", "d"), on("a", "b", "d"), 3),
        (("a", "b", "c", "d", "e"), _pair_rule, 0),
        (("a",), on("a"), 1),
    ],
)
def test_a_build_that_ends_is_logged_with_the_number_of_items_failing_alone(
    collected: tuple[str, ...], rule: Rule, failing: int, caplog: pytest.LogCaptureFixture
) -> None:
    chain = Chain(assemble_fails=rule)

    with caplog.at_level(logging.DEBUG), pytest.raises(ValueError):
        chain.run(_items(*collected), {}, Recorder())

    assert _build_warnings(caplog) == [
        FIRST_RUN_FAILED.format(place="ValueError"),
        FAILING_ALONE.format(failing=failing, collected=len(collected)),
    ]
    assert all(SOURCE_TEXT not in record.getMessage() for record in caplog.records)
    assert all(record.exc_info is None for record in caplog.records)


def test_a_second_run_that_fails_is_logged_with_its_place_and_enters_nothing(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    def rendering(guids: list[str]) -> BaseException | None:
        if len(guids) == 4:
            try:
                validate_path(OUTSIDE, "OUT_PATH")
            except InvalidPathError as exc:
                return type(exc)(SECOND_TEXT).with_traceback(exc.__traceback__)
        return None

    second_place = _place_inside_src()
    chain, report = LedgerChain(tmp_path, assemble_fails=on("c"), render_fails=rendering), Recorder()

    with caplog.at_level(logging.DEBUG), pytest.raises(ValueError) as raised:
        chain.run(_items("a", "b", "c", "d", "e"), {}, report)

    first, second = chain.raised[0], chain.raised[-1]
    assert raised.value is first
    assert isinstance(second, InvalidPathError) and str(second) == SECOND_TEXT
    assert raised.value.__suppress_context__ is True
    assert _build_warnings(caplog) == [
        FIRST_RUN_FAILED.format(place="ValueError"),
        SECOND_RUN_FAILED.format(failing=1, place=second_place),
    ]
    for text in (SOURCE_TEXT, SECOND_TEXT):
        assert all(text not in record.getMessage() for record in caplog.records)
    # ``validate_path`` logs its own failure at DEBUG; the build's lines carry no traceback.
    assert all(record.exc_info is None for record in caplog.records if record.name == "build_feed")
    assert report.skipped == []
    assert _skip_records(caplog) == []
    # The rendering of the four appended four rows before it failed.
    assert chain.steps[-2:] == ["assemble:4", "render:4"]
    assert _ledger(tmp_path) == []
