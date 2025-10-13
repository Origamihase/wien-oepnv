import json
import logging
from pathlib import Path

from src.utils import cache


def _prepare_cache(tmp_path: Path, monkeypatch, provider: str) -> Path:
    base = tmp_path / "cache-root"
    monkeypatch.setattr(cache, "_CACHE_DIR", base, raising=False)
    target = base / provider
    target.mkdir(parents=True, exist_ok=True)
    return target / "events.json"


def test_read_cache_returns_list(tmp_path, monkeypatch):
    cache_file = _prepare_cache(tmp_path, monkeypatch, "provider")
    cache_file.write_text(json.dumps([{"id": 1}]), encoding="utf-8")

    assert cache.read_cache("provider") == [{"id": 1}]


def test_read_cache_warns_on_non_list(tmp_path, monkeypatch, caplog):
    cache_file = _prepare_cache(tmp_path, monkeypatch, "provider")
    cache_file.write_text(json.dumps({"id": 1}), encoding="utf-8")

    caplog.set_level(logging.WARNING, logger="utils.cache")

    assert cache.read_cache("provider") == []

    assert "does not contain a JSON array" in caplog.text
