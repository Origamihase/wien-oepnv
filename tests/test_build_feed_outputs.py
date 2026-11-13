"""What a build writes, and what still happens when one write fails.

Since 2026-11-14 the blocks of ``build_feed.main`` that write the feed
files, the state and the health report are functions of
``src/feed_build/outputs.py``, and the report ``build_feed.lint`` prints is
``print_lint_report`` in ``src/feed_build/lint_report.py`` (operator decision
of that day, ``docs/modularisierung.md``). ``main`` and ``lint`` keep the
order of the steps. These tests hold that order and the failure paths: which
later steps still run after a failed write, and what the health report is
handed then.
"""

from __future__ import annotations

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


class _Run:
    """One ``main()`` with every output redirected to ``tmp_path`` and every step recorded."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        self.monkeypatch = monkeypatch
        self.events: list[str] = []
        self.health: list[dict[str, Any]] = []
        self.out = tmp_path / "feed.xml"
        self.en = tmp_path / "feed.en.xml"
        self.health_md = tmp_path / "feed-health.md"
        self.health_json = tmp_path / "feed-health.json"
        self.outputs = part(bf, "outputs")
        now = datetime.now(UTC)
        self.items = [_item("g1", "16A: Rettungseinsatz", now), _item("g2", "U4: Stromstörung", now)]

        monkeypatch.setattr(bf.feed_config, "OUT_PATH", self.out)
        monkeypatch.setattr(bf.feed_config, "STATE_FILE", tmp_path / "first_seen.json")
        monkeypatch.setattr(bf.feed_config, "FEED_HEALTH_PATH", self.health_md)
        monkeypatch.setattr(bf.feed_config, "FEED_HEALTH_JSON_PATH", self.health_json)
        # ``main`` would read the configuration from the environment again.
        monkeypatch.setattr(bf, "refresh_from_env", lambda: None)
        setattr_all(monkeypatch, bf, "validate_path", self.validate_path)
        monkeypatch.setattr(bf, "_invoke_collect_items", lambda report: list(self.items))
        monkeypatch.setattr(bf, "_load_state", lambda: {})
        setattr_all(monkeypatch, bf, "_make_rss", self.make_rss)
        monkeypatch.setattr(bf, "_record_translation_status", lambda now: self.events.append("translation_status"))
        monkeypatch.setattr(bf, "_remember_item_identity", lambda items, state, now: self.events.append("remember"))
        monkeypatch.setattr(self.outputs, "_save_state", self.save_state)
        monkeypatch.setattr(self.outputs, "write_feed_health_report", self.health_writer("health_md"))
        monkeypatch.setattr(self.outputs, "write_feed_health_json", self.health_writer("health_json"))

    def validate_path(self, path: Any, name: str) -> Path:
        self.events.append(f"validate:{name}:{Path(path).name}")
        return Path(path)

    def make_rss(self, items: list[FeedItem], now: datetime, state: dict[str, Any], lang: str = "de") -> str:
        self.events.append(f"rss:{lang}")
        return f"<rss lang='{lang}' items='{len(items)}'/>\n"

    def save_state(self, state: dict[str, Any], deletions: Any = None) -> None:
        self.events.append("save_state")

    def health_writer(self, label: str) -> Any:
        def write(report: RunReport, metrics: FeedHealthMetrics, *, output_path: Path) -> None:
            self.events.append(label)
            self.health.append(
                {
                    "label": label,
                    "path": output_path,
                    "successful": report.build_successful,
                    "warnings": list(report.warnings),
                    "feed_path": report.feed_path,
                    "metrics": metrics,
                }
            )

        return write

    def steps(self) -> list[str]:
        """The recorded steps without the path checks."""
        return [event for event in self.events if not event.startswith("validate:")]


_ALL_STEPS = ["rss:de", "rss:en", "translation_status", "remember", "save_state", "health_md", "health_json"]


def test_main_writes_feed_mirror_state_and_health_in_this_order(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run = _Run(monkeypatch, tmp_path)

    assert bf.main() == 0

    assert run.steps() == _ALL_STEPS
    assert run.out.read_bytes() == b"<rss lang='de' items='2'/>\n"
    assert run.en.read_bytes() == b"<rss lang='en' items='2'/>\n"
    # The health paths are checked before the build, the feed paths when they are written.
    assert [event for event in run.events if event.startswith("validate:")] == [
        "validate:FEED_HEALTH_PATH:feed-health.md",
        "validate:FEED_HEALTH_JSON_PATH:feed-health.json",
        "validate:OUT_PATH:feed.xml",
        "validate:OUT_PATH:feed.en.xml",
    ]
    assert run.events.index("validate:OUT_PATH:feed.xml") > run.events.index("rss:de")
    assert [(entry["label"], entry["path"]) for entry in run.health] == [
        ("health_md", run.health_md),
        ("health_json", run.health_json),
    ]
    for entry in run.health:
        assert entry["successful"] is True
        assert entry["feed_path"] == run.out.as_posix()
        assert entry["warnings"] == []
        assert entry["metrics"].raw_items == 2
        assert entry["metrics"].deduped_items == 2


def test_a_failed_state_save_is_a_warning_and_the_health_report_is_still_written(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    run = _Run(monkeypatch, tmp_path)

    def failing_save(state: dict[str, Any], deletions: Any = None) -> None:
        run.events.append("save_state")
        raise OSError("disk\x1b[31m full")

    monkeypatch.setattr(run.outputs, "_save_state", failing_save)

    with caplog.at_level(logging.WARNING, logger="build_feed"):
        assert bf.main() == 0

    assert run.steps() == _ALL_STEPS
    assert run.out.exists() and run.en.exists()
    for entry in run.health:
        assert entry["successful"] is True
        assert entry["warnings"] == [
            "State save failed: OSError — first_seen / translations / stats may drift on the next run."
        ]
    messages = [record.getMessage() for record in caplog.records if record.name == "build_feed"]
    saved = [message for message in messages if message.startswith("State speichern fehlgeschlagen")]
    assert len(saved) == 1
    assert saved[0].endswith("– Feed wurde geschrieben, State bleibt veraltet.")
    assert "\x1b" not in saved[0]


def test_a_failed_en_rendering_keeps_the_german_feed_and_the_later_steps(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    run = _Run(monkeypatch, tmp_path)

    def make_rss(items: list[FeedItem], now: datetime, state: dict[str, Any], lang: str = "de") -> str:
        run.events.append(f"rss:{lang}")
        if lang == "en":
            raise RuntimeError("translation broke")
        return "<rss lang='de'/>\n"

    setattr_all(monkeypatch, bf, "_make_rss", make_rss)

    with caplog.at_level(logging.WARNING, logger="build_feed"):
        assert bf.main() == 0

    assert run.steps() == _ALL_STEPS
    assert run.out.read_bytes() == b"<rss lang='de'/>\n"
    assert not run.en.exists()
    assert all(entry["successful"] is True and entry["warnings"] == [] for entry in run.health)
    messages = [record.getMessage() for record in caplog.records if record.name == "build_feed"]
    assert (
        "EN-Feed konnte nicht geschrieben werden (translation broke) – deutscher Feed ist bereits aktualisiert."
        in messages
    )


def test_a_failed_en_write_keeps_the_german_feed_and_the_later_steps(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    run = _Run(monkeypatch, tmp_path)
    real_atomic_write = run.outputs.atomic_write

    def atomic_write(path: Path, **kwargs: Any) -> Any:
        if Path(path).name == "feed.en.xml":
            raise OSError("no space left")
        return real_atomic_write(path, **kwargs)

    monkeypatch.setattr(run.outputs, "atomic_write", atomic_write)

    with caplog.at_level(logging.WARNING, logger="build_feed"):
        assert bf.main() == 0

    assert run.steps() == _ALL_STEPS
    assert run.out.read_bytes() == b"<rss lang='de' items='2'/>\n"
    assert not run.en.exists()
    assert any(
        record.getMessage().startswith("EN-Feed konnte nicht geschrieben werden (no space left)")
        for record in caplog.records
        if record.name == "build_feed"
    )


def test_a_rejected_en_path_fails_the_build_after_the_german_feed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The path check of the mirror stands outside the ``try`` around its
    # rendering: it ends the build, as it did inside ``main``.
    run = _Run(monkeypatch, tmp_path)

    def validate_path(path: Any, name: str) -> Path:
        if Path(path).name == "feed.en.xml":
            raise ValueError("outside the allowed roots")
        return run.validate_path(path, name)

    setattr_all(monkeypatch, bf, "validate_path", validate_path)

    with pytest.raises(ValueError, match="outside the allowed roots"):
        bf.main()

    assert run.steps() == ["rss:de", "health_md", "health_json"]
    assert run.out.exists() and not run.en.exists()
    assert all(entry["successful"] is False for entry in run.health)


def test_a_failed_german_write_fails_the_build_and_reports_the_counts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run = _Run(monkeypatch, tmp_path)

    def atomic_write(path: Path, **kwargs: Any) -> Any:
        raise OSError("read-only file system")

    monkeypatch.setattr(run.outputs, "atomic_write", atomic_write)

    with pytest.raises(OSError, match="read-only file system"):
        bf.main()

    # No mirror, no state, but both health outputs, as a failed build with
    # the counts the chain reached.
    assert run.steps() == ["rss:de", "health_md", "health_json"]
    assert not run.out.exists() and not run.en.exists()
    for entry in run.health:
        assert entry["successful"] is False
        assert entry["feed_path"] is None
        assert entry["metrics"].raw_items == 2
        assert entry["metrics"].filtered_items == 2
        assert entry["metrics"].deduped_items == 2


def test_a_failed_chain_reports_the_counts_reached_before_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run = _Run(monkeypatch, tmp_path)
    summary = DuplicateSummary(dedupe_key="k", count=3, titles=("a",))

    def failing_chain(items: list[FeedItem], state: dict[str, Any], now: datetime, *, stats: Any, decide: Any) -> Any:
        stats.filtered_count = 2
        stats.duplicate_summaries = [summary]
        raise RuntimeError("chain broke")

    monkeypatch.setattr(bf, "_assemble_feed_items", failing_chain)

    with pytest.raises(RuntimeError, match="chain broke"):
        bf.main()

    assert run.steps() == ["health_md", "health_json"]
    for entry in run.health:
        assert entry["successful"] is False
        metrics = entry["metrics"]
        assert (metrics.raw_items, metrics.filtered_items, metrics.deduped_items, metrics.new_items) == (2, 2, 0, 0)
        assert metrics.duplicates == (summary,)


def test_a_failed_health_markdown_still_writes_the_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    run = _Run(monkeypatch, tmp_path)

    def failing_markdown(report: RunReport, metrics: FeedHealthMetrics, *, output_path: Path) -> None:
        run.events.append("health_md")
        raise OSError("markdown\nbroke")

    monkeypatch.setattr(run.outputs, "write_feed_health_report", failing_markdown)

    with caplog.at_level(logging.WARNING, logger="build_feed"):
        assert bf.main() == 0

    assert run.steps() == _ALL_STEPS
    assert [entry["label"] for entry in run.health] == ["health_json"]
    messages = [record.getMessage() for record in caplog.records if record.name == "build_feed"]
    markdown = [message for message in messages if message.startswith("Feed-Health-Markdown konnte nicht")]
    assert len(markdown) == 1 and "\n" not in markdown[0]


# ------------------------------------------------------------ the functions


class _Stats:
    filtered_count = 7
    deduped_count = 5
    duplicates_removed = 2
    duplicate_summaries = [DuplicateSummary(dedupe_key="k", count=3, titles=("a", "b"))]
    filter_duration = 0.25
    dedupe_duration = 0.5


def test_health_metrics_from_reads_the_counters() -> None:
    metrics = part(bf, "outputs").health_metrics_from(9, _Stats(), 4)

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
    metrics = outputs.health_metrics_from(0, _Stats(), 0)

    with caplog.at_level(logging.WARNING, logger="build_feed"):
        outputs.write_health_outputs(report, metrics, tmp_path / "h.md", tmp_path / "h.json")

    assert calls == [("markdown", tmp_path / "h.md"), ("json", tmp_path / "h.json")]
    assert [record.getMessage() for record in caplog.records if record.name == "build_feed"] == [
        "Feed-Health-Markdown konnte nicht geschrieben werden: markdown failed",
        "Feed-Health-JSON konnte nicht geschrieben werden: json failed",
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
    metrics = part(bf, "outputs").health_metrics_from(9, _Stats(), 4)
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

    # A provider error outranks every finding.
    report = _Report(True, capsys)
    assert _lint(report, missing_guid_items=[{"title": "x"}]) == 2
    assert capsys.readouterr().out == "\nProvider-Fehler erkannt – siehe Log-Ausgabe für Details.\n"

    report = _Report(True, capsys)
    assert _lint(report) == 2
    assert capsys.readouterr().out == "\nProvider-Fehler erkannt – siehe Log-Ausgabe für Details.\n"
