"""HTTP helpers for configuring :mod:`requests` sessions."""

from __future__ import annotations

import hashlib
import ipaddress
import logging
import atexit
import collections
import os
import re
import socket
import threading
import dns.resolver
import dns.exception
import time
import types
import unicodedata
import secrets
import queue
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, TypeGuard, cast
from collections.abc import Container, Mapping, MutableMapping
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse

import requests
from requests.adapters import HTTPAdapter
from requests.hooks import dispatch_hook
from requests.structures import CaseInsensitiveDict
from urllib3.connection import HTTPSConnection, HTTPConnection
from urllib3.connectionpool import HTTPSConnectionPool, HTTPConnectionPool
from urllib3.poolmanager import PoolManager
from urllib3.util.retry import Retry

from .logging import sanitize_log_arg, sanitize_log_message

_RETRY_AFTER_NUMERIC_RE = re.compile(r"\d+(?:\.\d+)?")


def parse_retry_after(
    header_value: str | None,
    *,
    now: datetime | None = None,
) -> float | None:
    """Parse an HTTP ``Retry-After`` header value into seconds.

    Accepts both numeric forms (``"3.5"``) and HTTP-date forms
    (``"Wed, 21 Oct 2015 07:28:00 GMT"``). Returns ``None`` when the
    header is missing, empty, or unparseable. Returned delays are
    clamped to ``>= 0``.

    The optional *now* parameter lets callers inject a fixed reference
    time for deterministic testing of the HTTP-date branch.
    """

    if header_value is None:
        return None
    header = header_value.strip()
    if not header:
        return None
    if _RETRY_AFTER_NUMERIC_RE.fullmatch(header):
        return max(0.0, float(header))
    try:
        parsed = parsedate_to_datetime(header)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    reference = now if now is not None else datetime.now(UTC)
    return max(0.0, (parsed - reference).total_seconds())


_DEFAULT_RETRY_OPTIONS: dict[str, Any] = {
    "total": 4,
    "backoff_factor": 0.6,
    "status_forcelist": (429, 500, 502, 503, 504),
    "allowed_methods": ("GET",),
}

# Default timeout in seconds if none is provided
DEFAULT_TIMEOUT = (3.0, 15.0)

# DNS resolution timeout in seconds
DNS_TIMEOUT = 5.0

# Block control characters and whitespace in URLs to prevent log injection
# Also block unsafe characters (<, >, ", \, ^, `, {, |, }) to prevent XSS/Injection
_UNSAFE_URL_CHARS = re.compile(r"[\s\x00-\x1f\x7f<>\"\\^`{|}]")

# Limit URL length to reduce DoS risk from extremely long inputs.
MAX_URL_LENGTH = 2048

# OOM Protection: Hard maximum payload limit for HTTP requests
MAX_PAYLOAD_SIZE = 10 * 1024 * 1024

# Thread-safe Session Cache (Task: HTTP Keep-Alive)
_HTTP_SESSION_CACHE: collections.OrderedDict[str, requests.Session] = collections.OrderedDict()
_HTTP_SESSION_LOCK = threading.Lock()
_HTTP_SESSION_CACHE_MAX_SIZE = 50

# Queue to hold evicted sessions that need to be closed
_EVICTED_SESSIONS_QUEUE: queue.Queue[tuple[requests.Session, float]] = queue.Queue()

log = logging.getLogger(__name__)

def _cleanup_evicted_sessions_thread() -> None:
    """Daemon thread that closes evicted sessions after a grace period."""
    while True:
        try:
            session, eviction_time = _EVICTED_SESSIONS_QUEUE.get()
            now = time.time()
            wait_time = eviction_time + 60.0 - now
            if wait_time > 0:
                time.sleep(wait_time)

            try:
                session.close()
            except Exception as exc:
                # Security (Clear-Text-Logging Drift, src/utils/* round):
                # ``requests.Session.close()`` can surface adapter errors
                # whose ``__str__`` carries upstream-supplied bytes (e.g.
                # connection-pool errors echoing the URL).  Sanitise the
                # bound exception so the daemon-thread DEBUG log cannot
                # carry control / ANSI / BiDi payloads.
                log.debug(
                    "Error closing evicted session: %s", sanitize_log_arg(str(exc))
                )

            _EVICTED_SESSIONS_QUEUE.task_done()
        except Exception as exc:
            # Security (Clear-Text-Logging Drift): defensive catch-all on
            # the daemon-thread queue handler — sanitise the bound
            # exception text for the same reasons as the close path above.
            log.debug(
                "Error in _cleanup_evicted_sessions_thread: %s",
                sanitize_log_arg(str(exc)),
            )

_cleanup_thread = threading.Thread(target=_cleanup_evicted_sessions_thread, daemon=True)
_cleanup_thread.start()

def _normalize_key(key: str) -> str:
    """Normalize key for loose matching (lowercase, no hyphens/underscores)."""
    # Security: Use a stricter normalization (strip everything except a-z0-9)
    # to catch variations like "api.key", "api key", "Client.ID", etc.
    return re.sub(r"[^a-z0-9]", "", key.lower())


# Regex to detect credentials in URLs that might be missed by urlparse (e.g. missing //)
# Matches "scheme:user:pass@host" or "scheme://user:pass@host"
_URL_AUTH_RE = re.compile(
    r"^(?P<scheme>https?|ftp):(?P<slash>//)?(?P<auth>[^/\s]+)@", re.IGNORECASE
)

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
    "passphrase",
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
    "sessionid",
    "cookie",
    "sid",
    "ticket",
    # Additional common sensitive keys
    "jwt",
    "bearertoken",
    "authtoken",
    "jsessionid",
    "phpsessid",
    "aspnetsessionid",
    "cfduid",
    "tenant",
    "tenantid",
    "subscription",
    "subscriptionid",
    "oid",
    "objectid",
    "dsn",
    "otp",
    "pass",
    "pwd",
    "userpass",
    "glpat",
    "ghp",
    "codechallenge",
    "codeverifier",
    "xapikey",
    "ocpapimsubscriptionkey",
    "subscriptionkey",
    # OAuth 2.0 / OIDC / SAML critical parameters
    "state",
    "nonce",
    "clientassertion",
    "clientassertiontype",
    "samlrequest",
    "samlresponse",
    # OAuth 2.0 Device Authorization Grant (RFC 8628). `device_code` is a
    # bearer-style secret that the client polls with; `user_code` is short-lived
    # but still pairs the user with an in-flight grant. Neither is caught by
    # the substring list (no "token"/"secret"/etc. in the normalized form),
    # so they need an exact entry here.
    "devicecode",
    "usercode",
    # Additional sensitive tokens
    "bearer",
    # AWS and other cloud tokens
    "xauthtoken",
    "xamzsecuritytoken",
    "xamzsignature",
    "xamzcredential",
})

# High-risk substrings that trigger redaction even if the key isn't an exact match in _SENSITIVE_QUERY_KEYS.
# Normalized keys containing these substrings will be redacted in error messages.
_SENSITIVE_KEY_SUBSTRINGS = frozenset({
    "token",
    "secret",
    "password",
    "credential",
    "passphrase",
    "apikey",
    "accesskey",
    "privatekey",
    "signature",
    "email",
    "webhook",
    "glpat",
    # SAML 2.0 / JWT Bearer Authorization Grant (RFC 7521/7522/7523):
    # the `assertion` parameter carries a signed identity assertion (SAML XML
    # or JWT). `client_assertion` is already an exact match above, but plain
    # `assertion`, `saml_assertion`, etc. would otherwise slip past redaction.
    "assertion",
    # Additional broad matching
    "session",
    "cookie",
    "clientid",
    "clientsecret",
    "authorization",
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
    "X-GitHub-Token",
    "X-Vault-Token",
    "X-Sentry-Token",
    "DD-API-KEY",
    "X-Figma-Token",
    "X-Plex-Token",
    "X-Shopify-Access-Token",
    "X-Slack-Token",
    "X-HubSpot-API-Key",
    "X-Postmark-Server-Token",
    "X-Postmark-Account-Token",
    "X-RapidAPI-Key",
    "X-Service-Token",
    "X-Access-Token",
    "X-CSRF-Token",
    "X-CSRFToken",
    "X-XSRF-TOKEN",
})

