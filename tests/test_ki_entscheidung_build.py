"""``build_feed.main()`` and ``lint()`` with the Gemini decision wired in (``src/ki_entscheidung``).

Owner's decision of 2026-11-12 (Michael, card "Soll ich die
Gemini-Entscheidung für unklare Fälle so bauen?": "Ja, so bauen"). The
tests of the package drive ``Decider`` directly; these drive the build
itself, so a build that no longer asks, stores or reports fails here. No
test reaches the network: ``request_safe`` of the connector is replaced.
"""

from __future__ import annotations

import copy
import json
import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

import src.build_feed as bf
from src.feed_types import FeedItem
from src.ki_entscheidung import store
from src.markdown_stats import review_client as rc
from tests._conftest.build_feed_bindings import setattr_all

KEY = "SECRET-KEY-0123456789"
TITLE = "26: Züge halten in Tokiostraße"
CLOCK_RE = re.compile(r"Seit\s\d\d:\d\d")


class _Response:
    def __init__(self, kind: str) -> None:
        text = json.dumps({"entscheidungen": [{"nr": 1, "art": kind, "grund": "Hinweis über Gleisbauarbeiten daneben."}]})
        payload = {"candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": "STOP"}]}
        self.status_code = 200
        self.content = json.dumps(payload).encode("utf-8")


def _unclear(now: datetime) -> FeedItem:
    """A WL disruption without a cause that began two minutes ago: only the clock would decide."""
    start = now - timedelta(minutes=2)
    return {
        "source": "Wiener Linien",
        "category": "Störung",
        "title": TITLE,
        "description": "Züge halten in\nTokiostraße",
        "guid": "guid-tokio",
        "link": "https://www.wienerlinien.at/betriebsinfo",
        "pubDate": start,
        "starts_at": start,
        "ends_at": None,
    }


def _prepare(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, replies: list[_Response]) -> list[dict[str, Any]]:
    """Point the build at *tmp_path*, give it the key and one unclear message; the requests it sends."""
    monkeypatch.chdir(tmp_path)  # the store lies under tmp_path/data (``isolate_ki_decisions``)
    setattr_all(monkeypatch, bf, "validate_path", lambda path, name: path)
    monkeypatch.setattr(bf.feed_config, "OUT_PATH", tmp_path / "feed.xml")
    monkeypatch.setattr(bf.feed_config, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(bf.feed_config, "FEED_HEALTH_PATH", tmp_path / "feed-health.md")
    monkeypatch.setattr(bf.feed_config, "FEED_HEALTH_JSON_PATH", tmp_path / "feed-health.json")
    monkeypatch.setattr(bf, "refresh_from_env", lambda: None)
    collected = [_unclear(datetime.now(UTC))]
    monkeypatch.setattr(bf, "_invoke_collect_items", lambda report: copy.deepcopy(collected))
    setattr_all(monkeypatch, bf, "_get_translation_pipeline", lambda: None)
    monkeypatch.setenv(rc.GEMINI_KEY_ENV, KEY)
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "github_output"))
    calls: list[dict[str, Any]] = []

    def fake_request(session: object, url: str, **kwargs: Any) -> _Response:
        calls.append({"url": url, **kwargs})
        return replies.pop(0)

    monkeypatch.setattr(rc, "request_safe", fake_request)
    return calls


def _line(tmp_path: Path) -> str:
    channel = ET.parse(tmp_path / "feed.xml").getroot().find("channel")
    assert channel is not None
    (item,) = [item for item in channel.findall("item") if item.findtext("title") == TITLE]
    return item.findtext("description") or ""


def test_the_build_asks_once_marks_the_feed_stores_the_answer_and_reports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _prepare(monkeypatch, tmp_path, [_Response("geplant")])
    assert bf.main() == 0
    # One request for the build, whatever the single-item trials and the two languages do.
    assert len(calls) == 1 and calls[0]["headers"]["x-goog-api-key"] == KEY
    assert TITLE in json.loads(calls[0]["data"])["contents"][0]["parts"][0]["text"]
    # "geplant": no "[Seit hh:mm]" for the measure, whatever its clock says.
    assert CLOCK_RE.search(_line(tmp_path)) is None
    (record,) = store.load().values()
    assert record["art"] == "geplant" and record["titel"] == TITLE and len(record["gefragt"]) == 1
    assert KEY not in store.store_path().read_text(encoding="utf-8")
    output = tmp_path / "github_output"
    assert output.read_text(encoding="utf-8") == "ki_outcome=success\n"

    # The next build reads the answer and asks nobody.
    before = store.store_path().read_text(encoding="utf-8")
    assert bf.main() == 0
    assert len(calls) == 1 and CLOCK_RE.search(_line(tmp_path)) is None
    assert store.store_path().read_text(encoding="utf-8") == before
    assert output.read_text(encoding="utf-8") == "ki_outcome=success\n" * 2


def test_lint_reads_the_store_and_asks_nobody(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _prepare(monkeypatch, tmp_path, [])
    assert bf.lint() == 0
    assert calls == [] and not store.store_path().exists() and not (tmp_path / "github_output").exists()
