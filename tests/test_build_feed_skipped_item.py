"""``build_feed.main()`` with an item on which the chain or the rendering fails.

Operator decision of 2026-11-10: such an item leaves the run, the feed is
built with the other items, and the health report names the item. When the
failure is not the matter of single items, the build fails as it did before
and the published feed stays as it is.

Every run writes into a directory of its own under ``tmp_path``. The unit
tests of ``build_isolating`` are in ``tests/test_build_isolation.py``; entries
that are no mapping, ledger rows that cannot be written, items that cannot be
copied and a second run that fails are in
``tests/test_build_feed_skipped_item_edges.py``.
"""

from __future__ import annotations

import copy
import json
import re
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, cast

import pytest

import src.build_feed as bf
from src.feed.reporting import (
    _MAX_REPORT_MESSAGE_COUNT,
    _MAX_REPORT_MESSAGE_LENGTH,
    _REPORT_TRUNCATION_MARKER,
    FeedHealthMetrics,
    RunReport,
    build_feed_health_payload,
    render_feed_health_markdown,
)
from src.feed_types import FeedItem
from src.utils import stats as stats_utils
from src.utils.text import escape_markdown
from tests._conftest.build_feed_bindings import part, setattr_all

# Stands for text of the source inside an exception message.
SOURCE_TEXT = "wording of the notice 4711"
SECTION = "## Übersprungene Meldungen"
FAILING = "guid-3"
OTHERS = ["guid-1", "guid-2", "guid-4", "guid-5"]
# The innermost frame under src/ when the stand-in step raises.
PLACE = {
    "chain": re.compile(r"RuntimeError in src/build_feed\.py:\d+"),
    "rendering": re.compile(r"RuntimeError in src/feed_build/rss\.py:\d+"),
}
METRICS = FeedHealthMetrics(
    raw_items=5, filtered_items=4, deduped_items=4, new_items=1, duplicate_count=0, duplicates=()
)


def _wl(number: int, now: datetime) -> FeedItem:
    return {
        "source": "Wiener Linien",
        "category": "Störung",
        "title": f"{number}A: Rettungseinsatz Ort{number}",
        "description": f"Linie {number}A: Fahrtbehinderung wegen eines Rettungseinsatzes bei Ort{number}.",
        "guid": f"guid-{number}",
        "link": "",
        "pubDate": now - timedelta(minutes=5 + number),
        "starts_at": now - timedelta(minutes=5 + number),
        "ends_at": now + timedelta(hours=6),
    }


def _collected(now: datetime) -> list[FeedItem]:
    """Five running items, a second copy of the first and one that has run out."""
    items = [_wl(number, now) for number in range(1, 6)]
    expired = _wl(6, now)
    expired["guid"] = "guid-expired"
    expired["ends_at"] = now - timedelta(hours=2)
    return [*items, copy.deepcopy(items[0]), expired]


def _earlier_state(now: datetime) -> dict[str, Any]:
    """The state file as an earlier build left it."""

    def seen(hours: float) -> dict[str, str]:
        return {"first_seen": (now - timedelta(hours=hours)).isoformat()}

    return {"guid-1": seen(1), FAILING: seen(0.5), "guid-expired": seen(3), "guid-gone": seen(2)}


@dataclass
class Run:
    """Where one ``main()`` run wrote, and what it wrote."""

    directory: Path
    code: int | None = None

    def _channel(self, name: str) -> ET.Element:
        channel = ET.parse(self.directory / name).getroot().find("channel")
        assert channel is not None
        return channel

    def feed(self, name: str = "feed.xml") -> list[tuple[str, str, str]]:
        """(guid, title, description) of every item of the feed file *name*."""
        return [
            (item.findtext("guid") or "", item.findtext("title") or "", item.findtext("description") or "")
            for item in self._channel(name).findall("item")
        ]

    def guids(self, name: str = "feed.xml") -> list[str]:
        return [guid for guid, _title, _description in self.feed(name)]

    def language(self, name: str = "feed.xml") -> str | None:
        """The channel ``<language>`` of the feed file *name*."""
        return self._channel(name).findtext("language")

    @property
    def health(self) -> dict[str, Any]:
        return cast(dict[str, Any], json.loads((self.directory / "feed-health.json").read_text(encoding="utf-8")))

    @property
    def health_markdown(self) -> str:
        return (self.directory / "feed-health.md").read_text(encoding="utf-8")

    @property
    def state(self) -> dict[str, Any]:
        return cast(dict[str, Any], json.loads((self.directory / "state.json").read_text(encoding="utf-8")))


