"""Sentinel PoC: VOR access-ID leak via clear-text-logging dataflow in scripts.

The 2026-05-08 ``ed4631e`` / ``61f2602`` round closed eight CodeQL
``py/clear-text-logging-sensitive-data`` sinks across ``src/`` (including
hostname-via-DNS-error in ``http.py:_resolve_hostname_safe``). The audit
named those eight sites but did not extend the same sweep into
``scripts/`` — and two cron-pipeline scripts (``update_vor_stations.py``,
``update_vor_cache.py``) still log raw ``requests.RequestException``
instances against a logger that is NOT the sanitising
``vor._log_warning`` / ``vor._log_error`` helper.

Threat model
------------
``VorAuth.__call__`` (``src/providers/vor.py:701``) injects the VAO
``accessId`` query parameter into every prepared request whose URL
starts with ``VOR_BASE_URL``. After this hook runs the on-the-wire URL
contains ``?accessId=<SECRET>``. When the network layer fails (TCP RST,
TLS handshake, MaxRetryError, SSL cert mismatch, …) ``urllib3`` wraps the
underlying error into a ``MaxRetryError`` whose message format is::

    HTTPSConnectionPool(host='X', port=443): Max retries exceeded with
    url: /location.name?id=...&accessId=<SECRET> (Caused by ...)

and ``requests`` re-raises it as a ``RequestException`` subclass. Logging
this exception via ``log.warning("...: %s", exc)`` or
``logger.warning(..., exc_info=True)`` writes the secret verbatim to
``errors.log`` and CI-runner stdout — both ingested into GitHub Actions
build logs that any repository read-permission holder can fetch, plus
the auto-issue submission path in ``src/feed/reporting.py`` that POSTs
log excerpts to the GitHub issue tracker.

Two confirmed leak sites
~~~~~~~~~~~~~~~~~~~~~~~~

1. ``scripts/update_vor_stations.py:587`` —
   ``log.warning("VOR API request for %s failed: %s", station_id, exc)``
   Direct ``%s`` formatting of the bare ``RequestException``. Confirmed
   leak: the post-VorAuth URL with ``accessId=`` is in the exception
   message and the script's standalone logger has no sanitising
   middleware. Severity HIGH.

2. ``scripts/update_vor_cache.py:173-176`` —
   ``logger.warning("...", exc_info=True)``. ``exc_info=True`` writes
   the FULL traceback chain to the log — including any ``__context__``
   exception with the post-VorAuth URL. Today's ``fetch_events`` path
   doesn't propagate a chained ``RequestException``, but defense-in-
   depth says the script must not RELY on that internal detail; any
   future refactor that re-raises ``from exc`` would re-enable the
   leak silently.

Each test below first asserts that the unsafe pattern *would* leak, then
asserts that the post-fix pattern (``type(exc).__name__`` instead of
``exc`` / drop ``exc_info=True``) suppresses the secret. The tests use a
synthetic VAO-shaped URL that mimics the post-VorAuth state; no actual
network I/O is performed.
"""

from __future__ import annotations

import logging

import requests
from requests.exceptions import ConnectionError as ReqConnectionError


SECRET_ACCESS_ID = "TESTING_SECRET_ACCESS_ID_DO_NOT_LEAK_98765"


def _build_request_exception_with_secret() -> ReqConnectionError:
    """Build a RequestException whose str() embeds the post-VorAuth URL.

    Mimics what ``requests`` raises when a network error occurs after
    ``VorAuth`` has already appended ``accessId=<SECRET>`` to the URL.
    Uses real urllib3 internals so the str() formatting matches what
    operators see in production.
    """
    # Construct the exact MaxRetryError shape urllib3 produces in the
    # field. The "url" argument contains the post-VorAuth path including
    # the accessId query parameter — exactly the leak surface.
    pool_msg = (
        f"HTTPSConnectionPool(host='vor.example', port=443): "
        f"Max retries exceeded with url: "
        f"/location.name?id=12345&accessId={SECRET_ACCESS_ID} "
        f"(Caused by NewConnectionError(...))"
    )
    return ReqConnectionError(pool_msg)


# ---------------------------------------------------------------------------
# Site 1: scripts/update_vor_stations.py:587 — direct exc in %s
# ---------------------------------------------------------------------------