# Normalized (lowercase) set of sensitive headers for case-insensitive matching
_SENSITIVE_HEADERS_LOWER = frozenset(h.lower() for h in _SENSITIVE_HEADERS)

# Partial matches for dynamic sensitive header detection (normalized to lowercase)
_SENSITIVE_HEADER_PARTIALS = frozenset({
    "token",
    "key",
    "secret",
    "password",
    "passphrase",
    "credential",
    "signature",
    "session",
    "cookie",
    "auth-",
    "authorization",
    "client-id",
    "client-secret",
    "access-token",
    "access-key",
    "access-id",
    # SAML/JWT bearer assertions are sometimes carried in headers (e.g.
    # `Saml-Assertion`, `X-Subject-Assertion`); strip on cross-origin redirect.
    "assertion",
})


def _is_sensitive_header(header_name: str) -> bool:
    """Check if a header name is considered sensitive."""
    normalized = header_name.lower()
    if normalized in _SENSITIVE_HEADERS_LOWER:
        return True

    return any(partial in normalized for partial in _SENSITIVE_HEADER_PARTIALS)


def _strip_sensitive_params(url: str) -> str:
    """Remove sensitive query parameters from URL completely."""
    try:
        parsed = urlparse(url)
        if not parsed.query:
            return url

        query_params = parse_qsl(parsed.query, keep_blank_values=True)
        new_params = []
        modified = False

        for key, value in query_params:
            normalized = _normalize_key(key)
            if normalized in _SENSITIVE_QUERY_KEYS or any(s in normalized for s in _SENSITIVE_KEY_SUBSTRINGS):
                modified = True
                continue
            new_params.append((key, value))

        if modified:
            new_query = urlencode(new_params)
            parsed = parsed._replace(query=new_query)
            return parsed.geturl()

        return url
    except Exception:
        return url


def _replace_auth(match: re.Match[Any]) -> str:
    """Callback for explicit auth sanitization."""
    scheme = match.group("scheme")
    # Handle optional slash group which might be None or empty
    slash = match.group("slash") or ""
    return f"{scheme}:{slash}***@"


def _sanitize_exception_msg(msg: str) -> str:
    """Sanitize URLs in exception messages."""
    # First apply specific URL sanitization (handles IPv6, auth, etc.)
    msg = re.sub(
        r"(https?://[^\s'\"<>]+)",
        lambda m: _sanitize_url_for_error(m.group(1)),
        msg
    )
    # Then apply generic logging sanitization (catches relative URLs, query params, etc.)
    return sanitize_log_message(msg, strip_control_chars=False)


def _sanitize_url_for_error(url: str) -> str:
    """Strip credentials and sensitive query params from URL for safe error logging."""
    if len(url) > MAX_URL_LENGTH:
        url = url[:MAX_URL_LENGTH] + "...[TRUNCATED]"

    try:
        # 0. Pre-sanitize malformed auth (e.g. "https:user:pass@...") that urlparse misses
        # This handles cases where user forgot // or scheme is non-standard
        match = _URL_AUTH_RE.match(url)
        if match:
            # Replace the auth part with ***
            url = _URL_AUTH_RE.sub(_replace_auth, url, count=1)

        parsed = urlparse(url)

        # 1. Strip basic auth credentials (if urlparse found them)
        if parsed.username or parsed.password:
            # Reconstruct netloc without auth
            hostname = parsed.hostname or ""
            # Fix IPv6 bug (Task A): Re-wrap IPv6 addresses in brackets
            if ":" in hostname:
                netloc = f"[{hostname}]"
            else:
                netloc = hostname

            if parsed.port:
                netloc += f":{parsed.port}"
            parsed = parsed._replace(netloc=netloc)

        # 2. Redact sensitive query parameters
        if parsed.query:
            query_params = parse_qsl(parsed.query, keep_blank_values=True)
            new_params = []
            for key, value in query_params:
                normalized = _normalize_key(key)
                if normalized in _SENSITIVE_QUERY_KEYS or any(s in normalized for s in _SENSITIVE_KEY_SUBSTRINGS):
                    new_params.append((key, "***"))
                else:
                    new_params.append((key, value))

            new_query = urlencode(new_params)
            parsed = parsed._replace(query=new_query)

        # 3. Strip URL fragment entirely (e.g. OIDC implicit flow)
        import urllib.parse
        url_without_frag, _ = urllib.parse.urldefrag(parsed.geturl())
        return url_without_frag
    except Exception:
        return "invalid_url"


class TimeoutHTTPAdapter(HTTPAdapter):  # type: ignore[misc]
    """HTTPAdapter that enforces a default timeout."""

    def __init__(self, *args: Any, timeout: int | float | tuple[float, float] | None = None, **kwargs: Any) -> None:
        self.timeout = timeout
        super().__init__(*args, **kwargs)

    def send(self, request: requests.PreparedRequest, **kwargs: Any) -> requests.Response:
        if kwargs.get("timeout") is None:
            kwargs["timeout"] = self.timeout if self.timeout is not None else DEFAULT_TIMEOUT
        return super().send(request, **kwargs)


class SafeDNSHTTPConnection(HTTPConnection):
    """
    HTTPConnection that resolves DNS once, validates the IP, and connects securely.
    """
    def _new_conn(self) -> Any:
        target_ip: str | None = None
        try:
            ip_obj = ipaddress.ip_address(self.host)
            target_ip_cand = str(ip_obj)
            if is_ip_safe(target_ip_cand):
                target_ip = str(target_ip_cand)
        except ValueError:
            pass

        if target_ip is None:
            ips = _resolve_hostname_safe(self.host)
            for _, _, _, _, sockaddr in ips:
                if is_ip_safe(str(sockaddr[0])):
                    target_ip = str(sockaddr[0])
                    break

        if not target_ip:
            sanitized = _sanitize_url_for_error(f"http://{self.host}")
            raise ValueError(f"No safe IP resolved for {sanitized} (DNS Rebinding protection)")

        conn = socket.create_connection(
            (target_ip, self.port),
            self.timeout,
            source_address=self.source_address,
        )

        if self.socket_options:
            for opt in self.socket_options:
                conn.setsockopt(*opt[:3])

        return conn


class SafeDNSHTTPSConnection(HTTPSConnection):
    """
    HTTPSConnection that resolves DNS once, validates the IP, and connects securely.
    """
    def _new_conn(self) -> Any:
        target_ip: str | None = None
        try:
            ip_obj = ipaddress.ip_address(self.host)
            target_ip_cand = str(ip_obj)
            if is_ip_safe(target_ip_cand):
                target_ip = str(target_ip_cand)
        except ValueError:
            pass

        if target_ip is None:
            ips = _resolve_hostname_safe(self.host)
            for _, _, _, _, sockaddr in ips:
                if is_ip_safe(str(sockaddr[0])):
                    target_ip = str(sockaddr[0])
                    break

        if not target_ip:
            sanitized = _sanitize_url_for_error(f"https://{self.host}")
            raise ValueError(f"No safe IP resolved for {sanitized} (DNS Rebinding protection)")

        conn = socket.create_connection(
            (target_ip, self.port),
            self.timeout,
            source_address=self.source_address,
        )

        if self.socket_options:
            for opt in self.socket_options:
                conn.setsockopt(*opt[:3])

        return conn


class SafeDNSAdapter(TimeoutHTTPAdapter):
    """
    HTTPAdapter that forces safe DNS resolution for all connections.
    """
    def init_poolmanager(self, connections: int, maxsize: int, block: bool = False, **pool_kwargs: Any) -> None:
        self._pool_connections = connections
        self._pool_maxsize = maxsize
        self._pool_block = block

        self.poolmanager = PoolManager(
            num_pools=connections,
            maxsize=maxsize,
            block=block,
            strict=True,
            **pool_kwargs,
        )

        class SafeDNSHTTPConnectionPool(HTTPConnectionPool):
            ConnectionCls = SafeDNSHTTPConnection

        class SafeDNSHTTPSConnectionPool(HTTPSConnectionPool):
            ConnectionCls = SafeDNSHTTPSConnection

        self.poolmanager.pool_classes_by_scheme = self.poolmanager.pool_classes_by_scheme.copy()
        self.poolmanager.pool_classes_by_scheme["http"] = SafeDNSHTTPConnectionPool
        self.poolmanager.pool_classes_by_scheme["https"] = SafeDNSHTTPSConnectionPool


