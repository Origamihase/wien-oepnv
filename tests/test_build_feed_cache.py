import builtins
import importlib
import logging
import sys
from pathlib import Path
from datetime import datetime, timezone

def _import_build_feed_without_providers(monkeypatch):
    module_name = "src.build_feed"
    root = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(root))
    monkeypatch.syspath_prepend(str(root / "src"))

    sys.modules.pop(module_name, None)
    for mod in list(sys.modules):
        if mod == "providers" or mod.startswith("providers."):
            sys.modules.pop(mod, None)

    real_import = builtins.__import__

    def guard(name, globals=None, locals=None, fromlist=(), level=0):
        if name.startswith("providers"):
            raise AssertionError(f"unexpected provider import: {name}")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guard)
    return importlib.import_module(module_name)


def _patch_empty_cache(monkeypatch, tmp_path):
    cache_mod = importlib.import_module("utils.cache")
    monkeypatch.setattr(cache_mod, "_CACHE_DIR", tmp_path / "cache", raising=False)


def test_collect_items_missing_cache_logs_warning(monkeypatch, tmp_path, caplog):
    build_feed = _import_build_feed_without_providers(monkeypatch)
    _patch_empty_cache(monkeypatch, tmp_path)

    caplog.set_level(logging.WARNING, logger="build_feed")
    caplog.set_level(logging.WARNING, logger="utils.cache")

    items = build_feed._collect_items()

    assert items == []

    cache_warnings = {
        record.message
        for record in caplog.records
        if record.name == "build_feed" and "Cache für Provider" in record.message
    }
    assert cache_warnings == {
        "Cache für Provider 'wl' leer – generiere Feed ohne aktuelle Daten.",
        "Cache für Provider 'oebb' leer – generiere Feed ohne aktuelle Daten.",
        "Cache für Provider 'vor' leer – generiere Feed ohne aktuelle Daten.",
        "Cache für Provider 'baustellen' leer – generiere Feed ohne aktuelle Daten.",
    }


def test_collect_items_reports_cache_alerts(monkeypatch, tmp_path):
    build_feed = _import_build_feed_without_providers(monkeypatch)
    _patch_empty_cache(monkeypatch, tmp_path)

    report = build_feed.RunReport(build_feed._provider_statuses())
    items = build_feed._collect_items(report=report)

    assert items == []
    assert any(warning.startswith("Cache wl") for warning in report.warnings)
    assert any(warning.startswith("Provider wl") for warning in report.warnings)


def test_main_runs_without_network(monkeypatch, tmp_path, caplog):
    build_feed = _import_build_feed_without_providers(monkeypatch)
    _patch_empty_cache(monkeypatch, tmp_path)

    out_file = tmp_path / "feed.xml"
    state_file = tmp_path / "state.json"

    monkeypatch.setattr(build_feed, "validate_path", lambda path, name: path)
    monkeypatch.setattr(build_feed.feed_config, "OUT_PATH", out_file)
    monkeypatch.setattr(build_feed.feed_config, "STATE_FILE", state_file)
    monkeypatch.setattr(build_feed, "_save_state", lambda state: None)
    monkeypatch.setattr(build_feed, "_load_state", lambda: {})

    # Prevent main() from resetting config via refresh_from_env
    monkeypatch.setattr(build_feed, "refresh_from_env", lambda: None)

    caplog.set_level(logging.WARNING, logger="build_feed")
    caplog.set_level(logging.WARNING, logger="utils.cache")

    exit_code = build_feed.main()

    assert exit_code == 0
    assert out_file.exists()

    cache_messages = [
        record.message
        for record in caplog.records
        if record.name == "build_feed" and "Cache für Provider" in record.message
    ]
    assert set(cache_messages) == {
        "Cache für Provider 'wl' leer – generiere Feed ohne aktuelle Daten.",
        "Cache für Provider 'oebb' leer – generiere Feed ohne aktuelle Daten.",
        "Cache für Provider 'vor' leer – generiere Feed ohne aktuelle Daten.",
        "Cache für Provider 'baustellen' leer – generiere Feed ohne aktuelle Daten.",
    }


