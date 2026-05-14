"""Sentinel PoC: Clear-Text-Logging Drift in ``src/feed/reporting.py``.

Two sibling drift sites in :mod:`src.feed.reporting` interpolate
env-controlled strings via the bare ``%s`` format spec without
routing through :func:`sanitize_log_arg`. The journal pinned the
canonical defence shape on every other Trojan-Source-bearing log
boundary (places-client Retry-After, ÖBB Retry-After, ``read_capped
_*`` path-fingerprinting, scripts/ path-log sanitisation rounds 1-3,
…); these two sites in the GitHub-issue / log-results renderer were
the last remaining ``log.warning(..., env_str)`` /
``log.info(..., env_path)`` call shapes in :mod:`src.feed` that did
NOT mirror the canonical sanitiser.

Site 1 — :func:`_GithubIssueReporter.submit` rejection branch
============================================================

Pre-fix::

    if not _is_trusted_github_api(self._config.api_url):
        log.warning(
            "Automatisches GitHub-Issue abgebrochen: API-URL %s ist kein "
            "bekannter GitHub-Endpunkt; Token wird nicht gesendet.",
            self._config.api_url,
        )
        return

``self._config.api_url`` is sourced from the operator-controlled
``FEED_GITHUB_API_URL`` (preferred) / ``GITHUB_API_URL`` env vars in
:meth:`_GithubIssueConfig.from_env`. The interpolated value is
gated by :func:`_is_trusted_github_api` only AFTER it has already
been used in the WARNING line — meaning the rejection branch is the
exact shape that lets a hostile env var (compromised CI runner,
malicious workflow PR, leaked CI secret store, mistaken Enterprise
URL) flow into the operator log. The ``urlparse`` parse the
function performs accepts arbitrary bytes in the path/fragment;
the rejection branch is the ONLY time the value is logged before
``validate_http_url`` (the next defence layer) gets a chance to
strip it.

Site 2 — :meth:`RunReport.log_results` errors-detail hint
=========================================================

Pre-fix::

    if self.has_errors():
        log.info(
            "Hinweis: Fehler während des Feed-Laufs – Details siehe %s",
            error_log_path,
        )

``error_log_path`` is :file:`Path(LOG_DIR) / "errors.log"` where
``LOG_DIR`` is env-controlled via :func:`resolve_env_path` (see
``src/feed/config.py:LOG_DIR_PATH = resolve_env_path("LOG_DIR",
Path("log"), allow_fallback=True)``). :func:`validate_path` only
checks the path resolves under one of ``ALLOWED_ROOTS``; it does
NOT reject Trojan-Source / control-character / BiDi primitives
embedded in the path string, so a hostile ``LOG_DIR`` like
``log/sub<RLO>``, ``log<ESC>[31m``, ``log<NEWLINE>[INJECTED]``
flows verbatim into the INFO log line.

Threat model
============
A hostile env var value can carry the canonical primitives:

  * ``‮`` RIGHT-TO-LEFT OVERRIDE — visually reverses
    subsequent text. An operator skimming the log sees the
    inverse of the actual bytes (Trojan-Source, CVE-2021-42574).
  * ``​`` ZERO WIDTH SPACE — invisible cache-key / equality
    poisoning primitive.
  * ``؜`` ARABIC LETTER MARK — BiDi mark.
  * ``\x1b`` ESC — ANSI prefix; terminal-escape primitive
    (``\x1b[31m`` colourises subsequent terminal output).
  * ``\x9b`` 8-bit CSI — bypasses the 7-bit ``_ANSI_ESCAPE_RE``
    on terminals that honour 8-bit C1 (xterm with eightBitInput,
    BSD consoles, rxvt in 8-bit mode).
  * ``\x07`` BEL — terminal-bell denial-of-attention.
  * ``\n`` / ``\r`` — log-record forgery in any line-based
    consumer (journalctl, Docker logs, log aggregator splitters).
  * ``\U000e0020`` Unicode Tag SPACE — invisible-instruction
    smuggling primitive (2024 OpenAI disclosure).
  * ``︀`` VARIATION SELECTOR-1 — 4-bit-payload steganography.

Pre-fix every primitive flows verbatim into the WARNING/INFO log
line and from there into:

  * pytest's ``caplog`` capture (fires BEFORE :class:`SafeFormatter`
    runs — the project's CI surface);
  * any non-:class:`SafeFormatter` log handler (early-init plumbing,
    third-party log capture, future refactor that drops the
    safe formatter);
  * the public ``docs/feed-health.md`` artefact + the GitHub
    Issue body submitted by ``submit_auto_issue`` when the
    rejection branch is reached *before* the auto-issue itself
    is suppressed by the same gate.

Post-fix every primitive is stripped at the call-site by routing
the env-controlled value through :func:`sanitize_log_arg`, mirroring
the canonical defence pinned at every other Trojan-Source-bearing
log boundary in the codebase.
"""

