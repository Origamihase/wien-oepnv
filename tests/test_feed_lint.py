from __future__ import annotations

from datetime import datetime, UTC

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
        "pubDate": datetime.now(UTC),
    }


def test_feed_lint_reports_duplicates_and_missing_guid(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    now = datetime.now(UTC)
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


def test_feed_lint_sanitizes_missing_guid_output(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A guid-less item carrying control / BiDi / zero-width bytes in its
    title must not leak them raw into the operator-facing lint stdout.

    The duplicate-group print already sanitises via ``_summarize_duplicates``;
    this guards the sibling "Einträge ohne GUID" path against Trojan-Source /
    terminal-escape / log-forgery on the lint report.
    """
    now = datetime.now(UTC)
    # RLO (U+202E), zero-width space (U+200B), ANSI ESC (U+001B), BEL (U+0007)
    evil_title = "Bar\u202e\u200b\x1b[31m\x07evil"
    items = [_make_item("bar", guid=None, title=evil_title)]

    monkeypatch.setattr(build_feed, "_invoke_collect_items", lambda report: list(items))
    monkeypatch.setattr(build_feed, "_load_state", lambda: {})
    monkeypatch.setattr(build_feed, "_detect_stale_caches", lambda report, now: [])

    build_feed.lint()

    out = capsys.readouterr().out
    assert "Einträge ohne GUID" in out
    for bad in ("\u202e", "\u200b", "\x1b", "\x07"):
        assert bad not in out, f"unsanitised {bad!r} leaked into lint stdout"
    # The visible text still surfaces so the operator sees the offending item.
    assert "Bar" in out and "evil" in out


def test_feed_lint_ok_without_issues(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    items = [_make_item("foo", guid="foo-guid", title="Only Item")]

    monkeypatch.setattr(build_feed, "_invoke_collect_items", lambda report: list(items))
    monkeypatch.setattr(build_feed, "_load_state", lambda: {})
    # Defang the stale-cache check: this test only cares about the
    # structural-issue branch of ``lint()``. Without the patch the
    # real ``_detect_stale_caches`` reads the committed cache files'
    # mtimes from the working tree — those flip past the 24h cutoff
    # in any environment where the repo has been sitting idle for a
    # day (CI runners, dev machines that haven't refreshed the
    # cache via cron), and ``exit_code`` becomes ``1`` even though
    # the synthetic ``items`` list is squeaky-clean. Returning an
    # empty list mirrors the "all caches fresh" world the test
    # contract assumes.
    monkeypatch.setattr(
        build_feed, "_detect_stale_caches", lambda report, now: []
    )

    exit_code = build_feed.lint()

    captured = capsys.readouterr().out
    assert "Keine strukturellen Probleme gefunden" in captured
    assert exit_code == 0
