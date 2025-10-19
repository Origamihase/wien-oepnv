from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src import build_feed


def _make_item(
    identity: str,
    *,
    guid: str | None = None,
    title: str = "Title",
    description: str = "Desc",
) -> dict[str, object]:
    return {
        "_identity": identity,
        "guid": guid,
        "title": title,
        "description": description,
        "source": "Test",
        "category": "Info",
        "pubDate": datetime.now(timezone.utc),
    }


def test_feed_lint_reports_duplicates_and_missing_guid(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    now = datetime.now(timezone.utc)
    items = [
        _make_item("foo", guid="foo-guid", title="Foo A"),
        _make_item("foo", guid="foo-guid-2", title="Foo B"),
        _make_item("bar", guid=None, title="Bar"),
    ]

    monkeypatch.setattr(build_feed, "_invoke_collect_items", lambda report: list(items))
    monkeypatch.setattr(
        build_feed,
        "_load_state",
        lambda: {"foo": {"first_seen": now.isoformat()}},
    )

    exit_code = build_feed.lint()

    captured = capsys.readouterr().out
    assert "entfernte Duplikate: 1" in captured
    assert "Einträge ohne GUID" in captured
    assert exit_code == 1


def test_feed_lint_ok_without_issues(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    items = [_make_item("foo", guid="foo-guid", title="Only Item")]

    monkeypatch.setattr(build_feed, "_invoke_collect_items", lambda report: list(items))
    monkeypatch.setattr(build_feed, "_load_state", lambda: {})

    exit_code = build_feed.lint()

    captured = capsys.readouterr().out
    assert "Keine strukturellen Probleme gefunden" in captured
    assert exit_code == 0
