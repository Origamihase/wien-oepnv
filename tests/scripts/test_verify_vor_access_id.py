"""Tests for the VOR access ID verification script."""

from __future__ import annotations

import json
import logging
from typing import Any, Iterator

import pytest

from scripts import verify_vor_access_id as verify
from src.providers import vor as vor_module


@pytest.fixture(autouse=True)
def _reset_logging() -> Iterator[None]:
    logging.getLogger(verify.LOGGER.name).handlers.clear()
    yield
    logging.getLogger(verify.LOGGER.name).handlers.clear()


@pytest.fixture(autouse=True)
def _patch_env_loading(monkeypatch: pytest.MonkeyPatch) -> None:
    # The script calls load_default_env_files() at the top of main(); we don't
    # want it to read real .env files during tests.
    monkeypatch.setattr(verify, "load_default_env_files", lambda: {})


@pytest.fixture
def _with_credentials(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("VOR_ACCESS_ID", "dummy-token")
    monkeypatch.delenv("VAO_ACCESS_ID", raising=False)
    yield


def test_main_succeeds_on_valid_payload(
    monkeypatch: pytest.MonkeyPatch,
    _with_credentials: None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    payload = {"stopLocationOrCoordLocation": [{"StopLocation": {"name": "Wien Hbf"}}]}

    def fake_fetch(*args: Any, **kwargs: Any) -> bytes:
        return json.dumps(payload).encode("utf-8")

    class DummySession:
        headers: dict[str, str] = {}
        auth = None

        def __enter__(self) -> "DummySession":
            return self

        def __exit__(self, *exc: object) -> None:
            return None

    monkeypatch.setattr(verify, "session_with_retries", lambda *a, **kw: DummySession())
    monkeypatch.setattr(verify, "fetch_content_safe", fake_fetch)
    monkeypatch.setattr(vor_module, "apply_authentication", lambda session: None)

    caplog.set_level(logging.INFO, logger="vor.verify")
    assert verify.main() == 0
    assert any("credentials accepted" in r.getMessage() for r in caplog.records)


def test_main_returns_2_when_credentials_missing(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.delenv("VOR_ACCESS_ID", raising=False)
    monkeypatch.delenv("VAO_ACCESS_ID", raising=False)

    caplog.set_level(logging.INFO, logger="vor.verify")
    assert verify.main() == 2
    assert any("not set" in r.getMessage() for r in caplog.records)


def test_main_returns_1_on_request_failure(
    monkeypatch: pytest.MonkeyPatch,
    _with_credentials: None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def fake_fetch(*args: Any, **kwargs: Any) -> bytes:
        raise RuntimeError("network down")

    class DummySession:
        headers: dict[str, str] = {}
        auth = None

        def __enter__(self) -> "DummySession":
            return self

        def __exit__(self, *exc: object) -> None:
            return None

    monkeypatch.setattr(verify, "session_with_retries", lambda *a, **kw: DummySession())
    monkeypatch.setattr(verify, "fetch_content_safe", fake_fetch)
    monkeypatch.setattr(vor_module, "apply_authentication", lambda session: None)

    caplog.set_level(logging.INFO, logger="vor.verify")
    assert verify.main() == 1
    assert any("request failed" in r.getMessage() for r in caplog.records)


def test_main_returns_1_on_unexpected_payload(
    monkeypatch: pytest.MonkeyPatch,
    _with_credentials: None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def fake_fetch(*args: Any, **kwargs: Any) -> bytes:
        return b'{"unexpected": "shape"}'

    class DummySession:
        headers: dict[str, str] = {}
        auth = None

        def __enter__(self) -> "DummySession":
            return self

        def __exit__(self, *exc: object) -> None:
            return None

    monkeypatch.setattr(verify, "session_with_retries", lambda *a, **kw: DummySession())
    monkeypatch.setattr(verify, "fetch_content_safe", fake_fetch)
    monkeypatch.setattr(vor_module, "apply_authentication", lambda session: None)

    caplog.set_level(logging.INFO, logger="vor.verify")
    assert verify.main() == 1
    assert any("did not contain a recognised stop list" in r.getMessage() for r in caplog.records)


def test_looks_like_stop_recognises_legacy_shape() -> None:
    payload = {"LocationList": {"StopLocation": [{"name": "Wien Hbf"}]}}
    assert verify._looks_like_stop(payload) is True


def test_looks_like_stop_rejects_empty_list() -> None:
    assert verify._looks_like_stop({"stopLocationOrCoordLocation": []}) is False
    assert verify._looks_like_stop({"LocationList": {"StopLocation": []}}) is False
    assert verify._looks_like_stop("not a dict") is False
