
import pytest
from src.utils.http import validate_http_url
import socket

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
        # Fallback to real getaddrinfo for other domains if needed,
        # or just fail if we only expect this one.
        # But for valid domains in other tests we might need real resolution if we ran them in same session.
        # Here we only test this specific case.
        raise socket.gaierror("Name or service not known")

    monkeypatch.setattr(socket, "getaddrinfo", mock_getaddrinfo)

    assert validate_http_url("http://localtest.me") is None

def test_validate_http_url_dns_failure(monkeypatch):
    def mock_getaddrinfo_fail(host, port, proto=0, flags=0):
         raise socket.gaierror("Name or service not known")

    monkeypatch.setattr(socket, "getaddrinfo", mock_getaddrinfo_fail)
    # Should return None if DNS fails
    assert validate_http_url("http://nonexistent.example.com") is None

def test_validate_http_url_ipv6_scope_id():
    # It's hard to test scope ID locally as it depends on interface,
    # but we can mock getaddrinfo to return one.
    pass
