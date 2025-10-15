"""Tests for :mod:`scripts.check_vor_auth`."""

from __future__ import annotations

import base64
import json
from typing import Any, Dict

import pytest
import requests

import scripts.check_vor_auth as module


class DummySession:
    """Minimal context manager mimicking :class:`requests.Session`."""

    def __init__(self, response: requests.Response):
        self._response = response
        self.headers: Dict[str, str] = {}
        self.last_request: Dict[str, Any] | None = None

    def __enter__(self) -> "DummySession":
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None

    def get(self, url: str, *, params: Dict[str, Any], timeout: int) -> requests.Response:
        self.last_request = {
            "url": url,
            "params": params,
            "timeout": timeout,
            "headers": dict(self.headers),
        }
        prepared = requests.Request("GET", url, params=params).prepare()
        self._response.url = prepared.url
        return self._response


def _make_response(status_code: int, body: Dict[str, Any] | None = None) -> requests.Response:
    response = requests.Response()
    response.status_code = status_code
    if body is not None:
        response._content = json.dumps(body).encode("utf-8")
        response.headers["Content-Type"] = "application/json"
    else:
        response._content = b""
    return response


def test_sanitize_url_masks_access_id() -> None:
    url = "https://example.test/endpoint?format=json&accessId=secret&id=123"
    assert module._sanitize_url(url) == "https://example.test/endpoint?format=json&accessId=***&id=123"


def test_check_authentication_success(monkeypatch: pytest.MonkeyPatch) -> None:
    response = _make_response(200, {"stationBoard": []})
    session = DummySession(response)
    monkeypatch.setattr(module.vor, "VOR_ACCESS_ID", "token")
    monkeypatch.setattr(module.vor, "_VOR_ACCESS_TOKEN_RAW", "token")
    monkeypatch.setattr(module.vor, "_VOR_AUTHORIZATION_HEADER", "Bearer token")
    monkeypatch.setattr(module.vor, "refresh_access_credentials", lambda: "token")
    monkeypatch.setattr(module.vor, "VOR_BASE_URL", "https://example.test/")
    monkeypatch.setattr(module.vor, "VOR_RETRY_OPTIONS", {})
    monkeypatch.setattr(module, "session_with_retries", lambda *args, **kwargs: session)

    result = module.check_authentication("123")

    assert result["authenticated"] is True
    assert result["status_code"] == 200
    assert result["error_code"] is None
    assert result["url"].endswith("format=json&id=123&accessId=***")
    assert session.last_request == {
        "url": "https://example.test/departureboard",
        "params": {"format": "json", "id": "123", "accessId": "token"},
        "timeout": module.vor.HTTP_TIMEOUT,
        "headers": {
            "Accept": "application/json",
            "Authorization": "Bearer token",
        },
    }


def test_check_authentication_missing_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module.vor, "refresh_access_credentials", lambda: "")

    result = module.check_authentication("123")

    assert result == {
        "url": None,
        "status_code": None,
        "error_code": "MISSING_CREDENTIALS",
        "error_text": "No VOR access token configured in the environment.",
        "authenticated": False,
        "payload": None,
    }


def test_check_authentication_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    response = _make_response(401, {"errorCode": "API_AUTH", "errorText": "access denied"})
    session = DummySession(response)
    monkeypatch.setattr(module.vor, "VOR_ACCESS_ID", "token")
    monkeypatch.setattr(module.vor, "_VOR_ACCESS_TOKEN_RAW", "token")
    monkeypatch.setattr(module.vor, "_VOR_AUTHORIZATION_HEADER", "Bearer token")
    monkeypatch.setattr(module.vor, "refresh_access_credentials", lambda: "token")
    monkeypatch.setattr(module.vor, "VOR_BASE_URL", "https://example.test/")
    monkeypatch.setattr(module.vor, "VOR_RETRY_OPTIONS", {})
    monkeypatch.setattr(module, "session_with_retries", lambda *args, **kwargs: session)

    result = module.check_authentication("123")

    assert result["authenticated"] is False
    assert result["status_code"] == 401
    assert result["error_code"] == "API_AUTH"
    assert result["error_text"] == "access denied"
    assert result["url"].endswith("format=json&id=123&accessId=***")


def test_check_authentication_uses_basic_header(monkeypatch: pytest.MonkeyPatch) -> None:
    response = _make_response(200, {"stationBoard": []})
    session = DummySession(response)
    expected_header = base64.b64encode(b"user:secret").decode("ascii")
    monkeypatch.setattr(module.vor, "VOR_ACCESS_ID", "user:secret")
    monkeypatch.setattr(module.vor, "_VOR_ACCESS_TOKEN_RAW", "user:secret")
    monkeypatch.setattr(module.vor, "_VOR_AUTHORIZATION_HEADER", f"Basic {expected_header}")
    monkeypatch.setattr(module.vor, "refresh_access_credentials", lambda: "user:secret")
    monkeypatch.setattr(module.vor, "VOR_BASE_URL", "https://example.test/")
    monkeypatch.setattr(module.vor, "VOR_RETRY_OPTIONS", {})
    monkeypatch.setattr(module, "session_with_retries", lambda *args, **kwargs: session)

    result = module.check_authentication("900100")

    assert result["authenticated"] is True
    assert session.last_request is not None
    assert session.last_request["headers"]["Authorization"] == f"Basic {expected_header}"
    assert session.last_request["params"]["accessId"] == "user:secret"


def test_check_authentication_accepts_prefixed_basic(monkeypatch: pytest.MonkeyPatch) -> None:
    response = _make_response(200, {"stationBoard": []})
    session = DummySession(response)
    raw_token = "Basic user:secret"
    encoded = base64.b64encode(b"user:secret").decode("ascii")
    monkeypatch.setattr(module.vor, "VOR_ACCESS_ID", "user:secret")
    monkeypatch.setattr(module.vor, "_VOR_ACCESS_TOKEN_RAW", raw_token)
    monkeypatch.setattr(module.vor, "_VOR_AUTHORIZATION_HEADER", f"Basic {encoded}")
    monkeypatch.setattr(module.vor, "refresh_access_credentials", lambda: "user:secret")
    monkeypatch.setattr(module.vor, "VOR_BASE_URL", "https://example.test/")
    monkeypatch.setattr(module.vor, "VOR_RETRY_OPTIONS", {})
    monkeypatch.setattr(module, "session_with_retries", lambda *args, **kwargs: session)

    result = module.check_authentication("900100")

    assert result["authenticated"] is True
    assert session.last_request is not None
    assert session.last_request["headers"]["Authorization"] == f"Basic {encoded}"
    assert session.last_request["params"]["accessId"] == "user:secret"
