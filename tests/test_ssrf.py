from typing import Any
import pytest
from src.utils.http import validate_http_url, fetch_content_safe
import requests

def test_validate_http_url_valid() -> None:
    assert validate_http_url("https://example.com") == "https://example.com"
    assert validate_http_url("http://google.com") == "http://google.com"

def test_validate_http_url_invalid_schema() -> None:
    assert validate_http_url("ftp://example.com") is None
    assert validate_http_url("javascript:alert(1)") is None

def test_validate_http_url_localhost() -> None:
    assert validate_http_url("http://localhost") is None
    assert validate_http_url("http://LOCALHOST") is None

def test_validate_http_url_private_ip_literal() -> None:
    assert validate_http_url("http://127.0.0.1") is None
    assert validate_http_url("http://192.168.1.1") is None
    assert validate_http_url("http://10.0.0.1") is None
    assert validate_http_url("http://169.254.1.1") is None # Link-local
    assert validate_http_url("http://[::1]") is None

def _resolving(monkeypatch: pytest.MonkeyPatch, answers: dict[str, str]) -> None:
    """Let the DNS answer *answers* (host -> IPv4) and nothing for other names.

    The HTTP layer resolves through dnspython, not ``socket.getaddrinfo``:
    the mocks of ``getaddrinfo`` these tests used before were never called,
    and the tests passed on the real DNS (test-suite audit 2026-10-04).
    """
    import dns.resolver

    def _resolve(self: Any, qname: Any, rdtype: Any = "A", *args: Any, **kwargs: Any) -> Any:
        ip = answers.get(str(qname).rstrip("."))
        if ip is None:
            raise dns.resolver.NXDOMAIN
        if str(rdtype).upper().endswith("AAAA"):
            raise dns.resolver.NoAnswer
        return [type("A", (), {"address": ip})()]

    monkeypatch.setattr(dns.resolver.Resolver, "resolve", _resolve)


def test_validate_http_url_domain_resolving_to_localhost(monkeypatch: pytest.MonkeyPatch) -> None:
    # "localtest.me" is on the name blocklist and never reaches the DNS;
    # an ordinary name that resolves to localhost must be rejected too.
    _resolving(monkeypatch, {"intranet.example.org": "127.0.0.1"})

    assert validate_http_url("http://localtest.me") is None
    assert validate_http_url("http://intranet.example.org") is None

def test_validate_http_url_dns_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    _resolving(monkeypatch, {})
    # Should return None if DNS fails
    assert validate_http_url("http://nonexistent.example.com") is None

def test_fetch_content_safe_validates_url(monkeypatch: pytest.MonkeyPatch) -> None:
    # Ensure fetch_content_safe raises ValueError for unsafe URLs BEFORE making a request

    session = requests.Session()

    # We shouldn't need to mock session.get because it should raise before calling it
    # But just in case, let's mock it to fail
    def _fail_get(*args: object, **kwargs: object) -> object:
        pytest.fail("Should not have called get")

    monkeypatch.setattr(session, "get", _fail_get)

    with pytest.raises(ValueError, match="Unsafe or invalid URL"):
        fetch_content_safe(session, "http://localhost")

    with pytest.raises(ValueError, match="Unsafe or invalid URL"):
        fetch_content_safe(session, "http://127.0.0.1")

    # A "valid looking" domain that resolves to a private IP.
    _resolving(monkeypatch, {"evil.example.net": "192.168.1.5", "good.example.com": "93.184.216.34"})
    assert validate_http_url("http://good.example.com") == "http://good.example.com"

    with pytest.raises(ValueError, match="No safe IP resolved"):
         fetch_content_safe(session, "http://evil.example.net")