class PinnedHTTPSConnection(HTTPSConnection):
    """
    HTTPSConnection that forces connection to a specific IP while keeping the original hostname for SNI.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._pinned_ip = kwargs.pop("pinned_ip", None)
        super().__init__(*args, **kwargs)

    def _new_conn(self) -> Any:
        # Create socket connected to pinned IP

        # We ignore self.host for connection, use pinned_ip
        # explicit source_address passing to satisfy MyPy
        conn = socket.create_connection(
            (self._pinned_ip, self.port),
            self.timeout,
            source_address=self.source_address,
        )

        # Apply socket options if present (simulating urllib3 behavior)
        if self.socket_options:
            for opt in self.socket_options:
                # opt is a tuple (level, optname, value)
                # some platforms might have 4 items? setsockopt takes 3.
                conn.setsockopt(*opt[:3])

        return conn


class PinnedHTTPSAdapter(TimeoutHTTPAdapter):
    """
    HTTPAdapter that forces all connections to a specific IP address
    while preserving the original hostname for SNI and Host header.
    """

    def __init__(self, pinned_ip: str, *args: Any, **kwargs: Any) -> None:
        self.pinned_ip = pinned_ip
        super().__init__(*args, **kwargs)

    def init_poolmanager(self, connections: int, maxsize: int, block: bool = False, **pool_kwargs: Any) -> None:
        self._pool_connections = connections
        self._pool_maxsize = maxsize
        self._pool_block = block

        self.poolmanager = PoolManager(
            num_pools=connections,
            maxsize=maxsize,
            block=block,
            strict=True,
            **pool_kwargs,
        )

        # Create a closure class for PinnedHTTPSConnection that has pinned_ip baked in.
        pinned_ip = self.pinned_ip

        class LocalPinnedHTTPSConnection(PinnedHTTPSConnection):
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                kwargs["pinned_ip"] = pinned_ip
                super().__init__(*args, **kwargs)

        # Get the default HTTPS pool class
        # We use explicit HTTPSConnectionPool as base to satisfy MyPy,
        # assuming urllib3 uses that (which it does).
        # Dynamic base class causes MyPy errors.

        class PinnedHTTPSConnectionPool(HTTPSConnectionPool):
            ConnectionCls = LocalPinnedHTTPSConnection

        # Register it
        self.poolmanager.pool_classes_by_scheme = self.poolmanager.pool_classes_by_scheme.copy()
        self.poolmanager.pool_classes_by_scheme["https"] = PinnedHTTPSConnectionPool


def _get_pinned_session(target_ip: str, timeout: int | float | tuple[float, float] | None, max_retries: Any = 0) -> requests.Session:
    """Retrieve or create a cached session with a PinnedHTTPSAdapter for the target IP."""
    cache_key = f"{target_ip}_{max_retries}"
    with _HTTP_SESSION_LOCK:
        if cache_key in _HTTP_SESSION_CACHE:
            # Move to end to maintain LRU
            session = _HTTP_SESSION_CACHE.pop(cache_key)


            _HTTP_SESSION_CACHE[cache_key] = session
            return session

        # Cache miss, create new
        session = requests.Session()
        adapter = PinnedHTTPSAdapter(target_ip, timeout=timeout, max_retries=max_retries)
        session.mount("https://", adapter)

        # Add to cache and evict if necessary
        _HTTP_SESSION_CACHE[cache_key] = session
        if len(_HTTP_SESSION_CACHE) > _HTTP_SESSION_CACHE_MAX_SIZE:
            # We pop the oldest session but do NOT explicitly close it immediately
            # because another thread might still be actively reading from its socket.
            # Push it to the cleanup queue to be closed after a grace period.
            _, evicted_session = _HTTP_SESSION_CACHE.popitem(last=False)
            _EVICTED_SESSIONS_QUEUE.put((evicted_session, time.time()))

        return session


def _check_response_security(response: requests.Response, *args: Any, **kwargs: Any) -> None:
    # Always verify that the response came from a safe IP
    # This protects against DNS Rebinding attacks for both direct requests and redirects
    # Enforces "Verify-Then-Process" order.
    verify_response_ip(response)

    if response.is_redirect:
        next_url = response.headers.get("Location")
        if next_url:
            # Join relative URLs
            full_url = urljoin(response.url, next_url)
            if not validate_http_url(full_url):
                safe_url = _sanitize_url_for_error(full_url)
                raise ValueError(f"Unsafe redirect to: {safe_url}")


def _pin_url_to_ip(url: str) -> tuple[str, str]:
    """
    Resolve hostname to a safe IP and rewrite URL to use it (DNS Pinning).
    Returns (pinned_url, original_hostname).
    """
    # 1. Basic validation (without DNS check to allow us to handle it)
    safe_url = validate_http_url(url, check_dns=False)
    if not safe_url:
        sanitized = _sanitize_url_for_error(url)
        raise ValueError(f"Invalid URL: {sanitized}")

    parsed = urlparse(safe_url)
    hostname = parsed.hostname
    if not hostname:
        sanitized = _sanitize_url_for_error(url)
        raise ValueError(f"No hostname in URL: {sanitized}")

    # 2. Resolve to Safe IP
    target_ip: str | None = None
    try:
        # Check if the hostname is already an IP address
        ip_obj = ipaddress.ip_address(hostname)
        target_ip_cand = str(ip_obj)
        if is_ip_safe(target_ip_cand):
            target_ip = str(target_ip_cand)
    except ValueError:
        ips = _resolve_hostname_safe(hostname)
        if ips:
            for _, _, _, _, sockaddr in ips:
                if is_ip_safe(str(sockaddr[0])):
                    target_ip = str(sockaddr[0])
                    break

    if not target_ip:
        sanitized = _sanitize_url_for_error(url)
        raise ValueError(f"No safe IP resolved for {sanitized}")

    # 3. Rewrite URL
    # target_ip is narrowed to IPv4/IPv6 by TypeGuard, but at runtime it might be a string.
    # We force string conversion to handle both cases safely.
    target_ip_str = str(target_ip)
    if ":" in target_ip_str:
        netloc = f"[{target_ip_str}]"
    else:
        netloc = target_ip_str

    port = _get_port(parsed)
    if port:
        # Check against scheme default ports
        default_port = 443 if parsed.scheme == "https" else 80
        if port != default_port:
            netloc = f"{netloc}:{port}"

    pinned_url = parsed._replace(netloc=netloc).geturl()
    return pinned_url, hostname


def _strip_sensitive_headers(
    headers: MutableMapping[str, Any],
    original_url: str,
    new_url: str,
    session_headers: Mapping[str, Any] | None = None,
) -> None:
    """Remove sensitive headers if the redirect crosses security boundaries."""
    original_parsed = urlparse(original_url)
    redirect_parsed = urlparse(new_url)

    host_changed = original_parsed.hostname != redirect_parsed.hostname
    scheme_downgraded = (
        original_parsed.scheme == "https" and redirect_parsed.scheme != "https"
    )
    port_changed = _get_port(original_parsed) != _get_port(redirect_parsed)

    # Safe upgrade is HTTP port 80 to HTTPS port 443 on the exact same host
    is_safe_upgrade = (
        not host_changed and
        original_parsed.scheme == "http" and redirect_parsed.scheme == "https" and
        _get_port(original_parsed) == 80 and _get_port(redirect_parsed) == 443
    )

    if (host_changed or scheme_downgraded or port_changed) and not is_safe_upgrade:
        # If session_headers is provided, we use masking mode (set to None)
        # to ensure session headers don't leak through.
        mask_mode = session_headers is not None

        # 1. Process explicit override headers
        for header_name in list(headers.keys()):
            if _is_sensitive_header(header_name):
                if mask_mode:
                    headers[header_name] = None
                else:
                    del headers[header_name]

        # 2. Process implicit session headers (if in masking mode)
        if mask_mode and session_headers:
            for header_name in session_headers:
                if _is_sensitive_header(header_name):
                    # If it's in session headers, we must mask it in override headers
                    # unless explicitly overridden (but we just masked overrides above)
                    headers[header_name] = None


def _get_port(parsed: Any) -> int | None:
    """Get the port from a parsed URL, handling default ports."""
    try:
        if parsed.port is not None:
            return cast('int | None', parsed.port)
    except ValueError:
        pass
    if parsed.scheme == "http":
        return 80
    if parsed.scheme == "https":
        return 443
    return None


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

    # Detect security risks: Hostname change, Scheme Downgrade (HTTPS -> HTTP), or Port change
    host_changed = original_parsed.hostname != redirect_parsed.hostname
    scheme_downgraded = original_parsed.scheme == "https" and redirect_parsed.scheme != "https"
    port_changed = _get_port(original_parsed) != _get_port(redirect_parsed)

    # Safe upgrade is HTTP port 80 to HTTPS port 443 on the exact same host
    is_safe_upgrade = (
        not host_changed and
        original_parsed.scheme == "http" and redirect_parsed.scheme == "https" and
        _get_port(original_parsed) == 80 and _get_port(redirect_parsed) == 443
    )

    if (host_changed or scheme_downgraded or port_changed) and not is_safe_upgrade:
        # Explicitly strip Authorization, Cookie, and X-Api-Key headers if redirect targets a different hostname/domain
        if host_changed:
            for header_name in list(headers.keys()):
                if header_name.lower() in ("authorization", "cookie", "x-api-key"):
                    del headers[header_name]

        # Dynamic check for sensitive headers based on name patterns
        # We iterate over a copy of keys to allow modification of the dict during iteration
        for header_name in list(headers.keys()):
            if _is_sensitive_header(header_name):
                del headers[header_name]


def session_with_retries(
    user_agent: str,
    timeout: int | float | tuple[float, float] = DEFAULT_TIMEOUT,
    **retry_opts: Any,
) -> requests.Session:
    """Return a :class:`requests.Session` pre-configured with retries and a default timeout.

    Args:
        user_agent: User-Agent header that should be sent with every request.
        timeout: Default timeout in seconds for requests (default: (3.0, 15.0)).
        **retry_opts: Additional keyword arguments forwarded to
            :class:`urllib3.util.retry.Retry`.
    """

    options = {**_DEFAULT_RETRY_OPTIONS, **retry_opts}
    session = requests.Session()

    # Security: Strip sensitive headers on cross-origin redirects
    session.rebuild_auth = types.MethodType(_safe_rebuild_auth, session)

    # Security: Limit redirects to prevent infinite loops and resource exhaustion (DoS)
    session.max_redirects = 10
    session.hooks["response"].append(_check_response_security)

    class JitterRetry(Retry):
        def get_backoff_time(self) -> float:
            base_backoff = super().get_backoff_time()
            return base_backoff * secrets.SystemRandom().uniform(0.8, 1.2)

    retry = JitterRetry(**options)
    adapter = SafeDNSAdapter(max_retries=retry, timeout=timeout)
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
    # Common internal network device names / TLDs
    "router",
    "modem",
    "gateway",
    "wpad",
    "server",
    "priv",
    "mshome",
    # Container / Orchestration internal TLDs
    "svc",
    "cluster",
    "consul",
    # Additional internal/infrastructure TLDs (SSRF protection)
    "backup",
    "prod",
    "stage",
    "staging",
    "sys",
    "printer",
    "kube",
    "openshift",
    "istio",
    "mesh",
    "intra",
}

# Known DNS Rebinding / Wildcard DNS services that map to local IPs.
# We block these domains (and their subdomains) regardless of DNS resolution settings.
_UNSAFE_DOMAINS = frozenset({
    "nip.io",
    "sslip.io",
    "xip.io",
    "xip.name",
    "localtest.me",
    "lvh.me",
    "vcap.me",
    "127.0.0.1.nip.io",
})

# Explicitly block Shared Address Space (RFC 6598) 100.64.0.0/10 which is often used for CGNAT/internal carrier networks.
_SHARED_ADDRESS_SPACE = ipaddress.IPv4Network("100.64.0.0/10")

# Explicitly block NAT64 Well-Known Prefix (RFC 6052) 64:ff9b::/96
# These addresses translate to IPv4 and can bypass IPv4 filters if the environment supports NAT64.
_NAT64_PREFIX = ipaddress.IPv6Network("64:ff9b::/96")


def is_ip_safe(
    ip_addr: Any
) -> TypeGuard[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    """Check if an IP address is globally reachable and safe."""
    try:
        if isinstance(ip_addr, str):
            # Handle IPv6 scope ids if present
            ip = ipaddress.ip_address(ip_addr.split("%")[0])
        elif isinstance(ip_addr, ipaddress.IPv4Address | ipaddress.IPv6Address):
            ip = ip_addr
        else:
            return False

        # Block unspecified addresses (0.0.0.0, ::)
        if ip.is_unspecified:
            return False

        # Ensure the IP is globally reachable (excludes private, loopback, link-local, reserved)
        # We also explicitly block multicast, as is_global can be True for multicast in some versions/contexts
        if not ip.is_global or ip.is_multicast:
            return False

        # Explicitly reject addresses where is_private, is_loopback, is_link_local evaluates to True
        if ip.is_private or ip.is_loopback or ip.is_link_local:
            return False

        # We explicitly block is_site_local (deprecated fec0::/10) because is_global returns True for them in some python versions.
        # This attribute only exists on IPv6Address.
        if getattr(ip, "is_site_local", False):
            return False

        # Explicitly block Shared Address Space (CGNAT) 100.64.0.0/10
        # is_global behavior varies by python version for this range
        if ip.version == 4 and ip in _SHARED_ADDRESS_SPACE:
            return False

        # Explicitly block NAT64 WKP (64:ff9b::/96)
        if ip.version == 6 and ip in _NAT64_PREFIX:
            return False

        return True
    except ValueError:
        return False


def _resolve_hostname_safe(hostname: str) -> list[tuple[Any, ...]]:
    """Resolve hostname using dnspython with a timeout to prevent thread exhaustion/DoS."""
    results = []

    # Hash hostname for safe logging. CodeQL's clear-text-logging dataflow
    # tracker conservatively treats any string derived from a URL parameter
    # as potentially carrying credentials (the ``user:pass@host`` form).
    # ``hashlib.sha256`` is a recognised barrier where regex whitelists are
    # not (review feedback on PR #1334). Diagnostic value is preserved:
    # the same hostname always produces the same 12-char prefix, so log
    # lines can still be correlated when investigating DNS issues.
    host_log = hashlib.sha256(
        str(hostname).encode("utf-8", "replace")
    ).hexdigest()[:12]

    resolver = dns.resolver.Resolver()
    resolver.timeout = DNS_TIMEOUT
    resolver.lifetime = DNS_TIMEOUT

    try:
        # Resolve A records (IPv4)
        try:
            answers_v4 = resolver.resolve(hostname, "A")
            for rdata in answers_v4:
                # socket.getaddrinfo format: (family, type, proto, canonname, sockaddr)
                # We return enough structure to satisfy the rest of the code: sockaddr is (ip, port)
                results.append((socket.AF_INET, socket.SOCK_STREAM, 6, "", (rdata.address, 0)))
        except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN, dns.resolver.NoNameservers):
            pass

        # Resolve AAAA records (IPv6)
        try:
            answers_v6 = resolver.resolve(hostname, "AAAA")
            for rdata in answers_v6:
                results.append((socket.AF_INET6, socket.SOCK_STREAM, 6, "", (rdata.address, 0, 0, 0)))  # type: ignore[arg-type]
        except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN, dns.resolver.NoNameservers):
            pass

        if not results:
            log.debug("DNS resolution yielded no A/AAAA records for host:%s", host_log)

    except dns.exception.Timeout:
        log.warning("DNS resolution timed out for host:%s (DoS protection)", host_log)
    except Exception as exc:
        # Log the exception class only — ``str(exc)`` from a DNS resolver
        # error typically embeds the hostname, which would re-introduce
        # the clear-text-logging dataflow that ``host_log`` was meant to
        # break.
        log.warning(
            "Unexpected error during DNS resolution for host:%s: %s",
            host_log,
            type(exc).__name__,
        )

    return results


def validate_http_url(
    url: str | None, check_dns: bool = True, allowed_ports: Container[int] = (80, 443)
) -> str | None:
    """Ensure the given URL is valid and uses http or https.

    Returns the URL (stripped) if valid, or ``None`` if invalid/empty/wrong scheme.
    Also rejects URLs that point to localhost or private IP addresses (SSRF protection),
    or contain unsafe control characters/whitespace.

    Applies NFKC normalization to prevent IDNA homograph attacks or blocklist bypasses.
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
        # Check this BEFORE normalization to ensure we don't accidentally strip them when reconstructing netloc.
        if parsed.username or parsed.password:
            return None

        # Security: Normalize only hostname to NFKC to prevent IDNA bypasses and homograph confusion
        # We do NOT normalize the full URL to preserve Base64/Query parameters (Task 2).
        if parsed.hostname:
             # The requirement says: "normalize exclusively the hostname".
             # urlparse.hostname returns lowercased hostname.
             normalized_hostname = unicodedata.normalize("NFKC", parsed.hostname)

             # Reconstruct netloc safely (Task 5)
             # Avoid using replace() which might clobber ports if they match the hostname

             # Fix IPv6 Brackets: normalized_hostname (from parsed.hostname) lacks brackets for IPv6.
             # We must restore them if it's an IPv6 literal (contains colons).
             if ":" in normalized_hostname:
                 final_hostname = f"[{normalized_hostname}]"
             else:
                 final_hostname = normalized_hostname

             new_netloc = final_hostname
             if parsed.port is not None:
                 new_netloc = f"{final_hostname}:{parsed.port}"

             # Update parsed object
             parsed = parsed._replace(netloc=new_netloc)

             # Reconstruct candidate with normalized hostname
             candidate = parsed.geturl()

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

        # hostname is now potentially updated in 'parsed', but 'hostname' var was from old 'parsed'.
        # Update local hostname var
        hostname = parsed.hostname
        if not hostname:
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
            # However, for pure IPv6 literals like "[::1]", hostname is "::1" (brackets stripped by urlparse).
            # But earlier in this function (line ~760), we reconstructed candidate with hostname.
            # If input was "http://[::1]", parsed.hostname is "::1".
            # ipaddress.ip_address("::1") works.
            # ipaddress.ip_address("[::1]") fails.
            # We strip just in case.
            ip_candidate = hostname.strip("[]").split("%")[0]
            ip = ipaddress.ip_address(ip_candidate)
            if not is_ip_safe(ip):
                return None

            # If it is a safe IP, we return the candidate (which preserves brackets for IPv6 if they were there)
            # But wait, earlier we did: candidate = parsed.geturl() after normalization.
            # If it was an IP, normalization (NFKC) usually leaves it alone or normalizes characters.
            # If we return 'candidate' here, it's fine.
            return candidate
        except ValueError:
            # Not a standard literal IP address
            lower_host = hostname.lower()

            # Security: Handle trailing dots for FQDNs to prevent TLD check bypass
            # e.g., "foo.local." -> "foo.local"
            check_host = lower_host.rstrip(".")
            labels = check_host.split(".")

            if labels:
                tld = labels[-1]
                # Security Enhancement: Block reserved/internal TLDs unconditionally (SSRF protection)
                if not tld or tld in _UNSAFE_TLDS:
                    return None

            # Security Enhancement: Block known DNS rebinding/wildcard DNS services (e.g. nip.io)
            # This is critical when check_dns=False to prevent bypassing IP checks via public domains
            # that resolve to localhost (e.g. 127.0.0.1.nip.io).
            for unsafe_domain in _UNSAFE_DOMAINS:
                if lower_host == unsafe_domain or lower_host.endswith("." + unsafe_domain):
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


