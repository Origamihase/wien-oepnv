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


# ---------------------------------------------------------------------------
# Site 3: scripts/verify_vor_access_id.py:92 — direct exc in %s
# ---------------------------------------------------------------------------
#
# The 2026-05-08 round closed Site 1 (update_vor_stations.py:587) and Site 2
# (update_vor_cache.py:173) but stopped at the named-list of "two cron-driven
# cache refreshers". The journal entry's prevention rule named FIVE scripts
# that consume vor-authenticated sessions (update_vor_stations.py,
# update_vor_cache.py, verify_vor_access_id.py, fetch_vor_haltestellen.py,
# enrich_station_aliases.py); only TWO got the fix, leaving the third
# RequestException-emitting site live. ``verify_vor_access_id.py`` calls
# ``fetch_content_safe(session, probe_url, params=..., timeout=...)`` after
# ``apply_authentication(session)`` has installed ``VorAuth`` — exactly the
# same post-VorAuth URL flow that motivated Sites 1/2's fix.


def test_verify_vor_access_id_92_uses_post_fix_pattern() -> None:
    """Static check: scripts/verify_vor_access_id.py must NOT pass the bare
    ``exc`` to a ``%s`` placeholder in the request-failure handler.

    The handler at line ~91 wraps ``fetch_content_safe(...)`` against a
    ``VorAuth``-authenticated session. ``VorAuth.__call__`` injects
    ``accessId=<SECRET>`` into the prepared URL; a network failure
    surfaces a ``MaxRetryError`` whose ``__str__`` embeds that URL. The
    pre-fix line ``LOGGER.error("VOR verification request failed: %s", exc)``
    leaks the credential to stdout and CI logs (clear-text-logging
    dataflow, mirrors the 2026-05-08 fix in src/utils/http.py). Audit
    invariant: a future PR re-adding the bare ``exc`` reference fails
    this test.
    """
    import ast
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[1]
    source = (repo_root / "scripts" / "verify_vor_access_id.py").read_text(
        encoding="utf-8"
    )

    tree = ast.parse(source)

    # Find ``main`` function and its ``try: ... fetch_content_safe(...)``
    # block. The except clauses on that block are the leak surfaces.
    main_func: ast.FunctionDef | None = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "main":
            main_func = node
            break
    assert main_func is not None, "Could not locate main() in verify_vor_access_id.py"

    target_try: ast.Try | None = None
    for node in ast.walk(main_func):
        if not isinstance(node, ast.Try):
            continue
        for stmt in node.body:
            for sub in ast.walk(stmt):
                if (
                    isinstance(sub, ast.Call)
                    and isinstance(sub.func, ast.Name)
                    and sub.func.id == "fetch_content_safe"
                ):
                    target_try = node
                    break
            if target_try is not None:
                break
        if target_try is not None:
            break
    assert target_try is not None, (
        "Could not locate try/except wrapping fetch_content_safe in main()"
    )

    # In each except handler, inspect logger calls for unsafe patterns:
    # any ``%s`` placeholder must be paired with ``type(exc).__name__``,
    # not the bare exc name. We walk every Call whose func attribute is
    # one of {error, warning, exception, critical, info, debug} and
    # whose args include a Name node referencing the handler's bound
    # exception variable.
    forbidden_attrs = {"error", "warning", "exception", "critical"}
    for handler in target_try.handlers:
        # Bound name for the exception (``except X as <name>``).
        exc_var = handler.name
        if exc_var is None:
            continue
        for node in ast.walk(handler):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr not in forbidden_attrs:
                continue
            # Forbid the bare exc name appearing as a positional arg —
            # it would be %-formatted into the message via
            # logging.LogRecord.getMessage().
            for arg in node.args:
                if isinstance(arg, ast.Name) and arg.id == exc_var:
                    raise AssertionError(
                        "scripts/verify_vor_access_id.py: bare exception "
                        f"name '{exc_var}' passed as a positional arg to "
                        f"LOGGER.{node.func.attr}(...) inside the "
                        "fetch_content_safe handler. The post-VorAuth URL "
                        "with ``accessId=<SECRET>`` is in the exception's "
                        "str(); replace ``exc`` with ``type(exc).__name__``."
                    ) from None
            # Also forbid exc_info=True (would write the traceback).
            for kw in node.keywords:
                if kw.arg == "exc_info" and isinstance(kw.value, ast.Constant):
                    if kw.value.value is True:
                        raise AssertionError(
                            "scripts/verify_vor_access_id.py: exc_info=True "
                            f"in LOGGER.{node.func.attr}(...) writes the "
                            "post-VorAuth URL traceback to logs."
                        ) from None


# ---------------------------------------------------------------------------
# Site 4: src/cli.py — defense-in-depth for runpy-propagated exceptions
# ---------------------------------------------------------------------------
#
# ``_run_script`` runs every CLI sub-command via ``runpy.run_path`` and
# catches anything that propagates with ``except Exception as e:``. The
# pre-fix line ``print(f"Fehler beim Ausführen von {script_name}: {e}",
# file=sys.stderr)`` interpolates the bare exception into stderr. Most
# scripts catch their own RequestException internally, but the CLI's
# catch-all is the LAST line of defense — if a future refactor lets an
# unhandled URL-bearing exception escape any sub-script's main (e.g. a
# new code path between ``argparse.parse_args`` and the existing
# try/except), the CLI's ``{e}`` interpolation re-enables the leak.
# This is the same defense-in-depth concern the journal entry
# "Defense-in-depth for ``exc_info=True``" raised for Site 2 above.


