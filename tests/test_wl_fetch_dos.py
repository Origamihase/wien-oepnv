
from typing import Any, Iterator

import pytest
from unittest.mock import MagicMock
from src.providers import wl_fetch

def test_fetch_events_response_too_large(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test that fetch_events handles oversized responses gracefully (using fetch_content_safe)."""

    # Create a mock session that returns a huge response
    class HugeResponse:
        def raise_for_status(self) -> None:
            pass

        @property
        def headers(self) -> dict[str, str]:
            return {"Content-Length": str(20 * 1024 * 1024)} # 20MB

        def iter_content(self, chunk_size: int = 8192) -> Iterator[bytes]:
            # Infinite stream of data
            while True:
                yield b" " * chunk_size

    class HugeSession(MagicMock):
        def get(self, url: str, **kwargs: Any) -> HugeResponse:
            return HugeResponse()

    class MockSessionContext:
        headers: dict[str, str] = {}
        def __enter__(self) -> HugeSession:
            return HugeSession()
        def __exit__(self, *args: Any) -> None:
            pass
        def mount(self, *args: Any) -> None: pass

    # We mock fetch_content_safe to raise ValueError, simulating the behavior when size limit is exceeded
    # The actual implementation of fetch_content_safe does this.
    # But here we want to verifying `wl_fetch` catches it.

    def mock_fetch_safe_fail(*args: Any, **kwargs: Any) -> None:
        raise ValueError("Response too large")

    monkeypatch.setattr("src.providers.wl_fetch.fetch_content_safe", mock_fetch_safe_fail)

    # Mock session_with_retries to return a context manager
    monkeypatch.setattr("src.providers.wl_fetch.session_with_retries", lambda *a, **k: MockSessionContext())

    # This should NOT raise an exception, but log a warning and return empty list
    events = wl_fetch.fetch_events()
    assert events == []

    # Check logs
    assert "ungültig oder kein JSON" in caplog.text

def test_wl_fetch_uses_fetch_content_safe(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Verify that wl_fetch calls fetch_content_safe."""

    # Mock fetch_content_safe to track calls
    mock_fetch_safe = MagicMock(return_value=b'{"data": {"trafficInfos": []}}')
    monkeypatch.setattr("src.providers.wl_fetch.fetch_content_safe", mock_fetch_safe)

    # Mock session
    class DummySession:
        headers: dict[str, str] = {}
        def mount(self, *args: Any) -> None: pass
        def get(self, *args: Any, **kwargs: Any) -> MagicMock: return MagicMock()
        def request(self, *args: Any, **kwargs: Any) -> None: pass
        def __enter__(self) -> "DummySession": return self
        def __exit__(self, *args: Any) -> None: pass

    monkeypatch.setattr("src.providers.wl_fetch.session_with_retries", lambda *a, **k: DummySession())

    # This should trigger fetch_content_safe if my changes are applied
    wl_fetch.fetch_events()

    # Check if it was called
    if not mock_fetch_safe.called:
        pytest.fail("fetch_content_safe was not called by wl_fetch")
