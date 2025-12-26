
import pytest
from src.utils.http import validate_http_url, fetch_content_safe
import socket
import requests

def test_validate_http_url_valid():
    assert validate_http_url("https://example.com") == "https://example.com"
    assert validate_http_url("http://google.com") == "http://google.com"

def test_validate_http_url_invalid_schema():
    assert validate_http_url("ftp://example.com") is None
    assert validate_http_url("javascript:alert(1)") is None

def test_validate_http_url_localhost():
    assert validate_http_url("http://localhost") is None
    assert validate_http_url("http://LOCALHOST") is None

def test_validate_http_url_private_ip_literal():
    assert validate_http_url("http://127.0.0.1") is None
    assert validate_http_url("http://192.168.1.1") is None
    assert validate_http_url("http://10.0.0.1") is None
    assert validate_http_url("http://169.254.1.1") is None # Link-local
    assert validate_http_url("http://[::1]") is None

def test_validate_http_url_domain_resolving_to_localhost(monkeypatch):
    # Mock socket.getaddrinfo to simulate a domain resolving to localhost
    def mock_getaddrinfo(host, port, proto=0, flags=0):
        if host == "localtest.me":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 80))]
        raise socket.gaierror("Name or service not known")

    monkeypatch.setattr(socket, "getaddrinfo", mock_getaddrinfo)

    assert validate_http_url("http://localtest.me") is None

def test_validate_http_url_dns_failure(monkeypatch):
    def mock_getaddrinfo_fail(host, port, proto=0, flags=0):
         raise socket.gaierror("Name or service not known")

    monkeypatch.setattr(socket, "getaddrinfo", mock_getaddrinfo_fail)
    # Should return None if DNS fails
    assert validate_http_url("http://nonexistent.example.com") is None

def test_fetch_content_safe_validates_url(monkeypatch):
    # Ensure fetch_content_safe raises ValueError for unsafe URLs BEFORE making a request

    session = requests.Session()

    # We shouldn't need to mock session.get because it should raise before calling it
    # But just in case, let's mock it to fail
    monkeypatch.setattr(session, "get", lambda *args, **kwargs: pytest.fail("Should not have called get"))

    with pytest.raises(ValueError, match="Unsafe or invalid URL"):
        fetch_content_safe(session, "http://localhost")

    with pytest.raises(ValueError, match="Unsafe or invalid URL"):
        fetch_content_safe(session, "http://127.0.0.1")

    # Mock validation failure for a "valid looking" domain that resolves to private IP
    def mock_getaddrinfo(host, port, proto=0, flags=0):
        if host == "evil.internal":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('192.168.1.5', 80))]
        # For valid domains, we need to return something valid so validate_http_url passes
        if host == "good.example.com":
             return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 80))]
        raise socket.gaierror("Name or service not known")

    monkeypatch.setattr(socket, "getaddrinfo", mock_getaddrinfo)

    with pytest.raises(ValueError, match="Unsafe or invalid URL"):
         fetch_content_safe(session, "http://evil.internal")
