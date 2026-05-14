"""Sentinel PoC: ``scripts/preflight_quota_check.py`` — Clear-Text-Logging
Drift via ``logging.basicConfig`` instead of the canonical ``SafeFormatter``
shim.

Threat model
------------

``scripts/preflight_quota_check.py`` runs as the budget-gating preflight
of ``.github/workflows/update-cycle.yml`` (every ~30 min cron tick)::

    - name: Preflight VOR quota
      run: python scripts/preflight_quota_check.py --check vor --margin 2

Every other script in ``scripts/`` migrated to
``src.feed.logging_safe.setup_script_logging`` (Round 1 of the
Clear-Text-Logging Drift family) which installs a :class:`SafeFormatter`
that sanitises every log message before it is written. The pre-flight
deliberately could NOT pull that helper in because
``src.feed.logging_safe`` transitively imports
``src.feed.config`` -> ``src.utils.http`` -> ``requests``/``urllib3``/
``dns.resolver`` (all third-party), and the script's docstring pins
the invariant "zero non-stdlib runtime dependencies so it works in
the early Actions step ... before ``install-deps`` has run."

The pre-fix shape::

    logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s")

installs a default :class:`logging.Formatter` that does NOT sanitise.

Attack surface
~~~~~~~~~~~~~~

The ``_emit_outputs`` helper writes the GitHub-Actions output file at
the path declared by ``$GITHUB_OUTPUT``. When the open() fails (the
file is missing, has restrictive permissions, or — in the threat
model — the env var carries non-canonical bytes that the OS rejects),
an :class:`OSError` is raised whose ``__str__`` echoes the path bytes
back into a WARNING log line::

    LOGGER.warning("preflight: konnte $GITHUB_OUTPUT nicht schreiben: %s", exc)

A poisoned ``GITHUB_OUTPUT`` value (compromised CI runner, hostile
third-party Action that pollutes the env, leaked secret store with
non-ASCII bytes, BiDi-carrying repository name on a fork build)
carrying any of the canonical CVE-2021-42574 / log-injection /
8-bit-C1 / Tag-block / Variation-Selector / ANSI-ESC / log-forgery
primitives flows verbatim into the workflow stdout/stderr captured
by ``update-cycle.yml`` (visible in the GitHub Actions UI, ingested
by any downstream SIEM forwarder that scrapes the run log) and into
any ``LogRecord`` consumer that reads ``record.args``.

Severity
~~~~~~~~

MEDIUM — log-injection / Trojan-Source primitive smuggling into the
operator-facing cron-pipeline log. Same family that the existing
``test_sentinel_clear_text_logging_drift_utils.py`` tests pin for
``src/utils/cache.py``, ``src/utils/locking.py``, ``src/utils/http.py``
and ``src/build_feed.py``. No credential leak; no remote code
execution; but the operator log is the canonical incident-response
surface and any control-byte smuggling there blinds the IR
playbook.

Fix shape
---------

1. **Inline ``_PreflightSafeFormatter``** that mirrors
   :class:`src.feed.logging_safe.SafeFormatter` byte-for-byte, but
   defined inside the preflight script so the "stdlib-only" invariant
   is preserved (``src.utils.logging.sanitize_log_message`` is itself
   stdlib-only — imports just ``re`` and ``typing``).
2. **``_configure_safe_logging()``** installs the inline formatter on
   the root logger's :class:`StreamHandler`, replacing the historical
   ``logging.basicConfig(format=...)`` call.
3. **Call-site sanitisation** at the ``_emit_outputs`` ``OSError``
   warning — route the bound ``exc`` through
   :func:`src.utils.logging.sanitize_log_arg` so the log line is
   sanitised even if a future maintainer accidentally regresses the
   formatter-layer defence.

Closing-checklist invariant
---------------------------

The inventory test ``test_no_basicconfig_in_scripts`` walks every
``*.py`` in ``scripts/`` and asserts that ``logging.basicConfig`` does
NOT appear outside comments / docstrings. After this fix the preflight
is the last remaining ``basicConfig`` caller in ``scripts/`` — once
migrated, the grep is clean.
"""

