import json
import logging
from pathlib import Path

import pytest

from src.utils import cache
from src.utils.files import sanitize_filename


def _prepare_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str) -> Path:
    base = tmp_path / "cache-root"
    monkeypatch.setattr(cache, "_CACHE_DIR", base, raising=False)
    target = base / sanitize_filename(provider)
    target.mkdir(parents=True, exist_ok=True)
    return target / "events.json"


def test_read_cache_returns_list(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache_file = _prepare_cache(tmp_path, monkeypatch, "provider")
    cache_file.write_text(json.dumps([{"id": 1}]), encoding="utf-8")

    assert cache.read_cache("provider") == [{"id": 1}]


def test_read_cache_warns_on_non_list(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    cache_file = _prepare_cache(tmp_path, monkeypatch, "provider")
    cache_file.write_text(json.dumps({"id": 1}), encoding="utf-8")

    caplog.set_level(logging.WARNING, logger="src.utils.cache")

    assert cache.read_cache("provider") == []

    assert "does not contain a JSON array" in caplog.text


@pytest.mark.parametrize("pretty", [False, True])
def test_write_cache_explicit_pretty_flag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    pretty: bool,
) -> None:
    cache_file = _prepare_cache(tmp_path, monkeypatch, f"provider-{pretty}")

    cache.write_cache(f"provider-{pretty}", [{"id": 1}], pretty=pretty)

    expected = json.dumps(
        [{"id": 1}],
        ensure_ascii=False,
        indent=2 if pretty else None,
        separators=None if pretty else (",", ":"),
    )
    assert cache_file.read_text(encoding="utf-8") == expected


def test_write_cache_env_compact(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache_file = _prepare_cache(tmp_path, monkeypatch, "env-provider")
    monkeypatch.setenv("WIEN_OEPNV_CACHE_PRETTY", "0")

    cache.write_cache("env-provider", [{"id": 2}])

    expected = json.dumps(
        [{"id": 2}], ensure_ascii=False, indent=None, separators=(",", ":")
    )
    assert cache_file.read_text(encoding="utf-8") == expected


def test_write_status_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    base = tmp_path / "cache-root"
    monkeypatch.setattr(cache, "_CACHE_DIR", base, raising=False)

    payload = {
        "last_run_at": "2026-05-05T17:30:00+00:00",
        "status": "ok",
        "events_collected": 0,
        "stations_queried": 2,
    }
    cache.write_status("vor", payload)

    status_path = base / sanitize_filename("vor") / "last_run.json"
    assert status_path.exists()
    assert json.loads(status_path.read_text(encoding="utf-8")) == payload
    assert cache.read_status("vor") == payload


def test_read_status_returns_none_when_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = tmp_path / "cache-root"
    monkeypatch.setattr(cache, "_CACHE_DIR", base, raising=False)

    assert cache.read_status("vor") is None


def test_read_status_returns_none_on_invalid_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = tmp_path / "cache-root"
    monkeypatch.setattr(cache, "_CACHE_DIR", base, raising=False)
    target = base / sanitize_filename("vor")
    target.mkdir(parents=True, exist_ok=True)
    (target / "last_run.json").write_text("{not json", encoding="utf-8")

    assert cache.read_status("vor") is None


def test_write_status_rejects_non_dict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = tmp_path / "cache-root"
    monkeypatch.setattr(cache, "_CACHE_DIR", base, raising=False)

    with pytest.raises(TypeError):
        cache.write_status("vor", [1, 2, 3])  # type: ignore[arg-type]


def test_write_status_rejects_invalid_provider_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = tmp_path / "cache-root"
    monkeypatch.setattr(cache, "_CACHE_DIR", base, raising=False)

    with pytest.raises(ValueError):
        cache.write_status("../escape", {"status": "ok"})
