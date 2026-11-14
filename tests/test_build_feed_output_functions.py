"""The functions that write a build's outputs and print the lint report, one at a time.

``src/feed_build/outputs.py`` and ``src/feed_build/lint_report.py`` hold the
blocks of ``build_feed.main`` and ``build_feed.lint`` that became functions
on 2026-11-14 (operator decision of that day, ``docs/modularisierung.md``).
``tests/test_build_feed_outputs.py`` runs ``main`` and holds the order of
the steps and the failure paths; these tests call each function directly.
"""

from __future__ import annotations

import inspect
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import src.build_feed as bf
from src.feed.reporting import DuplicateSummary, FeedHealthMetrics, RunReport
from src.feed_types import FeedItem
from tests._conftest.build_feed_bindings import part, setattr_all


def _item(guid: str, title: str, now: datetime) -> FeedItem:
    return {
        "guid": guid,
        "title": title,
        "description": "",
        "link": "",
        "source": "Wiener Linien",
        "category": "Störung",
        "pubDate": now - timedelta(minutes=5),
        "ends_at": now + timedelta(hours=6),
    }


class _Stats:
    filtered_count = 7
    deduped_count = 5
    duplicates_removed = 2
    duplicate_summaries = [DuplicateSummary(dedupe_key="k", count=3, titles=("a", "b"))]
    filter_duration = 0.25
    dedupe_duration = 0.5


def test_health_metrics_from_reads_the_counters() -> None:
    metrics = part(bf, "outputs").health_metrics_from(raw_count=9, stats=_Stats(), new_items_count=4)

    assert metrics == FeedHealthMetrics(
        raw_items=9,
        filtered_items=7,
        deduped_items=5,
        new_items=4,
        duplicate_count=2,
        duplicates=tuple(_Stats.duplicate_summaries),
    )


def test_write_health_outputs_survives_both_writers_failing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    outputs = part(bf, "outputs")
    calls: list[tuple[str, Path]] = []

    def fail(label: str) -> Any:
        def write(report: Any, metrics: Any, *, output_path: Path) -> None:
            calls.append((label, output_path))
            raise RuntimeError(f"{label} failed")

        return write

    monkeypatch.setattr(outputs, "write_feed_health_report", fail("markdown"))
    monkeypatch.setattr(outputs, "write_feed_health_json", fail("json"))
    report = RunReport([])
    metrics = outputs.health_metrics_from(raw_count=0, stats=_Stats(), new_items_count=0)

    with caplog.at_level(logging.WARNING, logger="build_feed"):
        outputs.write_health_outputs(report, metrics, health_path=tmp_path / "h.md", health_json_path=tmp_path / "h.json")

    assert calls == [("markdown", tmp_path / "h.md"), ("json", tmp_path / "h.json")]
    assert [(record.levelno, record.getMessage()) for record in caplog.records if record.name == "build_feed"] == [
        (logging.WARNING, "Feed-Health-Markdown konnte nicht geschrieben werden: markdown failed"),
        (logging.WARNING, "Feed-Health-JSON konnte nicht geschrieben werden: json failed"),
    ]


def test_write_feed_reads_out_path_when_it_runs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    outputs = part(bf, "outputs")
    seen: list[tuple[Path, str]] = []

    def validate_path(path: Path, name: str) -> Path:
        seen.append((path, name))
        return path

    setattr_all(monkeypatch, bf, "validate_path", validate_path)
    monkeypatch.setattr(bf.feed_config, "OUT_PATH", str(tmp_path / "first.xml"))
    assert outputs.write_feed("eins\n") == tmp_path / "first.xml"
    monkeypatch.setattr(bf.feed_config, "OUT_PATH", str(tmp_path / "second.xml"))
    assert outputs.write_feed("zwei ä\n") == tmp_path / "second.xml"

    assert seen == [(tmp_path / "first.xml", "OUT_PATH"), (tmp_path / "second.xml", "OUT_PATH")]
    assert (tmp_path / "first.xml").read_bytes() == b"eins\n"
    assert (tmp_path / "second.xml").read_bytes() == "zwei ä\n".encode()
    assert ((tmp_path / "second.xml").stat().st_mode & 0o777) == 0o644