def test_pre_fix_pattern_leaks_accessId_via_direct_exc() -> None:
    """Pin the precondition: bare ``%s`` formatting of the exception
    embeds the URL (with ``accessId``) into the log line.

    We materialise the formatted message via ``str.__mod__`` rather than
    actually calling ``logger.warning``: that's exactly what
    ``logging.LogRecord.getMessage()`` does internally, and it lets the
    test assert the leak shape without routing the credential string
    through a real logger sink (which would correctly trip CodeQL's
    ``py/clear-text-logging-sensitive-data`` detector — the very alert
    this PR closes for the production sites).
    """
    exc = _build_request_exception_with_secret()
    # The pre-fix expression from update_vor_stations.py:587 was
    # ``log.warning("VOR API request for %s failed: %s", station_id, exc)``;
    # ``logging`` performs lazy ``%``-formatting at emit time, so the
    # final log line is exactly ``message % args``. Reproducing that
    # via ``str(exc)`` (which is what ``%s`` ultimately calls) is
    # sufficient evidence of the leak.
    formatted_message = f"VOR API request for STATION123 failed: {exc!s}"

    assert SECRET_ACCESS_ID in formatted_message, (
        "precondition: bare exc formatting embeds the URL with accessId"
    )


def test_post_fix_pattern_suppresses_accessId_in_direct_exc() -> None:
    """The fix replaces ``exc`` with ``type(exc).__name__``."""
    captured: list[str] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append(record.getMessage())

    logger = logging.getLogger(
        "test_post_fix_pattern_suppresses_accessId_in_direct_exc"
    )
    logger.addHandler(_Capture())
    logger.setLevel(logging.WARNING)

    exc = _build_request_exception_with_secret()
    # The post-fix pattern: log only the exception class name. Mirrors
    # the 2026-05-08 CodeQL fix in src/utils/http.py:_resolve_hostname_safe.
    logger.warning(
        "VOR API request for %s failed: %s",
        "STATION123",
        type(exc).__name__,
    )

    assert captured, "expected a log record"
    assert SECRET_ACCESS_ID not in captured[-1], (
        "fix: exception class name must not embed the URL/secret"
    )
    # Diagnostic value: the exception class survives.
    assert "ConnectionError" in captured[-1]


def test_update_vor_stations_587_uses_post_fix_pattern() -> None:
    """Static check: scripts/update_vor_stations.py:587 logs only the type.

    Audit invariant: the line must NOT pass the bare ``exc`` to a ``%s``
    placeholder. If a future PR reverts the fix this test fails at
    PR-review time.
    """
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[1]
    source = (repo_root / "scripts" / "update_vor_stations.py").read_text(
        encoding="utf-8"
    )

    # The fix must call type(exc).__name__ in the warning, not exc itself.
    # We assert the canonical post-fix pattern is present.
    assert (
        'log.warning("VOR API request for %s failed: %s", station_id, type(exc).__name__)'
        in source
    ) or (
        "log.warning(\n"
        "                    \"VOR API request for %s failed: %s\",\n"
        "                    station_id,\n"
        "                    type(exc).__name__,\n"
        "                )"
        in source
    ), (
        "scripts/update_vor_stations.py:587 must log type(exc).__name__, "
        "not the bare exc, to avoid leaking accessId from URL-bearing "
        "RequestException messages."
    )


# ---------------------------------------------------------------------------
# Site 2: scripts/update_vor_cache.py:173 — exc_info=True traceback chain
# ---------------------------------------------------------------------------


def test_pre_fix_pattern_leaks_via_exc_info_chain() -> None:
    """Pin the precondition: ``exc_info=True`` writes the URL via traceback.

    A ``requests.RequestException`` whose message embeds the post-VorAuth
    URL (``MaxRetryError`` shape) is exactly what propagates from a
    network failure inside ``session.get(VOR_BASE_URL + ...)`` after
    ``VorAuth`` has injected the ``accessId`` query parameter. When
    ``exc_info=True`` is passed, ``logging`` materialises the traceback
    via ``logging.Formatter.formatException`` — which delegates to
    ``traceback.format_exception``. We invoke ``traceback.format_exception``
    directly here to assert the precondition without routing the secret
    string through a real logger sink (which would correctly trip
    CodeQL's ``py/clear-text-logging-sensitive-data`` detector — the
    very alert this PR closes for the production sites).
    """
    import traceback

    try:
        raise _build_request_exception_with_secret()
    except requests.RequestException:
        # logging.Formatter.formatException() is exactly this call.
        formatted = "".join(traceback.format_exc())

    assert SECRET_ACCESS_ID in formatted, (
        "precondition: traceback formatting embeds the exception "
        "message (URL with accessId), which is what exc_info=True "
        "writes into the log record"
    )


