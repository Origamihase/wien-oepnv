"""Feed-Wächter, rule ``meldung-uebersprungen``: items the build left out.

The build records an item it could not process in its health report
(``docs/feed-health.json``, key ``skipped_items``) and builds the feed with
the rest. These tests cover how the watcher reads that list, what a finding
says, and that the issue opens once and closes like the issue of every other
rule.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Iterator, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from scripts import feed_watch as fw
from scripts import health_check
from src.feed import config as feed_config
from src.feed_watch import skipped_items as rule

from ._support import REPO_ROOT, FakeClient, _feed, _filler

RULE = "meldung-uebersprungen"
ISSUE_TITLE = "Feed-Wächter: Meldung beim Bau übersprungen"

POLIZEI = {
    "source": "Wiener Linien",
    "title": "2/71: Polizeieinsatz",
    "error": "OverflowError in src/feed_build/filters.py:75",
}
OEBB = {
    "source": "ÖBB",
    "title": "S80: Ersatzbus",
    "error": "ValueError in src/feed_build/titles.py:12",
}
# More digits than ``int`` converts from text (limit 4300): the JSON reader
# raises a plain ``ValueError`` for such a number, no ``JSONDecodeError``.
LONG_NUMBER = "9" * 5000


def _health(tmp_path: Path, payload: object) -> Path:
    path = tmp_path / "feed-health.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _skipped(*entries: Mapping[str, Any]) -> list[fw.SkippedItem]:
    return fw.parse_skipped_items({"skipped_items": list(entries)})


def _tick(
    skipped: Sequence[fw.SkippedItem],
    state: Mapping[str, Any],
    client: FakeClient,
    minute: int,
) -> fw.Result:
    """One Update run with a full feed and the given list of the build."""
    built = datetime(2026, 11, 10, 12, 0, tzinfo=UTC) + timedelta(minutes=minute)
    feed = fw.parse_feed(_feed(_filler(10), built=built.strftime("%a, %d %b %Y %H:%M:%S +0000")))
    result = fw.evaluate(feed, now=built, state=state, oebb_cache={}, sources=None, skipped=skipped)
    fw.sync_issues(client, result, built)  # type: ignore[arg-type]
    return result


@pytest.fixture
def health_in_data() -> Iterator[Path]:
    """A path the build would accept for its health report (under ``data/``)."""
    path = REPO_ROOT / "data" / f"test_feed_watch_health_{uuid.uuid4().hex}.json"
    yield path
    path.unlink(missing_ok=True)


# --- reading the health report ---------------------------------------------------


def test_no_health_report_gives_no_finding(tmp_path: Path) -> None:
    assert fw.load_skipped_items(tmp_path / "feed-health.json") == []
    assert fw.load_skipped_items(None) == []
    assert fw.check_skipped_items(None) == []


def test_an_empty_list_and_a_report_without_the_key_give_no_finding(tmp_path: Path) -> None:
    assert fw.load_skipped_items(_health(tmp_path, {"run": {"status": "success"}, "skipped_items": []})) == []
    # A report written before the key existed: nothing was left out.
    assert fw.load_skipped_items(_health(tmp_path, {"run": {"status": "success"}, "errors": []})) == []
    result = _tick([], {}, FakeClient(), 0)
    assert result.findings == []
    assert RULE not in result.state["rules"]


def test_one_skipped_item_gives_a_finding_with_source_title_and_error(tmp_path: Path) -> None:
    skipped = fw.load_skipped_items(_health(tmp_path, {"skipped_items": [POLIZEI]}))
    assert skipped == [fw.SkippedItem(POLIZEI["source"], POLIZEI["title"], POLIZEI["error"])]
    findings = fw.check_skipped_items(skipped)
    assert [f.rule_id for f in findings] == [RULE]
    shown = f"{findings[0].subject}: {findings[0].text}"
    assert "2/71: Polizeieinsatz" in shown
    assert "Wiener Linien" in shown
    assert "OverflowError in src/feed_build/filters.py:75" in shown
    assert "fehlt im Feed, bis die Ursache behoben ist" in shown


def test_every_skipped_item_gets_its_own_finding() -> None:
    findings = fw.check_skipped_items(_skipped(POLIZEI, OEBB, POLIZEI))
    assert [f.subject for f in findings] == ["2/71: Polizeieinsatz", "S80: Ersatzbus"]


@pytest.mark.parametrize(
    "content",
    [
        b"",
        b"{",
        b'{"skipped_items": [',
        b"[]",
        b'"skipped_items"',
        b"null",
        b'{"skipped_items": null}',
        b'{"skipped_items": "2/71"}',
        b'{"skipped_items": {"title": "2/71"}}',
        b'{"skipped_items": [null, 7, "2/71", ["x"]]}',
        b'{"skipped_items": [{"title": "x"}]} trailing',
        b"\xff\xfe\x00{",
        b'{"skipped_items": [{"title": "x", "n": NaN}]}',
        b"[" * 100_000,
        b'{"skipped_items": [], "n": ' + LONG_NUMBER.encode() + b"}",
        b'{"skipped_items": [{"title": ' + LONG_NUMBER.encode() + b"}]}",
        b'{"skipped_items": [{"title": "2/71"}], "durations": {"total": -' + LONG_NUMBER.encode() + b"}}",
    ],
)
def test_a_malformed_report_gives_no_finding_and_no_exception(tmp_path: Path, content: bytes) -> None:
    path = tmp_path / "feed-health.json"
    path.write_bytes(content)
    assert fw.load_skipped_items(path) == []


def test_a_report_with_a_number_too_long_to_convert_is_logged_as_unreadable(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "feed-health.json"
    path.write_text(json.dumps({"skipped_items": [POLIZEI]})[:-1] + f', "n": {LONG_NUMBER}}}', encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        assert rule.load_skipped_items(path) == []

    lines = [record.getMessage() for record in caplog.records if record.levelno == logging.WARNING]
    assert len(lines) == 1
    assert lines[0].startswith("Feed-Wächter: Feed-Health-Bericht nicht lesbar: ")
    # The line stays short: the number itself is not in it.
    assert LONG_NUMBER[:50] not in lines[0]


def test_a_directory_or_an_oversized_report_gives_no_finding(tmp_path: Path) -> None:
    assert fw.load_skipped_items(tmp_path) == []
    path = tmp_path / "feed-health.json"
    padding = "x" * (rule.MAX_HEALTH_BYTES + 1)
    path.write_text(json.dumps({"skipped_items": [POLIZEI], "padding": padding}), encoding="utf-8")
    assert fw.load_skipped_items(path) == []


def test_an_entry_with_missing_fields_is_still_reported() -> None:
    """The item was left out, whatever the build could say about it."""
    skipped = _skipped({"title": "2/71: Polizeieinsatz"}, {"source": 3, "title": "", "error": None})
    assert skipped == [
        fw.SkippedItem(rule.NO_SOURCE, "2/71: Polizeieinsatz", rule.NO_ERROR),
        fw.SkippedItem(rule.NO_SOURCE, rule.NO_TITLE, rule.NO_ERROR),
    ]
    assert len(fw.check_skipped_items(skipped)) == 2


def test_fields_are_one_bounded_line() -> None:
    skipped = _skipped(
        {
            "source": "Wiener\nLinien",
            "title": "2/71:\r\n\tPolizeieinsatz‮\x00\x1b[31m " + "x" * 500,
            "error": "OverflowError\nin src/feed_build/filters.py:75",
        }
    )
    item = skipped[0]
    assert item.source == "Wiener Linien"
    assert item.title.startswith("2/71: Polizeieinsatz[31m x")
    assert len(item.title) == rule.MAX_TITLE_CHARS and item.title.endswith("…")
    assert item.error == "OverflowError in src/feed_build/filters.py:75"
    finding = fw.check_skipped_items(skipped)[0]
    assert "\n" not in finding.subject + finding.text


def test_the_bounds_of_source_title_and_error() -> None:
    """A field of exactly its limit stays; one character more is cut to the limit, "…" included."""
    assert (rule.MAX_SOURCE_CHARS, rule.MAX_TITLE_CHARS, rule.MAX_ERROR_CHARS) == (80, 200, 200)
    at_limit = {"source": "Q" * 80, "title": "T" * 200, "error": "E" * 200}
    one_over = {"source": "Q" * 81, "title": "T" * 201, "error": "E" * 201}
    far_over = {"source": "Q" * 5000, "title": "T" * 5000, "error": "E" * 5000}

    (kept,), (cut,), (cut_far,) = _skipped(at_limit), _skipped(one_over), _skipped(far_over)

    assert kept == fw.SkippedItem("Q" * 80, "T" * 200, "E" * 200)
    assert cut == fw.SkippedItem("Q" * 79 + "…", "T" * 199 + "…", "E" * 199 + "…")
    assert cut_far == cut
    finding = fw.check_skipped_items([cut])[0]
    assert len(finding.subject) == 200
    assert len(finding.text) == len("Quelle , beim Bau übersprungen wegen ; fehlt im Feed, bis die Ursache behoben ist") + 80 + 200


def test_the_list_is_bounded() -> None:
    entries = [{"source": "ÖBB", "title": f"S{n}", "error": "ValueError"} for n in range(200)]
    assert len(_skipped(*entries)) == rule.MAX_SKIPPED_ITEMS


# --- where the path comes from -----------------------------------------------------


def test_the_path_is_the_one_the_build_writes_to(
    monkeypatch: pytest.MonkeyPatch, health_in_data: Path, tmp_path: Path
) -> None:
    monkeypatch.setattr(feed_config, "FEED_HEALTH_JSON_PATH", health_in_data)
    assert fw.health_report_path() == health_in_data
    assert fw.health_report_path(tmp_path / "other.json") == tmp_path / "other.json"


def test_a_configured_path_the_build_would_refuse_gives_no_finding(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    outside = _health(tmp_path, {"skipped_items": [POLIZEI]})
    monkeypatch.chdir(REPO_ROOT)
    monkeypatch.setattr(feed_config, "FEED_HEALTH_JSON_PATH", outside)
    assert fw.health_report_path() is None
    assert fw.load_skipped_items(fw.health_report_path()) == []


# --- repetition and recovery ---------------------------------------------------------


def test_one_issue_opened_once_and_closed_after_two_runs_without_a_skipped_item() -> None:
    client = FakeClient()
    first = _tick(_skipped(POLIZEI), {}, client, 0)
    second = _tick(_skipped(POLIZEI), first.state, client, 30)
    third = _tick(_skipped(POLIZEI), second.state, client, 60)
    assert client.calls == [("issues", None), ("create", ISSUE_TITLE)]
    assert third.state["rules"][RULE]["issue"] == 101
    assert third.state["rules"][RULE]["since"] == first.state["rules"][RULE]["since"]

    fourth = _tick([], third.state, client, 90)
    assert ("closed", 101) not in client.calls  # one run without a skipped item is not enough
    assert fourth.state["rules"][RULE]["held_runs"] == 1
    fifth = _tick([], fourth.state, client, 120)
    assert client.calls[-2:] == [("comment", 101), ("closed", 101)]
    assert RULE not in fifth.state["rules"]


def test_an_item_skipped_again_before_the_issue_closed_keeps_the_issue() -> None:
    client = FakeClient()
    first = _tick(_skipped(POLIZEI), {}, client, 0)
    second = _tick([], first.state, client, 30)
    third = _tick(_skipped(POLIZEI), second.state, client, 60)
    assert third.state["rules"][RULE]["held_runs"] == 0
    fourth = _tick([], third.state, client, 90)
    assert [call for call in client.calls if call[0] in ("create", "closed")] == [("create", ISSUE_TITLE)]
    assert fourth.state["rules"][RULE]["issue"] == 101


def test_a_second_skipped_item_joins_the_open_issue_of_the_rule() -> None:
    """One issue per rule, as for every rule: the state lists every item of the run."""
    client = FakeClient()
    first = _tick(_skipped(POLIZEI), {}, client, 0)
    second = _tick(_skipped(POLIZEI, OEBB), first.state, client, 30)
    assert client.calls == [("issues", None), ("create", ISSUE_TITLE)]
    assert [f["subject"] for f in second.state["findings"]] == ["2/71: Polizeieinsatz", "S80: Ersatzbus"]


def test_a_recently_closed_issue_is_reopened() -> None:
    closed = datetime(2026, 11, 10, 9, 0, tzinfo=UTC).isoformat().replace("+00:00", "Z")
    client = FakeClient([{"number": 8, "title": ISSUE_TITLE, "state": "closed", "closed_at": closed}])
    result = _tick(_skipped(POLIZEI), {}, client, 0)
    assert result.state["rules"][RULE]["issue"] == 8
    assert ("open", 8) in client.calls
    assert [call for call in client.calls if call[0] == "create"] == []


def test_the_issue_text_names_source_title_and_error() -> None:
    now = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
    body = fw._issue_body(fw.RULES[RULE], fw.check_skipped_items(_skipped(POLIZEI, OEBB)), now)
    assert "**Meldung beim Bau übersprungen** (Rang 1)" in body
    assert (
        "- 2/71: Polizeieinsatz: Quelle Wiener Linien, beim Bau übersprungen wegen "
        "OverflowError in src/feed_build/filters.py:75; fehlt im Feed, bis die Ursache behoben ist"
    ) in body
    assert "- S80: Ersatzbus: Quelle ÖBB, beim Bau übersprungen wegen ValueError" in body
    assert "fehlt im Feed, bis die Ursache behoben ist." in body  # the rule's own sentence


# --- the script ------------------------------------------------------------------------


def _write_feed(tmp_path: Path, items: int = 10) -> Path:
    feed = tmp_path / "feed.xml"
    built = datetime.now(UTC).strftime("%a, %d %b %Y %H:%M:%S +0000")
    feed.write_text(_feed(_filler(items), built=built), encoding="utf-8")
    return feed


def _main(feed: Path, state: Path, tmp_path: Path, *extra: str) -> int:
    return fw.main(
        ["--feed", str(feed), "--state", str(state), "--cache-dir", str(tmp_path / "cache"),
         "--raw-dir", str(tmp_path / "raw"), "--no-issues", *extra]
    )


def test_main_reports_a_skipped_item_and_still_ends_with_zero(data_file: Path, tmp_path: Path) -> None:
    health = _health(tmp_path, {"skipped_items": [POLIZEI]})
    assert _main(_write_feed(tmp_path), data_file, tmp_path, "--health-json", str(health)) == 0
    state = json.loads(data_file.read_text(encoding="utf-8"))
    assert [(f["rule"], f["subject"]) for f in state["findings"]] == [(RULE, "2/71: Polizeieinsatz")]
    assert state["findings"][0]["title"] == "Meldung beim Bau übersprungen"
    assert "error" not in state


def test_main_reads_the_configured_health_report(
    monkeypatch: pytest.MonkeyPatch, data_file: Path, health_in_data: Path, tmp_path: Path
) -> None:
    health_in_data.write_text(json.dumps({"skipped_items": [OEBB]}), encoding="utf-8")
    monkeypatch.setattr(feed_config, "FEED_HEALTH_JSON_PATH", health_in_data)
    assert _main(_write_feed(tmp_path), data_file, tmp_path) == 0
    state = json.loads(data_file.read_text(encoding="utf-8"))
    assert [(f["rule"], f["subject"]) for f in state["findings"]] == [(RULE, "S80: Ersatzbus")]


UNUSABLE_REPORTS = [
    None,
    "{",
    '{"skipped_items": 7}',
    "[1, 2]",
    f'{{"skipped_items": [], "durations": {{"total": {LONG_NUMBER}}}}}',
]


@pytest.mark.parametrize("content", UNUSABLE_REPORTS)
def test_main_without_a_usable_health_report_checks_the_other_rules(
    data_file: Path, tmp_path: Path, content: str | None
) -> None:
    health = tmp_path / "feed-health.json"
    if content is not None:
        health.write_text(content, encoding="utf-8")
    assert _main(_write_feed(tmp_path), data_file, tmp_path, "--health-json", str(health)) == 0
    state = json.loads(data_file.read_text(encoding="utf-8"))
    assert state["findings"] == []
    assert "error" not in state


@pytest.mark.parametrize("content", UNUSABLE_REPORTS)
def test_main_without_a_usable_health_report_still_reports_what_another_rule_finds(
    data_file: Path, tmp_path: Path, content: str | None
) -> None:
    """A feed of nine items: the rule on the number of items is checked and reported."""
    health = tmp_path / "feed-health.json"
    if content is not None:
        health.write_text(content, encoding="utf-8")
    assert _main(_write_feed(tmp_path, items=9), data_file, tmp_path, "--health-json", str(health)) == 0
    state = json.loads(data_file.read_text(encoding="utf-8"))
    assert [finding["rule"] for finding in state["findings"]] == ["anzahl"]
    assert "error" not in state


# --- Health check ----------------------------------------------------------------------


def test_the_health_check_shows_a_skipped_item_as_a_note(
    monkeypatch: pytest.MonkeyPatch, data_file: Path, tmp_path: Path
) -> None:
    """A finding of the watcher is a note there; red is a watcher that could not check."""
    health = _health(tmp_path, {"skipped_items": [POLIZEI]})
    assert _main(_write_feed(tmp_path), data_file, tmp_path, "--health-json", str(health)) == 0
    monkeypatch.setattr("src.health_check.sources.FEED_WATCH_STATE", data_file)
    check = health_check.check_feed_watch(datetime.now(UTC))
    assert check.ok is True
    assert check.note is True
    assert "HINWEIS — 1 Regel(n) verletzt: Meldung beim Bau übersprungen" in check.summary
    assert "2/71: Polizeieinsatz: Quelle Wiener Linien" in check.detail
