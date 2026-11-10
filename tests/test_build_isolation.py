"""``build_isolating``: one item on which the chain fails leaves the run.

Operator decision of 2026-11-10: the feed is built with the other items and
the item that was left out is reported. These tests drive
``src.feed_build.isolation.build_isolating`` with a small stand-in chain
(``Chain``) that behaves like the real one where it matters here: it changes
the items in place, takes entries out of the list it was given, writes into
the state and logs a line per step.

``tests/test_build_feed_skipped_item.py`` covers the same through
``build_feed.main()``. Entries that are no mapping, items that cannot be
copied, the ledger and the warning lines of a build that ends are in
``tests/test_build_isolation_edges.py``.
"""

from __future__ import annotations

import copy
import logging
import re
import traceback
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import pytest

from src.feed.config import InvalidPathError, validate_path
from src.feed.reporting import RunReport
from src.feed_build import isolation
from src.feed_build.isolation import build_isolating
from src.feed_types import FeedItem

State = dict[str, Any]
Rule = Callable[[list[str]], BaseException | None]

REPO_ROOT = Path(__file__).resolve().parents[1]
# Stands for text of the source inside an exception message.
SOURCE_TEXT = "wording of the notice 4711"
SKIP_PREFIX = "Meldung übersprungen, der Feed wurde ohne sie gebaut"
# A stand-in raises inside tests/: no frame under src/ besides the module
# that runs the trial, whose own frames do not count. Only the class is left.
TRIAL_PLACE = re.compile(r"ValueError")
CHAIN_LOG = logging.getLogger("tests.stand_in_chain")


def _items(*guids: str) -> list[FeedItem]:
    return [
        cast(FeedItem, {"source": "Quelle", "title": f"Titel {guid}", "guid": guid, "lines": [guid]})
        for guid in guids
    ]


def _guids(items: list[FeedItem]) -> list[str]:
    return [str(item.get("guid")) for item in items]


def _never(guids: list[str]) -> BaseException | None:
    return None


def on(*failing: str) -> Rule:
    """A rule that fails with a ``ValueError`` whenever one of *failing* is among the items."""

    def rule(guids: list[str]) -> BaseException | None:
        return ValueError(SOURCE_TEXT) if set(failing) & set(guids) else None

    return rule


@dataclass
class Call:
    """One call of a chain step, as the step saw it on entry."""

    step: str
    received: list[dict[str, Any]]
    list_id: int
    state_id: int
    logging_off: bool


class Recorder:
    """Collects what ``build_isolating`` reports."""

    def __init__(self) -> None:
        self.skipped: list[tuple[str, str, str]] = []

    def add_skipped_item(self, source: str, title: str, error: str) -> None:
        self.skipped.append((source, title, error))


class Chain:
    """A stand-in for the feed chain and the rendering."""

    def __init__(self, *, assemble_fails: Rule = _never, render_fails: Rule = _never, disk: State | None = None) -> None:
        self.assemble_fails = assemble_fails
        self.render_fails = render_fails
        self.disk: State = {"known": {"first_seen": "2026-11-01T08:00:00+00:00"}} if disk is None else disk
        self.calls: list[Call] = []
        self.steps: list[str] = []
        self.raised: list[BaseException] = []
        self.returned: list[list[FeedItem]] = []

    def _enter(self, step: str, items: list[FeedItem], state: State) -> list[str]:
        guids = _guids(items)
        quiet = logging.root.manager.disable >= logging.CRITICAL
        self.calls.append(Call(step, copy.deepcopy(cast(list[dict[str, Any]], items)), id(items), id(state), quiet))
        self.steps.append(f"{step}:{len(items)}")
        CHAIN_LOG.warning("%s over %d items", step, len(items))
        return guids

    def _fail(self, rule: Rule, guids: list[str]) -> None:
        exc = rule(guids)
        if exc is not None:
            self.raised.append(exc)
            raise exc

    def assemble(self, items: list[FeedItem], state: State) -> list[FeedItem]:
        guids = self._enter("assemble", items, state)
        ordered = list(items)
        del items[:]
        for item in ordered:
            changed = cast(dict[str, Any], item)
            changed["visits"] = changed.get("visits", 0) + 1
            changed["lines"].append("assembled")
            state[f"assembled:{changed.get('guid')}"] = {"run_size": len(ordered)}
        self._fail(self.assemble_fails, guids)
        self.returned.append(ordered)
        return ordered

    def render(self, items: list[FeedItem], state: State) -> str:
        guids = self._enter("render", items, state)
        for guid in guids:
            state[f"rendered:{guid}"] = {"run_size": len(guids)}
        self._fail(self.render_fails, guids)
        return "rss:" + ",".join(guids)

    def reload_state(self) -> State:
        self.steps.append("reload")
        return copy.deepcopy(self.disk)

    def reset(self) -> None:
        self.steps.append("reset")

    def run(self, collected: list[FeedItem], state: State, report: isolation.SkipReporter) -> tuple[list[FeedItem], str, float]:
        return build_isolating(
            collected, state, assemble=self.assemble, render=self.render,
            reload_state=self.reload_state, reset=self.reset, report=report,
        )

    def full_runs(self, step: str = "assemble") -> list[Call]:
        return [call for call in self.calls if call.step == step and not call.logging_off]

    def trials(self, step: str = "assemble") -> list[Call]:
        return [call for call in self.calls if call.step == step and call.logging_off]


