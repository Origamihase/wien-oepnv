"""Regression tests for ``scripts.update_station_directory.download_workbook``.

The download-with-fallback contract was introduced to close the
asymmetric soft-fail behaviour between the four upstream sources used
by the station-directory refresh: WL OGD, OSM, and Google Places all
have a soft-fail path (pinned CSV / circuit-breaker / quota-gated
fallback respectively); ÖBB used to be the only fail-fast source. A
transient ``data.oebb.at`` outage would then zero out a whole weekly
cron tick with no recovery path. The new behaviour reads from a
cached XLSX snapshot when the live download fails, and writes a fresh
snapshot back on every successful download.
"""
from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any

import pytest

from scripts import update_station_directory


def test_download_workbook_writes_cache_on_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A successful download must persist the bytes to ``cache_path``
    atomically so future cron ticks have a snapshot to fall back to.
    """
    payload = b"PK\x03\x04" + b"fake-xlsx-bytes-for-test" * 64
    cache_path = tmp_path / "oebb-verkehrsstationen.xlsx"

    def fake_session_with_retries(_user_agent: str) -> Any:
        class _DummySession:
            def __enter__(self) -> _DummySession:
                return self

            def __exit__(self, *_args: Any) -> None:
                return None

        return _DummySession()

    def fake_fetch(_session: Any, _url: str, *, timeout: Any) -> bytes:
        return payload

    monkeypatch.setattr(
        update_station_directory, "session_with_retries", fake_session_with_retries
    )
    monkeypatch.setattr(
        update_station_directory, "fetch_content_safe", fake_fetch
    )

    buf = update_station_directory.download_workbook(
        "https://example.test/wb.xlsx", cache_path=cache_path
    )

    assert isinstance(buf, BytesIO)
    assert buf.getvalue() == payload
    assert cache_path.exists()
    assert cache_path.read_bytes() == payload


def test_download_workbook_falls_back_to_cache_on_network_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the live download fails AND a cached snapshot exists, the
    function must return the cached bytes (with a warning logged) and
    NOT re-raise.
    """
    cached_payload = b"PK\x03\x04" + b"previously-cached-bytes" * 32
    cache_path = tmp_path / "oebb-verkehrsstationen.xlsx"
    cache_path.write_bytes(cached_payload)

    def fake_session_with_retries(_user_agent: str) -> Any:
        class _DummySession:
            def __enter__(self) -> _DummySession:
                return self

            def __exit__(self, *_args: Any) -> None:
                return None

        return _DummySession()

    def fake_fetch(_session: Any, _url: str, *, timeout: Any) -> bytes:
        raise OSError("simulated data.oebb.at outage")

    monkeypatch.setattr(
        update_station_directory, "session_with_retries", fake_session_with_retries
    )
    monkeypatch.setattr(
        update_station_directory, "fetch_content_safe", fake_fetch
    )

    buf = update_station_directory.download_workbook(
        "https://example.invalid/wb.xlsx", cache_path=cache_path
    )

    assert buf.getvalue() == cached_payload


def test_download_workbook_raises_when_neither_url_nor_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the live download fails AND no cached snapshot exists, the
    function must re-raise the original network error — there is no
    valid station directory state to proceed from.
    """
    cache_path = tmp_path / "oebb-verkehrsstationen.xlsx"
    assert not cache_path.exists()

    def fake_session_with_retries(_user_agent: str) -> Any:
        class _DummySession:
            def __enter__(self) -> _DummySession:
                return self

            def __exit__(self, *_args: Any) -> None:
                return None

        return _DummySession()

    def fake_fetch(_session: Any, _url: str, *, timeout: Any) -> bytes:
        raise OSError("simulated data.oebb.at outage")

    monkeypatch.setattr(
        update_station_directory, "session_with_retries", fake_session_with_retries
    )
    monkeypatch.setattr(
        update_station_directory, "fetch_content_safe", fake_fetch
    )

    with pytest.raises(OSError, match="simulated data.oebb.at outage"):
        update_station_directory.download_workbook(
            "https://example.invalid/wb.xlsx", cache_path=cache_path
        )