def _prepare(
    monkeypatch: pytest.MonkeyPatch, directory: Path, collected: list[FeedItem], state: dict[str, Any] | None = None
) -> Run:
    """Point ``main()`` at *directory* and let it collect *collected*."""
    directory.mkdir()
    setattr_all(monkeypatch, bf, "validate_path", lambda path, name: path)
    monkeypatch.setattr(bf.feed_config, "OUT_PATH", directory / "feed.xml")
    monkeypatch.setattr(bf.feed_config, "STATE_FILE", directory / "state.json")
    monkeypatch.setattr(bf.feed_config, "FEED_HEALTH_PATH", directory / "feed-health.md")
    monkeypatch.setattr(bf.feed_config, "FEED_HEALTH_JSON_PATH", directory / "feed-health.json")
    monkeypatch.setattr(bf, "refresh_from_env", lambda: None)
    monkeypatch.setattr(bf, "_invoke_collect_items", lambda report: copy.deepcopy(collected))
    # The English feed keeps the German text: no model is looked for.
    setattr_all(monkeypatch, bf, "_get_translation_pipeline", lambda: None)
    if state is not None:
        (directory / "state.json").write_text(json.dumps(state), encoding="utf-8")
    return Run(directory)


def _build(
    monkeypatch: pytest.MonkeyPatch, directory: Path, collected: list[FeedItem], state: dict[str, Any] | None = None
) -> Run:
    run = _prepare(monkeypatch, directory, collected, state)
    run.code = bf.main()
    return run


def _fail_on(
    monkeypatch: pytest.MonkeyPatch, step: str, failing: Callable[[FeedItem], bool]
) -> list[BaseException]:
    """Make the chain or the rendering raise when it meets an item *failing* names.

    ``chain``: the exact-duplicate step of ``_assemble_feed_items`` raises for
    the whole list. ``rendering``: ``_emit_item`` raises for the item, after
    the items before it were rendered. Returns the exceptions in the order
    they were raised.
    """
    raised: list[BaseException] = []

    def stop() -> None:
        raised.append(RuntimeError(SOURCE_TEXT))
        raise raised[-1]

    if step == "chain":
        real_dedupe = bf._dedupe_items

        def dedupe(items: list[FeedItem]) -> list[FeedItem]:
            if any(failing(item) for item in items):
                stop()
            return real_dedupe(items)

        monkeypatch.setattr(bf, "_dedupe_items", dedupe)
    else:
        rss = part(bf, "rss")
        real_emit = rss._emit_item

        def emit(item: FeedItem, *args: Any, **kwargs: Any) -> Any:
            if failing(item):
                stop()
            return real_emit(item, *args, **kwargs)

        monkeypatch.setattr(rss, "_emit_item", emit)
    return raised


def _is_failing(item: FeedItem) -> bool:
    return item.get("guid") == FAILING


def _pair(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, step: str) -> tuple[Run, Run, datetime]:
    """A build that meets the failing item, and one that never collected it."""
    now = datetime.now(UTC)
    collected = _collected(now)
    _fail_on(monkeypatch, step, _is_failing)
    skipped = _build(monkeypatch, tmp_path / "skipped", collected, _earlier_state(now))
    absent = [item for item in collected if not _is_failing(item)]
    reference = _build(monkeypatch, tmp_path / "reference", absent, _earlier_state(now))
    return skipped, reference, now


def _without_run_times(value: Any, started: datetime) -> Any:
    """*value* with every moment at or after *started* replaced by one marker."""
    if isinstance(value, dict):
        return {key: _without_run_times(entry, started) for key, entry in value.items()}
    if isinstance(value, list):
        return [_without_run_times(entry, started) for entry in value]
    if isinstance(value, str):
        try:
            moment = datetime.fromisoformat(value)
        except ValueError:
            return value
        if moment.tzinfo is not None and moment >= started:
            return "<this run>"
    return value


# --- one item fails: the feed -----------------------------------------------------


@pytest.mark.parametrize("step", ["chain", "rendering"])
def test_both_feeds_are_written_without_the_failing_item(
    step: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, time_line_today: None
) -> None:
    # ``time_line_today``: both builds word the time line against the same day.
    skipped, reference, _now = _pair(monkeypatch, tmp_path, step)

    assert skipped.code == 0
    assert sorted(skipped.guids()) == OTHERS
    assert sorted(skipped.guids("feed.en.xml")) == OTHERS
    # Order, titles and texts are those of a build that never saw the item.
    assert reference.code == 0
    assert skipped.feed() == reference.feed()
    assert skipped.feed("feed.en.xml") == reference.feed("feed.en.xml")
    # ``feed.xml`` is the German rendering also after a second run.
    assert (skipped.language(), skipped.language("feed.en.xml")) == ("de", "en")
    assert (reference.language(), reference.language("feed.en.xml")) == ("de", "en")