def _skip_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.getMessage().startswith(SKIP_PREFIX)]


@pytest.fixture
def info_switched_off() -> Iterator[int]:
    """Run the test with ``logging.disable(logging.INFO)``, as a caller might have set it."""
    previous = logging.root.manager.disable
    logging.disable(logging.INFO)
    try:
        yield logging.INFO
    finally:
        logging.disable(previous)


# --- no failure ----------------------------------------------------------------


def test_a_run_without_failure_calls_chain_and_rendering_once(caplog: pytest.LogCaptureFixture) -> None:
    chain, report = Chain(), Recorder()
    collected = _items("a", "b", "c")
    state: State = {"known": {}}

    with caplog.at_level(logging.DEBUG):
        ordered, rss, duration = chain.run(collected, state, report)

    assert chain.steps == ["assemble:3", "render:3"]
    assemble, render = chain.calls
    assert (assemble.list_id, assemble.state_id) == (id(collected), id(state))
    assert render.state_id == id(state)
    assert ordered is chain.returned[0]
    assert rss == "rss:a,b,c"
    assert isinstance(duration, float) and duration >= 0
    assert report.skipped == []
    assert _skip_records(caplog) == []


# --- one item fails ------------------------------------------------------------


@pytest.mark.parametrize("failing_step", ["assemble", "render"])
def test_the_feed_is_built_from_the_other_items(failing_step: str, caplog: pytest.LogCaptureFixture) -> None:
    rule = on("c")
    chain = Chain(assemble_fails=rule) if failing_step == "assemble" else Chain(render_fails=rule)
    report = Recorder()
    state: State = copy.deepcopy(chain.disk)

    with caplog.at_level(logging.DEBUG):
        ordered, rss, duration = chain.run(_items("a", "b", "c", "d", "e"), state, report)

    assert _guids(ordered) == ["a", "b", "d", "e"]
    assert ordered is chain.returned[-1]
    assert rss == "rss:a,b,d,e"
    assert duration >= 0

    assert len(report.skipped) == 1
    source, title, error = report.skipped[0]
    assert (source, title) == ("Quelle", "Titel c")
    assert TRIAL_PLACE.fullmatch(error), error

    # The state is read again and the caller's counters are cleared once,
    # after the failed run and before the chain runs over the four.
    assert chain.steps.count("reset") == 1
    first, reset, second = chain.steps.index("assemble:5"), chain.steps.index("reset"), chain.steps.index("assemble:4")
    assert first < reset < second
    assert "reload" in chain.steps[first:second]
    assert chain.steps[second:] == ["assemble:4", "render:4"]

    # The message of the exception stays out of the report and of the log.
    assert SOURCE_TEXT not in "".join(report.skipped[0])
    assert all(SOURCE_TEXT not in record.getMessage() for record in caplog.records)
    assert all(record.exc_info is None for record in caplog.records)