from __future__ import annotations

import ast
import logging
from pathlib import Path

import pytest


_PRIMITIVES: list[tuple[str, str]] = [
    ("‮", "U+202E RIGHT-TO-LEFT OVERRIDE"),
    ("​", "U+200B ZERO WIDTH SPACE"),
    ("؜", "U+061C ARABIC LETTER MARK"),
    ("\x1b", "U+001B ESC (ANSI prefix)"),
    ("\x9b", "U+009B 8-bit CSI"),
    ("\x07", "U+0007 BEL"),
    ("\n", "newline (log-record forgery)"),
    ("\r", "carriage return"),
    ("\U000e0020", "U+E0020 Unicode Tag SPACE"),
    ("︀", "U+FE00 VARIATION SELECTOR-1"),
]

_FORBIDDEN_FRAGMENTS = (
    "\x1b[31m",  # ANSI SGR colour
    "‮",
    "​",
    "؜",
    "\x9b",
    "\x07",
    "\U000e0020",
    "︀",
)


# ============================================================================
# Site 1: _GithubIssueReporter.submit() rejection branch
# ============================================================================


@pytest.mark.parametrize("primitive,primitive_label", _PRIMITIVES)
def test_submit_warning_strips_api_url_primitives(
    primitive: str,
    primitive_label: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A hostile ``FEED_GITHUB_API_URL`` carrying *primitive* MUST NOT
    propagate into the rejection-branch WARNING log line.

    Pre-fix ``self._config.api_url`` was interpolated via the bare
    ``%s`` format spec; post-fix the env-controlled value is routed
    through :func:`sanitize_log_arg` at the call site so every
    Trojan-Source primitive is stripped before reaching the log.
    """
    poisoned_url = f"https://evil.example.com/path{primitive}/foo"

    monkeypatch.setenv("FEED_GITHUB_CREATE_ISSUES", "1")
    monkeypatch.setenv("FEED_GITHUB_REPOSITORY", "demo/repo")
    monkeypatch.setenv("FEED_GITHUB_TOKEN", "secret-token")
    monkeypatch.setenv("FEED_GITHUB_API_URL", poisoned_url)
    monkeypatch.delenv("FEED_GITHUB_ENTERPRISE_HOSTS", raising=False)

    # Import inside the test to bind against the currently-installed
    # module (defensive against any prior test reloading reporting).
    from src.feed.reporting import (
        _GithubIssueConfig,
        _GithubIssueReporter,
        RunReport,
    )

    report = RunReport([("wl", True)])
    report.provider_error("wl", "Test error")
    report.finish(build_successful=False)

    config = _GithubIssueConfig.from_env()
    reporter = _GithubIssueReporter(config)

    caplog.set_level(logging.WARNING, logger="build_feed")
    reporter.submit(report)

    matching = [
        record.getMessage()
        for record in caplog.records
        if "Automatisches GitHub-Issue abgebrochen" in record.getMessage()
    ]
    assert matching, (
        "Expected the rejection-branch WARNING to fire when the API URL "
        f"is untrusted (got records: {[r.getMessage() for r in caplog.records]!r})"
    )
    for message in matching:
        assert primitive not in message, (
            f"{primitive_label} ({primitive!r}) leaked into the "
            f"submit() WARNING: {message!r}"
        )


def test_submit_warning_strips_full_attack_payload(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A hostile ``FEED_GITHUB_API_URL`` carrying the canonical
    attack-fragment union (ANSI SGR + BiDi RLO + ZWSP + 8-bit CSI +
    Tag SPACE + Variation Selector + log-record forgery newline)
    MUST NOT propagate any fragment into the WARNING log line.
    """
    payload = (
        "https://evil.example.com/"
        "\x1b[31m"  # ANSI SGR red
        "‮"  # BiDi RLO
        "​"  # ZWSP
        "؜"  # ALM
        "\x9b"  # 8-bit CSI
        "\x07"  # BEL
        "\U000e0020"  # Tag SPACE
        "︀"  # Variation Selector-1
        "\n[INJECTED] fake.log.line"  # log-record forgery
    )

    monkeypatch.setenv("FEED_GITHUB_CREATE_ISSUES", "1")
    monkeypatch.setenv("FEED_GITHUB_REPOSITORY", "demo/repo")
    monkeypatch.setenv("FEED_GITHUB_TOKEN", "secret-token")
    monkeypatch.setenv("FEED_GITHUB_API_URL", payload)
    monkeypatch.delenv("FEED_GITHUB_ENTERPRISE_HOSTS", raising=False)

    from src.feed.reporting import (
        _GithubIssueConfig,
        _GithubIssueReporter,
        RunReport,
    )

    report = RunReport([("wl", True)])
    report.provider_error("wl", "Test error")
    report.finish(build_successful=False)

    config = _GithubIssueConfig.from_env()
    reporter = _GithubIssueReporter(config)

    caplog.set_level(logging.WARNING, logger="build_feed")
    reporter.submit(report)

    matching = [
        record.getMessage()
        for record in caplog.records
        if "Automatisches GitHub-Issue abgebrochen" in record.getMessage()
    ]
    assert matching
    for message in matching:
        for fragment in _FORBIDDEN_FRAGMENTS:
            assert fragment not in message, (
                f"Forbidden fragment {fragment!r} survived in the "
                f"submit() WARNING: {message!r}"
            )
        # The injected fake-log-line fragment uses ASCII printables only;
        # the surviving newline would let a downstream line-splitter parse
        # the bytes after ``\n`` as a separate log record. ``sanitize_log_
        # message`` escapes ``\n`` to ``\\n`` (literal backslash-n) so the
        # raw newline cannot survive.
        assert "\n" not in message, (
            f"Raw newline survived in the submit() WARNING: {message!r}"
        )


# ============================================================================
# Site 2: RunReport.log_results errors-detail hint
# ============================================================================


@pytest.mark.parametrize("primitive,primitive_label", _PRIMITIVES)
def test_log_results_strips_error_log_path_primitives(
    primitive: str,
    primitive_label: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A hostile ``LOG_DIR`` (env-controlled via :func:`resolve_env_path`)
    that flows into ``error_log_path`` MUST NOT propagate *primitive*
    into the INFO log line that points operators at the error log.

    Pre-fix ``error_log_path`` was interpolated via the bare ``%s``
    format spec; post-fix the env-derived path string is routed
    through :func:`sanitize_log_arg` at the call site, mirroring the
    canonical defence shape applied at every other env-path boundary
    in the codebase (``src.utils.files.read_capped_*`` SHA-256
    fingerprint family, ``src.utils.env._warn_if_world_readable``).

    Disable the GitHub auto-issue submitter so the test isolates the
    INFO-level errors-detail-hint log line.
    """
    monkeypatch.delenv("FEED_GITHUB_CREATE_ISSUES", raising=False)

    from src.feed import reporting

    poisoned_path = Path(f"/tmp/log{primitive}/errors.log")
    monkeypatch.setattr(reporting, "error_log_path", poisoned_path)

    report = reporting.RunReport([("wl", True)])
    report.provider_error("wl", "Test error")
    report.finish(build_successful=False)

    caplog.set_level(logging.INFO, logger="build_feed")
    report.log_results()

    matching = [
        record.getMessage()
        for record in caplog.records
        if "Details siehe" in record.getMessage()
    ]
    assert matching, (
        "Expected the errors-detail-hint INFO line to fire when "
        f"has_errors() is True (got: {[r.getMessage() for r in caplog.records]!r})"
    )
    for message in matching:
        assert primitive not in message, (
            f"{primitive_label} ({primitive!r}) leaked into "
            f"log_results() INFO: {message!r}"
        )


def test_log_results_strips_full_attack_payload(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Sister of :func:`test_submit_warning_strips_full_attack_payload`
    for the ``error_log_path`` log line. Same canonical attack-fragment
    union, same forbidden-fragment invariant.
    """
    monkeypatch.delenv("FEED_GITHUB_CREATE_ISSUES", raising=False)

    from src.feed import reporting

    poisoned_name = (
        "errors"
        "\x1b[31m"
        "‮"
        "​"
        "؜"
        "\x9b"
        "\x07"
        "\U000e0020"
        "︀"
        ".log"
    )
    poisoned_path = Path("/tmp/log") / poisoned_name
    monkeypatch.setattr(reporting, "error_log_path", poisoned_path)

    report = reporting.RunReport([("wl", True)])
    report.provider_error("wl", "Test error")
    report.finish(build_successful=False)

    caplog.set_level(logging.INFO, logger="build_feed")
    report.log_results()

    matching = [
        record.getMessage()
        for record in caplog.records
        if "Details siehe" in record.getMessage()
    ]
    assert matching
    for message in matching:
        for fragment in _FORBIDDEN_FRAGMENTS:
            assert fragment not in message, (
                f"Forbidden fragment {fragment!r} survived in the "
                f"log_results() INFO line: {message!r}"
            )


# ============================================================================
# AST inventory invariant: walk src/feed/reporting.py and assert every
# ``log.warning(..., self._config.api_url)`` /
# ``log.info(..., error_log_path)`` site routes the env-derived arg
# through ``sanitize_log_arg``. A future refactor that re-introduces
# the bare ``%s`` shape fails this guard at PR-review time instead of
# waiting for a follow-up Sentinel sweep.
# ============================================================================


def _read_reporting_source() -> str:
    repo_root = Path(__file__).resolve().parents[1]
    return (repo_root / "src" / "feed" / "reporting.py").read_text(encoding="utf-8")


def test_no_bare_api_url_log_in_reporting() -> None:
    """Inventory invariant: no ``log.warning(..., self._config.api_url)``
    pattern survives in :mod:`src.feed.reporting` without
    :func:`sanitize_log_arg` wrapping. Mirrors the canonical
    inventory-walker shape that closes every prior clear-text-logging
    drift in the codebase (e.g. ``test_no_bare_exc_logging_in_clear_
    text_logging_modules``).
    """
    source = _read_reporting_source()
    tree = ast.parse(source)

    bare_sites: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        # Match ``log.warning`` / ``log.error`` / ``log.info`` /
        # ``LOGGER.<level>`` / ``logger.<level>`` calls.
        func = node.func
        if not isinstance(func, ast.Attribute):
            continue
        if func.attr not in {"warning", "error", "info"}:
            continue
        for arg in node.args[1:]:  # skip the format string
            if _is_bare_api_url_arg(arg):
                bare_sites.append(
                    f"line {node.lineno}: log.{func.attr}(..., {ast.unparse(arg)})"
                )
            if _is_bare_error_log_path_arg(arg):
                bare_sites.append(
                    f"line {node.lineno}: log.{func.attr}(..., {ast.unparse(arg)})"
                )

    assert not bare_sites, (
        "src/feed/reporting.py contains log call(s) that interpolate "
        "an env-controlled value via bare %s without sanitize_log_arg. "
        "Each site MUST route the value through sanitize_log_arg at "
        "the call site (defense-in-depth — caplog and any non-"
        "SafeFormatter handler see the pre-formatter message). "
        f"Offending site(s): {bare_sites}"
    )


def _is_bare_api_url_arg(arg: ast.expr) -> bool:
    """Return True iff *arg* is the bare ``self._config.api_url``
    attribute access (i.e. NOT wrapped in ``sanitize_log_arg``)."""
    return (
        isinstance(arg, ast.Attribute)
        and arg.attr == "api_url"
        and isinstance(arg.value, ast.Attribute)
        and arg.value.attr == "_config"
        and isinstance(arg.value.value, ast.Name)
        and arg.value.value.id == "self"
    )


def _is_bare_error_log_path_arg(arg: ast.expr) -> bool:
    """Return True iff *arg* is the bare ``error_log_path`` name
    reference (i.e. NOT wrapped in ``sanitize_log_arg``)."""
    return isinstance(arg, ast.Name) and arg.id == "error_log_path"