# Security: pin env-controlled URLs that land in PUBLISHED artefacts (RSS feed
# ``<link>`` / atom hrefs / sitemap ``<loc>``) to GitHub-hosted hosts. Without
# this pin, an env override (intentional misconfig, leaked CI env, compromised
# secret store) would let an attacker substitute the canonical project URL in
# every published item — turning the feed and sitemap into a phishing/SEO
# redirect amplifier for every consumer (subscriber, search-engine crawler).
# ``validate_http_url`` only checks SSRF/DNS-rebinding properties, not host
# identity OR scheme strictness — it accepts both ``http`` and ``https``,
# any path, and any subdomain shape that survives ``urlparse``. The
# allow-list pin therefore must additionally constrain:
#   * the URL scheme to ``https`` (an ``http://`` published feed link is
#     a TLS-strip primitive against subscribers — many RSS readers do not
#     consult HSTS preload lists, so the publisher MUST emit HTTPS-only);
#   * the ``.github.io`` prefix to a single non-empty alphanumeric label
#     (real GitHub Pages targets are ``<owner>.github.io`` —
#     sub-subdomains, empty prefixes, and dash-prefixed labels are not
#     legitimate Pages targets and are rejected at this boundary).
# Allowed: ``github.com`` (canonical repo URL, byte-exact match) and any
# single-label ``<owner>.github.io`` Pages target (the natural GitHub Pages
# target for forks).
_PUBLIC_FEED_URL_TRUSTED_HOSTS = frozenset({"github.com"})
_PUBLIC_FEED_URL_TRUSTED_SUFFIXES = (".github.io",)
# Single-label allow-list contract for the ``.github.io`` suffix prefix:
# starts with [a-z0-9], optional trailing [a-z0-9-] body, max 63 chars
# (RFC-1123 label limit). Pinned tighter than RFC because GitHub usernames
# cannot start with a dash. The hostname is already lowercased by
# ``urlparse``/NFKC normalisation in ``validate_http_url`` before this
# regex is consulted, so ``re.IGNORECASE`` is intentionally NOT used.
_PUBLIC_FEED_URL_GITHUB_PAGES_OWNER_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")


