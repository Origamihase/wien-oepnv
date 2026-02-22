"""Tests for the Google Places access verification script."""

from __future__ import annotations

import logging
import os
from typing import Iterable, Iterator

import pytest

from scripts import verify_google_places_access as verify
from src.places.client import GooglePlacesError, GooglePlacesPermissionError, Place


@pytest.fixture(autouse=True)
def _reset_logging():
    logging.getLogger(verify.LOGGER.name).handlers.clear()
    yield
    logging.getLogger(verify.LOGGER.name).handlers.clear()


@pytest.fixture(autouse=True)
def _set_default_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setitem(os.environ, "GOOGLE_ACCESS_ID", "AIzaDummyKey")
    yield
    monkeypatch.delenv("GOOGLE_ACCESS_ID", raising=False)


class _SuccessClient:
    def __init__(self, config):
        self.config = config
        self.request_count = 0

    def iter_nearby(self, tiles: Iterable[verify.Tile]) -> Iterator[Place]:
        self.request_count += 1
        yield Place(
            place_id="test-place",
            name="Test Station",
            latitude=48.2,
            longitude=16.37,
            types=["train_station"],
            formatted_address=None,
        )


class _PermissionDeniedClient:
    def __init__(self, config):
        self.config = config
        self.request_count = 0

    def iter_nearby(self, tiles: Iterable[verify.Tile]) -> Iterator[Place]:
        raise GooglePlacesPermissionError(
            "PERMISSION_DENIED: Requests to this API places.googleapis.com method google.maps.places.v1.Places.SearchNearby are blocked."
        )


class _ErrorClient:
    def __init__(self, config):
        self.config = config
        self.request_count = 0

    def iter_nearby(self, tiles: Iterable[verify.Tile]) -> Iterator[Place]:
        raise GooglePlacesError("internal server error")


def test_main_success(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setattr(verify, "load_default_env_files", lambda environ=None: {})
    monkeypatch.setattr(verify, "GooglePlacesClient", _SuccessClient)
    caplog.set_level(logging.INFO, logger="places.verify")

    assert verify.main([]) == 0
    assert any("Places API access verified" in record.message for record in caplog.records)


def test_main_permission_denied(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setattr(verify, "load_default_env_files", lambda environ=None: {})
    monkeypatch.setattr(verify, "GooglePlacesClient", _PermissionDeniedClient)
    caplog.set_level(logging.INFO, logger="places.verify")

    assert verify.main([]) == 1
    assert any("Places API denied" in record.message for record in caplog.records)
    assert any("Places API (New)" in record.message for record in caplog.records)


def test_main_generic_error(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setattr(verify, "load_default_env_files", lambda environ=None: {})
    monkeypatch.setattr(verify, "GooglePlacesClient", _ErrorClient)
    caplog.set_level(logging.INFO, logger="places.verify")

    assert verify.main([]) == 1
    assert any("Places API request failed" in record.message for record in caplog.records)
