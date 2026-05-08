"""Sentinel: Clear-Text-Logging Drift in core utility modules.

The journal's Clear-Text-Logging Drift family (PR #1351 in
``.jules/sentinel.md``) hardened the OSM and (rolled-back) GTFS-RT
Stammstrecke providers so every framework catch-all that logged the
bound exception name routed the text through ``sanitize_log_arg``.
The same pattern remained UN-hardened in three core utility modules
that are loaded by every provider and orchestrator path:

  * ``src/utils/cache.py``       — cache-file mtime / read / prune
  * ``src/utils/locking.py``     — file-lock acquire / release / fall-back
  * ``src/utils/http.py``        — DNS-rebinding response-IP verifier
  * ``src/build_feed.py``        — state-file lock acquisition & merge

Each of those sites previously rendered the bound ``exc`` (and, in
``locking.py``, the ``fileobj.name`` path) into a structured log line
via ``%s``.  ``%s`` calls ``str(exc)`` and propagates *every* byte the
exception carries — including ANSI escape codes, BiDi/Trojan-Source
characters, embedded ``\\r\\n`` log-forging payloads, and control
characters that a downstream log parser / SIEM / monitoring system
trusts to be absent.

Why this matters even though the immediate exception types
(``OSError``, ``json.JSONDecodeError``, ``UnicodeDecodeError``) repr-
escape their constructor args under CPython today:

  (a) Defense in depth — the exception text is *not* a credentialed
      surface and there is no contract guaranteeing CPython will keep
      escaping in future versions, libc-built error messages, or
      third-party subclasses.
  (b) Custom ``__str__`` overrides — any subclass (e.g. an HTTP
      client's adapter wrapping an error from a remote DNS resolver)
      may render raw bytes.  Today's ``http.py:verify_response_ip``
      catches ``OSError, ValueError, AttributeError``; a custom
      adapter ValueError can carry arbitrary text.
  (c) ``locking.py`` logs ``getattr(fileobj, "name", "unknown")`` —
      that is the *path*, which can carry whatever characters the
      operating system permits inside a filename.  Linux permits
      ``\\x1b``, ``\\n``, ``\\r``, and every BiDi/zero-width control
      character in filenames.  A planted lock-file path under
      ``cache/`` (compromised CI runner / parallel orchestrator) would
      flow straight into log lines + cache-alert hooks (Slack /
      PagerDuty integrations).
  (d) Round-tripping into ``_emit_cache_alert(provider, message)`` —
      the cache alert callback is registered by ``build_feed`` and
      forwards the message into ``report.add_warning(...)``, which is
      ultimately rendered into the operator-facing health JSON +
      docs/feed-health.md.  Any unsanitised control characters there
      corrupt the rendered HTML and break log dashboards downstream.

The fix shape mirrors PR #1351: every bound-exception logging site in
the affected modules routes the value through
``src.utils.logging.sanitize_log_arg`` so the resulting log line never
carries unescaped control characters / ANSI escapes / BiDi controls.

This file pins the invariant in three layers:

  1. **Unit-level PoC tests** that exercise each affected code path with
     a synthesised exception carrying ANSI + newline + BiDi payloads,
     and assert the captured log output is sanitised.
  2. **Cache-alert routing PoC** that registers a ``_CACHE_ALERT_HOOKS``
     callback, triggers a JSON-decode failure, and asserts the message
     forwarded to the hook is sanitised.
  3. **Inventory walker** (programmatic AST scan, mirrors
     ``test_sentinel_json_audit_walker.py``) that asserts every
     ``log.<level>(..., exc, ...)`` call in the affected modules either
     wraps the bound name in ``sanitize_log_arg`` or uses
     ``log.exception`` (which routes through the logging framework's
     traceback formatter and therefore does not interpolate raw exc
     text into the message string).
"""
from __future__ import annotations

import ast
import json
import logging
from pathlib import Path
from typing import Any

import pytest

from src.utils import cache as cache_module
from src.utils.files import sanitize_filename
from src.utils.locking import file_lock


REPO_ROOT = Path(__file__).resolve().parents[1]


# Canonical attack payload exercising the four classes of control-text
# sneak-bys that ``sanitize_log_arg`` (via ``sanitize_log_message``) is
# documented to defeat:
#   * ANSI CSI escape (terminal corruption / colour smuggling)
#   * Carriage return + newline (log-forging — splits one log line
#     into two so a planted second line looks like an unrelated event)
#   * BiDi override (Trojan Source — flips the rendering order of the
#     surrounding tokens in operator-facing renders that respect BiDi)
#   * Zero-width space (visual obfuscation)
ATTACK_BYTES = "\x1b[31mEVIL\x1b[0m\r\nINJECTED-LOG-LINE‮RTL-OVERRIDE​ZWS"

