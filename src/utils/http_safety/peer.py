"""Proxy trust and peer-IP verification of responses.

Moved verbatim from ``src/utils/http.py`` (700-line rule, 2026-11-02);
import from ``src.utils.http``, which re-exports every name.
"""

from __future__ import annotations

import ipaddress
import logging
from urllib.parse import urlparse

import requests
from requests.utils import get_environ_proxies, select_proxy

from ..logging import sanitize_log_arg
from .ip_guard import is_ip_safe
from .redaction import _sanitize_url_for_error

# The logger keeps the name it had in ``src/utils/http.py``.
log = logging.getLogger("src.utils.http")

# Behind a proxy the proxy resolves the hostname itself: for a tunnelled
# HTTPS request neither the pinned IP nor the peer-IP check (DNS-rebinding
# protection) holds, and the peer is the proxy. Requests through a proxy are
# therefore limited to the upstreams the project uses; any other host fails
# closed (audit 2026-09-17, B.3). A literal, safe IP stays allowed: the proxy
# connects to exactly that address (the pinned plain-HTTP path). Production
# workflows set no proxy; this guards developer machines and sandboxes.
# A new upstream must be added here.
PROXY_TRUSTED_HOSTS: frozenset[str] = frozenset({
    "api.github.com",  # src/feed/reporting.py
    "data.oebb.at",  # scripts/update_station_directory.py
    "data.wien.gv.at",  # scripts/update_baustellen_cache.py
    "fahrplan.oebb.at",  # src/providers/oebb.py, src/places/hafas_client.py
    "generativelanguage.googleapis.com",  # scripts/ki_waechter.py (Gemini API)
    "overpass-api.de",  # src/places/osm_client.py
    "origamihase.github.io",  # scripts/health_check.py, veröffentlichte Feeds (DEFAULT_PAGES_BASE_URL)
    "overpass.kumi.systems",  # src/places/osm_client.py
    "raw.githubusercontent.com",  # scripts/sync_hafas_profile.py
    "routenplaner.verkehrsauskunft.at",  # src/providers/vor.py, Stammstrecke monitor
    "www.wienerlinien.at",  # src/providers/wl_fetch.py, scripts/update_wl_stations.py
})


def _require_trusted_proxy_host(url: str) -> None:
    """Raise ``ValueError`` unless *url* may be fetched through a proxy.

    Allowed are the hosts of :data:`PROXY_TRUSTED_HOSTS` and literal, safe IPs.
    """
    host = (urlparse(url).hostname or "").rstrip(".")
    if host in PROXY_TRUSTED_HOSTS or is_ip_safe(host):
        return
    raise ValueError(
        f"Security: {_sanitize_url_for_error(url)} is not a trusted host for "
        "requests through a proxy (DNS Rebinding protection)"
    )


def _proxy_for(url: str) -> str | None:
    """The environment proxy ``requests`` would use for *url*, or ``None``.

    Respects ``NO_PROXY`` and the scheme-specific variables, as ``requests``
    does for a session that trusts the environment.
    """
    proxy = select_proxy(url, get_environ_proxies(url))
    return proxy if isinstance(proxy, str) else None


def verify_response_ip(response: requests.Response) -> None:
    """Verify that the response connection was made to a safe IP (DNS Rebinding protection)."""
    # Guard Clause for Mocks (Task 4)
    # Check if the connection object is a MockConnection (used by responses/mocks)
    # response.connection might be set by adapters, but we usually look at raw._connection

    # Check if it's a mock response (e.g. from 'responses' library)
    # Often mocks don't have a real socket or connection object.
    # We check for common mock signatures.
    try:
        conn = getattr(response.raw, "_connection", getattr(response.raw, "connection", None))
        # Handle MyPy safely: getattr might return None for __class__ if strict, but usually returns class type.
        # We explicitly check the class name.
        if conn:
            cls = getattr(conn, "__class__", None)
            if cls and getattr(cls, "__name__", "") == "MockConnection":
                return
    except Exception as exc:
        # Security (Clear-Text-Logging Drift): mock-skip diagnostic — the
        # underlying adapter's exception text could carry control bytes
        # via a hostile ``__str__``; sanitise once at the boundary.
        log.debug(
            "Validation of mock connection skipped: %s", sanitize_log_arg(str(exc))
        )

    # Behind a proxy the peer is the proxy, not the upstream: the peer-IP
    # check cannot hold, so the host must be trusted instead (fail closed).
    request_url = getattr(getattr(response, "request", None), "url", None)
    if isinstance(request_url, str) and _proxy_for(request_url) is not None:
        _require_trusted_proxy_host(request_url)
        return

    try:
        # r.raw.connection is usually a urllib3.connection.HTTPConnection
        # .sock is the underlying socket
        conn = getattr(response.raw, "_connection", getattr(response.raw, "connection", None))
        sock = getattr(conn, "sock", None)
        if sock:
            peer_info = sock.getpeername()
            peer_ip = peer_info[0]
            if not is_ip_safe(peer_ip):
                raise ValueError(
                    f"Connected to unsafe IP {peer_ip} (DNS Rebinding protection)"
                )
        else:
            # Fallback 1: Was the connection forced via PinnedHTTPSAdapter?
            pinned_ip = getattr(conn, "_pinned_ip", None)
            if pinned_ip:
                if not is_ip_safe(pinned_ip):
                    raise ValueError(f"Security: Pinned IP {pinned_ip} is unsafe (DNS Rebinding protection)")
                return  # IP is safely verified

            # Fallback 2: Was the HTTP URL rewritten to a bare IP?
            parsed_req = urlparse(getattr(response.request, "url", ""))
            if parsed_req.hostname:
                try:
                    ip_candidate = parsed_req.hostname.strip("[]").split("%")[0]
                    ip = ipaddress.ip_address(ip_candidate)
                    if not is_ip_safe(ip):
                        raise ValueError(f"Security: Requested IP {ip} is unsafe (DNS Rebinding protection)")
                    return  # IP is safely verified
                except ValueError:
                    pass

            # If all fallbacks fail, abort securely
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

        # Security (Clear-Text-Logging Drift, src/utils/* round): the
        # bound ``exc`` here is caught from the broad
        # ``(AttributeError, OSError, ValueError)`` tuple so a custom
        # adapter's exception (third-party HTTPS adapter / mocks /
        # downstream socket layer) can carry arbitrary text.  Route it
        # through ``sanitize_log_arg`` before WARNING-level emission so
        # operator-facing logs cannot be log-forged via a crafted
        # exception string.
        log.warning(
            "Security: Could not verify peer IP for %s (Fail Closed): %s",
            url,
            sanitize_log_arg(str(exc)),
        )
        raise ValueError(
            f"Security: Could not verify peer IP for {url} (DNS Rebinding protection)"
        ) from exc