def test_a_failure_inside_src_is_reported_with_its_innermost_src_frame() -> None:
    outside = Path("/outside-the-repository/feed.xml")

    def rule(guids: list[str]) -> BaseException | None:
        if "c" in guids:
            validate_path(outside, "OUT_PATH")
        return None

    with pytest.raises(InvalidPathError) as direct:
        validate_path(outside, "OUT_PATH")
    frame = traceback.extract_tb(direct.value.__traceback__)[-1]
    place = Path(frame.filename).resolve().relative_to(REPO_ROOT).as_posix()
    assert place == "src/feed/config.py"

    chain, report = Chain(assemble_fails=rule), Recorder()
    chain.run(_items("a", "b", "c", "d", "e"), {}, report)

    assert report.skipped == [("Quelle", "Titel c", f"InvalidPathError in {place}:{frame.lineno}")]


def test_a_failure_without_a_frame_in_src_is_reported_by_its_class_only() -> None:
    """``_where`` on its own; through ``build_isolating`` the trial's frame is always there."""
    try:
        raise KeyError(SOURCE_TEXT)
    except KeyError as exc:
        assert isolation._where(exc) == "KeyError"
    assert isolation._where(OverflowError(SOURCE_TEXT)) == "OverflowError"


def test_the_second_run_and_the_trials_get_the_items_as_collected() -> None:
    chain, report = Chain(assemble_fails=on("c")), Recorder()
    collected = _items("a", "b", "c", "d", "e")
    as_collected = copy.deepcopy(cast(list[dict[str, Any]], collected))

    ordered, _rss, _duration = chain.run(collected, {}, report)

    first, second = chain.full_runs()
    assert first.received == as_collected
    # The first run changed every item and emptied the list it was given.
    assert collected == []
    assert second.received == [item for item in as_collected if item["guid"] != "c"]
    assert [call.received for call in chain.trials()] == [[item] for item in as_collected]
    # What comes back went through the chain once.
    assert [cast(dict[str, Any], item)["visits"] for item in ordered] == [1, 1, 1, 1]
    assert [cast(dict[str, Any], item)["lines"] for item in ordered] == [[guid, "assembled"] for guid in "abde"]


@pytest.mark.parametrize("failing_step", ["assemble", "render"])
def test_the_state_is_the_reloaded_state_plus_the_second_run(failing_step: str) -> None:
    rule = on("c")
    chain = Chain(assemble_fails=rule) if failing_step == "assemble" else Chain(render_fails=rule)
    state: State = copy.deepcopy(chain.disk)
    state["only in memory"] = {"first_seen": "2026-11-10T09:00:00+00:00"}

    chain.run(_items("a", "b", "c", "d", "e"), state, Recorder())

    expected: State = copy.deepcopy(chain.disk)
    for guid in "abde":
        expected[f"assembled:{guid}"] = {"run_size": 4}
        expected[f"rendered:{guid}"] = {"run_size": 4}
    assert state == expected
    # Both full runs worked on the caller's state, no trial did.
    assert {call.state_id for call in chain.full_runs()} == {id(state)}
    assert id(state) not in {call.state_id for call in chain.trials()}
    assert len(chain.trials()) == 5
    assert chain.disk == {"known": {"first_seen": "2026-11-01T08:00:00+00:00"}}


# --- how many may fail -----------------------------------------------------------


def test_half_of_the_items_failing_are_still_left_out() -> None:
    chain, report = Chain(assemble_fails=on("b", "d")), Recorder()

    ordered, rss, _duration = chain.run(_items("a", "b", "c", "d"), {}, report)

    assert _guids(ordered) == ["a", "c"]
    assert rss == "rss:a,c"
    assert [(source, title) for source, title, _error in report.skipped] == [("Quelle", "Titel b"), ("Quelle", "Titel d")]


@pytest.mark.parametrize(
    ("collected", "failing"),
    [
        (("a", "b", "c", "d"), ("a", "b", "d")),
        (("a", "b", "c"), ("a", "c")),
        (("a",), ("a",)),
    ],
)
def test_more_than_half_failing_raises_the_first_exception(
    collected: tuple[str, ...], failing: tuple[str, ...], caplog: pytest.LogCaptureFixture
) -> None:
    chain, report = Chain(assemble_fails=on(*failing)), Recorder()

    with caplog.at_level(logging.DEBUG), pytest.raises(ValueError) as raised:
        chain.run(_items(*collected), {}, report)

    assert raised.value is chain.raised[0]
    assert len(chain.raised) == 1 + len(failing)
    assert report.skipped == []
    assert _skip_records(caplog) == []
    assert len(chain.full_runs()) == 1


