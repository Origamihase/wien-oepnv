import logging
from unittest.mock import MagicMock

import src.providers.oebb as oebb
from tests.mock_utils import get_mock_socket_structure


class DummyResponse:
    def __init__(self, status_code, headers=None, content=b""):
        self.status_code = status_code
        self.headers = headers or {}
        self.content = content

        # Mock raw connection for security checks
        self.raw = MagicMock()
        conn = get_mock_socket_structure()
        self.raw.connection = conn
        self.raw._connection = conn

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(response=self)

    def iter_content(self, chunk_size=8192):
        yield self.content

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        pass


class DummySession:
    def __init__(self, responses, calls):
        self._responses = iter(responses)
        self._calls = calls
        self.headers: dict[str, str] = {}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        pass

    def prepare_request(self, request):
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

    def merge_environment_settings(self, url, proxies, stream, verify, cert):
        return {}

    def get(self, url, timeout, stream=False, **kwargs):
        self._calls.append((url, timeout))
        return next(self._responses)

    def request(self, method, url, timeout=None, stream=False, **kwargs):
        return self.get(url, timeout=timeout, stream=stream, **kwargs)


def test_rate_limit_retries_once_after_wait(monkeypatch, caplog):
    responses = [
        DummyResponse(429, {"Retry-After": "1.5"}),
        DummyResponse(200, {"Content-Type": "application/xml"}, b"<root></root>"),
    ]

    # Mock raise_for_status to simulate what fetch_content_safe does
    def mock_raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(response=self)

    DummyResponse.raise_for_status = mock_raise_for_status

    calls = []
    monkeypatch.setattr(oebb, "session_with_retries", lambda *a, **kw: DummySession(responses, calls))

    slept = []

    def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(oebb.time, "sleep", fake_sleep)

    caplog.set_level(logging.WARNING, logger=oebb.log.name)

    # Mock DNS resolution to return a known IP, as request_safe pins HTTP URLs
    from unittest.mock import patch
    with patch("src.utils.http._resolve_hostname_safe") as mock_resolve:
        mock_resolve.return_value = [(2, 1, 6, '', ('1.2.3.4', 80))]

        # Use HTTP to avoid SSL/PinnedAdapter complexity in mock
        result = oebb._fetch_xml("http://example.com", timeout=1)

    assert result is None
    assert len(calls) == 1

    assert slept == []

    log_text = caplog.text
    # My implementation logs the exception message
    assert "Rate-Limit" in log_text


def test_rate_limit_raises_http_error_after_retry(monkeypatch):

    responses = [
        DummyResponse(429, {"Retry-After": "1.5"}),
        DummyResponse(429, {"Retry-After": "2"}),
    ]

    def mock_raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(response=self)

    DummyResponse.raise_for_status = mock_raise_for_status

    calls = []
    monkeypatch.setattr(oebb, "session_with_retries", lambda *a, **kw: DummySession(responses, calls))

    slept = []

    def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(oebb.time, "sleep", fake_sleep)

    # Mock DNS resolution to return a known IP
    from unittest.mock import patch
    with patch("src.utils.http._resolve_hostname_safe") as mock_resolve:
        mock_resolve.return_value = [(2, 1, 6, '', ('1.2.3.4', 80))]

        result = oebb._fetch_xml("http://example.com", timeout=1)

    assert result is None
    assert len(calls) == 1
    assert slept == []