def test_collect_items_reads_from_cache(monkeypatch):
    build_feed = _import_build_feed_without_providers(monkeypatch)

    calls = []

    def fake_read_cache(provider):
        calls.append(provider)
        return [{"provider": provider}]

    monkeypatch.setattr(build_feed, "read_cache", fake_read_cache)
    monkeypatch.setenv("WL_ENABLE", "1")
    monkeypatch.setenv("OEBB_ENABLE", "1")
    monkeypatch.setenv("VOR_ENABLE", "1")
    # Need to refresh config to pick up env vars because we removed auto-refresh
    build_feed.refresh_from_env()

    items = build_feed._collect_items()

    assert len(calls) == 4
    assert set(calls) == {"wl", "oebb", "vor", "baustellen"}
    assert sorted(items, key=lambda item: item["provider"]) == [
        {"provider": "baustellen"},
        {"provider": "oebb"},
        {"provider": "vor"},
        {"provider": "wl"},
    ]


def test_fmt_rfc2822_logs_and_uses_fallback(monkeypatch, caplog):
    build_feed = _import_build_feed_without_providers(monkeypatch)

    def broken_formatter(_):
        raise RuntimeError("kaputtes Datum")

    monkeypatch.setattr(build_feed, "format_datetime", broken_formatter)

    caplog.set_level(logging.WARNING, logger="build_feed")

    dt = datetime(2023, 1, 1, tzinfo=timezone.utc)

    result = build_feed._fmt_rfc2822(dt)

    assert result == build_feed._to_utc(dt).strftime(build_feed.feed_config.RFC)
    log_records = [
        record
        for record in caplog.records
        if record.name == "build_feed"
        and "strftime-Fallback" in record.getMessage()
    ]
    assert log_records, "Fehlender Logeintrag für strftime-Fallback"


def test_cache_iso_items_sorted_and_emit_pubdate(monkeypatch):
    build_feed = _import_build_feed_without_providers(monkeypatch)

    now = datetime(2024, 1, 2, 9, 0, tzinfo=timezone.utc)
    cache_items = [
        {
            "title": "Ältere Meldung",
            "description": "Ältere Beschreibung",
            "link": "https://example.com/older",
            "guid": "older-guid",
            "pubDate": "2024-01-01T23:30:00+0100",
            "starts_at": "2024-01-01T20:00:00Z",
            "ends_at": "2024-01-02T20:00:00+01:00",
            "source": "Testquelle",
            "category": "Info",
        },
        {
            "title": "Neuere Meldung",
            "description": "Neuere Beschreibung",
            "link": "https://example.com/new",
            "guid": "new-guid",
            "pubDate": "2024-01-02T08:30:00Z",
            "starts_at": "2024-01-02T07:30:00+0100",
            "ends_at": "2024-01-03T10:00:00+01:00",
            "source": "Testquelle",
            "category": "Info",
        },
        {
            "title": "Veraltete Meldung",
            "description": "Alte Beschreibung",
            "link": "https://example.com/old",
            "guid": "old-guid",
            "pubDate": "2021-01-01T12:00:00+0000",
            "starts_at": "2021-01-01T12:00:00Z",
            "ends_at": "2021-01-02T12:00:00+0000",
            "source": "Testquelle",
            "category": "Info",
        },
    ]

    build_feed._normalize_item_datetimes(cache_items)

    for idx in (0, 1):
        for field in ("pubDate", "starts_at", "ends_at"):
            assert isinstance(cache_items[idx][field], datetime)
            assert cache_items[idx][field].tzinfo is not None

    state = {}
    filtered = build_feed._drop_old_items(cache_items, now, state)
    assert {it["guid"] for it in filtered} == {"new-guid", "older-guid"}

    deduped = build_feed._dedupe_items(filtered)
    deduped.sort(key=build_feed._sort_key)

    assert [it["guid"] for it in deduped] == ["new-guid", "older-guid"]

    monkeypatch.setattr(build_feed, "_save_state", lambda state: None)
    rss = build_feed._make_rss(deduped, now, state)

    assert rss.count("<item>") == 2
    assert rss.count("<pubDate>") == 2
    assert "Veraltete Meldung" not in rss
    assert rss.index("Neuere Meldung") < rss.index("Ältere Meldung")
    assert "<pubDate>Tue, 02 Jan 2024 08:30:00 +0000</pubDate>" in rss
    assert "<pubDate>Mon, 01 Jan 2024 22:30:00 +0000</pubDate>" in rss