def test_a_failure_with_no_items_at_all_raises_the_first_exception() -> None:
    def rule(guids: list[str]) -> BaseException | None:
        return ValueError(SOURCE_TEXT)

    chain, report = Chain(assemble_fails=rule), Recorder()

    with pytest.raises(ValueError) as raised:
        chain.run([], {}, report)

    assert raised.value is chain.raised[0]
    assert report.skipped == []


def test_a_failure_that_needs_two_items_together_raises_the_first_exception(caplog: pytest.LogCaptureFixture) -> None:
    def rule(guids: list[str]) -> BaseException | None:
        return ValueError(SOURCE_TEXT) if {"b", "d"} <= set(guids) else None

    chain, report = Chain(assemble_fails=rule), Recorder()

    with caplog.at_level(logging.DEBUG), pytest.raises(ValueError) as raised:
        chain.run(_items("a", "b", "c", "d", "e"), {}, report)

    assert raised.value is chain.raised[0]
    assert len(chain.raised) == 1
    assert len(chain.trials()) == 5
    assert len(chain.full_runs()) == 1
    assert report.skipped == []
    assert _skip_records(caplog) == []


def test_a_second_run_that_fails_too_raises_the_first_exception(caplog: pytest.LogCaptureFixture) -> None:
    def rule(guids: list[str]) -> BaseException | None:
        if "c" in guids:
            return ValueError(SOURCE_TEXT)
        return RuntimeError("the run without the item") if len(guids) == 4 else None

    chain, report = Chain(assemble_fails=rule), Recorder()

    with caplog.at_level(logging.DEBUG), pytest.raises(ValueError) as raised:
        chain.run(_items("a", "b", "c", "d", "e"), {}, report)

    assert raised.value is chain.raised[0]
    assert isinstance(chain.raised[-1], RuntimeError)
    # The traceback of the build shows the first failure alone.
    assert raised.value.__cause__ is None
    assert raised.value.__suppress_context__ is True
    assert len(chain.full_runs()) == 2
    assert report.skipped == []
    assert _skip_records(caplog) == []


def test_two_identical_failing_items_are_both_left_out() -> None:
    chain, report = Chain(render_fails=on("x")), Recorder()
    collected = _items("a", "x", "b", "x", "c")
    assert collected[1] == collected[3]

    ordered, rss, _duration = chain.run(collected, {}, report)

    assert _guids(ordered) == ["a", "b", "c"]
    assert rss == "rss:a,b,c"
    assert [(source, title) for source, title, _error in report.skipped] == [("Quelle", "Titel x")] * 2


# --- logging -------------------------------------------------------------------


def test_the_trials_log_nothing_and_the_disable_level_comes_back(
    info_switched_off: int, caplog: pytest.LogCaptureFixture
) -> None:
    chain = Chain(assemble_fails=on("c"))

    with caplog.at_level(logging.WARNING):
        chain.run(_items("a", "b", "c", "d", "e"), {}, Recorder())

    assert logging.root.manager.disable == info_switched_off
    chain_lines = [record.getMessage() for record in caplog.records if record.name == CHAIN_LOG.name]
    assert chain_lines == ["assemble over 5 items", "assemble over 4 items", "render over 4 items"]
    # Five trials ran; the one of the failing item ended in the chain.
    assert [call.logging_off for call in chain.calls if len(call.received) == 1] == [True] * 9
    # During the two full runs the caller's level applied.
    assert len(chain.full_runs()) == 2


def test_the_disable_level_comes_back_when_a_trial_ends_the_process(info_switched_off: int) -> None:
    class Halt(BaseException):
        """Not an ``Exception``: passes through the trial's handler."""

    def rule(guids: list[str]) -> BaseException | None:
        if guids == ["c"]:
            return Halt()
        return ValueError(SOURCE_TEXT) if "c" in guids else None

    chain = Chain(assemble_fails=rule)

    with pytest.raises(Halt):
        chain.run(_items("a", "b", "c", "d", "e"), {}, Recorder())

    assert logging.root.manager.disable == info_switched_off


