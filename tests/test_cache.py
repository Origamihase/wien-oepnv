import json
import logging
from pathlib import Path

import pytest

from src.utils import cache
from src.utils.files import sanitize_filename


def _prepare_cache(tmp_path: Path, monkeypatch, provider: str) -> Path:
    base = tmp_path / "cache-root"
    monkeypatch.setattr(cache, "_CACHE_DIR", base, raising=False)
    target = base / sanitize_filename(provider)
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


@pytest.mark.parametrize("pretty", [False, True])
def test_write_cache_explicit_pretty_flag(tmp_path, monkeypatch, pretty):
    cache_file = _prepare_cache(tmp_path, monkeypatch, f"provider-{pretty}")

    cache.write_cache(f"provider-{pretty}", [{"id": 1}], pretty=pretty)

    expected = json.dumps(
        [{"id": 1}],
        ensure_ascii=False,
        indent=2 if pretty else None,
        separators=None if pretty else (",", ":"),
    )
    assert cache_file.read_text(encoding="utf-8") == expected


def test_write_cache_env_compact(tmp_path, monkeypatch):
    cache_file = _prepare_cache(tmp_path, monkeypatch, "env-provider")
    monkeypatch.setenv("WIEN_OEPNV_CACHE_PRETTY", "0")

    cache.write_cache("env-provider", [{"id": 2}])

    expected = json.dumps(
        [{"id": 2}], ensure_ascii=False, indent=None, separators=(",", ":")
    )
    assert cache_file.read_text(encoding="utf-8") == expected
