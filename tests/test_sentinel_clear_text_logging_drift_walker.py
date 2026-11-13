"""Clear-Text-Logging Drift, Layer 2: the inventory walker.

Second half of ``test_sentinel_clear_text_logging_drift_utils.py`` (the
threat model and the unit-level PoC tests of Layer 1 stay there).  This
module holds ``WALKER_MODULES``, the AST helpers and the two walker tests.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]


# ----------------------------------------------------------------------
# Layer 2: inventory walker
# ----------------------------------------------------------------------

# Modules that the journal commits to keeping clear-text-logging-clean.
# Each path is relative to REPO_ROOT.  Adding a module here makes every
# bound-exception logging site in it part of the regression contract.
WALKER_MODULES: tuple[str, ...] = (
    "src/utils/cache.py",
    "src/utils/locking.py",
    "src/utils/http.py",
    # Parts of http.py, reporting.py, vor.py, oebb.py and wl_fetch.py
    # (700-line limit, 2026-11-03), also those without log calls today.
    "src/utils/http_safety/__init__.py",
    "src/utils/http_safety/adapters.py",
    "src/utils/http_safety/ip_guard.py",
    "src/utils/http_safety/limits.py",
    "src/utils/http_safety/peer.py",
    "src/utils/http_safety/redaction.py",
    "src/utils/http_safety/request_steps.py",
    "src/utils/http_safety/sessions.py",
    "src/utils/http_safety/url_guard.py",
    "src/build_feed.py",
    *sorted(str(p.relative_to(REPO_ROOT)) for p in (REPO_ROOT / "src" / "feed_build").glob("*.py")),  # its parts (Etappe 8)
    # Extended in the post-Scribe Sentinel sweep (2026-05-08): every
    # production module whose ``except Exception as exc`` paths log
    # the bound exception now appears in this list. A regression here
    # means a future contributor either added a new bare-exc logging
    # site or removed an existing ``sanitize_log_arg`` wrap.
    "src/feed/providers.py",
    "src/feed/reporting.py",
    "src/feed/report_messages.py",
    "src/feed/feed_health.py",
    "src/feed/github_issue.py",
    "src/providers/vor.py",
    "src/providers/vor_auth.py",
    "src/providers/vor_env.py",
    "src/providers/vor_quota.py",
    "src/providers/oebb.py",
    "src/providers/oebb_title.py",
    "src/providers/oebb_routes.py",
    "src/providers/oebb_scope.py",
    "src/providers/oebb_relevance.py",
    "src/providers/oebb_route_title.py",
    "src/providers/wl_fetch.py",
    "src/providers/wl_api.py",
    "src/providers/wl_item_fields.py",
    "src/providers/wl_source_outage.py",
    "src/providers/wl_ticker_fold.py",
    "src/providers/wl_title_context.py",
    "src/providers/wl_aggregates.py",
    # Forward-defensive (2026-05-08): ``geo.py`` currently has no log
    # calls (it's pure math + ValueError on invalid input), but
    # locking it into the walker now prevents a future contributor
    # from adding an unsanitised ``log.warning("bad coords: %s", exc)``
    # in an ``except`` branch when extending the helper.
    "src/utils/geo.py",
)


def _is_log_call(node: ast.Call) -> str | None:
    """Return the log method name (``warning`` / ``error`` / ``debug`` /
    ``info`` / ``critical``) for a logger-style call, else ``None``.

    Recognises the canonical project shape ``log.<level>(...)`` /
    ``logger.<level>(...)`` / ``LOGGER.<level>(...)`` and the
    ``self.log.<level>(...)`` / ``cls.log.<level>(...)`` shapes.

    ``log.exception(...)`` is intentionally excluded — it routes through
    Python's logging framework's traceback formatter, which renders the
    bound exception via ``logging.Formatter.formatException`` (a
    repr-escaping path) rather than via ``%s`` on the raw arg.
    """
    func = node.func
    if not isinstance(func, ast.Attribute):
        return None
    if func.attr not in {"warning", "error", "debug", "info", "critical"}:
        return None
    receiver = func.value
    if isinstance(receiver, ast.Name) and receiver.id in {"log", "logger", "LOGGER"}:
        return func.attr
    if isinstance(receiver, ast.Attribute) and receiver.attr in {"log", "logger", "LOGGER"}:
        return func.attr
    return None


def _is_sanitized(arg: ast.expr) -> bool:
    """Return True iff *arg* is a call to ``sanitize_log_arg`` /
    ``sanitize_log_message`` / ``sanitize_message`` (the project's
    canonical sanitiser names).

    Also accepts the constant-shape calls that cannot ferry control
    characters: ``type(exc).__name__``, ``f"… {type(exc).__name__}"``
    expressions, and bare attribute access on the exception (e.g.
    ``exc.errno``) — those project-known safe shapes are out of scope
    for this walker by design.
    """
    if isinstance(arg, ast.Call):
        func = arg.func
        if isinstance(func, ast.Name) and func.id in {
            "sanitize_log_arg",
            "sanitize_log_message",
            "sanitize_message",
        }:
            return True
        if isinstance(func, ast.Attribute) and func.attr in {
            "sanitize_log_arg",
            "sanitize_log_message",
            "sanitize_message",
        }:
            return True
        # ``type(exc).__name__`` is a safe shape — type names contain
        # only Python identifier characters.
        if (
            isinstance(func, ast.Name)
            and func.id == "type"
            and isinstance(arg.func, ast.Name)
        ):
            return True
    if isinstance(arg, ast.Attribute):
        # ``exc.errno`` / ``exc.args[0]`` etc. — bare attribute access
        # is treated as opaque-but-not-string, so we trust the caller.
        # We only care about the argument NAME bound to ``as exc`` /
        # ``as e``; these attribute accesses target a different value.
        if arg.attr in {"errno", "winerror", "filename", "filename2", "strerror"}:
            return True
    if isinstance(arg, ast.Constant):
        return True
    return False


def _bound_exception_names(handler: ast.ExceptHandler) -> set[str]:
    """Return the set of names introduced by ``except X as <name>``."""
    if handler.name is None:
        return set()
    return {handler.name}


def _arg_references_exception(arg: ast.expr, exc_names: set[str]) -> bool:
    """Return True iff *arg* (recursively) references one of the bound
    exception names from the enclosing ``except`` clauses.

    Walks into f-strings (``ast.JoinedStr`` / ``ast.FormattedValue``)
    so that ``f"...: {exc}"`` is recognised as carrying the exception.
    A bare ``ast.Name`` whose ``.id`` matches one of *exc_names* counts
    as a direct reference.
    """
    if isinstance(arg, ast.Name) and arg.id in exc_names:
        return True
    if isinstance(arg, ast.JoinedStr):
        for piece in arg.values:
            if isinstance(piece, ast.FormattedValue):
                if _arg_references_exception(piece.value, exc_names):
                    return True
        return False
    if isinstance(arg, ast.Call):
        # ``str(exc)`` / ``repr(exc)`` / ``f"{exc}"`` — these all
        # carry the exception's text into the log unsanitised.  We
        # treat them as references unless the call is itself a
        # sanitiser.
        if _is_sanitized(arg):
            return False
        for child in [arg.func, *arg.args, *(kw.value for kw in arg.keywords)]:
            if _arg_references_exception(child, exc_names):
                return True
        return False
    if isinstance(arg, ast.BinOp):
        return _arg_references_exception(
            arg.left, exc_names
        ) or _arg_references_exception(arg.right, exc_names)
    return False


def _enclosing_handler_names(
    parents: dict[int, ast.AST], node: ast.AST
) -> set[str]:
    """Walk upward to the smallest enclosing ``except`` clause and return
    its bound exception names (the ``as <name>`` part)."""
    cur = parents.get(id(node))
    while cur is not None:
        if isinstance(cur, ast.ExceptHandler):
            return _bound_exception_names(cur)
        cur = parents.get(id(cur))
    return set()


def _build_parent_map(tree: ast.AST) -> dict[int, ast.AST]:
    parents: dict[int, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[id(child)] = parent
    return parents


def _audit_module(path: Path) -> list[tuple[int, str]]:
    findings: list[tuple[int, str]] = []
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    parents = _build_parent_map(tree)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        method = _is_log_call(node)
        if method is None:
            continue
        exc_names = _enclosing_handler_names(parents, node)
        if not exc_names:
            continue
        # Inspect every positional and keyword argument that is *not* the
        # format string (the first positional arg in the project's
        # logging convention).  Skip the format string itself.
        candidate_args = list(node.args[1:]) + [kw.value for kw in node.keywords]
        for arg_idx, arg in enumerate(candidate_args, start=1):
            if not _arg_references_exception(arg, exc_names):
                continue
            if _is_sanitized(arg):
                continue
            findings.append(
                (
                    node.lineno,
                    f"log.{method}(...) at arg #{arg_idx} interpolates "
                    f"bound exception {sorted(exc_names)} via raw %s/%r — "
                    "must route through sanitize_log_arg / "
                    "sanitize_log_message",
                )
            )
    return findings


def test_no_bare_exc_logging_in_clear_text_logging_modules() -> None:
    """Inventory walker: every WARNING/ERROR/INFO/DEBUG/CRITICAL log call
    in the closing-grep module list whose enclosing ``except`` clause
    binds a name MUST route the bound name through ``sanitize_log_arg``
    (or one of the recognised sanitisers).

    A regression here means a future contributor added a new bare-exc
    logging site that lets attacker-controlled exception text bleed
    into log lines, cache-alert hooks, and operator dashboards.
    """
    all_findings: list[tuple[Path, int, str]] = []
    for module in WALKER_MODULES:
        path = REPO_ROOT / module
        assert path.exists(), f"WALKER_MODULES references missing path: {module}"
        for lineno, reason in _audit_module(path):
            all_findings.append((path, lineno, reason))

    if not all_findings:
        return

    rendered = "\n".join(
        f"  {p.relative_to(REPO_ROOT)}:{lineno}: {reason}"
        for p, lineno, reason in all_findings
    )
    pytest.fail(
        f"{len(all_findings)} clear-text-logging drift site(s) in the "
        "closing-grep module list:\n"
        f"{rendered}\n\n"
        "Each site must wrap the bound-exception name in "
        "``sanitize_log_arg(str(exc))`` (or equivalent) so a hostile "
        "exception text cannot smuggle ANSI/BiDi/control characters "
        "into operator-facing logs.  Mirrors the PR #1351 hardening "
        "applied to the OSM and Stammstrecke providers."
    )


def test_walker_recognises_sanitised_and_unsanitised_shapes() -> None:
    """Smoke-test: the walker must distinguish the two canonical
    shapes documented in the docstring of
    ``test_sentinel_clear_text_logging_drift_utils.py``.

    Pin the precondition that the inventory walker fires on a bare
    ``log.warning("...: %s", exc)`` and accepts a wrapped
    ``log.warning("...: %s", sanitize_log_arg(exc))``.
    """
    sample_unsanitised = """\
import logging
log = logging.getLogger(__name__)

def f():
    try:
        x = 1
    except OSError as exc:
        log.warning("Failed: %s", exc)
"""
    sample_sanitised = """\
import logging
log = logging.getLogger(__name__)

def sanitize_log_arg(arg):
    return str(arg)

def f():
    try:
        x = 1
    except OSError as exc:
        log.warning("Failed: %s", sanitize_log_arg(exc))
"""

    def _findings_for(source: str) -> list[tuple[int, str]]:
        tree = ast.parse(source)
        parents = _build_parent_map(tree)
        out: list[tuple[int, str]] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            method = _is_log_call(node)
            if method is None:
                continue
            exc_names = _enclosing_handler_names(parents, node)
            if not exc_names:
                continue
            for arg in list(node.args[1:]) + [kw.value for kw in node.keywords]:
                if (
                    _arg_references_exception(arg, exc_names)
                    and not _is_sanitized(arg)
                ):
                    out.append((node.lineno, "bare"))
        return out

    assert _findings_for(sample_unsanitised), (
        "Walker must fire on bare-exc logging shape"
    )
    assert not _findings_for(sample_sanitised), (
        "Walker must accept the sanitize_log_arg-wrapped shape"
    )
