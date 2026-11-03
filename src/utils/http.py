"""HTTP helpers for configuring :mod:`requests` sessions."""

from __future__ import annotations

import ipaddress
import logging
import os
import time
import types
import secrets
from typing import Any, cast
from collections.abc import Container
from urllib.parse import urljoin, urlparse

import requests
from requests.hooks import dispatch_hook
from requests.structures import CaseInsensitiveDict
from urllib3.util.retry import Retry

# The parts below were moved verbatim out of this module (700-line rule,
# 2026-11-02). Every name stays importable and patchable from here.
from .http_safety.limits import (
    DEFAULT_TIMEOUT, DNS_TIMEOUT, MAX_PAYLOAD_SIZE, MAX_URL_LENGTH,
    _RETRY_AFTER_NUMERIC_RE, parse_retry_after,
)
from .http_safety.redaction import (
    _SENSITIVE_HEADERS, _SENSITIVE_HEADERS_LOWER, _SENSITIVE_HEADER_PARTIALS,
    _SENSITIVE_KEY_SUBSTRINGS, _SENSITIVE_QUERY_KEYS, _URL_AUTH_RE, _get_port, _is_sensitive_header,
    _normalize_key, _replace_auth, _safe_rebuild_auth, _sanitize_exception_msg,
    _sanitize_url_for_error, _strip_sensitive_headers, _strip_sensitive_params,
)
from .http_safety.ip_guard import (
    _NAT64_PREFIX, _SHARED_ADDRESS_SPACE, _resolve_hostname_safe, is_ip_safe,
)
from .http_safety.url_guard import (
    _PUBLIC_FEED_URL_GITHUB_PAGES_OWNER_RE, _PUBLIC_FEED_URL_TRUSTED_HOSTS,
    _PUBLIC_FEED_URL_TRUSTED_SUFFIXES, _UNSAFE_DOMAINS, _UNSAFE_TLDS, _UNSAFE_URL_CHARS,
    _has_embedded_credentials, _rebuild_netloc, validate_http_url, validate_public_feed_url,
)
from .http_safety.peer import (
    PROXY_TRUSTED_HOSTS, _proxy_for, _require_trusted_proxy_host, verify_response_ip,
)
from .http_safety.adapters import (
    KEEPALIVE_SOCKET_OPTIONS, PinnedHTTPSAdapter, PinnedHTTPSConnection, SafeDNSAdapter,
    SafeDNSHTTPConnection, SafeDNSHTTPSConnection, TimeoutHTTPAdapter,
)
from .http_safety.sessions import (
    _EVICTED_SESSIONS_QUEUE, _HTTP_SESSION_CACHE, _HTTP_SESSION_CACHE_MAX_SIZE, _HTTP_SESSION_LOCK,
    _cleanup_evicted_sessions_thread, _cleanup_thread, _get_pinned_session, cleanup_http_sessions,
)
from .http_safety.request_steps import (
    _TimeoutT, _apply_method_downgrade, _check_total_budget_or_raise, _compute_read_timeout,
    _compute_total_time_budget, _drop_body_for_get, _drop_host_header, _is_redirect,
    _per_request_timeout, _process_redirect, _strip_redirect_secrets, _validate_content_type,
    read_response_safe,
)