from __future__ import annotations

import io
import logging
import re
import sys
from pathlib import Path
from typing import Any

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _scripts_path_bootstrap() -> None:
    """Ensure ``scripts/`` is importable. The script's own ``sys.path``
    manipulation runs at script invocation, not at import."""
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))


# Canonical attack payload exercising the four classes of control-text
# sneak-bys that :func:`sanitize_log_arg` / :func:`sanitize_log_message`
# are documented to defeat. Mirrors ``ATTACK_BYTES`` in
# ``tests/test_sentinel_clear_text_logging_drift_utils.py`` so the
# invariant is uniform across the Clear-Text-Logging Drift family.
ATTACK_BYTES = "\x1b[31mEVIL\x1b[0m\r\nINJECTED-LOG-LINE‮RTL-OVERRIDE​ZWS"

# Substrings that must NOT appear in the captured log MESSAGE, individually,
# so a partially-applied fix still fails the test. Note: bare ``\n`` / ``\r``
# are NOT in this set because :class:`logging.StreamHandler` appends a record
# terminator after the formatted message — checking for them would yield a
# false positive on every log line. The log-forging primitive that matters
# is the EMBEDDED ``\r\n`` pair inside the rendered message; ``sanitize_
# log_message`` escapes it to literal ``\\r\\n`` so the assertion below
# catches a regression deterministically.
FORBIDDEN_FRAGMENTS = (
    "\x1b[",      # ANSI CSI introducer
    "\r\n",       # Embedded CR+LF log-forging primitive
    "‮",     # BiDi RIGHT-TO-LEFT OVERRIDE
    "​",     # Zero-width space
)


def _assert_no_attack_bytes(text: str, *, where: str) -> None:
    """Assert *text* does not carry any of the canonical attack fragments.

    Strips the trailing record terminator (``\\n`` / ``\\r\\n``) before
    scanning so a natural :class:`logging.StreamHandler` line break is
    not flagged as a log-injection primitive.
    """
    scrub = text.rstrip("\r\n")
    for fragment in FORBIDDEN_FRAGMENTS:
        assert fragment not in scrub, (
            f"clear-text-logging drift: attack fragment {fragment!r} "
            f"survived sanitisation in {where}.\nCaptured: {text!r}"
        )


# ============================================================================
# Layer 1 — unit-level PoC against the ``_emit_outputs`` OSError log site.
# ============================================================================


def test_emit_outputs_oserror_sanitises_logged_exception(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A hostile ``OSError`` carrying control bytes raised by
    ``open(GITHUB_OUTPUT, "a")`` must not flow raw into the WARNING
    log line emitted by ``_emit_outputs``.

    Threat model: ``GITHUB_OUTPUT`` is normally set by the GH Actions
    runner to a well-known tmp path, but a compromised CI runner / a
    hostile third-party Action / a leaked / mistyped env can plant a
    path carrying any of the canonical CVE-2021-42574 / log-injection
    / 8-bit-C1 / ANSI-ESC / log-forgery primitive bytes. The pre-fix
    shape interpolates the raw OSError text into ``%s``, leaking the
    attack bytes verbatim into the cron-pipeline log captured by
    ``.github/workflows/update-cycle.yml``.
    """
    from scripts import preflight_quota_check

    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "out.txt"))

    class HostileOSError(OSError):
        def __init__(self, msg: str) -> None:
            super().__init__(msg)
            self._msg = msg

        def __str__(self) -> str:
            return self._msg

    import builtins
    real_open = builtins.open

    def fake_open(target: Any, *args: Any, **kwargs: Any) -> Any:
        if isinstance(target, str) and target.endswith("out.txt"):
            raise HostileOSError(ATTACK_BYTES)
        return real_open(target, *args, **kwargs)

    monkeypatch.setattr("builtins.open", fake_open)

    caplog.set_level(logging.WARNING, logger="preflight_quota_check")
    preflight_quota_check._emit_outputs(True, 7, 100, "vor")

    _assert_no_attack_bytes(
        caplog.text, where="_emit_outputs OSError warning log"
    )


def test_emit_outputs_oserror_via_formatter_sanitises(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Defense-in-depth: the *formatter* installed by
    ``_configure_safe_logging`` must itself sanitise the rendered log
    record so a future maintainer who accidentally drops the
    call-site ``sanitize_log_arg`` cannot regress.

    Verifies the install-then-render path end-to-end: install the
    preflight's SafeFormatter on a fresh :class:`StreamHandler`,
    capture its output to an in-memory buffer, and assert the attack
    bytes do not survive the formatter.
    """
    from scripts import preflight_quota_check

    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "out.txt"))

    # Capture formatter output to an in-memory stream.
    buffer = io.StringIO()
    handler = logging.StreamHandler(buffer)
    # The preflight ships a stdlib-only SafeFormatter; the fix MUST
    # expose the installer so this test can assert it round-trips
    # the attack bytes through the formatter sink.
    handler.setFormatter(preflight_quota_check._build_safe_formatter())

    logger = logging.getLogger("preflight_quota_check.test_isolation")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.WARNING)

    try:
        logger.warning(
            "preflight: konnte $GITHUB_OUTPUT nicht schreiben: %s", ATTACK_BYTES
        )
    finally:
        handler.close()

    rendered = buffer.getvalue()
    _assert_no_attack_bytes(
        rendered, where="_PreflightSafeFormatter rendered output"
    )


