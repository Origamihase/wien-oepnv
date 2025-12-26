"""HTTP helpers for configuring :mod:`requests` sessions."""

from __future__ import annotations

import ipaddress
import re
import socket
from typing import Any
from urllib.parse import urlparse

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
        next_url = response.headers.get("Location")
        if next_url:
            # Join relative URLs
            full_url = requests.compat.urljoin(response.url, next_url)
            if not validate_http_url(full_url):
                raise ValueError(f"Unsafe redirect to: {full_url}")


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
    session.hooks["response"].append(_check_redirect_security)
    retry = Retry(**options)
    adapter = TimeoutHTTPAdapter(max_retries=retry, timeout=timeout)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update({"User-Agent": user_agent})
    return session


# Block control characters and whitespace in URLs to prevent log injection
_UNSAFE_URL_CHARS = re.compile(r"[\s\x00-\x1f\x7f]")


def validate_http_url(url: str | None) -> str | None:
    """Ensure the given URL is valid and uses http or https.

    Returns the URL (stripped) if valid, or ``None`` if invalid/empty/wrong scheme.
    Also rejects URLs that point to localhost or private IP addresses (SSRF protection),
    or contain unsafe control characters/whitespace.
    """
    if not url:
        return None

    candidate = url.strip()
    if not candidate:
        return None

    # Reject internal whitespace or control characters
    if _UNSAFE_URL_CHARS.search(candidate):
        return None

    try:
        parsed = urlparse(candidate)
        if parsed.scheme.lower() not in ("http", "https"):
            return None

        hostname = parsed.hostname
        if not hostname:
            return None

        # Block localhost
        if hostname.lower() == "localhost":
            return None

        # Resolve hostname to IPs to prevent DNS rebinding/aliasing to private IPs
        try:
            # We use socket.getaddrinfo to get all associated IPs
            addr_info = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)

            for _, _, _, _, sockaddr in addr_info:
                ip_str = sockaddr[0]
                # Handle IPv6 scope ids if present
                ip = ipaddress.ip_address(ip_str.split("%")[0])

                # Check for private, loopback, unspecified, and link-local (169.254.x.x)
                if (
                    ip.is_private
                    or ip.is_loopback
                    or ip.is_unspecified
                    or ip.is_link_local
                ):
                    return None

        except (socket.gaierror, ValueError):
            # DNS resolution failed or invalid IP -> treat as invalid URL
            return None

        return candidate
    except Exception:
        return None


def fetch_content_safe(
    session: requests.Session,
    url: str,
    max_bytes: int = 10 * 1024 * 1024,
    timeout: int | None = None,
    **kwargs: Any,
) -> bytes:
    """Fetch URL content with a size limit to prevent DoS.

    Args:
        session: The requests session to use.
        url: The URL to fetch.
        max_bytes: Maximum allowed response body size in bytes (default: 10MB).
        timeout: Request timeout in seconds.
        **kwargs: Additional arguments passed to session.get().

    Raises:
        ValueError: If URL is unsafe/invalid, or Content-Length/body size exceeds max_bytes.
        requests.RequestException: For network errors.
    """
    if not validate_http_url(url):
        raise ValueError(f"Unsafe or invalid URL: {url}")

    with session.get(url, stream=True, timeout=timeout, **kwargs) as r:
        r.raise_for_status()
        # Check Content-Length header if present
        content_length = r.headers.get("Content-Length")
        if content_length and int(content_length) > max_bytes:
            raise ValueError(f"Content-Length exceeds {max_bytes} bytes")

        content = b""
        for chunk in r.iter_content(chunk_size=8192):
            content += chunk
            if len(content) > max_bytes:
                raise ValueError(f"Response too large (> {max_bytes} bytes)")
        return content
