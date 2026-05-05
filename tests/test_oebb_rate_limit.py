import logging
from types import TracebackType
from typing import Any, Iterator
from unittest.mock import MagicMock

import pytest
import requests

import src.providers.oebb as oebb
from tests.mock_utils import get_mock_socket_structure


class DummyResponse:
    def __init__(
        self,
        status_code: int,
        headers: dict[str, str] | None = None,
        content: bytes = b"",
    ) -> None:
        self.status_code = status_code
        self.headers = headers or {}
        self.content = content

        # Mock raw connection for security checks
        self.raw = MagicMock()
        conn = get_mock_socket_structure()
        self.raw.connection = conn
        self.raw._connection = conn

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(response=self)

    def iter_content(self, chunk_size: int = 8192) -> Iterator[bytes]:
        yield self.content

    def __enter__(self) -> "DummyResponse":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        pass


class DummySession:
    def __init__(self, responses: list["DummyResponse"], calls: list[tuple[str, Any]]) -> None:
        self._responses = iter(responses)
        self._calls = calls
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

    def prepare_request(self, request: requests.Request) -> requests.PreparedRequest:
        from requests.models import PreparedRequest
        p = PreparedRequest()
        p.prepare(
            method=request.method,
            url=request.url,
            headers=request.headers,
            files=request.files,
            data=request.data,
            json=request.json,
            params=request.params,
            auth=request.auth,
            cookies=request.cookies,
            hooks=request.hooks,
        )
        return p

    def merge_environment_settings(
        self,
        url: Any,
        proxies: Any,
        stream: Any,
        verify: Any,
        cert: Any,
    ) -> dict[str, Any]:
        return {}

    def get(self, url: str, timeout: Any, stream: bool = False, **kwargs: Any) -> "DummyResponse":
        self._calls.append((url, timeout))
        return next(self._responses)

    def request(
        self,
        method: str,
        url: str,
        timeout: Any = None,
        stream: bool = False,
        **kwargs: Any,
    ) -> "DummyResponse":
        return self.get(url, timeout=timeout, stream=stream, **kwargs)


def test_rate_limit_retries_once_after_wait(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    responses = [
        DummyResponse(429, {"Retry-After": "1.5"}),
        DummyResponse(200, {"Content-Type": "application/xml"}, b"<root></root>"),
    ]

    calls: list[tuple[str, Any]] = []
    monkeypatch.setattr(oebb, "session_with_retries", lambda *a, **kw: DummySession(responses, calls))

    caplog.set_level(logging.WARNING, logger=oebb.log.name)

    # Mock DNS resolution to return a known IP, as request_safe pins HTTP URLs
    from unittest.mock import patch
    with patch("src.utils.http._resolve_hostname_safe") as mock_resolve:
        mock_resolve.return_value = [(2, 1, 6, '', ('1.2.3.4', 80))]

        # Use HTTP to avoid SSL/PinnedAdapter complexity in mock
        result = oebb._fetch_xml("http://example.com", timeout=1)

    assert result is not None
    assert result.tag == "root"

    log_text = caplog.text
    # My implementation logs the exception message
    assert "Rate-Limit" in log_text


def test_rate_limit_raises_http_error_after_retry(monkeypatch: pytest.MonkeyPatch) -> None:

    responses = [
        DummyResponse(429, {"Retry-After": "1.5"}),
        DummyResponse(429, {"Retry-After": "2"}),
    ]

    def mock_raise_for_status(self: Any) -> None:
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(response=self)

    # Patch the dummy class instead of an instance: tests need this method
    # on every DummyResponse the mock session will produce.
    DummyResponse.raise_for_status = mock_raise_for_status  # type: ignore[method-assign]

    calls: list[tuple[str, Any]] = []
    monkeypatch.setattr(oebb, "session_with_retries", lambda *a, **kw: DummySession(responses, calls))

    # Mock DNS resolution to return a known IP
    from unittest.mock import patch
    with patch("src.utils.http._resolve_hostname_safe") as mock_resolve:
        mock_resolve.return_value = [(2, 1, 6, '', ('1.2.3.4', 80))]

        import pytest
        import requests
        with pytest.raises(requests.HTTPError):
            oebb._fetch_xml("http://example.com", timeout=1)

    assert len(calls) == 2
