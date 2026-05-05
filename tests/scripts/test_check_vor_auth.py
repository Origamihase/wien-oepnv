"""Tests for the offline VOR auth diagnostic script."""

from __future__ import annotations

import logging
from typing import Iterator

import pytest

from scripts import check_vor_auth as check
from src.providers import vor as vor_module


@pytest.fixture(autouse=True)
def _reset_logging() -> Iterator[None]:
    logging.getLogger(check.LOGGER.name).handlers.clear()
    yield
    logging.getLogger(check.LOGGER.name).handlers.clear()


@pytest.fixture(autouse=True)
def _patch_env_loading(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(check, "load_default_env_files", lambda: {})


def test_returns_0_when_credentials_present_over_https(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("VOR_ACCESS_ID", "dummy-token-12345")
    monkeypatch.delenv("VAO_ACCESS_ID", raising=False)
    monkeypatch.setattr(vor_module, "VOR_BASE_URL", "https://example.test/api/v1.11.0/")
    # Skip the configuration refresh so our patched VOR_BASE_URL stays in place.
    monkeypatch.setattr(vor_module, "refresh_base_configuration", lambda: vor_module.VOR_BASE_URL)

    caplog.set_level(logging.INFO, logger="vor.auth_check")
    assert check.main() == 0
    messages = " ".join(r.getMessage() for r in caplog.records)
    assert "looks consistent" in messages
    assert "accessId query parameter will be injected" in messages


def test_returns_2_when_no_credentials(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.delenv("VOR_ACCESS_ID", raising=False)
    monkeypatch.delenv("VAO_ACCESS_ID", raising=False)

    caplog.set_level(logging.INFO, logger="vor.auth_check")
    assert check.main() == 2
    assert any("No VOR credentials" in r.getMessage() for r in caplog.records)


def test_returns_1_when_base_url_is_plain_http(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("VOR_ACCESS_ID", "dummy-token-12345")
    monkeypatch.delenv("VAO_ACCESS_ID", raising=False)
    monkeypatch.setattr(vor_module, "VOR_BASE_URL", "http://insecure.test/api/v1.11.0/")
    monkeypatch.setattr(vor_module, "refresh_base_configuration", lambda: vor_module.VOR_BASE_URL)

    caplog.set_level(logging.INFO, logger="vor.auth_check")
    assert check.main() == 1
    assert any("plain HTTP" in r.getMessage() for r in caplog.records)


def test_scheme_label_detection() -> None:
    assert check._scheme_label("Bearer abcdef") == "Bearer"
    assert check._scheme_label("Basic dXNlcjpwYXNz") == "Basic"
    assert check._scheme_label("Custom xyz") == "unknown"
