"""Tests for environment-driven Nearby search configuration."""

from __future__ import annotations

import importlib
import os
from contextlib import contextmanager
from types import ModuleType
from typing import Any, Dict, Iterator
from unittest.mock import MagicMock

from src.places.tiling import Tile

_ENV_KEYS = ("PLACES_RADIUS_M", "PLACES_MAX_RESULTS", "PLACES_RANK_PREFERENCE")


class _DummyResponse:
    def __init__(self) -> None:
        self.status_code = 200
        self.text = "{}"
        self.headers: Dict[str, str] = {}
        self.raw = MagicMock()
        # Use a public IP to pass verify_response_ip
        self.raw.connection.sock.getpeername.return_value = ("8.8.8.8", 443)

    def json(self) -> Dict[str, Any]:
        return {"places": []}

    def iter_content(self, chunk_size: int = 1) -> Iterator[bytes]:
        yield b"{}"

    def close(self) -> None:
        pass

    def __enter__(self) -> _DummyResponse:
        return self

    def __exit__(self, *args: Any) -> None:
        pass


class _RecordingSession:
    def __init__(self) -> None:
        self.last_json: Dict[str, Any] | None = None
        self.calls = 0

    def post(
        self,
        url: str,
        *,
        headers: Dict[str, str],
        json: Dict[str, Any],
        timeout: float,
        **kwargs: Any,
    ) -> _DummyResponse:
        self.calls += 1
        self.last_json = json
        return _DummyResponse()


@contextmanager
def _client_module_with_env(env: Dict[str, str] | None) -> Iterator[ModuleType]:
    module = importlib.import_module("src.places.client")
    previous = {key: os.environ.get(key) for key in _ENV_KEYS}
    try:
        values = env or {}
        for key in _ENV_KEYS:
            if key in values:
                os.environ[key] = values[key]
            else:
                os.environ.pop(key, None)
        module = importlib.reload(module)
        yield module
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        importlib.reload(module)


def _exercise_client(module: ModuleType) -> Dict[str, Any]:
    config = module.GooglePlacesConfig(
        api_key="dummy",
        included_types=list(module.DEFAULT_INCLUDED_TYPES),
        language="de",
        region="AT",
        radius_m=module.RADIUS_M,
        timeout_s=1.0,
        max_retries=0,
        max_result_count=module.MAX_RESULTS,
    )
    session = _RecordingSession()
    client = module.GooglePlacesClient(config, session=session)
    tile = Tile(latitude=48.0, longitude=16.0)
    list(client.iter_nearby([tile]))
    assert session.last_json is not None
    return session.last_json


def test_env_values_are_applied() -> None:
    with _client_module_with_env(
        {
            "PLACES_RADIUS_M": "1234",
            "PLACES_MAX_RESULTS": "7",
            "PLACES_RANK_PREFERENCE": "distance",
        }
    ) as module:
        body = _exercise_client(module)
    assert body["locationRestriction"]["circle"]["radius"] == 1234
    assert body["maxResultCount"] == 7
    assert body["rankPreference"] == "DISTANCE"


def test_max_results_lower_bound() -> None:
    with _client_module_with_env({"PLACES_MAX_RESULTS": "0"}) as module:
        body = _exercise_client(module)
    assert body["maxResultCount"] == 1


def test_max_results_upper_bound() -> None:
    with _client_module_with_env({"PLACES_MAX_RESULTS": "99"}) as module:
        body = _exercise_client(module)
    assert body["maxResultCount"] == 20


def test_radius_bounds() -> None:
    with _client_module_with_env({"PLACES_RADIUS_M": "0"}) as module:
        body = _exercise_client(module)
    assert body["locationRestriction"]["circle"]["radius"] == 1

    with _client_module_with_env({"PLACES_RADIUS_M": "999999"}) as module:
        body = _exercise_client(module)
    assert body["locationRestriction"]["circle"]["radius"] == 50000


def test_defaults_when_env_missing() -> None:
    with _client_module_with_env(None) as module:
        body = _exercise_client(module)
    assert body["locationRestriction"]["circle"]["radius"] == 2500
    assert body["maxResultCount"] == 20
    assert body["rankPreference"] == "POPULARITY"
