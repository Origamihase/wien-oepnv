"""The health report a build writes, read by the Feed-Wächter.

``tests/test_build_feed_skipped_item.py`` reads the written report with
``json.loads``; ``tests/test_feed_watch/test_skipped_items.py`` gives the
watcher hand-written JSON. Here the two halves meet: ``build_feed.main()``
skips items and writes ``feed-health.json``, the watcher's own reader
(``src.feed_watch.skipped_items``) reads that file, and the issue text is
rendered with ``src.feed_watch.issues._issue_body``.

An item without a title is written by the build as ``(ohne Titel)``, the
placeholder the watcher uses too. The issue body is Markdown: a placeholder
in angle brackets would be read there as an HTML tag and the bullet would
name no item (review of 2026-11-10).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest

from src.feed_types import FeedItem
from src.feed_watch.issues import _issue_body
from src.feed_watch.model import RULES
from src.feed_watch.skipped_items import (
    NO_TITLE,
    RULE_ID,
    SkippedItem,
    check_skipped_items,
    load_skipped_items,
)
from tests.test_build_feed_skipped_item import PLACE, _build, _fail_on, _wl

TITLED, UNTITLED = "guid-3", "guid-5"
SHOWN = ["guid-1", "guid-2", "guid-4", "guid-6"]
FINDING_TEXT = "Quelle {source}, beim Bau übersprungen wegen {error}; fehlt im Feed, bis die Ursache behoben ist"


@pytest.mark.parametrize("step", ["chain", "rendering"])
@pytest.mark.parametrize(
    ("blank", "source"),
    [
        ({"title": ""}, "Wiener Linien"),
        ({"title": " \n ", "source": ""}, "unbekannt"),
        ({"title": None, "source": None}, "unbekannt"),
    ],
)
def test_the_watcher_reads_what_the_build_wrote_about_its_skipped_items(
    step: str, blank: dict[str, Any], source: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    now = datetime.now(UTC)
    collected = [_wl(number, now) for number in range(1, 7)]
    collected[4] = cast(FeedItem, {**collected[4], **blank})
    _fail_on(monkeypatch, step, lambda item: item.get("guid") in (TITLED, UNTITLED))

    run = _build(monkeypatch, tmp_path / "run", collected)

    assert run.code == 0
    assert run.guids() == SHOWN

    # The watcher's reader on the file the build wrote.
    skipped = load_skipped_items(run.directory / "feed-health.json")
    assert [(item.source, item.title) for item in skipped] == [
        ("Wiener Linien", "3A: Rettungseinsatz Ort3"),
        (source, "(ohne Titel)"),
    ]
    assert NO_TITLE == "(ohne Titel)"
    for item in skipped:
        assert isinstance(item, SkippedItem)
        assert PLACE[step].fullmatch(item.error), item.error
    error = skipped[0].error

    findings = check_skipped_items(skipped)
    assert [(finding.rule_id, finding.subject) for finding in findings] == [
        (RULE_ID, "3A: Rettungseinsatz Ort3"),
        (RULE_ID, "(ohne Titel)"),
    ]

    # The issue the watcher opens: one bullet per item, each names its item.
    body = _issue_body(RULES[RULE_ID], findings, now)
    bullets = [line for line in body.splitlines() if line.startswith("- ")]
    assert bullets == [
        "- 3A: Rettungseinsatz Ort3: " + FINDING_TEXT.format(source="Wiener Linien", error=error),
        "- (ohne Titel): " + FINDING_TEXT.format(source=source, error=error),
    ]
    # Plain text for a Markdown renderer: nothing it would take for an HTML tag.
    assert "<" not in body and ">" not in body


def test_a_build_that_skipped_nothing_gives_the_watcher_no_finding(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    now = datetime.now(UTC)

    run = _build(monkeypatch, tmp_path / "run", [_wl(number, now) for number in range(1, 7)])

    assert run.code == 0
    assert run.health["skipped_items"] == []
    skipped = load_skipped_items(run.directory / "feed-health.json")
    assert skipped == []
    assert check_skipped_items(skipped) == []
