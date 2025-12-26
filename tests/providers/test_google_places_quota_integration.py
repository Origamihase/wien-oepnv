from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List

import pytest

from scripts.fetch_google_places_stations import main as fetch_main
from src.places.client import GooglePlacesClient, GooglePlacesConfig
from src.places.quota import MonthlyQuota, QuotaConfig
from src.places.tiling import Tile


class DummyResponse:
    def __init__(self, status_code: int, payload: dict | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {}
        self.text = json.dumps(self._payload)

    def json(self) -> dict:
        return self._payload


class DummySession:
    def __init__(self, responses: Iterable[DummyResponse]) -> None:
        self._queue = list(responses)
        self.calls: List[dict] = []
        self.headers: dict = {}
        self.hooks: dict = {"response": []}

    def mount(self, prefix: str, adapter: object) -> None:
        pass

    def post(self, url: str, *, headers: dict, json: dict, timeout: float) -> DummyResponse:
        if not self._queue:
            raise AssertionError("Unexpected HTTP call: no responses queued")
        self.calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        return self._queue.pop(0)


def _make_config(max_retries: int = 0) -> GooglePlacesConfig:
    return GooglePlacesConfig(
        api_key="key",
        included_types=["train_station"],
        language="de",
        region="AT",
        radius_m=1000,
        timeout_s=5.0,
        max_retries=max_retries,
    )


def _save_quota(path: Path, quota: MonthlyQuota) -> None:
    quota.save_atomic(path)


def test_client_short_circuits_when_quota_reached(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    quota_path = tmp_path / "quota.json"
    quota = MonthlyQuota(month_key="2024-05")
    quota.counts["nearby"] = 1
    quota.total = 1
    _save_quota(quota_path, quota)

    config = _make_config()
    quota_limits = QuotaConfig(limit_total=1, limit_nearby=1, limit_text=None, limit_details=None)
    session = DummySession([])
    initial = quota_path.read_text(encoding="utf-8")

    client = GooglePlacesClient(
        config,
        session=session,
        quota=MonthlyQuota.load(quota_path, now_func=lambda: datetime(2024, 5, 1, tzinfo=timezone.utc)),
        quota_config=quota_limits,
        quota_state_path=quota_path,
        enforce_quota=True,
    )

    caplog.set_level("WARNING", logger="places.google")
    results = list(client.iter_nearby([Tile(latitude=48.2, longitude=16.3)]))

    assert results == []
    assert session.calls == []
    assert client.quota_skipped_kinds == {"nearby"}
    assert "Places free cap reached" in caplog.text
    assert quota_path.read_text(encoding="utf-8") == initial


def test_successful_request_updates_quota_state(tmp_path: Path) -> None:
    quota_path = tmp_path / "quota.json"
    quota = MonthlyQuota(month_key="2024-05")
    _save_quota(quota_path, quota)

    session = DummySession(
        [
            DummyResponse(
                200,
                {
                    "places": [
                        {
                            "id": "abc",
                            "displayName": {"text": "Station"},
                            "location": {"latitude": 48.2, "longitude": 16.3},
                            "types": ["train_station"],
                        }
                    ]
                },
            )
        ]
    )

    client = GooglePlacesClient(
        _make_config(),
        session=session,
        quota=MonthlyQuota.load(quota_path),
        quota_config=QuotaConfig(limit_total=5, limit_nearby=5, limit_text=5, limit_details=5),
        quota_state_path=quota_path,
        enforce_quota=True,
    )

    results = list(client.iter_nearby([Tile(latitude=48.2, longitude=16.3)]))
    assert len(results) == 1
    updated = json.loads(quota_path.read_text(encoding="utf-8"))
    assert updated["counts"]["nearby"] == 1
    assert updated["total"] == 1


def test_rate_limit_does_not_consume_quota(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    quota_path = tmp_path / "quota.json"
    quota = MonthlyQuota(month_key="2024-05")
    _save_quota(quota_path, quota)

    session = DummySession([
        DummyResponse(429, {"error": "rate limit"}),
        DummyResponse(
            200,
            {
                "places": [],
            },
        ),
    ])

    client = GooglePlacesClient(
        _make_config(max_retries=1),
        session=session,
        quota=MonthlyQuota.load(quota_path),
        quota_config=QuotaConfig(limit_total=10, limit_nearby=10, limit_text=10, limit_details=10),
        quota_state_path=quota_path,
        enforce_quota=True,
    )
    monkeypatch.setattr(client, "_backoff", lambda attempt: 0.0)
    monkeypatch.setattr("src.places.client.time.sleep", lambda _: None)

    list(client.iter_nearby([Tile(latitude=48.2, longitude=16.3)]))

    data = json.loads(quota_path.read_text(encoding="utf-8"))
    assert data["counts"]["nearby"] == 1
    assert data["total"] == 1


def _configure_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, quota_path: Path) -> None:
    monkeypatch.setenv("GOOGLE_ACCESS_ID", "key")
    monkeypatch.setenv("PLACES_TILES", json.dumps([{"lat": 48.2, "lng": 16.3}]))
    monkeypatch.setenv("OUT_PATH_STATIONS", str(tmp_path / "stations.json"))
    monkeypatch.setenv("PLACES_QUOTA_STATE", str(quota_path))
    monkeypatch.setenv("MERGE_MAX_DIST_M", "150")


def test_cli_short_circuits_on_quota(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    quota_path = tmp_path / "quota.json"
    quota = MonthlyQuota(month_key=MonthlyQuota.current_month_key())
    quota.counts["nearby"] = 1500
    quota.total = 1500
    _save_quota(quota_path, quota)

    responses: List[DummyResponse] = []
    sessions: List[DummySession] = []

    def factory() -> DummySession:
        session = DummySession(list(responses))
        sessions.append(session)
        return session

    monkeypatch.setattr("src.places.client.requests.Session", factory)
    _configure_env(monkeypatch, tmp_path, quota_path)
    caplog.set_level("INFO", logger="places.cli")

    exit_code = fetch_main(["--write"])

    assert exit_code == 0
    assert not sessions or sessions[0].calls == []
    assert "Quota reached" in caplog.text
    assert not (tmp_path / "stations.json").exists()
    with quota_path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    assert data["counts"]["nearby"] == 1500


def test_cli_dry_run_reports_quota(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    responses = [
        DummyResponse(
            200,
            {
                "places": [],
            },
        )
    ]
    quota_path = tmp_path / "quota.json"
    quota = MonthlyQuota(month_key=MonthlyQuota.current_month_key())
    _save_quota(quota_path, quota)

    sessions: List[DummySession] = []

    def factory() -> DummySession:
        session = DummySession(list(responses))
        sessions.append(session)
        return session

    monkeypatch.setattr("src.places.client.requests.Session", factory)
    _configure_env(monkeypatch, tmp_path, quota_path)
    caplog.set_level("INFO", logger="places.cli")

    exit_code = fetch_main(["--dry-run"])

    assert exit_code == 0
    assert "Quota status" in caplog.text
    assert sessions[0].calls
    assert not (tmp_path / "stations.json").exists()

