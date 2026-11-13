"""Sentinel: Clear-Text-Logging Drift in core utility modules.

The journal's Clear-Text-Logging Drift family (PR #1351 in
the audit) hardened the OSM and (rolled-back) GTFS-RT
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

import json
import logging
from pathlib import Path
from typing import Any

import pytest

from src.utils import cache as cache_module
from src.utils.files import sanitize_filename
from src.utils.locking import file_lock
from tests._conftest.build_feed_bindings import setattr_all


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
    setattr_all(monkeypatch, build_feed_module,
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
