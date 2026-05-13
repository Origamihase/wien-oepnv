import logging
from types import TracebackType
from typing import Any

import pytest
import requests
import src.providers.oebb as oebb

class DummySession:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {}
    def __enter__(self) -> "DummySession":
        return self
    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        pass

    def close(self) -> None:
        pass


def test_oebb_retry_after_capped(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Verify that OEBB provider caps the Retry-After delay."""

    def fake_fetch_safe(session: Any, url: str, **kwargs: Any) -> None:
        resp = requests.Response()
        resp.status_code = 429
        resp.headers["Retry-After"] = "99999"
        raise requests.HTTPError(response=resp)

    # We need to patch fetch_content_safe in oebb module scope
    monkeypatch.setattr(oebb, "fetch_content_safe", fake_fetch_safe)
    monkeypatch.setattr(oebb, "session_with_retries", lambda *a, **kw: DummySession())

    caplog.set_level(logging.WARNING, logger=oebb.log.name)

    result = oebb.fetch_events()

    assert len(result) == 0
    assert any("Fail-Fast" in message for message in caplog.messages)
