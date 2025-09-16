"""HTTP helpers for configuring :mod:`requests` sessions."""

from __future__ import annotations

from typing import Any

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