def test_the_disable_level_comes_back_when_the_build_fails(info_switched_off: int) -> None:
    chain = Chain(assemble_fails=on("a", "b", "c"))

    with pytest.raises(ValueError):
        chain.run(_items("a", "b", "c", "d"), {}, Recorder())

    assert logging.root.manager.disable == info_switched_off


def test_every_skipped_item_gets_one_error_line(caplog: pytest.LogCaptureFixture) -> None:
    chain, report = Chain(assemble_fails=on("b", "d")), Recorder()

    with caplog.at_level(logging.DEBUG):
        chain.run(_items("a", "b", "c", "d", "e"), {}, report)

    records = _skip_records(caplog)
    assert [(record.name, record.levelno) for record in records] == [("build_feed", logging.ERROR)] * 2
    assert [record.getMessage() for record in records] == [
        f"{SKIP_PREFIX} ({error}): {source} – {title}" for source, title, error in report.skipped
    ]
    assert [title for _source, title, _error in report.skipped] == ["Titel b", "Titel d"]
    # The lines follow the second run.
    last_chain_line = max(index for index, record in enumerate(caplog.records) if record.name == CHAIN_LOG.name)
    assert all(caplog.records.index(record) > last_chain_line for record in records)


# --- what is reported about the item -------------------------------------------


@pytest.mark.parametrize(
    "fields",
    [
        {},
        {"source": "", "title": ""},
        {"source": None, "title": None},
    ],
)
def test_an_item_without_source_and_title_is_reported_with_placeholders(fields: dict[str, Any]) -> None:
    chain, report = Chain(assemble_fails=on("c")), Recorder()
    collected = _items("a", "b", "d")
    collected.insert(2, cast(FeedItem, {"guid": "c", "lines": [], **fields}))

    ordered, _rss, _duration = chain.run(collected, {}, report)

    assert _guids(ordered) == ["a", "b", "d"]
    assert [(source, title) for source, title, _error in report.skipped] == [("unbekannt", "(ohne Titel)")]


def test_control_characters_are_taken_out_of_source_and_title(caplog: pytest.LogCaptureFixture) -> None:
    chain, report = Chain(assemble_fails=on("c")), Recorder()
    collected = _items("a", "b", "c", "d", "e")
    collected[2]["source"] = "Wiener\x00 Linien\N{RIGHT-TO-LEFT OVERRIDE}"
    collected[2]["title"] = "U6: Rettungs\x1beinsatz\N{ZERO WIDTH SPACE}\N{LINE SEPARATOR}Alser\x07 Straße\N{ZERO WIDTH NO-BREAK SPACE}"

    with caplog.at_level(logging.DEBUG):
        chain.run(collected, {}, report)

    assert [(source, title) for source, title, _error in report.skipped] == [
        ("Wiener Linien", "U6: Rettungseinsatz Alser Straße")
    ]
    assert [record.getMessage().partition("): ")[2] for record in _skip_records(caplog)] == [
        "Wiener Linien – U6: Rettungseinsatz Alser Straße"
    ]


def test_a_title_of_several_lines_is_one_line_in_the_run_report() -> None:
    chain, report = Chain(assemble_fails=on("c")), RunReport([])
    collected = _items("a", "b", "c", "d", "e")
    collected[2]["source"] = "Wiener\nLinien"
    collected[2]["title"] = "U6: Rettungseinsatz\r\nzweite Zeile\n\tdritte\x0bvierte\N{PARAGRAPH SEPARATOR}fünfte"

    chain.run(collected, {}, report)

    assert len(report.skipped_items) == 1
    entry = report.skipped_items[0]
    assert (entry["source"], entry["title"]) == (
        "Wiener Linien", "U6: Rettungseinsatz zweite Zeile dritte vierte fünfte"
    )
    assert TRIAL_PLACE.fullmatch(entry["error"]), entry["error"]


def test_two_identical_failing_items_are_one_entry_of_the_run_report() -> None:
    chain, report = Chain(assemble_fails=on("x")), RunReport([])

    ordered, _rss, _duration = chain.run(_items("a", "x", "b", "x", "c"), {}, report)

    assert _guids(ordered) == ["a", "b", "c"]
    assert [(entry["source"], entry["title"]) for entry in report.skipped_items] == [("Quelle", "Titel x")]