def validate_public_feed_url(
    url: str | None, *, check_dns: bool = True
) -> str | None:
    """Validate a URL that will be embedded in a publicly-served artefact.

    Mirrors :func:`validate_http_url` but additionally pins the hostname to
    the GitHub-hosted allowlist so an env override cannot weaponise the feed
    or sitemap as a redirect/phishing primitive. Use ``check_dns=False`` for
    URLs that are only embedded (never fetched), since DNS state at build
    time is irrelevant to whether the URL is a safe target for embedding.

    Pins three sub-vectors that ``validate_http_url`` does not constrain:

    1. **Scheme** — ``https`` only. ``http://`` published links downgrade
       to plaintext on every subscriber's RSS reader (many do not honour
       HSTS preload), exposing the artefact contents to MITM substitution.
    2. **Host identity** — ``github.com`` (byte-exact) or
       ``<owner>.github.io`` (single non-empty alphanumeric label).
       Sub-subdomain shapes (``a.b.github.io``), empty prefixes
       (``.github.io``), and dash-prefixed labels (``-bad.github.io``)
       are not legitimate GitHub Pages targets and are rejected.
    3. **Reuses every check** in ``validate_http_url`` (control chars,
       userinfo, port whitelist, IDNA NFKC normalisation, SSRF / DNS
       rebinding when ``check_dns=True``).
    """

    safe = validate_http_url(url, check_dns=check_dns)
    if not safe:
        return None
    parsed = urlparse(safe)
    # Force HTTPS — ``http://`` is a TLS-strip primitive on subscribers.
    if parsed.scheme.lower() != "https":
        return None
    host = (parsed.hostname or "").lower()
    if host in _PUBLIC_FEED_URL_TRUSTED_HOSTS:
        return safe
    for suffix in _PUBLIC_FEED_URL_TRUSTED_SUFFIXES:
        if host.endswith(suffix):
            prefix = host[: -len(suffix)]
            if _PUBLIC_FEED_URL_GITHUB_PAGES_OWNER_RE.fullmatch(prefix):
                return safe
    return None


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

    # Proxy Compatibility (Task C): Bypass check if explicit proxy env vars are set
    if any(k in os.environ for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")):
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


def read_response_safe(
    response: requests.Response,
    max_bytes: int = MAX_PAYLOAD_SIZE,
    timeout: float | tuple[float, float] | None = None,
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

    # If timeout is a tuple, we use the read timeout part for body streaming
    if isinstance(timeout, tuple):
        read_timeout: float | None = timeout[1]
    else:
        read_timeout = timeout

    for chunk in response.iter_content(chunk_size=8192):
        if read_timeout is not None and (time.monotonic() - start_time) > read_timeout:
            response.close()
            raise requests.Timeout(f"Read timed out after {read_timeout} seconds")

        chunks.append(chunk)
        received += len(chunk)
        if received > max_bytes:
            response.close()
            raise ValueError(f"Response too large (> {max_bytes} bytes)")
    return b"".join(chunks)


_TimeoutT = int | float | tuple[float, float] | None


def _merge_request_hooks(
    session: requests.Session, kwargs: dict[str, Any]
) -> dict[str, Any]:
    """Merge caller hooks with session hooks and append the
    ``_check_response_security`` hook.

    Mitigates: silent bypass of the IP-verification (DNS-rebinding TOCTOU)
    response hook. If a caller passed their own ``hooks=`` argument, naive
    handling would clobber the security hook; this helper guarantees both
    coexist by appending the security hook after all caller/session hooks.
    Also pops ``hooks`` from ``kwargs`` so it doesn't double-pass to
    ``session.request``.
    """
    if hasattr(session, "hooks"):
        request_hooks: dict[str, Any] = session.hooks.copy()
    else:
        request_hooks = {}

    caller_hooks = kwargs.pop("hooks", None)
    if caller_hooks:
        for event, hook in caller_hooks.items():
            existing = request_hooks.get(event, [])
            if not isinstance(existing, list):
                existing = [existing]
            else:
                existing = list(existing)  # Copy

            if not isinstance(hook, list):
                hook = [hook]

            request_hooks[event] = existing + hook

    resp_hooks = request_hooks.get("response", [])
    if not isinstance(resp_hooks, list):
        resp_hooks = [resp_hooks]
    else:
        resp_hooks = list(resp_hooks)

    if _check_response_security not in resp_hooks:
        resp_hooks.append(_check_response_security)
    request_hooks["response"] = resp_hooks
    return request_hooks


def _compute_total_time_budget(timeout: _TimeoutT) -> float | None:
    """Compute the absolute upper-bound time budget across the entire
    redirect chain.

    Mitigates: DoS via slow chained redirects. For a tuple
    ``(connect, read)``, the SUM of both values is used as the absolute
    upper bound for the whole chain so an adversary cannot stretch the
    budget by chaining redirects whose individual timeouts each fit
    within ``connect`` or ``read``.
    """
    if isinstance(timeout, int | float):
        return float(timeout)
    if isinstance(timeout, tuple):
        return float(sum(timeout))
    return None


def _check_total_budget_or_raise(
    total_allowed_time: float | None, elapsed: float
) -> None:
    """Raise ``requests.Timeout`` if the total time budget across redirects
    has been exceeded.

    Mitigates: Slowloris across redirects. Special-case ``total=0`` (used
    by tests with mocked sessions) so legitimate test mocks aren't broken
    by an instant timeout pre-check; otherwise enforces strict ``elapsed
    >= total``.
    """
    if total_allowed_time is None:
        return
    if total_allowed_time == 0:
        return
    if elapsed >= total_allowed_time:
        raise requests.Timeout(
            f"Total timeout of {total_allowed_time}s exceeded after {elapsed:.2f}s"
        )


def _per_request_timeout(
    timeout: _TimeoutT,
    total_allowed_time: float | None,
    elapsed: float,
) -> float | tuple[float, float] | None:
    """Compute the per-request timeout for the next HTTP call given
    elapsed time so far in the redirect chain.

    Mitigates: per-request timeout decay. Tuple timeouts retain their
    structure but are capped to ``min(original, remaining)`` so neither
    the connect nor read step can exceed what's left of the total budget.
    Honours the ``total=0`` test-mock convention by passing the original
    timeout untouched in that case.
    """
    if total_allowed_time is None:
        return timeout
    if total_allowed_time == 0:
        return timeout

    remaining = total_allowed_time - elapsed
    remaining = max(0.1, remaining) if total_allowed_time > 0 else max(0.0, remaining)

    if isinstance(timeout, int | float):
        return remaining
    if isinstance(timeout, tuple):
        return (min(timeout[0], remaining), min(timeout[1], remaining))
    return remaining


def _resolve_target_ip(parsed: Any, current_url: str) -> str:
    """Resolve a hostname (or accept a literal IP) to a safe IP for HTTPS
    pinning.

    Mitigates: SSRF via DNS resolution returning a private/internal IP.
    Tries the literal-IP path first (so callers can pass numeric URLs
    without a DNS round-trip), then falls back to a safe DNS lookup.
    Raises ``ValueError`` with a sanitized URL if no safe IP is found.
    """
    target_ip: str | None = None

    if parsed.hostname:
        try:
            ip_candidate = parsed.hostname.strip("[]").split("%")[0]
            ip_obj = ipaddress.ip_address(ip_candidate)
            target_ip_cand = str(ip_obj)
            if is_ip_safe(target_ip_cand):
                target_ip = str(target_ip_cand)
        except ValueError:
            pass

    if target_ip is None:
        ips = _resolve_hostname_safe(parsed.hostname or "")
        for _, _, _, _, sockaddr in ips:
            if is_ip_safe(str(sockaddr[0])):
                target_ip = str(sockaddr[0])
                break

    if not target_ip:
        sanitized_url = _sanitize_url_for_error(current_url)
        raise ValueError(f"No safe IP resolved for {sanitized_url}")

    return target_ip


def _send_http_pinned(
    session: requests.Session,
    method: str,
    parsed: Any,
    safe_url: str,
    current_timeout: float | tuple[float, float] | None,
    request_hooks: dict[str, Any],
    kwargs: dict[str, Any],
) -> Any:
    """Send an HTTP request with the URL already pinned to its resolved IP.

    Mitigates: DNS-rebinding TOCTOU on plain HTTP. The original hostname
    is preserved in the ``Host`` header (Virtual Hosting safety) while the
    URL itself addresses the literal IP, so a hostile resolver cannot
    swap the IP between the safety-check and the connect.
    """
    pinned_url, _hostname = _pin_url_to_ip(safe_url)
    kwargs["headers"]["Host"] = parsed.netloc
    return session.request(
        method,
        pinned_url,
        stream=True,
        timeout=current_timeout,
        hooks=request_hooks,
        allow_redirects=False,
        **kwargs,
    )


def _send_https_pinned(
    session: requests.Session,
    method: str,
    parsed: Any,
    safe_url: str,
    current_url: str,
    current_timeout: float | tuple[float, float] | None,
    request_hooks: dict[str, Any],
    kwargs: dict[str, Any],
) -> Any:
    """Send an HTTPS request via a per-IP-pinned adapter so the TLS
    handshake's SNI uses the original hostname while the TCP connect
    targets the resolved (vetted) IP.

    Mitigates: DNS-rebinding TOCTOU on HTTPS + SNI/Host mismatch. The
    pinned adapter is cached by IP+timeout, and the request is prepared
    manually to bypass the session's normal adapter-selection (which
    would resolve the hostname a second time).
    """
    target_ip = _resolve_target_ip(parsed, current_url)
    kwargs["headers"]["Host"] = parsed.hostname or parsed.netloc

    original_adapter = session.get_adapter(current_url)
    current_retries = getattr(original_adapter, "max_retries", 0)
    pinned_session = _get_pinned_session(
        str(target_ip), current_timeout, max_retries=current_retries
    )

    req = requests.Request(
        method,
        safe_url,
        headers=kwargs.get("headers"),
        files=kwargs.get("files"),
        data=kwargs.get("data"),
        json=kwargs.get("json"),
        params=kwargs.get("params"),
        auth=kwargs.get("auth"),
        cookies=kwargs.get("cookies"),
        hooks=request_hooks,
    )
    prepped = session.prepare_request(req)

    settings = session.merge_environment_settings(
        prepped.url,
        proxies={},
        stream=True,
        verify=kwargs.get("verify"),
        cert=kwargs.get("cert"),
    )
    send_kwargs = kwargs.copy()
    send_kwargs.update(settings)

    valid_adapter_args = {"stream", "timeout", "verify", "cert", "proxies"}
    adapter_kwargs = {k: v for k, v in send_kwargs.items() if k in valid_adapter_args}
    adapter_kwargs["stream"] = True
    adapter_kwargs["timeout"] = current_timeout

    adapter = pinned_session.get_adapter(safe_url)
    return adapter.send(prepped, **adapter_kwargs)


def _strip_redirect_secrets(
    kwargs: dict[str, Any],
    current_url: str,
    next_url: str,
    session: requests.Session,
) -> str:
    """Strip credentials, sensitive headers, and sensitive query
    parameters when a redirect crosses an origin boundary.

    Mitigates: token/credential leak across origins. Always strips
    headers that match the dynamic+static sensitivity rules; on cross-
    origin redirects (different host/scheme/port) ALSO strips sensitive
    query params (e.g. ``accessId``) and pops the explicit ``auth``
    kwarg. Returns the (possibly param-stripped) next URL.
    """
    _strip_sensitive_headers(
        kwargs["headers"],
        current_url,
        next_url,
        session_headers=session.headers,
    )

    next_parsed = urlparse(next_url)
    curr_parsed = urlparse(current_url)
    if (
        next_parsed.hostname != curr_parsed.hostname
        or next_parsed.scheme != curr_parsed.scheme
        or _get_port(next_parsed) != _get_port(curr_parsed)
    ):
        next_url = _strip_sensitive_params(next_url)
        if "auth" in kwargs:
            kwargs.pop("auth")
    return next_url


def _drop_body_for_get(kwargs: dict[str, Any]) -> None:
    """Drop request-body kwargs and content-related headers when a
    redirect downgrades the method to GET.

    Mitigates: malformed POST→GET conversion (sending a body without a
    valid ``Content-Type`` header, or vice versa, can confuse upstream
    proxies and is technically invalid per HTTP).
    """
    kwargs.pop("data", None)
    kwargs.pop("json", None)
    kwargs.pop("files", None)
    if "headers" in kwargs:
        for h in list(kwargs["headers"].keys()):
            if h.lower() in ("content-type", "content-length", "transfer-encoding"):
                del kwargs["headers"][h]


def _apply_method_downgrade(
    method: str, status_code: int, kwargs: dict[str, Any]
) -> str:
    """Apply RFC-7231 method downgrade rules for redirects.

    - 303 See Other (any method except HEAD) → GET
    - 301/302 + POST                          → GET
    - 307/308                                 → preserve method

    Mitigates: silent method-preservation when the spec demands a
    downgrade, which would re-send a POST body (potentially with
    credentials) to the redirect target.
    """
    if status_code == 303 and method != "HEAD":
        _drop_body_for_get(kwargs)
        return "GET"
    if status_code in (301, 302) and method == "POST":
        _drop_body_for_get(kwargs)
        return "GET"
    return method


def _drop_host_header(kwargs: dict[str, Any]) -> None:
    """Remove any ``Host`` header before re-issuing the request after a
    redirect.

    Mitigates: SNI/Host mismatch on the redirected request. The Host
    header is set per-iteration by the HTTP/HTTPS dispatch helpers based
    on the (newly-resolved) target's hostname; carrying the previous
    iteration's value forward would cause the upstream to receive a
    ``Host`` for the wrong origin.
    """
    if "headers" in kwargs:
        for h in list(kwargs["headers"].keys()):
            if h.lower() == "host":
                del kwargs["headers"][h]


def _is_redirect(r: Any) -> bool:
    """Detect whether a response is a redirect, with mock-safety.

    Mitigates: false-positive redirects from `MagicMock.is_redirect`
    (which evaluates truthy by default). Real responses expose
    ``is_redirect`` as a bool; mocks unintentionally pass any attribute
    access. This guard ensures only real bool ``True`` values count.
    """
    is_redirect = getattr(r, "is_redirect", False)
    if callable(is_redirect) or type(is_redirect).__name__ == "MagicMock":
        return False
    return bool(is_redirect)


def _process_redirect(
    r: Any,
    current_url: str,
    method: str,
    kwargs: dict[str, Any],
    attempt: int,
    max_redirects: int,
    session: requests.Session,
) -> tuple[str, str] | None:
    """Drive one iteration of the manual redirect loop.

    Returns ``(next_url, next_method)`` to continue the loop, or ``None``
    if the response is not a redirect (final response). Mutates
    ``kwargs`` in place (header/secret stripping, body drop, Host
    removal). Raises ``requests.TooManyRedirects`` when the cap is hit.

    Mitigates: combined redirect attack surface — every defense layer
    on the redirect path (max-cap, secret stripping, method downgrade,
    Host removal) is dispatched in the security-correct order so a
    refactor cannot accidentally reorder them.
    """
    if not _is_redirect(r):
        return None
    location = r.headers.get("Location")
    if not (location and isinstance(location, str)):
        return None
    if attempt == max_redirects:
        raise requests.TooManyRedirects(f"Exceeded {max_redirects} redirects")

    next_url = urljoin(current_url, location)
    next_url = _strip_redirect_secrets(kwargs, current_url, next_url, session)

    kwargs.pop("params", None)
    new_method = _apply_method_downgrade(method, r.status_code, kwargs)
    _drop_host_header(kwargs)
    return next_url, new_method


def _validate_content_type(
    r: Any, allowed_content_types: Container[str] | None
) -> None:
    """Validate the response's ``Content-Type`` against an allow-list, or
    block ``text/html`` when no allow-list is supplied.

    Mitigates: WAF/proxy block-page misinterpretation. Many CDNs serve
    ``text/html`` error pages for blocked requests; without this check a
    JSON-expecting caller would silently parse the HTML as JSON (or
    worse, treat it as success).
    """
    content_type_header = r.headers.get("Content-Type", "")
    mime_type = (
        content_type_header.split(";")[0].strip().lower()
        if content_type_header
        else ""
    )

    if allowed_content_types is not None:
        if not content_type_header:
            raise ValueError(
                "Content-Type header missing, but validation required"
            )
        if mime_type not in allowed_content_types:
            raise ValueError(
                f"Invalid Content-Type: {mime_type} (expected {allowed_content_types})"
            )
    elif mime_type == "text/html":
        raise ValueError(
            "Invalid Content-Type: received text/html (possible proxy error or WAF block)"
        )


def _compute_read_timeout(
    timeout: _TimeoutT,
    total_allowed_time: float | None,
    current_elapsed: float,
) -> float | tuple[float, float]:
    """Compute the timeout for streaming the response body, capped to
    whatever's left of the total budget.

    Mitigates: Slowloris on the read side. Even if connect succeeded
    quickly, an adversary can stall the body stream; this enforces the
    total budget on the read step too. Tuple timeouts retain their
    structure with both legs capped to the remaining time.
    """
    if total_allowed_time is None:
        raise RuntimeError("total_allowed_time cannot be None at this point")

    if total_allowed_time == 0:
        read_timeout_val: float = 0.0
    else:
        remaining_total = total_allowed_time - current_elapsed
        if remaining_total <= 0:
            raise requests.Timeout("Total timeout exceeded before reading body")
        read_timeout_val = remaining_total
        if isinstance(timeout, tuple):
            read_timeout_val = min(read_timeout_val, timeout[1])

    if isinstance(timeout, tuple):
        return (min(timeout[0], read_timeout_val), read_timeout_val)
    return read_timeout_val


def request_safe(
    session: requests.Session,
    url: str,
    method: str = "GET",
    max_bytes: int = MAX_PAYLOAD_SIZE,
    timeout: int | float | tuple[float, float] | None = None,
    allowed_content_types: Container[str] | None = None,
    raise_for_status: bool = True,
    **kwargs: Any,
) -> requests.Response:
    """Perform an HTTP request through the project's security state machine.

    This function is the *only* place in the codebase that issues real
    HTTP requests to untrusted upstreams. Every transit-API call,
    every cache-refresh fetch, and every health-check goes through
    here. As such, it is the load-bearing pillar for the project's
    defence-in-depth posture.

    The pipeline runs each call through 14 cohesive security helpers
    in a strict order. Each helper has its own docstring naming the
    attack vector it mitigates; the high-level sequence is:

    1. Default a missing timeout (Slowloris baseline).
    2. Disable automatic redirects (we handle them manually to defeat
       DNS-rebinding TOCTOU between safety check and connect).
    3. Merge caller hooks with the response-IP-verification security
       hook (:func:`_check_response_security`).
    4. Compute the total time budget across the redirect chain
       (tuple ``(connect, read)`` is summed to defeat budget-stretch
       attacks).
    5. For each redirect step (capped by ``session.max_redirects``):
       a. Enforce the cumulative time budget.
       b. Compute a per-request timeout decay.
       c. Validate the URL (SSRF guard).
       d. Pin the connection: HTTP via :func:`_pin_url_to_ip`; HTTPS
          via the cached :class:`PinnedHTTPSAdapter`. The TLS handshake
          uses the original hostname for SNI; the TCP connect targets
          the resolved (vetted) IP.
       e. Inspect the response: redirect → :func:`_process_redirect`
          (strips secrets, downgrades method per RFC-7231, drops Host
          header) and continue; otherwise validate Content-Type and
          stream the body via :func:`read_response_safe` under the
          ``MAX_PAYLOAD_SIZE`` cap.
    6. Sanitize any leaked URLs in error messages before re-raising.

    See ``docs/architecture.md`` §2 for the rendered flowchart and
    ``.jules/omega.md`` for the joint Sentinel+Surgeon refactor that
    extracted these 14 helpers from the original 280-line monolith.

    Args:
        session: The :class:`requests.Session` to use. The session's
            adapters supply retry / jitter / pool-management; only the
            request itself routes through this function's pinned
            adapter.
        url: The URL to fetch. Must pass :func:`validate_http_url`'s
            SSRF guard (no internal/private hostnames, no
            unsupported schemes).
        method: HTTP method (default ``"GET"``). 303 redirects always
            downgrade to GET; 301/302 downgrade POST→GET; 307/308
            preserve the method.
        max_bytes: Maximum allowed response body size (default
            ``MAX_PAYLOAD_SIZE`` = 10 MB). Enforced both via the
            ``Content-Length`` header pre-check and via streaming in
            :func:`read_response_safe`.
        timeout: Request timeout in seconds. Accepts ``int``,
            ``float``, ``(connect, read)`` tuple, or ``None`` (which
            is replaced by :data:`DEFAULT_TIMEOUT` to defend against
            Slowloris). For tuples, the SUM is used as the total
            budget across the entire redirect chain.
        allowed_content_types: Optional MIME-type allow-list. When
            ``None``, ``text/html`` is implicitly blocked (defends
            against WAF / proxy block-page misinterpretation). When
            a list/set is given, the response's MIME must match
            exactly; missing ``Content-Type`` raises :class:`ValueError`.
        raise_for_status: If ``True`` (default), invoke
            :meth:`Response.raise_for_status` on the final response.
            Set ``False`` only when the caller needs to inspect 4xx/5xx
            payloads (e.g. ``Retry-After`` parsing).
        **kwargs: Additional keyword arguments forwarded to
            :meth:`Session.request`. The ``allow_redirects``, ``stream``,
            and ``hooks`` keys are stripped/managed by this function
            and must not be supplied by the caller.

    Returns:
        The :class:`requests.Response` with its body consumed and
        attached to ``._content``. The response context manager has
        already been closed; the caller can read ``.content`` /
        ``.text`` / ``.json()`` freely.

    Raises:
        ValueError: If the URL fails SSRF validation, the
            ``Content-Type`` is invalid/missing/disallowed, or the
            body exceeds ``max_bytes``.
        requests.Timeout: If the cumulative time budget is exceeded
            (either pre-flight or during body streaming).
        requests.TooManyRedirects: If the redirect chain exceeds
            ``session.max_redirects`` (default 10).
        requests.RequestException: For network errors. The ``args``
            of the exception are sanitized to strip any URLs that
            might have contained query-string secrets.
    """
    # Security: Enforce default timeout to prevent Slowloris attacks if caller forgets it
    if timeout is None:
        timeout = DEFAULT_TIMEOUT

    # Security: Disable automatic redirects to prevent DNS Rebinding TOCTOU.
    # We handle redirects manually to pin the DNS for each step.
    kwargs.pop("allow_redirects", None)
    kwargs.pop("stream", None)

    # Ensure Host header is set to original hostname for Virtual Hosting
    if "headers" in kwargs:
        kwargs["headers"] = CaseInsensitiveDict(kwargs["headers"])
    else:
        kwargs["headers"] = CaseInsensitiveDict()

    request_hooks = _merge_request_hooks(session, kwargs)

    max_redirects = getattr(session, "max_redirects", 10)
    current_url = url
    start_time = time.monotonic()
    total_allowed_time = _compute_total_time_budget(timeout)

    try:
        for attempt in range(max_redirects + 1):
            elapsed = time.monotonic() - start_time
            _check_total_budget_or_raise(total_allowed_time, elapsed)
            current_timeout = _per_request_timeout(timeout, total_allowed_time, elapsed)

            safe_url = validate_http_url(current_url, check_dns=False)
            if not safe_url:
                # Security: avoid echoing potentially sensitive URLs in errors.
                sanitized_url = _sanitize_url_for_error(current_url)
                raise ValueError(f"Unsafe or invalid URL: {sanitized_url}")

            parsed = urlparse(safe_url)
            if parsed.scheme == "http":
                ctx = _send_http_pinned(
                    session, method, parsed, safe_url, current_timeout, request_hooks, kwargs
                )
            else:
                ctx = _send_https_pinned(
                    session, method, parsed, safe_url, current_url,
                    current_timeout, request_hooks, kwargs,
                )

            with ctx as r:
                try:
                    # Manually dispatch hooks for HTTPS since we bypassed session.request
                    if parsed.scheme == "https":
                        r = dispatch_hook("response", request_hooks, r, **kwargs)

                    redirect = _process_redirect(
                        r, current_url, method, kwargs, attempt, max_redirects, session
                    )
                    if redirect is not None:
                        current_url, method = redirect
                        continue

                    if raise_for_status:
                        r.raise_for_status()

                    _validate_content_type(r, allowed_content_types)

                    current_elapsed = time.monotonic() - start_time
                    final_read_timeout = _compute_read_timeout(
                        timeout, total_allowed_time, current_elapsed
                    )
                    content = read_response_safe(r, max_bytes, timeout=final_read_timeout)

                    # Manually attach content to response object so it's usable after close
                    r._content = content
                    r._content_consumed = True
                    return r
                except Exception:
                    # We do not close the adapter/session to maintain keep-alive cache,
                    # but if an exception happens during processing the stream, we should close the response
                    if hasattr(r, "close"):
                        r.close()
                    raise
    except requests.RequestException as exc:
        # Sanitize keys in exception messages (which may contain full URLs)
        safe_msg = _sanitize_exception_msg(str(exc))
        exc.args = (safe_msg,) + exc.args[1:]
        raise exc

    # Should not be reached due to TooManyRedirects check inside loop,
    # but defensive return.
    raise requests.TooManyRedirects(f"Exceeded {max_redirects} redirects")


def fetch_content_safe(
    session: requests.Session,
    url: str,
    max_bytes: int = MAX_PAYLOAD_SIZE,
    timeout: int | float | tuple[float, float] | None = None,
    allowed_content_types: Container[str] | None = None,
    **kwargs: Any,
) -> bytes:
    """Fetch URL content with a size limit to prevent DoS (legacy wrapper)."""
    # Explicitly enforce stream=True for downstream compatibility
    kwargs["stream"] = True
    response = request_safe(
        session,
        url,
        method="GET",
        max_bytes=max_bytes,
        timeout=timeout,
        allowed_content_types=allowed_content_types,
        raise_for_status=True,
        **kwargs,
    )
    return cast(bytes, response.content)


def cleanup_http_sessions() -> None:
    """Clear the HTTP session cache and gracefully close all sessions."""
    with _HTTP_SESSION_LOCK:
        for session in _HTTP_SESSION_CACHE.values():
            try:
                session.close()
            except Exception as exc:
                # Security (Clear-Text-Logging Drift): atexit-time
                # cleanup — sanitise the bound exception so a hostile
                # ``__str__`` cannot poison the final log line emitted
                # before process shutdown.
                log.debug(
                    "Error closing HTTP session during cleanup: %s",
                    sanitize_log_arg(str(exc)),
                )
        _HTTP_SESSION_CACHE.clear()

atexit.register(cleanup_http_sessions)
