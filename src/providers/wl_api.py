"""Wiener Linien API: base URL, HTTP session headers and the JSON call.

Moved verbatim from ``src/providers/wl_fetch.py`` (700-line rule,
2026-11-02). The logger keeps the name ``src.providers.wl_fetch``.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any
from urllib.parse import urlparse

import requests

from ..utils.files import loads_finite
from ..utils.http import session_with_retries, validate_http_url, fetch_content_safe
from ..utils.logging import sanitize_log_arg


# Basis-URL aus Secret/ENV, Fallback: OGD-Endpoint
_WL_DEFAULT_BASE = "https://www.wienerlinien.at/ogd_realtime"

# Security: only accept env overrides that point at the official Wiener Linien
# OGD host. Without this, ``WL_RSS_URL=https://evil.com`` would (a) inject
# attacker-controlled JSON into the cached feed items and (b) put
# ``https://evil.com`` into every WL item's ``<link>`` element, weaponising the
# public RSS feed as a phishing redirector. ``validate_http_url()`` only
# checks SSRF/DNS-rebinding properties, not host identity.
#
# 2026-05-10 (HTTPS-only Provider URL Drift): the validator additionally
# pins the scheme to ``https``. ``validate_http_url`` accepts both ``http``
# and ``https``; without this pin, an env override such as
# ``WL_RSS_URL=http://www.wienerlinien.at/ogd_realtime`` would be accepted,
# every ``_fetch_traffic_infos`` / ``_fetch_news`` call would issue a
# plaintext request, and an MITM could substitute arbitrary alerts that
# flow verbatim into the public ``docs/feed.xml`` artefact. Mirrors the
# canonical ``validate_public_feed_url`` HTTPS-only pin
# (``src/utils/http.py``) and the VOR / OEBB sibling validators.
_WL_TRUSTED_HOSTS = frozenset({"www.wienerlinien.at"})


def _validated_wl_base(raw: str) -> str | None:
    safe = validate_http_url(raw)
    if not safe:
        return None
    parsed = urlparse(safe)
    # Security: refuse plaintext HTTP — see header above.
    if parsed.scheme.lower() != "https":
        return None
    host = (parsed.hostname or "").lower()
    if host not in _WL_TRUSTED_HOSTS:
        return None
    return safe


_WL_BASE_ENV = os.getenv("WL_RSS_URL", "").strip()
_WL_BASE_OVERRIDE = _validated_wl_base(_WL_BASE_ENV) if _WL_BASE_ENV else None
if _WL_BASE_ENV and _WL_BASE_OVERRIDE is None:
    logging.getLogger("src.providers.wl_fetch").warning(
        "WL_RSS_URL %r ist kein bekannter Wiener-Linien-Host; verwende Standard.",
        _WL_BASE_ENV,
    )
WL_BASE = (_WL_BASE_OVERRIDE or _WL_DEFAULT_BASE).rstrip("/")

# Item ``<link>`` for every WL notice: the operator's public disruption page.
# ``WL_BASE`` is the API root, which answers 404 to a browser (and 403 with a
# trailing slash), so a reader clicking a WL item landed on an error page
# (live check 17.10.2026). The OGD feed has no per-notice page; the
# Betriebsinfo page lists the current notices. The GUID stays as it was, so
# first_seen, dedupe and the TV rotation do not change.
WL_INFO_URL = "https://www.wienerlinien.at/betriebsinfo"


log = logging.getLogger("src.providers.wl_fetch")

# ---------------- HTTP-Session mit Retry ----------------

WL_USER_AGENT = "Origamihase-wien-oepnv/3.1 (+https://github.com/Origamihase/wien-oepnv)"
WL_SESSION_HEADERS = {"Accept": "application/json"}


# ---------------- API Calls ----------------

def _get_json(
    path: str,
    params: list[tuple[Any, ...]] | None = None,
    timeout: int = 20,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    url = f"{WL_BASE.rstrip('/')}/{path.lstrip('/')}"

    def _fetch(s: requests.Session) -> dict[str, Any]:
        max_retries = 3
        for attempt in range(1, max_retries + 1):
            try:
                content = fetch_content_safe(
                    s,
                    url,
                    params=params or None,
                    timeout=timeout,
                    allowed_content_types=("application/json",),
                )
                # Security: ``loads_finite`` pins parse_constant +
                # parse_float hooks that reject NaN / Infinity / -Infinity
                # literals AND scientific-notation overflow (``1e1000`` →
                # +inf). Closes the network-tainted sibling of the
                # writer-pin family (Round 1485 / 1487 / 1488) and the
                # on-disk reader-pin family (Round 1503): a compromised
                # Wiener Linien upstream / MITM / DNS-hijack serving
                # crafted JSON would otherwise propagate ``float('nan')``
                # / ``float('inf')`` into the live-events dedup pipeline.
                data = loads_finite(content)
                if not isinstance(data, dict):
                    log.warning(
                        "Unerwartetes Payload-Format von %s: %s erwartet, aber %s erhalten (Zero Trust)",
                        sanitize_log_arg(url),
                        "dict",
                        type(data).__name__,
                    )
                    return {}
                return data
            except (ValueError, json.JSONDecodeError, RecursionError) as exc:
                # Resilience: include ``RecursionError`` so a malicious
                # upstream serving deeply-nested JSON cannot crash the
                # build process. ``json.loads`` on a deeply-nested array
                # / object exceeds Python's recursion limit and raises
                # ``RecursionError`` (NOT a subclass of ``JSONDecodeError``).
                log.warning(
                    "Antwort von %s ungültig oder kein JSON: %s",
                    sanitize_log_arg(url),
                    sanitize_log_arg(exc),
                )
                return {}
            except requests.RequestException as exc:
                if attempt < max_retries:
                    log.warning(
                        "Sporadischer Netzwerkfehler bei %s (Versuch %d/%d): %s",
                        sanitize_log_arg(url),
                        attempt,
                        max_retries,
                        sanitize_log_arg(exc),
                    )
                    time.sleep(2)
                    continue
                else:
                    log.error(
                        "Verbindung zu %s schlug nach %d Versuchen endgültig fehl: %s",
                        sanitize_log_arg(url),
                        max_retries,
                        sanitize_log_arg(exc),
                    )
                    return {}
        return {}

    if session is not None:
        return _fetch(session)

    with session_with_retries(WL_USER_AGENT, raise_on_status=False) as s:
        s.headers.update(WL_SESSION_HEADERS)
        return _fetch(s)
