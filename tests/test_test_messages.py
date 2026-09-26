"""Providers' test messages never reach the feed (audit 2026-09-26).

Wiener Linien published two on 2026-09-23; each stood in the German feed for
one cycle and took a slot on the ten-item info displays. The kept titles are
real feed items whose text contains "test" inside other words.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

import src.build_feed as bf
from src.feed_types import FeedItem


def _item(title: str, description: str = "", source: str = "Wiener Linien") -> FeedItem:
    return {"title": title, "description": description, "link": "", "source": source, "category": "Störung"}


@pytest.mark.parametrize(
    ("title", "description"),
    [
        ("71/72: Dies ist eine Testmeldung", "<p>Dies ist ein Test</p>"),
        # WL's real markup (cf. the cache): the words count after the tags go.
        (
            "U6: Hinweis",
            '<p class="MsoNormal"><span style="font-size: 12.0pt; line-height: 107%;">Dies ist ein Test</span></p>',
        ),
        ("62: F57f Test", "F57 f Test"),
        ("U1: Störung", "TEST"),
        ("Test", ""),
        ("12A: Hinweis", "Bitte ignorieren, das ist eine Testmeldung der Leitstelle zur Überprüfung."),
    ],
)
def test_test_messages_are_recognised(title: str, description: str) -> None:
    assert bf._is_test_message(_item(title, description))


@pytest.mark.parametrize(
    ("title", "description"),
    [
        (
            "72A: Kraftwerk Simmering",
            "Haltestellenverlegung der Linie 72A in Richtung Hasenleiten Haltestelle: Kraftwerk Simmering",
        ),
        ("U4: Stromstörung", "Fahrtbehinderung in Richtung Hütteldorf. Grund: Stromstörung im Haltestellenbereich Heiligenstadt."),
        ("D: Testbetrieb", "Testbetrieb der neuen Flexity-Straßenbahnen"),
        ("D: Hinweis", "Test-Fahrten der neuen Straßenbahn zwischen Nußdorf und Hauptbahnhof ab Montag"),
        ("Wien Hütteldorf ↔ Wien Hauptbahnhof", "Wegen Bauarbeiten fahren einige Fernverkehrszüge mit geänderten Fahrzeiten."),
        ("", ""),
    ],
)
def test_real_messages_are_kept(title: str, description: str) -> None:
    assert not bf._is_test_message(_item(title, description))


def test_drop_test_messages_keeps_order_and_logs(caplog: pytest.LogCaptureFixture) -> None:
    real = [_item("16A: Rettungseinsatz"), _item("U4: Stromstörung")]
    items = [real[0], _item("62: F57f Test", "F57 f Test"), real[1]]
    with caplog.at_level("WARNING"):
        assert bf._drop_test_messages(items) == real
    assert "Testmeldung verworfen: 62: F57f Test" in caplog.text


def test_main_drops_the_test_message() -> None:
    now = datetime.now(UTC)
    test = _item("71/72: Dies ist eine Testmeldung", "Dies ist ein Test")
    disruption = _item("16A: Rettungseinsatz")
    for guid, it in (("gT", test), ("gD", disruption)):
        it["guid"] = guid
        it["pubDate"] = now - timedelta(minutes=5)
        it["ends_at"] = now + timedelta(hours=6)
    rendered: list[list[str]] = []

    def fake_make_rss(items: list[FeedItem], *args: Any, **kwargs: Any) -> str:
        rendered.append([it["title"] for it in items])
        return ""

    with patch.object(bf, "_invoke_collect_items", return_value=[test, disruption]), \
         patch.object(bf, "_load_state", return_value={}), \
         patch.object(bf, "_make_rss", side_effect=fake_make_rss), \
         patch.object(bf, "_save_state", MagicMock()), \
         patch.object(bf, "atomic_write", MagicMock()), \
         patch("src.build_feed.validate_path", MagicMock()), \
         patch("src.build_feed.write_feed_health_report", MagicMock()), \
         patch("src.build_feed.write_feed_health_json", MagicMock()):
        assert bf.main() == 0

    assert rendered[0] == ["16A: Rettungseinsatz"]


def test_lint_ignores_the_test_message(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    # Without the filter the guid-less test message would count as a defect.
    items = [_item("62: F57f Test", "F57 f Test")]
    monkeypatch.setattr(bf, "_invoke_collect_items", lambda report: list(items))
    monkeypatch.setattr(bf, "_load_state", lambda: {})
    monkeypatch.setattr(bf, "_detect_stale_caches", lambda report, now: [])
    assert bf.lint() == 0
    assert "Einträge ohne GUID" not in capsys.readouterr().out
