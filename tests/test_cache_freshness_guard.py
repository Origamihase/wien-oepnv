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
    monkeypatch.setattr(build_feed.feed_config, "CACHE_MAX_AGE_HOURS", 1)
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
    monkeypatch.setattr(build_feed.feed_config, "CACHE_MAX_AGE_HOURS", 2)
    now = datetime.now(timezone.utc)
    report = build_feed.RunReport([(loader._provider_cache_name, True)])

    monkeypatch.setattr(
        build_feed, "cache_modified_at", lambda name: now - timedelta(hours=1)
    )

    messages = build_feed._detect_stale_caches(report, now)

    assert messages == []
    assert not report.warnings

def test_cache_freshness_guard_future(monkeypatch, tmp_path):
    import os
    from src.utils.cache import cache_modified_at

    provider = "demo"
    monkeypatch.setattr("src.utils.cache._CACHE_DIR", tmp_path)

    # Create the directory and file
    cache_path = tmp_path / provider
    cache_path.mkdir(parents=True, exist_ok=True)
    cache_file = cache_path / "events.json"
    cache_file.write_text("[]")

    # Set mtime to 48 hours in the future
    future_time = datetime.now(timezone.utc) + timedelta(hours=48)
    os.utime(cache_file, (future_time.timestamp(), future_time.timestamp()))

    # Calling cache_modified_at should return None because it's too far in the future
    assert cache_modified_at(provider) is None
