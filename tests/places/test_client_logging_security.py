"""Tests for security logging in the Places client."""

from __future__ import annotations

import logging
from typing import Any, Dict, Iterator
from unittest.mock import MagicMock

import pytest
import requests

from src.places.client import GooglePlacesClient, GooglePlacesConfig, GooglePlacesTileError
from src.places.tiling import Tile

class _MockResponse:
    def __init__(self, status_code: int, payload: Dict[str, Any]):
        self.status_code = status_code
        self._payload = payload
        self.headers: Dict[str, str] = {}
        self.raw = MagicMock()
        self.raw.connection.sock.getpeername.return_value = ("8.8.8.8", 443)
        self._content_consumed = True
        self._content = b""

    def json(self) -> Dict[str, Any]:
        return self._payload

    def iter_content(self, chunk_size: int = 1) -> Iterator[bytes]:
        yield b""

    def close(self) -> None:
        pass

    def __enter__(self) -> _MockResponse:
        return self

    def __exit__(self, *args: Any) -> None:
        pass


class _MockSession:
    def __init__(self, response: _MockResponse):
        self._response = response

    def post(
        self,
        url: str,
        *,
        headers: Dict[str, str],
        json: Dict[str, Any],
        timeout: float,
        **kwargs: Any,
    ) -> _MockResponse:
        return self._response


def test_client_redacts_secrets_in_parsing_warnings(caplog: pytest.LogCaptureFixture) -> None:
    """Ensure that secrets in the payload are not logged when parsing fails."""
    secret = "SUPER_SECRET_API_KEY"
    # Payload that triggers "Ignoring unexpected place payload" (not a dict)
    # or "Skipping place without valid id" (missing id)
    # We use a payload that is a dict but missing 'id', and contains the secret.
    payload = {
        "places": [
            {
                "no_id": "here",
                "leaked_secret": secret
            }
        ]
    }

    response = _MockResponse(200, payload)
    session = _MockSession(response)
    config = GooglePlacesConfig(
        api_key=secret, # The secret is also the API key
        included_types=["train_station"],
        language="de",
        region="AT",
        radius_m=2500,
        timeout_s=1,
        max_retries=0,
    )
    client = GooglePlacesClient(config, session=session)

    tile = Tile(48.0, 16.0)

    # Enable logging capture
    caplog.set_level(logging.WARNING, logger="places.google")

    # Run iter_nearby, which calls _parse_place
    list(client.iter_nearby([tile]))

    # Check logs
    assert "Skipping place without valid id" in caplog.text
    # THIS SHOULD FAIL BEFORE FIX
    if secret in caplog.text:
         pytest.fail(f"Secret key found in logs: {caplog.text}")

def test_client_redacts_secrets_in_request_errors(caplog: pytest.LogCaptureFixture) -> None:
    """Ensure that secrets in exception messages are redacted."""
    secret = "SUPER_SECRET_API_KEY"

    # Mock session that raises an exception with the secret in the message
    session = MagicMock()
    session.post.side_effect = requests.RequestException(f"Connection failed to {secret}")

    config = GooglePlacesConfig(
        api_key=secret,
        included_types=["train_station"],
        language="de",
        region="AT",
        radius_m=2500,
        timeout_s=1,
        max_retries=0,
    )
    client = GooglePlacesClient(config, session=session)

    caplog.set_level(logging.WARNING, logger="places.google")

    with pytest.raises(RuntimeError): # GooglePlacesError wraps it
        client._post("endpoint", {})

    # Check logs
    # THIS SHOULD FAIL BEFORE FIX
    if secret in caplog.text:
         pytest.fail(f"Secret key found in error logs: {caplog.text}")
