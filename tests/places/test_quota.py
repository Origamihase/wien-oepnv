from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.places.quota import (
    MonthlyQuota,
    QuotaConfig,
    load_quota_config_from_env,
    resolve_quota_state_path,
)


def _utc(year: int, month: int, day: int = 1) -> datetime:
    return datetime(year, month, day, tzinfo=timezone.utc)


def test_maybe_reset_month_resets_counts(caplog: pytest.LogCaptureFixture) -> None:
    quota = MonthlyQuota(
        month_key="2024-04",
        counts={"nearby": 3, "text": 2, "details": 1},
        total=6,
        _now_func=lambda: _utc(2024, 5, 1),
    )
    caplog.set_level("INFO", logger="places.quota")

    reset = quota.maybe_reset_month()

    assert reset is True
    assert quota.month_key == "2024-05"
    assert quota.counts == {"nearby": 0, "text": 0, "details": 0}
    assert quota.total == 0
    assert "Quota reset for new month 2024-05" in caplog.text


def test_can_consume_and_consume_respects_limits() -> None:
    config = QuotaConfig(limit_total=3, limit_nearby=2, limit_text=2, limit_details=None)
    quota = MonthlyQuota(month_key="2024-05")

    assert quota.can_consume("nearby", config)
    quota.consume("nearby", config)
    assert quota.counts["nearby"] == 1
    assert quota.total == 1

    assert quota.can_consume("nearby", config)
    quota.consume("nearby", config)
    assert quota.counts["nearby"] == 2
    assert not quota.can_consume("nearby", config)

    assert quota.can_consume("text", config)
    quota.consume("text", config)
    assert quota.total == 3
    assert not quota.can_consume("text", config)
    assert not quota.can_consume("nearby", config)

    with pytest.raises(RuntimeError):
        quota.consume("nearby", config)


def test_save_and_load_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "quota.json"
    quota = MonthlyQuota(month_key="2024-05")
    quota.counts["nearby"] = 5
    quota.total = 7

    quota.save_atomic(path)

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["counts"]["nearby"] == 5
    assert data["total"] == 7

    loaded = MonthlyQuota.load(path, now_func=lambda: _utc(2024, 5, 1))
    assert loaded.counts == quota.counts
    assert loaded.total == quota.total
    assert loaded.month_key == quota.month_key


def test_load_initialises_missing_state(tmp_path: Path) -> None:
    path = tmp_path / "quota.json"
    quota = MonthlyQuota.load(path, now_func=lambda: _utc(2024, 7, 1))
    assert quota.month_key == "2024-07"
    assert quota.counts == {"nearby": 0, "text": 0, "details": 0}
    assert quota.total == 0


def test_load_quota_config_from_env_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PLACES_LIMIT_TOTAL", raising=False)
    monkeypatch.delenv("PLACES_LIMIT_NEARBY", raising=False)
    monkeypatch.delenv("PLACES_LIMIT_TEXT", raising=False)
    monkeypatch.delenv("PLACES_LIMIT_DETAILS", raising=False)

    config = load_quota_config_from_env({})
    assert config.limit_total == 4000
    assert config.limit_nearby == 1500
    assert config.limit_text == 1500
    assert config.limit_details == 1000


def test_resolve_quota_state_path_prefers_env(tmp_path: Path) -> None:
    override = Path("data") / "places_quota_override.json"
    path = resolve_quota_state_path({"PLACES_QUOTA_STATE": str(override)})
    assert path == override.resolve()

    base = Path("data") / "state"
    result = resolve_quota_state_path({"STATE_PATH": str(base)})
    assert result == (base / "places_quota.json").resolve()


def test_resolve_quota_state_path_rejects_outside_repo(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        resolve_quota_state_path({"PLACES_QUOTA_STATE": str(tmp_path / "custom.json")})