__all__ = [
    "_RETRY_AFTER_NUMERIC_RE", "parse_retry_after", "_DEFAULT_RETRY_OPTIONS", "DEFAULT_TIMEOUT",
    "DNS_TIMEOUT", "_UNSAFE_URL_CHARS", "MAX_URL_LENGTH", "MAX_PAYLOAD_SIZE", "_HTTP_SESSION_CACHE",
    "_HTTP_SESSION_LOCK", "_HTTP_SESSION_CACHE_MAX_SIZE", "_EVICTED_SESSIONS_QUEUE", "log",
    "_cleanup_evicted_sessions_thread", "_cleanup_thread", "_normalize_key", "_URL_AUTH_RE",
    "_SENSITIVE_QUERY_KEYS", "_SENSITIVE_KEY_SUBSTRINGS", "_SENSITIVE_HEADERS",
    "_SENSITIVE_HEADERS_LOWER", "_SENSITIVE_HEADER_PARTIALS", "_is_sensitive_header",
    "_strip_sensitive_params", "_replace_auth", "_sanitize_exception_msg",
    "_sanitize_url_for_error", "TimeoutHTTPAdapter", "SafeDNSHTTPConnection",
    "SafeDNSHTTPSConnection", "KEEPALIVE_SOCKET_OPTIONS", "SafeDNSAdapter", "PinnedHTTPSConnection",
    "PinnedHTTPSAdapter", "_get_pinned_session", "_check_response_security", "_pin_url_to_ip",
    "_strip_sensitive_headers", "_get_port", "_safe_rebuild_auth", "session_with_retries",
    "_UNSAFE_TLDS", "_UNSAFE_DOMAINS", "_SHARED_ADDRESS_SPACE", "_NAT64_PREFIX", "is_ip_safe",
    "_resolve_hostname_safe", "_rebuild_netloc", "_has_embedded_credentials", "validate_http_url",
    "_PUBLIC_FEED_URL_TRUSTED_HOSTS", "_PUBLIC_FEED_URL_TRUSTED_SUFFIXES",
    "_PUBLIC_FEED_URL_GITHUB_PAGES_OWNER_RE", "validate_public_feed_url", "PROXY_TRUSTED_HOSTS",
    "_require_trusted_proxy_host", "_proxy_for", "verify_response_ip", "read_response_safe",
    "_TimeoutT", "_merge_request_hooks", "_compute_total_time_budget",
    "_check_total_budget_or_raise", "_per_request_timeout", "_resolve_target_ip",
    "_send_http_pinned", "_send_https_pinned", "_strip_redirect_secrets", "_drop_body_for_get",
    "_apply_method_downgrade", "_drop_host_header", "_is_redirect", "_process_redirect",
    "_validate_content_type", "_compute_read_timeout", "request_safe", "fetch_content_safe",
    "cleanup_http_sessions",
]

log = logging.getLogger(__name__)

_DEFAULT_RETRY_OPTIONS: dict[str, Any] = {
    "total": 4,
    "backoff_factor": 0.6,
    "status_forcelist": (429, 500, 502, 503, 504),
    "allowed_methods": ("GET",),
}


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


def session_with_retries(
    user_agent: str,
    timeout: int | float | tuple[float, float] = DEFAULT_TIMEOUT,
    *,
    keepalive: bool = False,
    **retry_opts: Any,
) -> requests.Session:
    """Return a :class:`requests.Session` pre-configured with retries and a default timeout.

    Args:
        user_agent: User-Agent header that should be sent with every request.
        timeout: Default timeout in seconds for requests (default: (3.0, 15.0)).
        keepalive: TCP keepalive for requests that wait minutes for their
            answer (see :data:`KEEPALIVE_SOCKET_OPTIONS`).
        **retry_opts: Additional keyword arguments forwarded to
            :class:`urllib3.util.retry.Retry`.
    """

    options = {**_DEFAULT_RETRY_OPTIONS, **retry_opts}
    session = requests.Session()

    # Security: Strip sensitive headers on cross-origin redirects
    # Deliberate per-instance override of a method (mypy: method-assign).
    session.rebuild_auth = types.MethodType(_safe_rebuild_auth, session)  # type: ignore[method-assign]

    # Security: Limit redirects to prevent infinite loops and resource exhaustion (DoS)
    session.max_redirects = 10
    session.hooks["response"].append(_check_response_security)

    class JitterRetry(Retry):
        def get_backoff_time(self) -> float:
            base_backoff = super().get_backoff_time()
            jittered = base_backoff * secrets.SystemRandom().uniform(0.8, 1.2)
            # ``super().get_backoff_time()`` is already capped at ``backoff_max``;
            # re-cap AFTER applying the +/-20% jitter so the upward jitter cannot
            # push a sleep past the documented ceiling (e.g. 120s -> 144s).
            return min(float(self.backoff_max), jittered)

    retry = JitterRetry(**options)
    adapter = SafeDNSAdapter(max_retries=retry, timeout=timeout, keepalive=keepalive)
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
            "Requests through the proxy are limited to PROXY_TRUSTED_HOSTS; "
            "other hosts fail closed (DNS Rebinding protection)."
        )

    return session


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
            pass  # host is a name, not an IP literal: resolved safely below

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
) -> requests.Response:
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
) -> requests.Response:
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
    # The session's TCP keepalive goes along: the pinned adapter, not the
    # session's own, opens the connection (until 2026-11-02 the KI-Wächter's
    # ``keepalive=True`` reached only the session's adapter, which HTTPS
    # requests through this function never use).
    pinned_session = _get_pinned_session(
        str(target_ip), current_timeout, max_retries=current_retries,
        keepalive=bool(getattr(original_adapter, "keepalive", False)),
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
        cast(str, prepped.url),
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

    See ``docs/architecture.md`` §2 for the rendered flowchart.

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
    return response.content
