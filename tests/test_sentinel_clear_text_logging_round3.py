"""Sentinel PoC: Clear-Text-Logging Drift Round 3 in newly-introduced live providers.

The 2026-05-08 *Clear-Text-Logging Drift Round 2* round closed the
``except`` -> ``logger.<level>(..., exc)`` pattern in three named VOR-
adjacent sites (``verify_vor_access_id.py:92``, ``cli.py:_run_script:83``
and ``fetch_vor_haltestellen.py:fetch_access_id:157``). Its
*Framework catch-all rule* explicitly extended the closing-checklist to
**every framework-level catch-all that prints/logs an exception**
including subprocess wrappers, CLI runners and *any defensive
``except Exception``* in newly-introduced live providers — because the
catch-all is reached precisely when the inner handler doesn't run.

PR #1350 (*OSM-First Station Directory + GTFS-RT Stammstrecke Live
Provider*, ``f8de996``) introduced two new live HTTP-fetching modules
(``src/places/osm_client.py``, ``src/providers/gtfs_stammstrecke.py``)
and an OSM enrichment hook in ``scripts/update_station_directory.py``.
Re-running the closing-checklist's auto-discoverable AST walker
(``ExceptHandler.name`` referenced as a positional argument to a
``log[ger]?.<level>(...)`` call OR embedded into an f-string raise)
returns **NINE** new sites added by that PR that violate the Round 2
*Framework catch-all rule*:

    1. ``src/providers/gtfs_stammstrecke.py:219`` — bare ``exc`` in
       ``log.warning("Could not load scripts.gtfs.read_gtfs_stops: %s", exc)``.

    2. ``src/providers/gtfs_stammstrecke.py:224-228`` — bare ``exc``
       (``OSError, ValueError``) inside the GTFS stops file read.

    3. ``src/providers/gtfs_stammstrecke.py:360-365`` — bare ``exc`` in
       the defensive ``except Exception`` of ``_iter_trip_updates``.

    4. ``src/providers/gtfs_stammstrecke.py:395-400`` — bare ``exc`` in
       the defensive ``except Exception`` while iterating the
       ``stop_time_update`` list.

    5. ``src/providers/gtfs_stammstrecke.py:533-537`` — bare ``exc``
       caught from ``load_stop_id_index`` inside ``fetch_events``.

    6. ``src/places/osm_client.py:249`` — bare ``exc`` in the defensive
       ``except Exception`` of ``OSMOverpassClient.close()``.

    7. ``src/places/osm_client.py:288`` — RAISE-side embedding of bare
       ``str(ValueError)`` text into ``OSMOverpassError`` —
       ``raise OSMOverpassError(f"Overpass request rejected: {exc}")
       from exc``. The chained exception text PROPAGATES UPSTREAM via
       ``str(OSMOverpassError)`` to ``update_station_directory.py``'s
       framework catch-all (sites 8-9), where it is logged raw.

    8. ``scripts/update_station_directory.py:_enrich_with_osm`` line
       837 — ``logger.error("OSM Overpass enrichment failed: %s", exc)``;
       bare ``OSMOverpassError`` (which carries the embedded ValueError
       text from site 7).

    9. ``scripts/update_station_directory.py:_enrich_with_osm`` line
       840 — bare ``exc`` inside the defensive ``except Exception``.

Threat model
------------

The two upstream endpoints (``overpass-api.de``, ``realtime.oebb.at``)
are PUBLIC and carry no auth today, so the IMMEDIATE blast radius is
not credential leakage but **log-injection via control characters**:

* ``urllib3`` ``MaxRetryError.__str__`` and protobuf-parser exception
  messages can embed attacker-controlled bytes (the response body is
  parsed up to a bounded byte count, but the *parser*'s error message
  can quote raw bytes from the failure offset). When those bytes
  contain ``\\n`` / ``\\r`` / ANSI escape sequences, the bare ``%s, exc``
  pattern writes them into log lines verbatim — defeating post-hoc
  forensic analysis on the cron-runner logs and the auto-issue
  submission path in ``src/feed/reporting.py``.

* ``request_safe`` raises ``ValueError`` for SSRF / size / content-type
  failures with a *sanitised* URL embedded; ``OSMOverpassError`` then
  re-raises ``f"Overpass request rejected: {exc}"`` (site 7) which
  embeds that ValueError's ``str()`` raw. ``logger.error("...: %s",
  exc)`` (sites 8-9) writes the OSMOverpassError with the embedded
  ValueError text. Today's ``request_safe`` ValueErrors do not carry
  credentials in their text, but **defense-in-depth says the script
  must not RELY on that internal contract**: a future refactor that
  added auth to ``request_safe``'s URL canonicalisation (or that
  raised a different ``ValueError`` shape) would silently re-enable
  the leak.

The journal's *Framework catch-all rule* (Round 2 prevention rule (b))
codifies this exact threat model as a **defense-in-depth class** and
demands the same ``type(<name>).__name__`` (or
``sanitize_log_arg(str(exc))``) treatment as direct credential-bearing
handlers — because the catch-all is reached precisely when the inner
handler doesn't run.

Each test below first asserts the pre-fix pattern *would* propagate the
attacker-controlled bytes through the logger / chained exception, then
asserts the post-fix pattern (``sanitize_log_arg(str(exc))`` for
non-credential-bearing diagnostic context, or ``type(exc).__name__``
for the most conservative shape) keeps the bytes out.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Common fixtures
# ---------------------------------------------------------------------------

# Marker bytes that should never reach the log line. Combines a CR/LF
# pair (log-line forge), an ANSI escape sequence (terminal injection)
# and a sentinel ASCII payload ("ATTACKER_CTRL_BYTES") so the test
# assertions key off a single grep.
ATTACK_MARKER_PLAIN = "ATTACKER_CTRL_BYTES_DO_NOT_LEAK"
ATTACK_MARKER_NEWLINE = f"\nFAKE LOG LINE: {ATTACK_MARKER_PLAIN}"
ATTACK_MARKER_ANSI = f"\x1b[31m{ATTACK_MARKER_PLAIN}\x1b[0m"


def _capture_logger(name: str) -> tuple[logging.Logger, list[str], logging.Handler]:
    """Return a (logger, captured_messages, handler) tuple.

    The handler captures the FORMATTED message — i.e., what the logger
    would actually write to disk after % expansion. This mirrors what
    ``logging.LogRecord.getMessage()`` does internally.
    """
    captured: list[str] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append(record.getMessage())

    logger = logging.getLogger(name)
    handler = _Capture()
    logger.handlers = [handler]
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    return logger, captured, handler


# ---------------------------------------------------------------------------
# Site 1: src/providers/gtfs_stammstrecke.py:219
#   log.warning("Could not load scripts.gtfs.read_gtfs_stops: %s", exc)
# ---------------------------------------------------------------------------


def test_pre_fix_pattern_leaks_attack_bytes_via_bare_exc() -> None:
    """Pin the precondition: bare ``%s`` of an exception with attacker
    bytes embeds the bytes into the formatted log message verbatim.

    This is the canonical pre-fix shape that all nine sites share:
    ``logger.<level>(format, ..., exc)`` where ``%s`` ultimately calls
    ``str(exc)`` and writes the result into the log record. We
    materialise the formatted message to assert the leak shape WITHOUT
    routing the marker bytes through a real logger sink (which would
    correctly trip the log-injection detector — the very class this
    PR closes).
    """
    exc = OSError(ATTACK_MARKER_NEWLINE)

    formatted_message = f"Could not load scripts.gtfs.read_gtfs_stops: {exc!s}"

    assert ATTACK_MARKER_PLAIN in formatted_message, (
        "precondition: bare exc formatting embeds attacker-controlled bytes"
    )
    assert "\n" in formatted_message, (
        "precondition: bare exc formatting embeds the literal newline (forges log lines)"
    )


def test_post_fix_pattern_strips_log_injection_via_sanitize_log_arg() -> None:
    """The fix routes ``str(exc)`` through ``sanitize_log_arg``.

    ``sanitize_log_arg`` -> ``sanitize_log_message`` strips ANSI escape
    codes, redacts secret-shaped values, and escapes ``\\n`` / ``\\r`` /
    ``\\t`` so the formatted log line cannot forge a new line.
    """
    from src.utils.logging import sanitize_log_arg

    exc = OSError(ATTACK_MARKER_NEWLINE)

    sanitized = str(sanitize_log_arg(str(exc)))

    # Newlines must be escaped (literal "\n" tokens, not a real newline).
    assert "\n" not in sanitized, (
        "fix: sanitize_log_arg must escape real newlines into the literal sequence"
    )
    # The ANSI fixture variant must drop the escape sequence entirely.
    sanitized_ansi = str(sanitize_log_arg(ATTACK_MARKER_ANSI))
    assert "\x1b" not in sanitized_ansi, (
        "fix: sanitize_log_arg must strip ANSI escape codes"
    )
    # Diagnostic value: the plain-text marker SURVIVES so operators can
    # still triage the fault in the wild.
    assert ATTACK_MARKER_PLAIN in sanitized_ansi


# ---------------------------------------------------------------------------
# Static-check sites: verify each fix is in place (AST/source-level)
# ---------------------------------------------------------------------------


REPO_ROOT = Path(__file__).resolve().parents[1]


def _read_source(relpath: str) -> str:
    return (REPO_ROOT / relpath).read_text(encoding="utf-8")


def _find_bare_exc_logger_calls(source: str) -> list[tuple[int, str]]:
    """AST walk: find every `log[ger]?.<level>(...)` whose args contain
    a bare ``Name`` reference whose id starts with ``exc`` or matches a
    handler name, with no wrapping helper call around it.

    Returns a list of ``(line_number, source_excerpt)`` tuples for
    diagnostic reporting in test failures.
    """
    import ast

    tree = ast.parse(source)
    findings: list[tuple[int, str]] = []

    LOG_NAMES = {"log", "logger", "LOGGER"}
    LEVEL_METHODS = {"warning", "error", "exception", "info", "debug", "critical"}

    def is_logger_call(node: ast.Call) -> bool:
        if not isinstance(node.func, ast.Attribute):
            return False
        if node.func.attr not in LEVEL_METHODS:
            return False
        recv = node.func.value
        if isinstance(recv, ast.Name) and recv.id in LOG_NAMES:
            return True
        return False

    # Track exception-handler-bound names so we only flag ``exc`` /
    # ``e`` / etc. references that are inside an ``except ... as <name>:``
    # body. References outside an except-block (e.g., a variable that
    # happens to be named ``exc`` for unrelated reasons) are not the
    # leak shape.
    class _Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.handler_stack: list[str] = []

        def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
            if node.name:
                self.handler_stack.append(node.name)
                for child in node.body:
                    self.visit(child)
                self.handler_stack.pop()
            else:
                self.generic_visit(node)

        def visit_Call(self, node: ast.Call) -> None:
            if is_logger_call(node) and self.handler_stack:
                handler_names = set(self.handler_stack)
                for arg in node.args:
                    if isinstance(arg, ast.Name) and arg.id in handler_names:
                        line_no = node.lineno
                        excerpt = ast.unparse(node)[:200]
                        findings.append((line_no, excerpt))
                        break
            self.generic_visit(node)

    _Visitor().visit(tree)
    return findings


def test_gtfs_stammstrecke_no_bare_exc_logging() -> None:
    """Site 1-5: ``src/providers/gtfs_stammstrecke.py`` must not log bare
    ``exc`` as a ``%s`` argument anywhere.

    The fix shape replaces ``exc`` with ``sanitize_log_arg(str(exc))``
    (preserves diagnostic info, strips control chars / ANSI / secrets)
    OR with ``type(exc).__name__`` (most conservative).
    """
    source = _read_source("src/providers/gtfs_stammstrecke.py")
    findings = _find_bare_exc_logger_calls(source)

    assert not findings, (
        "src/providers/gtfs_stammstrecke.py contains bare-exc logger calls "
        "(Clear-Text-Logging Drift Round 3): "
        + "; ".join(f"line {ln}: {excerpt}" for ln, excerpt in findings)
    )


def test_osm_client_no_bare_exc_logging() -> None:
    """Site 6: ``src/places/osm_client.py`` must not log bare ``exc``.

    The fix shape replaces ``exc`` with ``sanitize_log_arg(str(exc))``
    (preserves diagnostic info while stripping injection vectors).
    """
    source = _read_source("src/places/osm_client.py")
    findings = _find_bare_exc_logger_calls(source)

    assert not findings, (
        "src/places/osm_client.py contains bare-exc logger calls "
        "(Clear-Text-Logging Drift Round 3): "
        + "; ".join(f"line {ln}: {excerpt}" for ln, excerpt in findings)
    )


def test_update_station_directory_enrich_with_osm_no_bare_exc_logging() -> None:
    """Sites 8-9: ``scripts/update_station_directory.py:_enrich_with_osm``
    must not log bare ``exc``.

    The function bodies of ``_enrich_with_osm`` (introduced by
    PR #1350) contain TWO bare-exc logger calls — both inside the
    ``try: places = fetch_osm_places()`` block. The walker is scoped to
    the function body to avoid colliding with pre-existing bare-exc
    sites elsewhere in this script (those are out of scope for this
    round).
    """
    import ast

    source = _read_source("scripts/update_station_directory.py")
    tree = ast.parse(source)

    target_func: ast.FunctionDef | None = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_enrich_with_osm":
            target_func = node
            break
    assert target_func is not None, (
        "scripts/update_station_directory.py must define _enrich_with_osm "
        "(introduced by PR #1350)"
    )

    body_source = ast.unparse(target_func)
    findings = _find_bare_exc_logger_calls(body_source)

    assert not findings, (
        "scripts/update_station_directory.py:_enrich_with_osm contains bare-exc "
        "logger calls (Clear-Text-Logging Drift Round 3): "
        + "; ".join(f"line {ln}: {excerpt}" for ln, excerpt in findings)
    )


# ---------------------------------------------------------------------------
# Site 7: src/places/osm_client.py:288 — RAISE-side OSMOverpassError chain
# ---------------------------------------------------------------------------


def test_osm_client_does_not_embed_value_error_text_in_overpass_error() -> None:
    """Site 7: ``OSMOverpassError`` must NOT embed the str() of a chained
    ValueError.

    Pre-fix at ``_fetch_payload``::

        except ValueError as exc:
            raise OSMOverpassError(f"Overpass request rejected: {exc}") from exc

    The ``{exc}`` interpolation embeds the ValueError's full ``str()``
    into the OSMOverpassError message — which then PROPAGATES UPSTREAM
    via ``str(OSMOverpassError)`` to ``update_station_directory.py``'s
    framework catch-all where it is logged. Defense-in-depth: replace
    the embed with ``type(exc).__name__`` so future ValueError shapes
    (with credential-bearing text) cannot silently re-enable a leak.
    """
    source = _read_source("src/places/osm_client.py")

    # Find every "raise OSMOverpassError(f"..." block.
    risky = re.findall(
        r"raise\s+OSMOverpassError\(f[\"'][^\"']*\{exc[^}]*\}",
        source,
    )
    assert not risky, (
        "src/places/osm_client.py raises OSMOverpassError with embedded "
        "{exc} interpolation (Clear-Text-Logging Drift Round 3 site 7): "
        + "; ".join(risky)
    )


def test_post_fix_osm_client_propagation_drops_attack_bytes() -> None:
    """End-to-end behavioural check: when ``request_safe`` raises a
    ValueError carrying attacker bytes, the OSMOverpassError surfaces
    only the type name; the upstream logger sees only sanitised content.
    """
    from src.places import osm_client

    config = osm_client.OSMOverpassConfig(
        endpoint="https://overpass-api.de/api/interpreter",
        user_agent="test-ua/1.0 (+https://example.invalid)",
    )

    poisoned_value_error = ValueError(
        f"sanitised={ATTACK_MARKER_NEWLINE}; injection={ATTACK_MARKER_ANSI}"
    )

    with patch.object(osm_client, "request_safe", side_effect=poisoned_value_error):
        client = osm_client.OSMOverpassClient(config)
        with pytest.raises(osm_client.OSMOverpassError) as exc_info:
            client.fetch_stations()

    surfaced_message = str(exc_info.value)

    # The marker bytes must NOT appear in the surfaced exception text —
    # because ``str(OSMOverpassError)`` is what the upstream framework
    # catch-all in update_station_directory.py logs.
    assert ATTACK_MARKER_PLAIN not in surfaced_message, (
        "fix: OSMOverpassError must not embed the chained ValueError text"
    )
    assert "\n" not in surfaced_message, (
        "fix: OSMOverpassError must not propagate raw newlines from a "
        "chained ValueError (log-injection vector)"
    )
    assert "\x1b" not in surfaced_message, (
        "fix: OSMOverpassError must not propagate raw ANSI escape codes"
    )


# ---------------------------------------------------------------------------
# End-to-end behavioural check for the gtfs_stammstrecke catch sites
# ---------------------------------------------------------------------------


def test_post_fix_gtfs_stammstrecke_logs_strip_attack_bytes() -> None:
    """End-to-end: the GTFS-RT cache update script must not write
    attacker-controlled bytes from a chained-OSError through to the
    cron logger.

    The catch site moved with the refactor that promoted the
    Stammstrecke monitor to the standard cache-driven architecture
    (PR ``claude/refactor-gtfs-rt-cache-7mAbe``): the GTFS reader's
    OSError is now caught inside ``scripts/update_gtfs_cache.py``
    rather than the read-side provider. The sanitisation invariant
    is unchanged — every ``except (OSError, ValueError)`` body that
    logs the bound name MUST route through ``sanitize_log_arg``.
    """
    import scripts.gtfs  # noqa: F401 — ensure the module is importable
    from scripts import update_gtfs_cache

    poisoned = OSError(
        f"sanitised={ATTACK_MARKER_NEWLINE}; injection={ATTACK_MARKER_ANSI}"
    )

    logger, captured, _ = _capture_logger("test_update_gtfs_cache_post_fix")

    with patch.object(update_gtfs_cache, "LOGGER", logger):
        with patch("scripts.gtfs.read_gtfs_stops", side_effect=poisoned):
            result = update_gtfs_cache.load_stop_id_index(
                stops_path=Path("/nonexistent/stops.txt"),
                station_names=("Wien Mitte",),
            )

    # The catch returned a fresh empty index (the heal path).
    assert result == {"Wien Mitte": frozenset()}

    # The poisoned bytes must NOT appear in any captured message —
    # neither as a raw newline (forges log lines) nor as an ANSI
    # escape sequence (terminal injection).
    full_log = "\n".join(captured)
    assert "\x1b" not in full_log, (
        "fix: ANSI escape codes must be stripped from log output"
    )
    for record in captured:
        assert "\n" not in record, (
            "fix: real newlines in exception text must be escaped at the "
            f"sanitisation boundary (record={record!r})"
        )


# ---------------------------------------------------------------------------
# Inventory walker (audit guard against future drift)
# ---------------------------------------------------------------------------


def test_no_bare_exc_logging_in_pr1350_modules() -> None:
    """Inventory walker: scan the three PR #1350 modules and assert NO
    bare-exc logger call exists anywhere.

    This is the auto-discoverable invariant the journal's *Framework
    catch-all rule* (Round 2 prevention rule (b)) demands: every
    framework-level catch-all in newly-introduced live providers MUST
    follow the same ``type(<name>).__name__`` /
    ``sanitize_log_arg(str(<name>))`` rule as direct credential-bearing
    handlers. Any future regression fails this test at PR-review time.
    """
    targets = [
        "src/providers/gtfs_stammstrecke.py",
        "src/places/osm_client.py",
        # The Stammstrecke cache-update script inherited the network-fetch
        # logic when the provider was demoted to cache-only. The
        # sanitisation invariant follows the catch sites — see
        # ``test_post_fix_gtfs_stammstrecke_logs_strip_attack_bytes``.
        "scripts/update_gtfs_cache.py",
    ]
    failures: list[str] = []
    for relpath in targets:
        source = _read_source(relpath)
        for line_no, excerpt in _find_bare_exc_logger_calls(source):
            failures.append(f"{relpath}:{line_no}: {excerpt}")

    assert not failures, (
        "Bare-exc logger calls survive in PR #1350 modules "
        "(Clear-Text-Logging Drift Round 3): " + "; ".join(failures)
    )