def test_the_outputs_read_feed_config_as_their_own_global(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # A test that replaces the name ``feed_config`` (``setattr_all``) reaches
    # the functions here too.
    outputs = part(bf, "outputs")
    stand_in = SimpleNamespace(OUT_PATH=str(tmp_path / "stand-in.xml"), MAX_ITEMS=1)
    setattr_all(monkeypatch, bf, "feed_config", stand_in)
    setattr_all(monkeypatch, bf, "validate_path", lambda path, name: path)

    assert outputs.write_feed("x") == tmp_path / "stand-in.xml"
    with caplog.at_level(logging.INFO, logger="build_feed"):
        outputs.record_successful_build(
            RunReport([]), tmp_path / "stand-in.xml", [{}, {}], raw_count=2, stats=_Stats(), collect_duration=0.0,
            rss_duration=0.0, total_duration=0.0,
        )

    assert (tmp_path / "stand-in.xml").read_text(encoding="utf-8") == "x"
    assert [record.getMessage() for record in caplog.records if record.name == "build_feed"] == [
        f"Feed geschrieben: {tmp_path / 'stand-in.xml'} (1 Items) in 0.00s (RSS-Erzeugung: 0.00s)"
    ]


def test_write_en_mirror_renders_english_next_to_the_feed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    outputs = part(bf, "outputs")
    now = datetime.now(UTC)
    items = [_item("g1", "16A: Rettungseinsatz", now)]
    state: dict[str, Any] = {"k": {}}
    calls: list[tuple[Any, ...]] = []

    def make_rss(*args: Any, **kwargs: Any) -> str:
        calls.append((*args, kwargs))
        return "<rss/>"

    setattr_all(monkeypatch, bf, "_make_rss", make_rss)
    setattr_all(monkeypatch, bf, "validate_path", lambda path, name: path)

    outputs.write_en_mirror(tmp_path / "anders.xml", items, now, state)

    assert calls == [(items, now, state, {"lang": "en"})]
    assert calls[0][0] is items and calls[0][2] is state
    assert (tmp_path / "feed.en.xml").read_bytes() == b"<rss/>"
    assert not (tmp_path / "anders.xml").exists()


def test_save_state_or_warn_passes_the_state_without_deletions(monkeypatch: pytest.MonkeyPatch) -> None:
    outputs = part(bf, "outputs")
    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    monkeypatch.setattr(outputs, "_save_state", lambda *args, **kwargs: calls.append((args, kwargs)))
    report = RunReport([])
    state: dict[str, Any] = {"k": {"first_seen": "2026-11-14T00:00:00+00:00"}}

    outputs.save_state_or_warn(state, report)

    assert calls == [((state,), {})] and calls[0][0][0] is state
    assert report.warnings == []


def test_record_successful_build_closes_the_report_and_logs_the_shown_items(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    outputs = part(bf, "outputs")
    now = datetime.now(UTC)
    items = [_item(f"g{n}", f"{n}A: Umleitung", now) for n in range(5)]
    report = RunReport([])
    # Read when the function runs, not at import.
    monkeypatch.setattr(bf.feed_config, "MAX_ITEMS", 3)

    with caplog.at_level(logging.INFO, logger="build_feed"):
        outputs.record_successful_build(
            report, tmp_path / "feed.xml", items, raw_count=8, stats=_Stats(), collect_duration=1.0,
            rss_duration=2.0, total_duration=4.125,
        )

    assert [record.getMessage() for record in caplog.records if record.name == "build_feed"] == [
        f"Feed geschrieben: {tmp_path / 'feed.xml'} (3 Items) in 4.12s (RSS-Erzeugung: 2.00s)"
    ]
    assert report.build_successful is True
    assert report.raw_item_count == 8
    assert report.final_item_count == 5
    assert report.feed_path == (tmp_path / "feed.xml").as_posix()
    assert report.durations == {"collect": 1.0, "filter": 0.25, "dedupe": 0.5, "rss": 2.0, "total": 4.125}


# ------------------------------------------------------------ the lint report


class _Report:
    def __init__(self, errors: bool, out: pytest.CaptureFixture[str]) -> None:
        self.errors = errors
        self.out = out
        self.printed_before: str | None = None

    def has_errors(self) -> bool:
        self.printed_before = self.out.readouterr().out
        return self.errors


_HEAD = (
    "Feed-Lint Bericht\n"
    "==================\n"
    "Rohdaten: 9\n"
    "Nach Altersfilter: 7\n"
    "Nach Deduplizierung: 5 (entfernte Duplikate: 2)\n"
    "Neue Items (vs. State): 4\n"
)


def _lint(report: _Report, **findings: Any) -> int:
    metrics = part(bf, "outputs").health_metrics_from(raw_count=9, stats=_Stats(), new_items_count=4)
    args = {"duplicate_summaries": [], "stale_cache_messages": [], "missing_guid_items": [], **findings}
    return int(part(bf, "lint_report").print_lint_report(metrics, report=report, **args))


def test_lint_report_without_findings(capsys: pytest.CaptureFixture[str]) -> None:
    report = _Report(False, capsys)

    assert _lint(report) == 0

    assert report.printed_before == _HEAD
    assert capsys.readouterr().out == "\nKeine strukturellen Probleme gefunden.\n"


def test_lint_report_prints_the_findings_before_it_asks_for_provider_errors(
    capsys: pytest.CaptureFixture[str],
) -> None:
    report = _Report(False, capsys)
    summaries = [
        DuplicateSummary(dedupe_key="k1", count=3, titles=("a", "", "c")),
        DuplicateSummary(dedupe_key="k2", count=2, titles=()),
    ]
    missing: list[Any] = [{"source": "ÖBB\x1b[31m", "title": "S1\u202e"}, {}]

    source, title = bf._sanitize_text("ÖBB\x1b[31m"), bf._sanitize_text("S1\u202e")

    code = _lint(report, duplicate_summaries=summaries, stale_cache_messages=["wl: 30 h"], missing_guid_items=missing)

    assert code == 1
    assert report.printed_before == _HEAD + (
        "\nErkannte Duplikat-Gruppen:\n"
        "- 3× Schlüssel k1: a, <ohne Titel>, c\n"
        "- 2× Schlüssel k2: <keine Beispiele>\n"
        "\nVeraltete Cache-Dateien:\n"
        "- wl: 30 h\n"
        "\nEinträge ohne GUID:\n"
        f"- {source}: {title}\n"
        "- unbekannt: <ohne Titel>\n"
    )
    assert "\x1b" not in report.printed_before and "\u202e" not in report.printed_before
    assert capsys.readouterr().out == ""


def test_lint_report_exit_codes(capsys: pytest.CaptureFixture[str]) -> None:
    # Stale caches alone: exit 1, and still "no structural problems".
    report = _Report(False, capsys)
    assert _lint(report, stale_cache_messages=["oebb: 40 h"]) == 1
    assert capsys.readouterr().out == "\nKeine strukturellen Probleme gefunden.\n"

    # A finding alone: exit 1, and the closing line is left out.
    report = _Report(False, capsys)
    assert _lint(report, missing_guid_items=[{"title": "x"}]) == 1
    assert capsys.readouterr().out == ""

    report = _Report(False, capsys)
    assert _lint(report, duplicate_summaries=[DuplicateSummary(dedupe_key="k", count=2, titles=("a",))]) == 1
    assert capsys.readouterr().out == ""

    # A provider error outranks every finding.
    report = _Report(True, capsys)
    assert _lint(report, missing_guid_items=[{"title": "x"}]) == 2
    assert capsys.readouterr().out == "\nProvider-Fehler erkannt – siehe Log-Ausgabe für Details.\n"

    report = _Report(True, capsys)
    assert _lint(report) == 2
    assert capsys.readouterr().out == "\nProvider-Fehler erkannt – siehe Log-Ausgabe für Details.\n"


def test_parameters_a_call_could_swap_are_passed_by_name() -> None:
    # Two counts, two paths: a call that swapped them would still run.
    for module in (part(bf, "outputs"), part(bf, "lint_report")):
        for name in module.__all__:
            func = getattr(module, name)
            if not inspect.isfunction(func):
                continue
            positional = [
                parameter.annotation
                for parameter in inspect.signature(func).parameters.values()
                if parameter.kind in (parameter.POSITIONAL_ONLY, parameter.POSITIONAL_OR_KEYWORD)
            ]
            assert len(positional) == len(set(positional)), f"{module.__name__}.{name}: {positional}"

    # The three lists of the lint report differ in type, so the loop above
    # passes them either way; their names are held here.
    lint_parameters = inspect.signature(part(bf, "lint_report").print_lint_report).parameters.values()
    assert [p.name for p in lint_parameters if p.kind is not p.KEYWORD_ONLY] == ["metrics"]
