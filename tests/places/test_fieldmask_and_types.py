"""Tests for Places field mask handling and type sanitisation."""

from __future__ import annotations

import json
from typing import Any
from collections.abc import Iterator
from unittest.mock import MagicMock

from src.places.client import (
    DEFAULT_INCLUDED_TYPES,
    FIELD_MASK_NEARBY,
    GooglePlacesClient,
    GooglePlacesConfig,
)
from src.places.tiling import Tile


class _RecordingResponse:
    def __init__(self, status_code: int, payload: dict[str, Any]) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)
        self.headers: dict[str, str] = {}
        self.raw = MagicMock()
        conn = MagicMock()
        conn.sock.getpeername.return_value = ("8.8.8.8", 443)
        self.raw.connection = conn
        self.raw._connection = conn

    def json(self) -> dict[str, Any]:
        return self._payload

    def iter_content(self, chunk_size: int = 1) -> Iterator[bytes]:
        yield self.text.encode("utf-8")

    def close(self) -> None:
        pass

    def __enter__(self) -> _RecordingResponse:
        return self

    def __exit__(self, *args: Any) -> None:
        pass


class _RecordingSession:
    def __init__(self, response: _RecordingResponse) -> None:
        self._response = response
        self.headers: dict[str, str] | None = None
        self.body: dict[str, Any] | None = None
        self.calls = 0

    def post(
        self,
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, Any],
        timeout: float,
        **kwargs: Any,
    ) -> _RecordingResponse:
        self.calls += 1
        self.headers = headers
        self.body = json
        return self._response


def _make_client(
    *,
    included_types: list[str],
) -> tuple[GooglePlacesClient, _RecordingSession]:
    config = GooglePlacesConfig(
        api_key="dummy",
        included_types=included_types,
        language="de",
        region="AT",
        radius_m=2500,
        timeout_s=1,
        max_retries=0,
    )
    response = _RecordingResponse(200, {"places": []})
    session = _RecordingSession(response)
    client = GooglePlacesClient(config, session=session)
    return client, session


def test_field_mask_excludes_next_page_token() -> None:
    client, session = _make_client(included_types=list(DEFAULT_INCLUDED_TYPES))
    tile = Tile(latitude=48.0, longitude=16.0)

    list(client.iter_nearby([tile]))

    assert session.headers is not None
    assert session.headers["X-Goog-FieldMask"] == FIELD_MASK_NEARBY
    assert "nextPageToken" not in session.headers["X-Goog-FieldMask"]


def test_invalid_types_are_removed() -> None:
    client, session = _make_client(included_types=["train_station", "transit_station"])
    tile = Tile(latitude=48.0, longitude=16.0)

    list(client.iter_nearby([tile]))

    assert session.body is not None
    assert session.body["includedTypes"] == ["train_station"]


def test_empty_types_fallback_to_defaults() -> None:
    client, session = _make_client(included_types=[])
    tile = Tile(latitude=48.0, longitude=16.0)

    list(client.iter_nearby([tile]))

    assert session.body is not None
    assert session.body["includedTypes"] == list(DEFAULT_INCLUDED_TYPES)
