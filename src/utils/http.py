"""HTTP helpers for configuring :mod:`requests` sessions."""

from __future__ import annotations

import ipaddress
import logging
import os
import re
import socket
import time
import types
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from typing import Any, Container
from urllib.parse import parse_qsl, urlencode, urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

_DEFAULT_RETRY_OPTIONS: dict[str, Any] = {
    "total": 4,
    "backoff_factor": 0.6,
    "status_forcelist": (429, 500, 502, 503, 504),
    "allowed_methods": ("GET",),
}

# Default timeout in seconds if none is provided
DEFAULT_TIMEOUT = 20

# DNS resolution timeout in seconds
DNS_TIMEOUT = 5.0

# Shared executor for DNS resolution to avoid thread exhaustion
_DNS_EXECUTOR = ThreadPoolExecutor(max_workers=8, thread_name_prefix="DNS_Resolver")

log = logging.getLogger(__name__)

def _normalize_key(key: str) -> str:
    """Normalize key for loose matching (lowercase, no hyphens/underscores)."""
    return key.lower().replace("-", "").replace("_", "")


# Keys in query parameters that should be redacted in logs
# We store them normalized (lowercase, no separators) to catch variations
# like x-api-key, x_api_key, X-Api-Key, etc.
_SENSITIVE_QUERY_KEYS = frozenset({
    "accessid",
    "token",
    "key",
    "apikey",
    "password",
    "secret",
    "authorization",
    "auth",
    "clientsecret",
    "clientid",
    "accesstoken",
    "refreshtoken",
    "idtoken",
    "code",
    "sig",
    "signature",
    "session",
    "sid",
    "ticket",
    # Additional common sensitive keys
    "bearertoken",
    "authtoken",
    "jsessionid",
    "phpsessid",
    "asp.netsessionid",
    "cfduid",
    "tenant",
    "tenantid",
    "subscription",
    "subscriptionid",
    "oid",
    "objectid",
    "codechallenge",
    "codeverifier",
    "xapikey",
    "ocpapimsubscriptionkey",
    "subscriptionkey",
    # AWS and other cloud tokens
    "xauthtoken",
    "xamzsecuritytoken",
    "xamzsignature",
    "xamzcredential",
})

# Headers that must be stripped on cross-origin redirects or scheme downgrades
_SENSITIVE_HEADERS = frozenset({
    "Authorization",
    "Proxy-Authorization",
    "X-Goog-Api-Key",
    "X-Api-Key",
    "X-Auth-Token",
    "Private-Token",
    "Cookie",
    "Set-Cookie",
    "Ocp-Apim-Subscription-Key",
    "X-Amz-Security-Token",
    "X-Gitlab-Token",
    "X-Vault-Token",
})


def _sanitize_url_for_error(url: str) -> str:
    """Strip credentials and sensitive query params from URL for safe error logging."""
    try:
        parsed = urlparse(url)

        # 1. Strip basic auth credentials
        if parsed.username or parsed.password:
            # Reconstruct netloc without auth
            netloc = parsed.hostname or ""
            if parsed.port:
                netloc += f":{parsed.port}"
            parsed = parsed._replace(netloc=netloc)

        # 2. Redact sensitive query parameters
        if parsed.query:
            query_params = parse_qsl(parsed.query, keep_blank_values=True)
            new_params = []
            for key, value in query_params:
                if _normalize_key(key) in _SENSITIVE_QUERY_KEYS:
                    new_params.append((key, "***"))
                else:
                    new_params.append((key, value))

            new_query = urlencode(new_params)
            parsed = parsed._replace(query=new_query)

        # 3. Redact sensitive fragment parameters (e.g. OIDC implicit flow)
        if parsed.fragment:
            fragment_params = parse_qsl(parsed.fragment, keep_blank_values=True)
            new_fragment_params = []
            any_sensitive_fragment = False

            for key, value in fragment_params:
                if _normalize_key(key) in _SENSITIVE_QUERY_KEYS:
                    new_fragment_params.append((key, "***"))
                    any_sensitive_fragment = True
                else:
                    new_fragment_params.append((key, value))

            if any_sensitive_fragment:
                new_fragment = urlencode(new_fragment_params)
                parsed = parsed._replace(fragment=new_fragment)

        return parsed.geturl()
    except Exception:
        return "invalid_url"


