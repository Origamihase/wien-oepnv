
import logging
import pytest
from unittest.mock import MagicMock
from src.providers import wl_fetch

def test_fetch_events_response_too_large(monkeypatch, caplog):
    """Test that fetch_events handles oversized responses gracefully (using fetch_content_safe)."""

    # Create a mock session that returns a huge response
    class HugeResponse:
        def raise_for_status(self):
            pass

        @property
        def headers(self):
            return {"Content-Length": str(20 * 1024 * 1024)} # 20MB

        def iter_content(self, chunk_size=8192):
            # Infinite stream of data
            while True:
                yield b" " * chunk_size

    class HugeSession(MagicMock):
        def get(self, url, **kwargs):
            return HugeResponse()

    class MockSessionContext:
        headers = {}
        def __enter__(self):
            return HugeSession()
        def __exit__(self, *args):
            pass
        def mount(self, *args): pass

    # We mock fetch_content_safe to raise ValueError, simulating the behavior when size limit is exceeded
    # The actual implementation of fetch_content_safe does this.
    # But here we want to verifying `wl_fetch` catches it.

    def mock_fetch_safe_fail(*args, **kwargs):
        raise ValueError("Response too large")

    monkeypatch.setattr("src.providers.wl_fetch.fetch_content_safe", mock_fetch_safe_fail)

    # Mock session_with_retries to return a context manager
    monkeypatch.setattr("src.providers.wl_fetch.session_with_retries", lambda *a, **k: MockSessionContext())

    # This should NOT raise an exception, but log a warning and return empty list
    events = wl_fetch.fetch_events()
    assert events == []

    # Check logs
    assert "zu groß oder ungültig" in caplog.text

def test_wl_fetch_uses_fetch_content_safe(monkeypatch, caplog):
    """Verify that wl_fetch calls fetch_content_safe."""

    # Mock fetch_content_safe to track calls
    mock_fetch_safe = MagicMock(return_value=b'{"data": {"trafficInfos": []}}')
    monkeypatch.setattr("src.providers.wl_fetch.fetch_content_safe", mock_fetch_safe)

    # Mock session
    class DummySession:
        headers = {}
        def mount(self, *args): pass
        def get(self, *args, **kwargs): return MagicMock()
        def request(self, *args, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass

    monkeypatch.setattr("src.providers.wl_fetch.session_with_retries", lambda *a, **k: DummySession())

    # This should trigger fetch_content_safe if my changes are applied
    wl_fetch.fetch_events()

    # Check if it was called
    if not mock_fetch_safe.called:
        pytest.fail("fetch_content_safe was not called by wl_fetch")