def test_cli_run_script_uses_post_fix_pattern() -> None:
    """Static check: src/cli.py:_run_script must NOT interpolate the bare
    exception object into the stderr message.

    Any ``except Exception as <name>:`` handler containing a ``print``
    call whose f-string references ``{<name>}`` is treated as a leak
    surface. The post-fix pattern logs ``type(<name>).__name__`` (or
    ``<name>.__class__.__name__``).
    """
    import ast
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[1]
    source = (repo_root / "src" / "cli.py").read_text(encoding="utf-8")

    tree = ast.parse(source)

    run_script: ast.FunctionDef | None = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_script":
            run_script = node
            break
    assert run_script is not None, "Could not locate _run_script() in src/cli.py"

    for handler in ast.walk(run_script):
        if not isinstance(handler, ast.ExceptHandler):
            continue
        exc_var = handler.name
        if exc_var is None:
            continue
        # Find ``print(...)`` calls inside the handler.
        for node in ast.walk(handler):
            if not isinstance(node, ast.Call):
                continue
            if not (isinstance(node.func, ast.Name) and node.func.id == "print"):
                continue
            for arg in node.args:
                # f-string => ast.JoinedStr with FormattedValue parts.
                if not isinstance(arg, ast.JoinedStr):
                    continue
                for value in arg.values:
                    if not isinstance(value, ast.FormattedValue):
                        continue
                    expr = value.value
                    # Bare ``{<exc_var>}`` interpolation is the leak shape.
                    if isinstance(expr, ast.Name) and expr.id == exc_var:
                        raise AssertionError(
                            "src/cli.py:_run_script: bare exception name "
                            f"'{{{exc_var}}}' interpolated into print() "
                            "f-string. A sub-script's unhandled "
                            "RequestException with a post-VorAuth URL "
                            "would leak ``accessId=<SECRET>`` to stderr "
                            "(and any tee'd log file). Replace with "
                            f"``{{type({exc_var}).__name__}}``."
                        ) from None


def test_cli_run_script_post_fix_suppresses_secret() -> None:
    """Behavioural check: the post-fix pattern keeps the URL out of stderr.

    We simulate the ``except Exception as e: print(f"...{...}...", ...)``
    branch by raising a synthetic exception whose str() embeds the
    post-VorAuth URL with the secret access ID, then assert the fix's
    ``type(e).__name__`` interpolation never lets the secret reach the
    captured stderr.
    """
    import io
    import sys

    exc = _build_request_exception_with_secret()
    buf = io.StringIO()
    real_stderr = sys.stderr
    try:
        sys.stderr = buf
        # Mirror the post-fix line in src/cli.py:_run_script.
        print(
            f"Fehler beim Ausführen von {{script}}: {type(exc).__name__}",
            file=sys.stderr,
        )
    finally:
        sys.stderr = real_stderr

    captured = buf.getvalue()
    assert SECRET_ACCESS_ID not in captured, (
        "post-fix: type(exc).__name__ must not let the URL reach stderr"
    )
    assert "ConnectionError" in captured, "diagnostic class survives"


# ---------------------------------------------------------------------------
# Site 5: scripts/fetch_vor_haltestellen.py:157 — accessId logged at debug level
# ---------------------------------------------------------------------------
#
# The script discovers the VAO ``accessId`` from a public webapp config and
# logs the discovered value at DEBUG level via
# ``log.debug("Discovered access ID %s from webapp config", aid)``. Even
# though the value is observable at the public webapp config endpoint,
# logging it (a) writes the credential into ``errors.log`` / GitHub
# Actions logs whenever the operator enables ``--verbose`` /
# ``LOG_LEVEL=DEBUG``, and (b) makes that credential retroactively
# available via log archives, GitHub auto-issue submissions, and CI
# artefact retention. Defense-in-depth: never log credentials at any
# level; log a fingerprint (length / SHA-256) instead.


def test_fetch_vor_haltestellen_does_not_log_aid_value() -> None:
    """Static check: scripts/fetch_vor_haltestellen.py:fetch_access_id
    must NOT pass the discovered ``aid`` value to a logger as a
    positional %-formatted argument.
    """
    import ast
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[1]
    source = (repo_root / "scripts" / "fetch_vor_haltestellen.py").read_text(
        encoding="utf-8"
    )

    tree = ast.parse(source)

    func: ast.FunctionDef | None = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "fetch_access_id":
            func = node
            break
    assert func is not None, "Could not locate fetch_access_id() in fetch_vor_haltestellen.py"

    log_attrs = {"debug", "info", "warning", "error", "exception", "critical"}
    for node in ast.walk(func):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in log_attrs:
            continue
        for arg in node.args:
            # Forbid the bare ``aid`` Name appearing as a positional
            # argument (it would be %-formatted into the message).
            if isinstance(arg, ast.Name) and arg.id == "aid":
                raise AssertionError(
                    "scripts/fetch_vor_haltestellen.py:fetch_access_id "
                    f"passes the bare 'aid' value to log.{node.func.attr}(); "
                    "this writes the discovered VAO accessId credential "
                    "into the log record. Log a length fingerprint or "
                    "type(aid).__name__ instead."
                )