class TimeoutHTTPAdapter(HTTPAdapter):
    """HTTPAdapter that enforces a default timeout."""

    def __init__(self, *args: Any, timeout: int | None = None, **kwargs: Any) -> None:
        self.timeout = timeout
        super().__init__(*args, **kwargs)

    def send(self, request: requests.PreparedRequest, **kwargs: Any) -> requests.Response:
        if kwargs.get("timeout") is None:
            kwargs["timeout"] = self.timeout
        return super().send(request, **kwargs)


def _check_redirect_security(response: requests.Response, *args: Any, **kwargs: Any) -> None:
    if response.is_redirect:
        # Verify that the intermediate response we just received came from a safe IP
        # This protects against DNS Rebinding attacks during the redirect chain
        verify_response_ip(response)

        next_url = response.headers.get("Location")
        if next_url:
            # Join relative URLs
            full_url = requests.compat.urljoin(response.url, next_url)
            if not validate_http_url(full_url):
                safe_url = _sanitize_url_for_error(full_url)
                raise ValueError(f"Unsafe redirect to: {safe_url}")


def _safe_rebuild_auth(self: requests.Session, prepared_request: requests.PreparedRequest, response: requests.Response) -> None:
    """Override for requests.Session.rebuild_auth to strip sensitive headers on cross-origin redirects."""
    # Call original implementation to handle Authorization header standard logic
    requests.Session.rebuild_auth(self, prepared_request, response)

    headers = prepared_request.headers
    url = prepared_request.url

    if "Location" not in response.headers:
        # Should not happen if rebuild_auth is called correctly by requests on redirect
        return

    original_parsed = urlparse(response.request.url)
    redirect_parsed = urlparse(url)

    # Detect security risks: Hostname change or Scheme Downgrade (HTTPS -> HTTP)
    host_changed = original_parsed.hostname != redirect_parsed.hostname
    scheme_downgraded = original_parsed.scheme == "https" and redirect_parsed.scheme != "https"

    if host_changed or scheme_downgraded:
        for header in _SENSITIVE_HEADERS:
            if header in headers:
                del headers[header]


def session_with_retries(
    user_agent: str, timeout: int = DEFAULT_TIMEOUT, **retry_opts: Any
) -> requests.Session:
    """Return a :class:`requests.Session` pre-configured with retries and a default timeout.

    Args:
        user_agent: User-Agent header that should be sent with every request.
        timeout: Default timeout in seconds for requests (default: 20).
        **retry_opts: Additional keyword arguments forwarded to
            :class:`urllib3.util.retry.Retry`.
    """

    options = {**_DEFAULT_RETRY_OPTIONS, **retry_opts}
    session = requests.Session()

    # Security: Strip sensitive headers on cross-origin redirects
    session.rebuild_auth = types.MethodType(_safe_rebuild_auth, session)  # type: ignore

    # Security: Limit redirects to prevent infinite loops and resource exhaustion (DoS)
    session.max_redirects = 10
    session.hooks["response"].append(_check_redirect_security)
    retry = Retry(**options)
    adapter = TimeoutHTTPAdapter(max_retries=retry, timeout=timeout)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update({
        "User-Agent": user_agent,
    })

    proxies_configured = any(
        k.upper() in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY") for k in os.environ
    )
    if session.trust_env and proxies_configured:
        log.warning(
            "Security: Proxy configuration detected in environment. "
            "DNS Rebinding protection (verify_response_ip) may be bypassed."
        )

    return session


# Block control characters and whitespace in URLs to prevent log injection
_UNSAFE_URL_CHARS = re.compile(r"[\s\x00-\x1f\x7f]")

# Limit URL length to reduce DoS risk from extremely long inputs.
MAX_URL_LENGTH = 2048