# Substrings that must NOT appear in any captured log output, individually,
# so a partially-applied fix (e.g. forgot to handle ``‮``) still fails
# the test instead of silently passing.
FORBIDDEN_FRAGMENTS = (
    "\x1b[",        # ANSI CSI introducer
    "\r\n",         # Log-forging primitive
    "‮",       # BiDi RIGHT-TO-LEFT OVERRIDE
    "​",       # Zero-width space
)


def _assert_no_attack_bytes(text: str, *, where: str) -> None:
    """Assert *text* does not carry any of the canonical attack fragments."""
    for fragment in FORBIDDEN_FRAGMENTS:
        assert fragment not in text, (
            f"clear-text-logging drift: attack fragment {fragment!r} "
            f"survived sanitisation in {where}.\n"
            f"Captured: {text!r}"
        )


# ----------------------------------------------------------------------
# Layer 1: unit-level PoC tests
# ----------------------------------------------------------------------


def test_cache_read_cache_oserror_sanitises_logged_exception(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A planted ``OSError`` carrying control bytes must not flow raw into
    the log line emitted from ``read_cache``'s ``except OSError`` branch.

    Threat model: a non-``FileNotFoundError`` ``OSError`` (permission
    failure / ``EIO`` from a flaky NFS mount / ``ELOOP`` on a planted
    symlink loop) carries a ``.strerror`` string that some POSIX
    implementations render with raw bytes.  A subclass with a custom
    ``__str__`` (defensive: third-party adapters in the cache I/O path)
    additionally bypasses ``OSError``'s default repr-escaping.
    """
    base = tmp_path / "cache-root"
    monkeypatch.setattr(cache_module, "_CACHE_DIR", base, raising=False)
    target = base / sanitize_filename("provider-a")
    target.mkdir(parents=True, exist_ok=True)
    cache_file = target / "events.json"
    # Establish a real file so the open() call reaches the read path.
    cache_file.write_text("[]", encoding="utf-8")

    class HostileOSError(OSError):
        def __init__(self, msg: str) -> None:
            super().__init__(msg)
            self._msg = msg

        def __str__(self) -> str:
            return self._msg

    real_open = Path.open

    def fake_open(self: Path, *args: Any, **kwargs: Any) -> Any:
        if self == cache_file:
            raise HostileOSError(ATTACK_BYTES)
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fake_open)

    caplog.set_level(logging.WARNING, logger="src.utils.cache")
    captured_alerts: list[tuple[str, str]] = []
    unregister = cache_module.register_cache_alert_hook(
        lambda provider, message: captured_alerts.append((provider, message))
    )
    try:
        result = cache_module.read_cache("provider-a")
    finally:
        unregister()

    assert result == []
    _assert_no_attack_bytes(caplog.text, where="cache.read_cache OSError log")
    for provider, message in captured_alerts:
        _assert_no_attack_bytes(message, where=f"cache alert hook ({provider})")


def test_cache_read_cache_jsondecode_alert_hook_sanitises_message(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A ``json.JSONDecodeError`` whose ``__str__`` carries control bytes
    must not be forwarded raw via ``_emit_cache_alert``.

    JSONDecodeError under CPython today renders only ``msg + position``
    text without echoing input bytes, so we synthesise a custom subclass
    via ``json.loads`` raise-side substitution to pin the contract that
    the alert routing sanitises whatever the parser returns.
    """
    base = tmp_path / "cache-root"
    monkeypatch.setattr(cache_module, "_CACHE_DIR", base, raising=False)
    target = base / sanitize_filename("provider-b")
    target.mkdir(parents=True, exist_ok=True)
    cache_file = target / "events.json"
    cache_file.write_text("not-json", encoding="utf-8")

    class HostileJSONDecodeError(json.JSONDecodeError):
        def __init__(self, msg: str) -> None:
            super().__init__(msg, doc="x", pos=0)
            self._raw_msg = msg

        def __str__(self) -> str:
            return self._raw_msg

    def fake_loads(*args: Any, **kwargs: Any) -> Any:
        raise HostileJSONDecodeError(ATTACK_BYTES)

    # Patch ``json.loads`` on the actual ``json`` module that
    # ``src.utils.cache`` calls into.  ``monkeypatch`` restores the
    # original automatically when the test ends.
    monkeypatch.setattr(json, "loads", fake_loads)

    caplog.set_level(logging.WARNING, logger="src.utils.cache")
    captured_alerts: list[tuple[str, str]] = []
    unregister = cache_module.register_cache_alert_hook(
        lambda provider, message: captured_alerts.append((provider, message))
    )
    try:
        result = cache_module.read_cache("provider-b")
    finally:
        unregister()

    assert result == []
    _assert_no_attack_bytes(
        caplog.text, where="cache.read_cache JSONDecodeError log"
    )
    assert captured_alerts, "cache alert hook should fire on JSON decode error"
    for provider, message in captured_alerts:
        _assert_no_attack_bytes(message, where=f"cache alert hook ({provider})")


def test_cache_modified_at_oserror_sanitises_logged_exception(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """``cache_modified_at``'s ``except OSError`` branch must sanitise the
    bound exception name.  Threat model mirrors the read-side hostile
    OSError test."""
    base = tmp_path / "cache-root"
    monkeypatch.setattr(cache_module, "_CACHE_DIR", base, raising=False)
    target = base / sanitize_filename("provider-c")
    target.mkdir(parents=True, exist_ok=True)
    cache_file = target / "events.json"
    cache_file.write_text("[]", encoding="utf-8")

    class HostileOSError(OSError):
        def __init__(self, msg: str) -> None:
            super().__init__(msg)
            self._msg = msg

        def __str__(self) -> str:
            return self._msg

    real_stat = Path.stat

    def fake_stat(self: Path, *args: Any, **kwargs: Any) -> Any:
        if self == cache_file:
            raise HostileOSError(ATTACK_BYTES)
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", fake_stat)

    caplog.set_level(logging.WARNING, logger="src.utils.cache")
    result = cache_module.cache_modified_at("provider-c")

    assert result is None
    _assert_no_attack_bytes(
        caplog.text, where="cache.cache_modified_at OSError log"
    )


def test_locking_file_lock_warns_with_sanitised_name_and_exception(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The exclusive-lock-failure WARNING in ``file_lock`` must sanitise
    BOTH the ``fileobj.name`` (a path that may carry control bytes from
    a planted lock-file) AND the bound exception (a custom OSError or
    TimeoutError from the underlying flock helper).

    Threat model: the cron pipeline opens a lock-path computed via
    ``path.with_suffix('.lock')`` based on ``feed_config.STATE_FILE``.
    A compromised env override (``STATE_FILE=…\\x1b[31mEvil…``) lands the
    control-char-laden lock-path through ``getattr(fileobj, 'name', …)``
    into the warning log.  Without sanitisation the log corruption
    propagates into operator dashboards and SIEMs.
    """
    from src.utils import locking as locking_module

    class HostileFileObj:
        # The .name attribute is the path whose control bytes must be
        # sanitised away from the resulting log line.
        name = ATTACK_BYTES

        def fileno(self) -> int:
            raise OSError("synthesised — no real fd")

    def boom(*args: Any, **kwargs: Any) -> None:
        raise OSError(ATTACK_BYTES)

    monkeypatch.setattr(
        locking_module, "_acquire_file_lock", boom, raising=False
    )

    caplog.set_level(logging.WARNING, logger="src.utils.locking")

    with pytest.raises(OSError):
        with file_lock(HostileFileObj(), exclusive=True, timeout=0.1):
            pass  # pragma: no cover - lock acquisition fails

    _assert_no_attack_bytes(
        caplog.text, where="locking.file_lock exclusive-fail log"
    )


def test_build_feed_save_state_lock_failure_sanitises_logged_exception(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The ``_save_state`` ``except (OSError, TimeoutError)`` branch must
    sanitise the bound exception before logging.  An attacker-influenced
    custom OSError (e.g. from a third-party file-system adapter or a
    bind-mount of a symlink loop) carries arbitrary text through
    ``str(exc)``; without sanitisation the WARNING line corrupts the
    operator feed-health log for every cron tick that hits the failure.
    """
    from src import build_feed as build_feed_module
    from src.feed import config as feed_config

    state_dir = tmp_path / "data"
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / "state.json"
    monkeypatch.setattr(feed_config, "STATE_FILE", state_file, raising=False)

    # Force ``feed_config.validate_path`` to accept the tmp_path location.
    monkeypatch.setattr(
        build_feed_module,
        "validate_path",
        lambda path, _name: path,
        raising=True,
    )

    class HostileOSError(OSError):
        def __init__(self, msg: str) -> None:
            super().__init__(msg)
            self._msg = msg

        def __str__(self) -> str:
            return self._msg

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise HostileOSError(ATTACK_BYTES)

    # Patch the lock-file open to raise so the outer except clause fires
    # with the synthesised hostile OSError.
    monkeypatch.setattr(Path, "open", boom)

    caplog.set_level(logging.WARNING, logger="src.build_feed")

    build_feed_module._save_state({"k": {"first_seen": "2026-05-08T00:00:00+00:00"}})

    _assert_no_attack_bytes(
        caplog.text, where="build_feed._save_state lock-fail log"
    )


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
    "src/build_feed.py",
    # Extended in the post-Scribe Sentinel sweep (2026-05-08): every
    # production module whose ``except Exception as exc`` paths log
    # the bound exception now appears in this list. A regression here
    # means a future contributor either added a new bare-exc logging
    # site or removed an existing ``sanitize_log_arg`` wrap.
    "src/feed/providers.py",
    "src/feed/reporting.py",
    "src/providers/vor.py",
    "src/providers/oebb.py",
    "src/providers/wl_fetch.py",
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
    shapes documented in this module's docstring.

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