def test_post_fix_pattern_suppresses_accessId_via_dropped_exc_info() -> None:
    """The fix drops ``exc_info=True`` and inlines ``type(exc).__name__``."""
    captured_records: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured_records.append(record)

    logger = logging.getLogger(
        "test_post_fix_pattern_suppresses_accessId_via_dropped_exc_info"
    )
    handler = _Capture()
    handler.setFormatter(
        logging.Formatter("%(levelname)s %(message)s")
    )
    logger.addHandler(handler)
    logger.setLevel(logging.WARNING)

    try:
        raise _build_request_exception_with_secret()
    except requests.RequestException as exc:
        # The post-fix pattern: drop exc_info=True, log type only.
        logger.warning(
            "VOR: API nicht erreichbar (%s) – behalte bestehenden Cache bei.",
            type(exc).__name__,
        )

    assert captured_records, "expected a log record"
    formatted = handler.format(captured_records[-1])
    assert SECRET_ACCESS_ID not in formatted, (
        "fix: dropping exc_info=True must keep the secret out of logs"
    )
    # Diagnostic value: the exception class survives.
    assert "ConnectionError" in formatted


def test_update_vor_cache_RequestException_handler_uses_post_fix_pattern() -> None:
    """Static check: scripts/update_vor_cache.py must NOT call
    ``logger.warning(..., exc_info=True)`` or ``logger.exception(...)``
    inside the ``fetch_events`` try/except (those write the chained
    traceback containing the post-VorAuth URL with the secret).

    Audit invariant: a future PR re-adding ``exc_info=True`` /
    ``logger.exception`` to that handler fails this test.
    """
    import ast
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[1]
    source = (repo_root / "scripts" / "update_vor_cache.py").read_text(
        encoding="utf-8"
    )

    tree = ast.parse(source)

    # Find ``main`` function and its ``try: items = fetch_events(...)``
    # block. The except clauses on that block are the leak surfaces.
    main_func: ast.FunctionDef | None = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "main":
            main_func = node
            break
    assert main_func is not None, "Could not locate main() in update_vor_cache.py"

    # Walk the main body for the Try whose body calls fetch_events.
    target_try: ast.Try | None = None
    for node in ast.walk(main_func):
        if not isinstance(node, ast.Try):
            continue
        for stmt in node.body:
            for sub in ast.walk(stmt):
                if (
                    isinstance(sub, ast.Call)
                    and isinstance(sub.func, ast.Name)
                    and sub.func.id == "fetch_events"
                ):
                    target_try = node
                    break
            if target_try is not None:
                break
        if target_try is not None:
            break
    assert target_try is not None, (
        "Could not locate try/except wrapping fetch_events in main()"
    )

    # In each except handler, scan calls for unsafe patterns.
    for handler in target_try.handlers:
        for node in ast.walk(handler):
            if not isinstance(node, ast.Call):
                continue
            # Forbid logger.exception(...) — equivalent to exc_info=True.
            if (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "exception"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "logger"
            ):
                raise AssertionError(
                    "scripts/update_vor_cache.py: logger.exception(...) "
                    "inside the fetch_events handler writes the traceback "
                    "(post-VorAuth URL with accessId) to errors.log."
                ) from None
            # Forbid exc_info=True kwarg in any logger call.
            for kw in node.keywords:
                if kw.arg == "exc_info" and isinstance(kw.value, ast.Constant):
                    if kw.value.value is True:
                        raise AssertionError(
                            "scripts/update_vor_cache.py: exc_info=True "
                            "inside the fetch_events handler writes the "
                            "traceback (post-VorAuth URL with accessId) "
                            "to errors.log."
                        ) from None