# TLDs that are reserved or commonly used for internal networks.
# We block these when DNS checks are skipped to prevent leaking internal names
# or generating invalid links in feeds.
_UNSAFE_TLDS = {
    "local",
    "localhost",
    "test",
    "example",
    "invalid",
    "lan",
    "home",
    "corp",
    "internal",
    "intranet",
    "private",
    "onion",  # Tor Hidden Services
    "i2p",    # Invisible Internet Project
    "arpa",   # Infrastructure TLD
    "kubernetes", # Kubernetes internal DNS
    "localdomain", # Linux/Unix default
    "domain", # Generic internal
    "workgroup", # Windows workgroup
}

# Explicitly block Shared Address Space (RFC 6598) 100.64.0.0/10 which is often used for CGNAT/internal carrier networks.
_SHARED_ADDRESS_SPACE = ipaddress.IPv4Network("100.64.0.0/10")


def is_ip_safe(ip_addr: str | ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Check if an IP address is globally reachable and safe."""
    try:
        if isinstance(ip_addr, str):
            # Handle IPv6 scope ids if present
            ip = ipaddress.ip_address(ip_addr.split("%")[0])
        else:
            ip = ip_addr

        # Block unspecified addresses (0.0.0.0, ::)
        if ip.is_unspecified:
            return False

        # Ensure the IP is globally reachable (excludes private, loopback, link-local, reserved)
        # We also explicitly block multicast, as is_global can be True for multicast in some versions/contexts
        if not ip.is_global or ip.is_multicast:
            return False

        # We explicitly block is_site_local (deprecated fec0::/10) because is_global returns True for them in some python versions.
        # This attribute only exists on IPv6Address.
        if getattr(ip, "is_site_local", False):
            return False

        # Explicitly block Shared Address Space (CGNAT) 100.64.0.0/10
        # is_global behavior varies by python version for this range
        if ip.version == 4 and ip in _SHARED_ADDRESS_SPACE:
            return False

        return True
    except ValueError:
        return False


def _resolve_hostname_safe(hostname: str) -> list[tuple[Any, ...]]:
    """Resolve hostname with a timeout to prevent DoS."""
    try:
        # Reuse the shared executor instead of creating one per call
        future = _DNS_EXECUTOR.submit(socket.getaddrinfo, hostname, None, proto=socket.IPPROTO_TCP)
        return future.result(timeout=DNS_TIMEOUT)
    except TimeoutError:
        log.warning("DNS resolution timed out for %s (DoS protection)", hostname)
        return []
    except (socket.gaierror, ValueError) as exc:
        log.debug("DNS resolution failed for %s: %s", hostname, exc)
        return []
    except Exception as exc:
        log.warning("Unexpected error during DNS resolution for %s: %s", hostname, exc)
        return []
    # We do not shutdown the shared executor


def validate_http_url(
    url: str | None, check_dns: bool = True, allowed_ports: Container[int] = (80, 443)
) -> str | None:
    """Ensure the given URL is valid and uses http or https.

    Returns the URL (stripped) if valid, or ``None`` if invalid/empty/wrong scheme.
    Also rejects URLs that point to localhost or private IP addresses (SSRF protection),
    or contain unsafe control characters/whitespace.

    Now enforces a port whitelist to prevent scanning of non-standard ports.

    Args:
        url: The URL to validate.
        check_dns: If True (default), resolves the hostname to ensure it exists
                   and doesn't point to a private IP (SSRF protection).
                   If False, only syntax and scheme checks are performed.
        allowed_ports: Container of allowed ports (default: 80, 443).
    """
    if not url:
        return None

    candidate = url.strip()
    if not candidate:
        return None

    # Guard against excessively long URLs (DoS protection).
    if len(candidate) > MAX_URL_LENGTH:
        return None

    # Reject internal whitespace or control characters
    if _UNSAFE_URL_CHARS.search(candidate):
        return None

    try:
        parsed = urlparse(candidate)
        if parsed.scheme.lower() not in ("http", "https"):
            return None

        # Disallow embedded credentials to avoid leaking secrets via logs or proxies.
        if parsed.username or parsed.password:
            return None

        hostname = parsed.hostname
        if not hostname:
            return None

        # Validate port
        try:
            port = parsed.port
        except ValueError:
            # Invalid port number (e.g. out of range or non-numeric)
            return None

        if port is None:
            # Default ports are implicit
            if parsed.scheme.lower() == "http":
                port = 80
            elif parsed.scheme.lower() == "https":
                port = 443

        if port not in allowed_ports:
            return None

        # Block localhost (handle trailing dot bypass)
        if hostname.lower().rstrip(".") == "localhost":
            return None

        # Block private IP literals even if DNS check is disabled.
        # This prevents leaking private network structure in generated feeds
        # or bypassing SSRF checks by avoiding DNS resolution steps.
        try:
            # Handle IPv6 brackets and scope IDs if present
            # urlparse.hostname strips brackets for IPv6, so we just handle scope/formatting
            ip_candidate = hostname.strip("[]").split("%")[0]
            ip = ipaddress.ip_address(ip_candidate)
            if not is_ip_safe(ip):
                return None
        except ValueError:
            # Not a standard literal IP address
            lower_host = hostname.lower()
            labels = lower_host.split(".")

            if labels:
                tld = labels[-1]
                # Security Enhancement: Block reserved/internal TLDs unconditionally (SSRF protection)
                if tld in _UNSAFE_TLDS:
                    return None

            # Security Enhancement: If DNS resolution is skipped, we must be stricter.
            # We reject hostnames that look like obfuscated IPs (integer/hex) or invalid TLDs.
            if not check_dns:
                # 1. Check if it looks like a Hex IP (e.g., 0x7f000001)
                if lower_host.startswith("0x") and re.fullmatch(r"0x[0-9a-f]+", lower_host):
                    return None

                # 2. Check TLD validity
                # Valid public TLDs must start with a letter (RFC 1123).
                # This catches:
                # - Integer IPs (2130706433 -> TLD "2130706433" starts with digit)
                # - Dotted Quad IPs (127.0.0.1 -> TLD "1" starts with digit)
                # - Short numeric (127.1 -> TLD "1" starts with digit)
                # - Dotted Hex (0x7f.0x1 -> TLD "0x1" starts with digit)
                if labels:
                    tld = labels[-1]
                    if not tld or not tld[0].isalpha():
                        return None

                    # Security Enhancement: Require FQDN (at least one dot) for non-DNS validated hosts
                    # This filters out local hostnames (e.g. "http://myserver", "http://router")
                    # unless they are standard IPs (caught above) or localhost (caught earlier).
                    if len(labels) < 2:
                        return None

        # Resolve hostname to IPs to prevent DNS rebinding/aliasing to private IPs
        # This now includes a timeout mechanism
        if check_dns:
            addr_info = _resolve_hostname_safe(hostname)

            # If resolution yielded no results (timeout or failure), reject the URL
            if not addr_info:
                return None

            for _, _, _, _, sockaddr in addr_info:
                if not is_ip_safe(sockaddr[0]):
                    return None

        return candidate
    except Exception:
        return None


def verify_response_ip(response: requests.Response) -> None:
    """Verify that the response connection was made to a safe IP (DNS Rebinding protection)."""
    try:
        # r.raw.connection is usually a urllib3.connection.HTTPConnection
        # .sock is the underlying socket
        conn = getattr(response.raw, "connection", None)
        sock = getattr(conn, "sock", None)
        if sock:
            peer_info = sock.getpeername()
            peer_ip = peer_info[0]
            if not is_ip_safe(peer_ip):
                raise ValueError(
                    f"Security: Connected to unsafe IP {peer_ip} (DNS Rebinding protection)"
                )
        else:
            # If we cannot find the socket, we cannot verify the IP.
            # Fail securely.
            raise ValueError(
                f"Security: Could not retrieve socket for {response.url} (DNS Rebinding protection)"
            )

    except (AttributeError, OSError, ValueError) as exc:
        # If we cannot verify the IP (e.g. mocks, strange adapters),
        # we fail securely instead of failing open.
        # If is_ip_safe returned False (ValueError raised above), we propagate it.
        if "DNS Rebinding protection" in str(exc):
            raise

        # Robustly get URL or use fallback
        raw_url = getattr(response, "url", "unknown_url")
        url = _sanitize_url_for_error(raw_url)

        log.warning(
            "Security: Could not verify peer IP for %s (Fail Closed): %s", url, exc
        )
        raise ValueError(
            f"Security: Could not verify peer IP for {url} (DNS Rebinding protection)"
        ) from exc


def read_response_safe(
    response: requests.Response,
    max_bytes: int = 10 * 1024 * 1024,
    timeout: float | None = None,
) -> bytes:
    """Read response content safely, enforcing size limits and timeouts.

    Args:
        response: The requests Response object (must be opened with stream=True).
        max_bytes: Maximum allowed size in bytes.
        timeout: Maximum time in seconds allowed for reading the body.

    Raises:
        ValueError: If Content-Length or actual size exceeds max_bytes.
        requests.Timeout: If the read operation exceeds the timeout.
    """
    # Check Content-Length header if present
    content_length = response.headers.get("Content-Length")
    if content_length:
        try:
            length = int(content_length)
        except ValueError:
            # Ignore malformed Content-Length header; strict check happens in loop
            pass
        else:
            if length > max_bytes:
                raise ValueError(f"Content-Length exceeds {max_bytes} bytes")

    chunks = []
    received = 0
    start_time = time.monotonic()

    for chunk in response.iter_content(chunk_size=8192):
        if timeout is not None and (time.monotonic() - start_time) > timeout:
            raise requests.Timeout(f"Read timed out after {timeout} seconds")

        chunks.append(chunk)
        received += len(chunk)
        if received > max_bytes:
            raise ValueError(f"Response too large (> {max_bytes} bytes)")
    return b"".join(chunks)


def fetch_content_safe(
    session: requests.Session,
    url: str,
    max_bytes: int = 10 * 1024 * 1024,
    timeout: int | None = None,
    allowed_content_types: Container[str] | None = None,
    **kwargs: Any,
) -> bytes:
    """Fetch URL content with a size limit to prevent DoS.

    Args:
        session: The requests session to use.
        url: The URL to fetch.
        max_bytes: Maximum allowed response body size in bytes (default: 10MB).
        timeout: Request timeout in seconds.
        allowed_content_types: Optional list of allowed MIME types (e.g. ["application/json"]).
        **kwargs: Additional arguments passed to session.get().

    Raises:
        ValueError: If URL is unsafe, Content-Type is invalid, or body size exceeds max_bytes.
        requests.RequestException: For network errors.
    """
    safe_url = validate_http_url(url)
    if not safe_url:
        # Security: avoid echoing potentially sensitive URLs (e.g., embedded credentials) in errors.
        sanitized_url = _sanitize_url_for_error(url)
        raise ValueError(f"Unsafe or invalid URL: {sanitized_url}")

    # Security: Enforce default timeout to prevent Slowloris attacks if caller forgets it
    if timeout is None:
        timeout = DEFAULT_TIMEOUT

    start_time = time.monotonic()
    with session.get(safe_url, stream=True, timeout=timeout, **kwargs) as r:
        # Prevent DNS Rebinding: Check the actual connected IP
        # MUST be done before raise_for_status() to prevent leaking info via error codes
        # if the attacker redirects to an internal IP that returns 404/500.
        verify_response_ip(r)

        r.raise_for_status()

        if allowed_content_types is not None:
            content_type_header = r.headers.get("Content-Type", "")
            if not content_type_header:
                raise ValueError("Content-Type header missing, but validation required")
            # Robust parsing: take first part before ';', strip, lower case
            mime_type = content_type_header.split(";")[0].strip().lower()
            if mime_type not in allowed_content_types:
                raise ValueError(
                    f"Invalid Content-Type: {mime_type} (expected {allowed_content_types})"
                )

        # Calculate remaining time for reading body if a total timeout was specified
        read_timeout: float | None = None
        if timeout is not None:
            elapsed = time.monotonic() - start_time
            # Ensure we have at least a small window to read data
            read_timeout = max(0.1, float(timeout) - elapsed)

        return read_response_safe(r, max_bytes, timeout=read_timeout)