# ============================================================================
# Layer 2 — structural invariants on the preflight module.
# ============================================================================


def test_build_safe_formatter_returns_log_formatter() -> None:
    """The inline SafeFormatter factory must return a
    :class:`logging.Formatter` instance — the contract every handler
    expects."""
    from scripts import preflight_quota_check

    fmt = preflight_quota_check._build_safe_formatter()
    assert isinstance(fmt, logging.Formatter)


def test_configure_safe_logging_installs_safe_formatter() -> None:
    """Calling ``_configure_safe_logging`` once must install a handler
    whose formatter sanitises log records. Subsequent calls must be
    idempotent (no duplicate handlers)."""
    from scripts import preflight_quota_check

    root = logging.getLogger()
    pre_handlers = list(root.handlers)
    try:
        preflight_quota_check._configure_safe_logging(logging.INFO)
        first_count = len(root.handlers)
        preflight_quota_check._configure_safe_logging(logging.INFO)
        second_count = len(root.handlers)
        assert second_count == first_count, (
            "_configure_safe_logging must be idempotent — "
            f"first={first_count}, second={second_count}"
        )
        # Every handler newly added must use a sanitising formatter.
        new_handlers = [h for h in root.handlers if h not in pre_handlers]
        assert new_handlers, "_configure_safe_logging did not install a handler"
        for handler in new_handlers:
            fmt = handler.formatter
            assert fmt is not None, "Handler installed without a formatter"
            # Round-trip ATTACK_BYTES through the formatter to verify
            # it sanitises.
            record = logging.LogRecord(
                name="preflight_quota_check",
                level=logging.WARNING,
                pathname=__file__,
                lineno=0,
                msg="%s",
                args=(ATTACK_BYTES,),
                exc_info=None,
            )
            rendered = fmt.format(record)
            _assert_no_attack_bytes(
                rendered, where="_configure_safe_logging handler formatter"
            )
    finally:
        # Restore root handlers so we don't pollute later tests.
        root.handlers = pre_handlers


# ============================================================================
# Layer 3 — closing-checklist invariant: no remaining ``logging.basicConfig``
# in ``scripts/``. Pins the post-fix contract auto-discoverably.
# ============================================================================


_BASICCONFIG_RE = re.compile(r"\blogging\.basicConfig\b")
# Strip Python comments (``# ...`` to end-of-line) AND triple-quoted
# string blocks before scanning so the inventory test ignores docstring
# references to ``logging.basicConfig`` (e.g. the
# ``src/feed/logging_safe.py`` docstring that documents what it
# replaces).
_TRIPLE_QUOTED_RE = re.compile(r'"""(?:.|\n)*?"""|\'\'\'(?:.|\n)*?\'\'\'')
_LINE_COMMENT_RE = re.compile(r"#[^\n]*")


