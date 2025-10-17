"""Tests for Google Places API key resolution."""

from __future__ import annotations

import logging
from typing import Iterator

import pytest

from src.places.client import get_places_api_key


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.delenv("GOOGLE_ACCESS_ID", raising=False)
    monkeypatch.delenv("GOOGLE_MAPS_API_KEY", raising=False)
    yield


def test_get_places_api_key_prefers_access_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOOGLE_ACCESS_ID", "access-token")

    result = get_places_api_key()

    assert result == "access-token"


def test_get_places_api_key_warns_on_legacy(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "legacy-token")

    with caplog.at_level(logging.WARNING, logger="places.google"):
        result = get_places_api_key()

    assert result == "legacy-token"
    assert any("DEPRECATED" in record.getMessage() for record in caplog.records)


def test_get_places_api_key_ignores_legacy_when_access_id_present(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("GOOGLE_ACCESS_ID", "fresh-token")
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "legacy-token")

    with caplog.at_level(logging.WARNING, logger="places.google"):
        result = get_places_api_key()

    assert result == "fresh-token"
    assert not any("DEPRECATED" in record.getMessage() for record in caplog.records)


def test_get_places_api_key_missing(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.ERROR, logger="places.google"):
        with pytest.raises(SystemExit) as excinfo:
            get_places_api_key()

    assert excinfo.value.code == 2
    assert str(excinfo.value) == "Missing GOOGLE_ACCESS_ID (preferred) or GOOGLE_MAPS_API_KEY."
    assert any("Missing GOOGLE_ACCESS_ID" in record.getMessage() for record in caplog.records)
