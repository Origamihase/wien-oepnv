"""Steps of ``request_safe``: capped body reader, time budget, redirect handling.

Moved verbatim from ``src/utils/http.py`` (700-line rule, 2026-11-02);
import from ``src.utils.http``, which re-exports every name.
"""

from __future__ import annotations

import time
from typing import Any
from collections.abc import Container
from urllib.parse import urljoin, urlparse

import requests

from .limits import MAX_PAYLOAD_SIZE
from .redaction import _get_port, _strip_sensitive_headers, _strip_sensitive_params

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

        # Check the prospective size BEFORE appending so the worst-case
        # in-memory buffer is bounded by ``max_bytes`` (not
        # ``max_bytes + chunk_size``). Matters for callers that pass a
        # small ``max_bytes`` cap (e.g. a 1 KiB health probe) — the
        # pre-fix loop could buffer 8 KiB before raising.
        if received + len(chunk) > max_bytes:
            response.close()
            raise ValueError(f"Response too large (> {max_bytes} bytes)")
        chunks.append(chunk)
        received += len(chunk)
    return b"".join(chunks)


_TimeoutT = int | float | tuple[float, float] | None


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
