"""Circuit-breaker behaviour for the Google Places client.

The Places client drives the shared ``src.utils.circuit_breaker``
primitive through the lower-level ``record_*`` / ``state`` API (rather
than :meth:`CircuitBreaker.call`) so that only 5xx responses count as
failures and each HTTP attempt still debits the monthly quota *before*
it runs. These tests pin the trip point (5 consecutive 5xx) and the OPEN
short-circuit that protects both the upstream and the per-attempt quota
budget.
"""

from __future__ import annotations

from typing import Any
from collections.abc import Iterator
from unittest.mock import MagicMock

import pytest
import requests

from src.places.client import (
    _BREAKER,
    GooglePlacesClient,
    GooglePlacesConfig,
    GooglePlacesError,
)
from src.utils.circuit_breaker import CircuitState

# ``max_retries=5`` admits up to six attempts per ``_post`` call, enough
# for a single call to accumulate five consecutive 5xx and trip the
# breaker mid-loop without spilling into a second ``_post``.
_CONFIG = GooglePlacesConfig(
    api_key="dummy",
    included_types=["bus_station"],
    language="de",
    region="AT",
    radius_m=1000,
    timeout_s=1.0,
    max_retries=5,
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

    def json(self, **kwargs: Any) -> Any:
        import json

        return json.loads(self._content, **kwargs)

    def close(self) -> None:
        pass

    def __enter__(self) -> _MockResponse:
        return self

    def __exit__(self, *args: Any) -> None:
        pass


def test_breaker_trips_after_five_consecutive_5xx(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Five consecutive 5xx responses open the breaker and stop the loop."""
    monkeypatch.setattr("src.places.client.time.sleep", lambda _s: None)
    session = MagicMock(spec=requests.Session)
    session.post.return_value = _MockResponse(503)
    client = GooglePlacesClient(_CONFIG, session=session)

    with pytest.raises(GooglePlacesError, match="Circuit breaker open"):
        client._post("places:searchNearby", {}, quota_kind="nearby")

    # Exactly five requests went out: the fifth 5xx trips the breaker, and
    # the post-attempt guard raises before a sixth request is issued.
    assert session.post.call_count == 5
    assert _BREAKER.state is CircuitState.OPEN


def test_open_breaker_short_circuits_without_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Once OPEN, the next call fails fast at the pre-loop guard — no I/O."""
    monkeypatch.setattr("src.places.client.time.sleep", lambda _s: None)
    session = MagicMock(spec=requests.Session)
    session.post.return_value = _MockResponse(503)
    client = GooglePlacesClient(_CONFIG, session=session)

    with pytest.raises(GooglePlacesError, match="Circuit breaker open"):
        client._post("places:searchNearby", {}, quota_kind="nearby")
    assert session.post.call_count == 5

    # The breaker streak persists per process, so a subsequent call
    # short-circuits at the pre-loop guard: no further request — and
    # therefore no further quota debit — is issued.
    with pytest.raises(GooglePlacesError, match="Circuit breaker open"):
        client._post("places:searchNearby", {}, quota_kind="nearby")
    assert session.post.call_count == 5


def test_success_resets_failure_streak(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 200 between 5xx responses clears the streak, so the breaker holds."""
    monkeypatch.setattr("src.places.client.time.sleep", lambda _s: None)
    session = MagicMock(spec=requests.Session)
    session.post.side_effect = [
        _MockResponse(503),
        _MockResponse(503),
        _MockResponse(503),
        _MockResponse(503),
        _MockResponse(200, b'{"places": []}'),
    ]
    client = GooglePlacesClient(_CONFIG, session=session)

    result = client._post("places:searchNearby", {}, quota_kind="nearby")

    assert result == {"places": []}
    assert _BREAKER.state is CircuitState.CLOSED
    assert session.post.call_count == 5
