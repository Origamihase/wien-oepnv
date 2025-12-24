"""HTTP helpers for configuring :mod:`requests` sessions."""

from __future__ import annotations

import ipaddress
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


def session_with_retries(user_agent: str, **retry_opts: Any) -> requests.Session:
    """Return a :class:`requests.Session` pre-configured with retries.

    Args:
        user_agent: User-Agent header that should be sent with every request.
        **retry_opts: Additional keyword arguments forwarded to
            :class:`urllib3.util.retry.Retry`.
    """

    options = {**_DEFAULT_RETRY_OPTIONS, **retry_opts}
    session = requests.Session()
    retry = Retry(**options)
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update({"User-Agent": user_agent})
    return session


def validate_http_url(url: str | None) -> str | None:
    """Ensure the given URL is valid and uses http or https.

    Returns the URL (stripped) if valid, or ``None`` if invalid/empty/wrong scheme.
    Also rejects URLs that point to localhost or private IP addresses (SSRF protection).
    """
    if not url:
        return None

    candidate = url.strip()
    if not candidate:
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

        # Block private IPs
        try:
            ip = ipaddress.ip_address(hostname)
            # Check for private, loopback, unspecified, and link-local (169.254.x.x)
            if (ip.is_private or
                ip.is_loopback or
                ip.is_unspecified or
                ip.is_link_local):
                return None
        except ValueError:
            # Not an IP literal, proceed
            pass

        return candidate
    except Exception:
        return None
