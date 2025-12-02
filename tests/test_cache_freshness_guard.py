from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src import build_feed


def _make_loader(name: str):
    def _loader() -> list[object]:
        return []

    setattr(_loader, "_provider_cache_name", name)
    return _loader


def test_detect_stale_caches_records_warning(monkeypatch):
    loader = _make_loader("demo")
    monkeypatch.setattr(build_feed, "PROVIDERS", [("DEMO_ENABLE", loader)])
    monkeypatch.setattr(build_feed, "CACHE_MAX_AGE_HOURS", 1)
    now = datetime.now(timezone.utc)
    report = build_feed.RunReport([(loader._provider_cache_name, True)])

    monkeypatch.setattr(
        build_feed, "cache_modified_at", lambda name: now - timedelta(hours=2)
    )

    messages = build_feed._detect_stale_caches(report, now)

    assert messages
    assert any("demo" in message.lower() for message in messages)
    assert any("demo" in warning.lower() for warning in report.warnings)


def test_detect_stale_caches_skips_recent(monkeypatch):
    loader = _make_loader("demo")
    monkeypatch.setattr(build_feed, "PROVIDERS", [("DEMO_ENABLE", loader)])
    monkeypatch.setattr(build_feed, "CACHE_MAX_AGE_HOURS", 2)
    now = datetime.now(timezone.utc)
    report = build_feed.RunReport([(loader._provider_cache_name, True)])

    monkeypatch.setattr(
        build_feed, "cache_modified_at", lambda name: now - timedelta(hours=1)
    )

    messages = build_feed._detect_stale_caches(report, now)

    assert messages == []
    assert not report.warnings
