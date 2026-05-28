"""Regression tests for the round-7 http.py bundle.

Pins four defence-in-depth fixes:

1. ``parse_retry_after`` rejects non-finite parsed values (e.g. a
   309-digit numeric header that ``float()`` lifts to ``+inf``).
2. ``read_response_safe`` checks the prospective body size BEFORE
   appending the chunk, so the in-memory buffer is bounded by
   ``max_bytes`` even for callers that pass a small cap.
3. ``cleanup_http_sessions`` drains the eviction queue at atexit,
   not just the active cache.
4. ``_safe_rebuild_auth`` strips sensitive query parameters from the
   redirected URL when crossing an origin (the existing header strip
   covered only the header surface).
"""
from __future__ import annotations

from typing import Any, cast
from unittest.mock import MagicMock

import pytest
import requests

from src.utils.http import (
    _EVICTED_SESSIONS_QUEUE,
    _safe_rebuild_auth,
    cleanup_http_sessions,
    parse_retry_after,
    read_response_safe,
)


# ---------------------------------------------------------------------------
# Fix #1 — parse_retry_after rejects non-finite parsed values
# ---------------------------------------------------------------------------


def test_parse_retry_after_rejects_oversized_numeric_payload() -> None:
    """A 400-digit numeric header parses as ``+inf``; the helper must
    return ``None`` rather than let a caller ``time.sleep(+inf)``."""
    assert parse_retry_after("9" * 400) is None


def test_parse_retry_after_accepts_normal_values() -> None:
    """Regression guard: legitimate numeric values still parse cleanly."""
    assert parse_retry_after("0") == 0.0
    assert parse_retry_after("60") == 60.0
    assert parse_retry_after("3.5") == pytest.approx(3.5)


# ---------------------------------------------------------------------------
# Fix #2 — read_response_safe bounds the in-memory buffer at max_bytes
# ---------------------------------------------------------------------------


class _ResponseStub:
    """Minimal Response-like double for ``read_response_safe``.

    ``iter_content`` returns the chunks we hand it; ``headers`` is a
    plain dict and ``close`` is a no-op stub.
    """

    def __init__(self, chunks: list[bytes], headers: dict[str, str] | None = None) -> None:
        self._chunks = chunks
        self.headers = headers or {}
        self.closed = False

    def iter_content(self, chunk_size: int = 8192) -> Any:
        return iter(self._chunks)

    def close(self) -> None:
        self.closed = True


def test_read_response_safe_caps_buffer_before_overshoot() -> None:
    """Pre-fix the chunk was appended BEFORE the size check, so the
    in-memory buffer could grow to ``max_bytes + chunk_size``.

    With ``max_bytes=10`` and a 5-byte first chunk + an 8 KiB second
    chunk the pre-fix loop would buffer 8197 bytes before raising. The
    post-fix loop refuses the second chunk on the prospective check —
    the buffer never grows past ``max_bytes`` (plus the chunk currently
    inspected, which is not in ``chunks``).
    """
    big_chunk = b"x" * 8192
    response = _ResponseStub([b"hello", big_chunk])
    with pytest.raises(ValueError, match="Response too large"):
        read_response_safe(cast("requests.Response", response), max_bytes=10)
    assert response.closed is True


def test_read_response_safe_accepts_at_the_cap() -> None:
    """Boundary check: ``received == max_bytes`` is allowed."""
    response = _ResponseStub([b"abcdef"])
    data = read_response_safe(cast("requests.Response", response), max_bytes=6)
    assert data == b"abcdef"


# ---------------------------------------------------------------------------
# Fix #3 — cleanup_http_sessions drains _EVICTED_SESSIONS_QUEUE
# ---------------------------------------------------------------------------


def test_cleanup_http_sessions_drains_evicted_queue() -> None:
    """Sessions queued for delayed close must be closed at atexit too.

    Pre-fix ``cleanup_http_sessions`` only walked ``_HTTP_SESSION_CACHE``;
    a burst of evictions queued beyond what the 60 s grace-window worker
    closed before shutdown leaked the underlying sockets.
    """
    fake_session = MagicMock(spec=requests.Session)
    _EVICTED_SESSIONS_QUEUE.put((fake_session, 0.0))
    try:
        cleanup_http_sessions()
        fake_session.close.assert_called_once()
        assert _EVICTED_SESSIONS_QUEUE.empty()
    finally:
        # Defence-in-depth: leave the queue empty for sibling tests.
        while not _EVICTED_SESSIONS_QUEUE.empty():
            _EVICTED_SESSIONS_QUEUE.get_nowait()


# ---------------------------------------------------------------------------
# Fix #4 — _safe_rebuild_auth strips sensitive query params cross-origin
# ---------------------------------------------------------------------------


def _build_prepared_request(url: str) -> requests.PreparedRequest:
    req = requests.Request("GET", url)
    session = requests.Session()
    try:
        return session.prepare_request(req)
    finally:
        session.close()


def test_safe_rebuild_auth_strips_query_secrets_cross_origin() -> None:
    """A cross-origin redirect must not carry ``access_token=…`` in the
    URL of the next request.

    The existing header-strip path covered Authorization / Cookie /
    X-Api-Key; sensitive QUERY parameters were left intact. Callers
    that use the session's native redirect handling (instead of the
    manual loop in ``request_safe``) would otherwise hand the token
    to the attacker-controlled redirect target via the URL line.
    """
    original_request = MagicMock()
    original_request.url = "https://origin.example.com/login"
    prepared = _build_prepared_request(
        "https://attacker.example.com/callback?access_token=SECRET123&id=42"
    )
    redirect_response = MagicMock(spec=requests.Response)
    redirect_response.headers = {"Location": prepared.url}
    redirect_response.request = original_request

    session = requests.Session()
    try:
        _safe_rebuild_auth(session, prepared, redirect_response)
    finally:
        session.close()

    assert prepared.url is not None
    assert "access_token" not in prepared.url
    # The benign query key survives.
    assert "id=42" in prepared.url


def test_safe_rebuild_auth_same_origin_redirect_preserves_url() -> None:
    """Same-origin redirects must NOT have query params stripped — the
    strip-on-cross-origin gate keys exclusively on host/scheme/port
    change."""
    original_request = MagicMock()
    original_request.url = "https://api.example.com/auth"
    prepared = _build_prepared_request(
        "https://api.example.com/auth/callback?access_token=SECRET&id=42"
    )
    redirect_response = MagicMock(spec=requests.Response)
    redirect_response.headers = {"Location": prepared.url}
    redirect_response.request = original_request

    session = requests.Session()
    try:
        _safe_rebuild_auth(session, prepared, redirect_response)
    finally:
        session.close()

    assert prepared.url is not None
    assert "access_token=SECRET" in prepared.url