def _strip_comments_and_docstrings(source: str) -> str:
    no_docs = _TRIPLE_QUOTED_RE.sub("", source)
    return _LINE_COMMENT_RE.sub("", no_docs)


def test_no_basicconfig_in_scripts() -> None:
    """Auto-discoverable invariant: walk every ``*.py`` in
    ``scripts/`` and assert that ``logging.basicConfig`` does NOT
    appear outside comments / docstrings.

    Every script must route through :func:`setup_script_logging` (or
    the stdlib-only :func:`_configure_safe_logging` shim in the
    preflight) so the SafeFormatter is installed uniformly. The pre-
    flight is the LAST remaining ``basicConfig`` site closed by this
    PR — once migrated, the grep stays clean."""
    scripts_dir = REPO_ROOT / "scripts"
    offenders: list[str] = []
    for path in sorted(scripts_dir.glob("*.py")):
        try:
            source = path.read_text(encoding="utf-8")
        except OSError:  # pragma: no cover - defensive
            continue
        scrubbed = _strip_comments_and_docstrings(source)
        if _BASICCONFIG_RE.search(scrubbed):
            offenders.append(path.name)
    assert not offenders, (
        "Clear-Text-Logging Drift: ``logging.basicConfig`` still present "
        f"in scripts/ outside comments/docstrings: {offenders}. "
        "Migrate to ``setup_script_logging`` (or, when third-party deps "
        "are forbidden, the stdlib-only ``_configure_safe_logging`` "
        "shim used by ``scripts/preflight_quota_check.py``)."
    )


# ============================================================================
# Layer 4 — end-to-end PoC: hostile env-var path lands in WARNING log line.
# ============================================================================


def test_main_emit_outputs_path_with_attack_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Integration: invoke ``main(["--check", "vor", "--margin", "0"])``
    with ``GITHUB_OUTPUT`` set to a path that the OS rejects (NUL
    byte) and assert no attack fragment survives in the log.

    The state file is missing, so the preflight projects a count of
    margin=0 against the 100/day cap, the quota is OK, and
    ``_emit_outputs`` is invoked with the poisoned path.
    """
    from scripts import preflight_quota_check

    monkeypatch.setenv("VAO_DAILY_COUNT_FILE", str(tmp_path / "vor.json"))
    monkeypatch.setattr(
        preflight_quota_check, "VOR_QUOTA_FILE", tmp_path / "vor.json"
    )

    # Plant a "/proc/<garbage>" style path that the OS will reject
    # with OSError on open(). The :class:`OSError` ``__str__`` echoes
    # the path back via its ``.filename`` attribute — which is how
    # the canonical Errno message ``[Errno N] <strerror>: <path>``
    # builds. Path bytes carrying BiDi / ANSI / zero-width primitives
    # land in the OSError text verbatim.
    #
    # The env path itself must be syntactically representable in
    # ``os.environ`` (no NUL bytes); pytest's ``monkeypatch.setenv``
    # rejects NUL via :class:`ValueError`. We instead exercise the
    # log-injection surface via the synthetic ``HostileOSError``
    # below — the same shape the upstream unit-level PoC above
    # exercises but routed through the full ``main()`` entry point.
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "out.txt"))

    import builtins
    real_open = builtins.open

    class _HostileOSError(OSError):
        def __init__(self, msg: str) -> None:
            super().__init__(msg)
            self._msg = msg

        def __str__(self) -> str:
            return self._msg

    def fake_open(target: Any, *args: Any, **kwargs: Any) -> Any:
        if isinstance(target, str) and target.endswith("out.txt"):
            raise _HostileOSError(ATTACK_BYTES)
        return real_open(target, *args, **kwargs)

    monkeypatch.setattr("builtins.open", fake_open)

    caplog.set_level(logging.WARNING, logger="preflight_quota_check")
    rc = preflight_quota_check.main(["--check", "vor", "--margin", "0"])
    # quota OK (0 + 0 <= 100); exit 0 regardless of output-write failure.
    assert rc == 0

    _assert_no_attack_bytes(
        caplog.text, where="main() OSError WARNING log"
    )
