"""HTTP helpers for configuring :mod:`requests` sessions."""

from __future__ import annotations

import ipaddress
import logging
import re
import socket
from concurrent.futures import ThreadPoolExecutor, TimeoutError
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

# DNS resolution timeout in seconds
DNS_TIMEOUT = 5.0

log = logging.getLogger(__name__)


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


def is_ip_safe(ip_addr: str | ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Check if an IP address is globally reachable and safe."""
    try:
        if isinstance(ip_addr, str):
            # Handle IPv6 scope ids if present
            ip = ipaddress.ip_address(ip_addr.split("%")[0])
        else:
            ip = ip_addr

        # Ensure the IP is globally reachable (excludes private, loopback, link-local, reserved)
        # We also explicitly block multicast, as is_global can be True for multicast in some versions/contexts
        if not ip.is_global or ip.is_multicast:
            return False
        return True
    except ValueError:
        return False


def _resolve_hostname_safe(hostname: str) -> list[tuple[Any, ...]]:
    """Resolve hostname with a timeout to prevent DoS."""
    executor = ThreadPoolExecutor(max_workers=1)
    try:
        future = executor.submit(socket.getaddrinfo, hostname, None, proto=socket.IPPROTO_TCP)
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
    finally:
        # We must not wait for the thread to finish if it's stuck,
        # otherwise we block the main thread.
        executor.shutdown(wait=False)


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
        # This now includes a timeout mechanism
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

        # Prevent DNS Rebinding: Check the actual connected IP
        try:
            # r.raw.connection is usually a urllib3.connection.HTTPConnection
            # .sock is the underlying socket
            conn = getattr(r.raw, "connection", None)
            sock = getattr(conn, "sock", None)
            if sock:
                peer_info = sock.getpeername()
                peer_ip = peer_info[0]
                if not is_ip_safe(peer_ip):
                    raise ValueError(f"Security: Connected to unsafe IP {peer_ip} (DNS Rebinding protection)")
        except (AttributeError, OSError, ValueError) as exc:
            # If we cannot verify the IP (e.g. mocks, strange adapters),
            # we log a debug message but don't crash unless it was a clear validation failure.
            # If is_ip_safe returned False (ValueError raised above), we propagate it.
            if "DNS Rebinding protection" in str(exc):
                raise
            log.debug("Could not verify peer IP for %s: %s", url, exc)

        # Check Content-Length header if present
        content_length = r.headers.get("Content-Length")
        if content_length and int(content_length) > max_bytes:
            raise ValueError(f"Content-Length exceeds {max_bytes} bytes")

        chunks = []
        received = 0
        for chunk in r.iter_content(chunk_size=8192):
            chunks.append(chunk)
            received += len(chunk)
            if received > max_bytes:
                raise ValueError(f"Response too large (> {max_bytes} bytes)")
        return b"".join(chunks)
