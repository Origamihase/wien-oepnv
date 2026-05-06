"""Quota safety tests for the Places client retry path.

Google Places bills per request, regardless of HTTP response status. The
client must therefore consume budget BEFORE each HTTP attempt — otherwise a
429/5xx retry storm could silently exceed the configured monthly cap.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from collections.abc import Iterator
from unittest.mock import MagicMock

import pytest
import requests

from src.places.client import GooglePlacesClient, GooglePlacesConfig
from src.places.quota import MonthlyQuota, QuotaConfig

_CONFIG = GooglePlacesConfig(
    api_key="dummy",
    included_types=["bus_station"],
    language="de",
    region="AT",
    radius_m=1000,
    timeout_s=1.0,
    max_retries=3,
    max_result_count=20,
)


class _MockSocket:
    def getpeername(self) -> tuple[str, int]:
        return ("8.8.8.8", 443)


class _MockRaw:
    def __init__(self) -> None:
        self.connection = MagicMock()
        self.connection.sock = _MockSocket()
        self._connection = self.connection


class _MockResponse:
    def __init__(self, status: int, body: bytes = b"{}") -> None:
        self.status_code = status
        self.headers: dict[str, str] = {}
        self.raw = _MockRaw()
        self.url = "https://places.googleapis.com/v1/places:searchNearby"
        self._content = body
        self._content_consumed = False
        self.text = body.decode("utf-8", errors="replace")

    def iter_content(self, chunk_size: int = 1) -> Iterator[bytes]:
        yield self._content

    def json(self) -> Any:
        import json

        return json.loads(self._content)

    def close(self) -> None:
        pass

    def __enter__(self) -> _MockResponse:
        return self

    def __exit__(self, *args: Any) -> None:
        pass


def _make_client(tmp_path: Path, session: MagicMock, *, daily_limit: int = 10) -> tuple[
    GooglePlacesClient, MonthlyQuota, QuotaConfig
]:
    quota = MonthlyQuota(month_key="2024-05")
    cfg = QuotaConfig(
        limit_total=None,
        limit_nearby=None,
        limit_text=None,
        limit_details=None,
        limit_daily=daily_limit,
    )
    state = tmp_path / "quota.json"
    client = GooglePlacesClient(
        _CONFIG,
        session=session,
        quota=quota,
        quota_config=cfg,
        quota_state_path=state,
        enforce_quota=True,
    )
    return client, quota, cfg


def test_quota_increments_per_attempt_on_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 429 followed by 200 must consume two units of quota, not one.

    Google bills per request, so silently masking the failed first attempt
    would let the retry loop exceed the configured cap.
    """
    monkeypatch.setattr("src.places.client.time.sleep", lambda _s: None)
    session = MagicMock(spec=requests.Session)
    session.post.side_effect = [
        _MockResponse(429),
        _MockResponse(200, b'{"places": []}'),
    ]
    client, quota, _ = _make_client(tmp_path, session)
    client._post("places:searchNearby", {}, quota_kind="nearby")

    # One attempt was 429 (still counted), one was 200 — two consumed.
    assert quota.counts["nearby"] == 2
    assert quota.daily_total == 2


def test_quota_aborts_when_daily_cap_reached_during_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If retries would exceed the daily cap the client must stop early."""
    monkeypatch.setattr("src.places.client.time.sleep", lambda _s: None)
    session = MagicMock(spec=requests.Session)
    # Provide several 429s so the loop wants to keep retrying.
    session.post.side_effect = [_MockResponse(429) for _ in range(5)]
    # daily_limit=2 lets exactly two attempts run; the third must short-circuit.
    client, quota, _ = _make_client(tmp_path, session, daily_limit=2)
    result = client._post("places:searchNearby", {}, quota_kind="nearby")

    assert quota.counts["nearby"] == 2
    assert quota.daily_total == 2
    # The client signals that the call was skipped due to quota exhaustion.
    assert result.get("skipped_due_to_quota") is True
    # Only two HTTP attempts actually went out.
    assert session.post.call_count == 2


def test_quota_increments_on_single_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A clean 200 still increments by exactly one (regression guard)."""
    monkeypatch.setattr("src.places.client.time.sleep", lambda _s: None)
    session = MagicMock(spec=requests.Session)
    session.post.return_value = _MockResponse(200, b'{"places": []}')
    client, quota, _ = _make_client(tmp_path, session)
    client._post("places:searchNearby", {}, quota_kind="nearby")

    assert quota.counts["nearby"] == 1
    assert quota.daily_total == 1
