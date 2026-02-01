"""Security tests for GooglePlacesClient (DoS and SSRF protection)."""

from __future__ import annotations

import pytest
import requests
from typing import Iterator, Any, Dict
from unittest.mock import MagicMock

from src.places.client import GooglePlacesClient, GooglePlacesConfig, GooglePlacesError

# Dummy config for tests
_CONFIG = GooglePlacesConfig(
    api_key="dummy",
    included_types=["bus_station"],
    language="de",
    region="AT",
    radius_m=1000,
    timeout_s=1.0,
    max_retries=0,
    max_result_count=20,
)

class MockSocket:
    def __init__(self, ip: str):
        self._ip = ip

    def getpeername(self) -> tuple[str, int]:
        return (self._ip, 443)

class MockConnection:
    def __init__(self, ip: str):
        self.sock = MockSocket(ip)

class MockRaw:
    def __init__(self, ip: str):
        self.connection = MockConnection(ip)

class InfiniteStreamResponse:
    """A mock response that yields an infinite stream of data."""
    def __init__(self, ip: str = "1.1.1.1"):
        self.status_code = 200
        self.headers: Dict[str, str] = {}
        self.raw = MockRaw(ip)
        self.url = "https://places.googleapis.com/v1/places:searchNearby"

    def iter_content(self, chunk_size: int = 1) -> Iterator[bytes]:
        # Yield 1KB chunks infinitely
        chunk = b" " * 1024
        while True:
            yield chunk

    def json(self) -> Any:
        # If the client calls .json() directly (vulnerable), it will hang or OOM.
        # But we can't easily simulate OOM in unit test without killing the runner.
        # So we raise a special exception to signal "tried to read all".
        raise RuntimeError("VULNERABILITY: Client called .json() on infinite stream!")

    def close(self) -> None:
        pass

    def __enter__(self) -> InfiniteStreamResponse:
        return self

    def __exit__(self, *args: Any) -> None:
        pass

class PrivateIPResponse:
    """A mock response that comes from a private IP."""
    def __init__(self, ip: str = "127.0.0.1"):
        self.status_code = 200
        self.headers: Dict[str, str] = {"Content-Length": "2"}
        self.raw = MockRaw(ip)
        self.url = "https://places.googleapis.com/v1/places:searchNearby"
        self._content = b"{}"

    def iter_content(self, chunk_size: int = 1) -> Iterator[bytes]:
        yield self._content

    def json(self) -> Any:
        return {}

    def close(self) -> None:
        pass

    def __enter__(self) -> PrivateIPResponse:
        return self

    def __exit__(self, *args: Any) -> None:
        pass

class LargeHeaderResponse:
    """A mock response with a Content-Length header exceeding the limit."""
    def __init__(self, size: int):
        self.status_code = 200
        self.headers = {"Content-Length": str(size)}
        self.raw = MockRaw("1.1.1.1")
        self.url = "https://places.googleapis.com/v1/places:searchNearby"

    def iter_content(self, chunk_size: int = 1) -> Iterator[bytes]:
        yield b"{}"

    def json(self) -> Any:
        return {}

    def close(self) -> None:
        pass

    def __enter__(self) -> LargeHeaderResponse:
        return self

    def __exit__(self, *args: Any) -> None:
        pass

def test_client_dos_protection_infinite_stream() -> None:
    """Verify that the client rejects an infinite stream."""
    session = MagicMock(spec=requests.Session)
    session.post.return_value = InfiniteStreamResponse()

    client = GooglePlacesClient(_CONFIG, session=session)

    # This should raise GooglePlacesError (wrapping ValueError) when we fix it.
    # Currently it will raise RuntimeError "VULNERABILITY..." because it calls .json()
    with pytest.raises(GooglePlacesError) as exc_info:
        # We need to trigger a POST
        client._post("endpoint", {})

    assert "Response too large" in str(exc_info.value) or "Content-Length exceeds" in str(exc_info.value)

def test_client_dos_protection_content_length() -> None:
    """Verify that the client rejects a response with excessive Content-Length."""
    session = MagicMock(spec=requests.Session)
    # 20MB content length
    session.post.return_value = LargeHeaderResponse(20 * 1024 * 1024)

    client = GooglePlacesClient(_CONFIG, session=session)

    with pytest.raises(GooglePlacesError) as exc_info:
        client._post("endpoint", {})

    assert "Content-Length exceeds" in str(exc_info.value)

def test_client_ssrf_protection() -> None:
    """Verify that the client rejects connections to private IPs."""
    session = MagicMock(spec=requests.Session)
    session.post.return_value = PrivateIPResponse(ip="127.0.0.1")

    client = GooglePlacesClient(_CONFIG, session=session)

    with pytest.raises(GooglePlacesError) as exc_info:
        client._post("endpoint", {})

    assert "Security: Connected to unsafe IP" in str(exc_info.value)