def test_an_item_ending_in_the_year_9999_does_not_stop_the_build(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The item of the replay of 2026-11-10: it is shown or it is reported, the others are shown."""
    now = datetime.now(UTC)
    collected = [_wl(number, now) for number in range(1, 6)]
    collected[2]["ends_at"] = datetime(9999, 12, 31, 23, 59, 59, tzinfo=timezone(timedelta(hours=1)))

    run = _build(monkeypatch, tmp_path / "run", collected)

    assert run.code == 0
    shown = run.guids()
    assert [guid for guid in OTHERS if guid not in shown] == []
    reported = [entry["title"] for entry in run.health["skipped_items"]]
    assert (FAILING in shown) != (reported == ["3A: Rettungseinsatz Ort3"])
    assert sorted(run.guids("feed.en.xml")) == sorted(shown)


# --- one item fails: the health report ----------------------------------------------


@pytest.mark.parametrize("step", ["chain", "rendering"])
def test_the_health_report_names_the_skipped_item(step: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    skipped, reference, _now = _pair(monkeypatch, tmp_path, step)

    entries = skipped.health["skipped_items"]
    assert len(entries) == 1
    assert {key: entries[0][key] for key in ("source", "title")} == {
        "source": "Wiener Linien", "title": "3A: Rettungseinsatz Ort3",
    }
    assert sorted(entries[0]) == ["error", "source", "title"]
    assert PLACE[step].fullmatch(entries[0]["error"]), entries[0]["error"]
    assert skipped.health["run"]["status"] == "success"

    markdown = skipped.health_markdown.splitlines()
    assert "- **Status:** ✅ Erfolgreich" in markdown
    section = markdown.index(SECTION)
    assert markdown[section + 1 : section + 4] == [
        "",
        f"- Wiener Linien: 3A: Rettungseinsatz Ort3 ({escape_markdown(entries[0]['error'])})",
        "",
    ]

    # The message of the exception is in neither file.
    assert SOURCE_TEXT not in json.dumps(skipped.health, ensure_ascii=False)
    assert SOURCE_TEXT not in skipped.health_markdown

    # A build without such an item: the key is there and empty, the section is not.
    assert reference.health["skipped_items"] == []
    assert SECTION not in reference.health_markdown


@pytest.mark.parametrize("step", ["chain", "rendering"])
def test_the_counters_are_those_of_a_build_without_the_item(
    step: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    skipped, reference, _now = _pair(monkeypatch, tmp_path, step)

    # Seven collected: four shown, the copy of the first, the one that ran out, the failing one.
    assert reference.health["metrics"] == {
        "raw_items": 6, "filtered_items": 5, "deduped_items": 4, "new_items": 3, "duplicate_count": 1,
    }
    assert skipped.health["metrics"] == {**reference.health["metrics"], "raw_items": 7}
    assert skipped.health["duplicates"] == reference.health["duplicates"]
    assert (skipped.health["run"]["raw_item_count"], skipped.health["run"]["final_item_count"]) == (7, 4)
    assert (reference.health["run"]["raw_item_count"], reference.health["run"]["final_item_count"]) == (6, 4)


@pytest.mark.parametrize("step", ["chain", "rendering"])
def test_the_state_file_is_that_of_a_build_without_the_item(
    step: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    skipped, reference, now = _pair(monkeypatch, tmp_path, step)

    # The earlier entry of the failing item stays, the one that ran out goes,
    # and nothing of the failed run or of the single-item trials is left.
    assert sorted(skipped.state) == sorted([*OTHERS, FAILING, "guid-gone"])
    assert skipped.state[FAILING] == _earlier_state(now)[FAILING]
    assert _without_run_times(skipped.state, now) == _without_run_times(reference.state, now)


# --- the failure is not the matter of single items ----------------------------------


@pytest.mark.parametrize("step", ["chain", "rendering"])
@pytest.mark.parametrize("failing", [tuple(OTHERS) + (FAILING,), ("guid-1", "guid-2", FAILING)])
def test_the_build_fails_as_before_when_more_than_half_of_the_items_fail(
    step: str, failing: tuple[str, ...], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Passes on the code before the change too: this path is unchanged."""
    now = datetime.now(UTC)
    collected = [_wl(number, now) for number in range(1, 6)]
    raised = _fail_on(monkeypatch, step, lambda item: item.get("guid") in failing)
    run = _prepare(monkeypatch, tmp_path / "run", collected, _earlier_state(now))
    for name in ("feed.xml", "feed.en.xml"):
        (run.directory / name).write_text(f"published {name}", encoding="utf-8")
    state_before = (run.directory / "state.json").read_bytes()

    with pytest.raises(RuntimeError) as failure:
        bf.main()

    assert failure.value is raised[0]
    for name in ("feed.xml", "feed.en.xml"):
        assert (run.directory / name).read_text(encoding="utf-8") == f"published {name}"
    assert (run.directory / "state.json").read_bytes() == state_before
    assert run.health["run"]["status"] == "error"
    assert run.health["run"]["feed_path"] is None
    assert run.health["metrics"]["raw_items"] == 5
    assert any(f"RuntimeError: {SOURCE_TEXT}" in error for error in run.health["errors"])
    assert "- **Status:** ❌ Fehlerhaft" in run.health_markdown.splitlines()


@pytest.mark.parametrize("step", ["chain", "rendering"])
def test_a_failed_build_reports_no_skipped_item(step: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    now = datetime.now(UTC)
    _fail_on(monkeypatch, step, lambda item: True)
    run = _prepare(monkeypatch, tmp_path / "run", [_wl(number, now) for number in range(1, 6)])

    with pytest.raises(RuntimeError):
        bf.main()

    assert run.health["skipped_items"] == []
    assert SECTION not in run.health_markdown
    assert not (run.directory / "feed.xml").exists()
    assert not (run.directory / "state.json").exists()


@pytest.mark.parametrize(
    ("step", "counted"),
    # The chain stand-in raises after the age filter, the rendering after the whole chain.
    [("chain", {"filtered_items": 5, "deduped_items": 0, "new_items": 0}),
     ("rendering", {"filtered_items": 5, "deduped_items": 5, "new_items": 5})],
)
def test_a_failed_build_reports_what_its_one_run_counted(
    step: str, counted: dict[str, int], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Passes on the code before the change too: the single-item trials count nothing."""
    now = datetime.now(UTC)
    _fail_on(monkeypatch, step, lambda item: True)
    run = _prepare(monkeypatch, tmp_path / "run", [_wl(number, now) for number in range(1, 6)])

    with pytest.raises(RuntimeError):
        bf.main()

    assert run.health["metrics"] == {"raw_items": 5, "duplicate_count": 0, **counted}


def _ledger_rows() -> list[str]:
    """The rows of the statistics ledger of this test (fixture ``isolate_stats_writes``)."""
    files = sorted(Path(stats_utils.DEFAULT_STATS_DIR).glob("stoerungen_*.csv"))
    return [row for path in files for row in path.read_text(encoding="utf-8").splitlines()[1:]]


@pytest.mark.parametrize("step", ["chain", "rendering"])
def test_the_ledger_counts_what_the_published_feed_shows(
    step: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The failed run and the single-item trials enter nothing into the ledger."""
    now = datetime.now(UTC)
    collected = [_wl(number, now) for number in range(1, 6)]
    _fail_on(monkeypatch, step, _is_failing)

    run = _build(monkeypatch, tmp_path / "run", collected)

    assert run.guids() == OTHERS
    rows = _ledger_rows()
    assert len(rows) == 4, rows
    assert all(",Wiener Linien," in row for row in rows)


@pytest.mark.parametrize("step", ["chain", "rendering"])
def test_a_failed_build_enters_nothing_into_the_ledger(
    step: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    now = datetime.now(UTC)
    _fail_on(monkeypatch, step, lambda item: item.get("guid") != "guid-1")
    _prepare(monkeypatch, tmp_path / "run", [_wl(number, now) for number in range(1, 6)])

    with pytest.raises(RuntimeError):
        bf.main()

    assert _ledger_rows() == []


def test_a_build_without_a_failure_enters_every_new_item_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    now = datetime.now(UTC)
    collected = [_wl(number, now) for number in range(1, 6)]

    _build(monkeypatch, tmp_path / "first", collected)
    assert len(_ledger_rows()) == 5
    # The same items again, now known to the state: no further row.
    _build(monkeypatch, tmp_path / "second", collected, json.loads((tmp_path / "first" / "state.json").read_text("utf-8")))
    assert len(_ledger_rows()) == 5


# --- RunReport.add_skipped_item -----------------------------------------------------


def test_the_run_report_keeps_an_entry_once() -> None:
    report = RunReport([])
    assert report.skipped_items == []

    report.add_skipped_item("Wiener Linien", "U6: Rettungseinsatz", "OverflowError in src/feed_build/filters.py:75")
    report.add_skipped_item("Wiener Linien", "U6: Rettungseinsatz", "OverflowError in src/feed_build/filters.py:75")
    report.add_skipped_item("Wiener Linien", "U6: Rettungseinsatz", "ValueError in src/feed_build/rss.py:10")

    assert report.skipped_items == [
        {"source": "Wiener Linien", "title": "U6: Rettungseinsatz", "error": "OverflowError in src/feed_build/filters.py:75"},
        {"source": "Wiener Linien", "title": "U6: Rettungseinsatz", "error": "ValueError in src/feed_build/rss.py:10"},
    ]


def test_the_run_report_keeps_a_bounded_number_of_entries() -> None:
    report = RunReport([])

    for number in range(_MAX_REPORT_MESSAGE_COUNT + 25):
        report.add_skipped_item("Wiener Linien", f"{number}A: Rettungseinsatz", "OverflowError")

    assert len(report.skipped_items) == _MAX_REPORT_MESSAGE_COUNT
    assert report.skipped_items[-1]["title"] == f"{_MAX_REPORT_MESSAGE_COUNT - 1}A: Rettungseinsatz"


def test_the_run_report_bounds_and_cleans_the_texts() -> None:
    report = RunReport([])
    long = "x" * (_MAX_REPORT_MESSAGE_LENGTH + 500)

    report.add_skipped_item(long, long, long)
    report.add_skipped_item("  Wiener\n Linien ", "U6:\r\n\tRettungseinsatz  Alser Straße", " OverflowError\nin src ")
    report.add_skipped_item("", " \n ", "")

    bounded, cleaned, empty = report.skipped_items
    for text in bounded.values():
        assert len(text) == _MAX_REPORT_MESSAGE_LENGTH
        assert text.endswith(_REPORT_TRUNCATION_MARKER)
    assert cleaned == {
        "source": "Wiener Linien", "title": "U6: Rettungseinsatz Alser Straße", "error": "OverflowError in src",
    }
    assert empty == {"source": "unbekannt", "title": "(ohne Titel)", "error": "unbekannt"}


# --- the health report on its own ---------------------------------------------------


def test_the_health_report_lists_the_entries_of_the_run_report() -> None:
    report = RunReport([])
    assert build_feed_health_payload(report, METRICS)["skipped_items"] == []
    assert SECTION not in render_feed_health_markdown(report, METRICS)

    report.add_skipped_item("Wiener Linien", "U6: *Rettungseinsatz* [Alser Straße]", "OverflowError in src/feed_build/filters.py:75")
    report.add_skipped_item("*ÖBB* [Bahn](https://example.org)", "S80: Ersatzbus", "ValueError in src/feed_build/rss.py:10")

    assert build_feed_health_payload(report, METRICS)["skipped_items"] == report.skipped_items
    assert build_feed_health_payload(report, METRICS)["skipped_items"] is not report.skipped_items
    markdown = render_feed_health_markdown(report, METRICS).splitlines()
    section = markdown.index(SECTION)
    assert markdown[section + 1 : section + 5] == [
        "",
        "- Wiener Linien: U6: "
        + escape_markdown("*Rettungseinsatz* [Alser Straße]")
        + " ("
        + escape_markdown("OverflowError in src/feed_build/filters.py:75")
        + ")",
        "- "
        + escape_markdown("*ÖBB* [Bahn](https://example.org)")
        + ": S80: Ersatzbus ("
        + escape_markdown("ValueError in src/feed_build/rss.py:10")
        + ")",
        "",
    ]
    # Source, title and error are text in the Markdown report, not markup.
    for raw in ("*Rettungseinsatz*", "*ÖBB*", "[Bahn](https://example.org)", "src/feed_build/"):
        assert raw not in "\n".join(markdown[section:])


def test_a_report_without_the_list_counts_as_no_skipped_item() -> None:
    report = RunReport([])
    del report.skipped_items

    assert build_feed_health_payload(report, METRICS)["skipped_items"] == []
    assert SECTION not in render_feed_health_markdown(report, METRICS)
