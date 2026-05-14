"""Sentinel PoC: Clear-Text-Logging Drift in ``scripts/fetch_google_places_stations.py``.

``_resolve_stations_out_path`` (lines 136-162 pre-fix) interpolates the
operator-controlled ``OUT_PATH_STATIONS`` env value into the WARNING log
line via the bare ``%s`` format spec — the canonical
clear-text-logging-drift shape closed at every other env-value /
env-path log boundary in the codebase (see the journal entries:
``src/feed/reporting.py`` PR #1473, ``src/places/client.py`` PR #1472,
``src/providers/oebb.py`` Retry-After hardening, the ``read_capped_*``
SHA-256 fingerprint family, the scripts/ path-log sanitisation rounds).

Pre-fix::

    def _resolve_stations_out_path(candidate: str | None) -> Path:
        text = (candidate or "").strip()
        if not text:
            return validate_path(_DEFAULT_STATIONS_PATH, "OUT_PATH_STATIONS")
        try:
            return validate_path(Path(text), "OUT_PATH_STATIONS")
        except InvalidPathError:
            LOGGER.warning(
                "OUT_PATH_STATIONS %s is outside the allowed roots; using default %s.",
                text,
                _DEFAULT_STATIONS_PATH,
            )
            return validate_path(_DEFAULT_STATIONS_PATH, "OUT_PATH_STATIONS")

``text`` is the env-controlled ``OUT_PATH_STATIONS`` value at the
moment the rejection branch fires. ``validate_path`` rejects any
path that resolves outside the project's ``ALLOWED_ROOTS``
(``docs/``, ``data/``, ``log/``) but does NOT strip Trojan-Source /
ANSI / BiDi / control-character primitives from the bytes — those
primitives flow verbatim into the WARNING log line and from there
into:

  * pytest's ``caplog`` capture (fires BEFORE :class:`SafeFormatter`
    runs — the project's CI surface);
  * any non-:class:`SafeFormatter` log handler (early-init plumbing
    before :func:`_configure_logging` runs, third-party log capture,
    future refactor that drops the safe formatter);
  * journalctl / Docker logs / log aggregator splitters that parse
    line-based records (newline log-record-forgery surface);
  * any future operator dashboard that ingests the log file as
    structured data (UTF-8 strings with embedded BiDi RLO render
    inverted).

Threat model
============
A hostile env var value (compromised CI runner, malicious workflow
PR, leaked CI secret store, operator typo combined with copy-paste
from a Trojan-Source-bearing diff) can carry the canonical
primitives:

  * ``‮`` U+202E RIGHT-TO-LEFT OVERRIDE — visually reverses
    subsequent text (Trojan-Source, CVE-2021-42574). An operator
    skimming the log sees the inverse of the actual bytes.
  * ``​`` U+200B ZERO WIDTH SPACE — invisible cache-key /
    equality-poisoning primitive.
  * ``؜`` U+061C ARABIC LETTER MARK — BiDi mark.
  * ``\x1b`` U+001B ESC — ANSI prefix; terminal-escape primitive
    (``\x1b[31m`` colourises subsequent terminal output, ``\x1b[2K``
    erases the previous line on the operator's terminal).
  * ``\x9b`` U+009B 8-bit CSI — bypasses the 7-bit ``_ANSI_ESCAPE_RE``
    on terminals that honour 8-bit C1 (xterm with eightBitInput,
    BSD consoles, rxvt in 8-bit mode).
  * ``\x07`` U+0007 BEL — terminal-bell denial-of-attention.
  * ``\n`` / ``\r`` — log-record forgery in any line-based
    consumer.
  * ``\U000e0020`` U+E0020 Unicode Tag SPACE — invisible-instruction
    smuggling primitive (2024 OpenAI disclosure).
  * ``︀`` U+FE00 VARIATION SELECTOR-1 — 4-bit-payload
    steganography.

Post-fix every primitive is stripped at the call-site by routing
``text`` through :func:`sanitize_log_arg`, mirroring the canonical
defence pinned at every other Trojan-Source-bearing log boundary in
the codebase.
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
    "\x1b[31m",
    "‮",
    "​",
    "؜",
    "\x9b",
    "\x07",
    "\U000e0020",
    "︀",
)


# ============================================================================
# Site: _resolve_stations_out_path InvalidPathError branch
# ============================================================================


@pytest.mark.parametrize("primitive,primitive_label", _PRIMITIVES)
def test_resolve_outpath_warning_strips_primitives(
    primitive: str,
    primitive_label: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A hostile ``OUT_PATH_STATIONS`` env value carrying *primitive*
    MUST NOT propagate into the rejection-branch WARNING log line.

    Pre-fix the env-controlled ``text`` was interpolated via the bare
    ``%s`` format spec; post-fix the value is routed through
    :func:`sanitize_log_arg` at the call site so every Trojan-Source
    primitive is stripped before reaching the log record.
    """
    from scripts.fetch_google_places_stations import _resolve_stations_out_path

    # Path outside ALLOWED_ROOTS so validate_path raises InvalidPathError.
    poisoned = f"/tmp/evil{primitive}path.json"

    caplog.set_level(logging.WARNING, logger="places.cli")
    _resolve_stations_out_path(poisoned)

    matching = [
        record.getMessage()
        for record in caplog.records
        if "OUT_PATH_STATIONS" in record.getMessage()
        and "outside the allowed roots" in record.getMessage()
    ]
    assert matching, (
        "Expected the InvalidPathError-branch WARNING to fire when "
        "OUT_PATH_STATIONS resolves outside the allowlist "
        f"(got records: {[r.getMessage() for r in caplog.records]!r})"
    )
    for message in matching:
        assert primitive not in message, (
            f"{primitive_label} ({primitive!r}) leaked into the "
            f"_resolve_stations_out_path WARNING: {message!r}"
        )


def test_resolve_outpath_warning_strips_full_attack_payload(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A hostile ``OUT_PATH_STATIONS`` carrying the canonical
    attack-fragment union (ANSI SGR + BiDi RLO + ZWSP + ALM +
    8-bit CSI + BEL + Tag SPACE + Variation Selector + log-record
    forgery newline) MUST NOT propagate any fragment into the
    WARNING log line.
    """
    from scripts.fetch_google_places_stations import _resolve_stations_out_path

    payload = (
        "/tmp/"
        "\x1b[31m"
        "‮"
        "​"
        "؜"
        "\x9b"
        "\x07"
        "\U000e0020"
        "︀"
        "\n[INJECTED] fake.log.line"
        "/stations.json"
    )

    caplog.set_level(logging.WARNING, logger="places.cli")
    _resolve_stations_out_path(payload)

    matching = [
        record.getMessage()
        for record in caplog.records
        if "OUT_PATH_STATIONS" in record.getMessage()
        and "outside the allowed roots" in record.getMessage()
    ]
    assert matching
    for message in matching:
        for fragment in _FORBIDDEN_FRAGMENTS:
            assert fragment not in message, (
                f"Forbidden fragment {fragment!r} survived in the "
                f"_resolve_stations_out_path WARNING: {message!r}"
            )
        # Raw newline would let a downstream line-splitter parse the
        # bytes after ``\n`` as a separate log record. sanitize_log_arg
        # escapes ``\n`` to ``\\n`` (literal backslash-n) so the raw
        # newline cannot survive.
        assert "\n" not in message, (
            f"Raw newline survived in the _resolve_stations_out_path "
            f"WARNING: {message!r}"
        )


def test_resolve_outpath_returns_default_on_invalid_path() -> None:
    """Regression: the rejection branch must still fall back to the
    default stations path. Post-fix the sanitisation MUST NOT alter
    the resolved-path return value (the sanitiser only touches the
    log argument).
    """
    from scripts.fetch_google_places_stations import (
        _DEFAULT_STATIONS_PATH,
        _resolve_stations_out_path,
    )

    result = _resolve_stations_out_path("/tmp/outside/allowed/roots.json")
    # The function returns the validated default — match by suffix
    # because validate_path resolves against the current working dir.
    assert result.name == _DEFAULT_STATIONS_PATH.name
    assert result.parent.name == _DEFAULT_STATIONS_PATH.parent.name


def test_resolve_outpath_returns_default_on_empty() -> None:
    """Regression: empty / None input must return the default."""
    from scripts.fetch_google_places_stations import (
        _DEFAULT_STATIONS_PATH,
        _resolve_stations_out_path,
    )

    result_none = _resolve_stations_out_path(None)
    result_empty = _resolve_stations_out_path("")
    result_spaces = _resolve_stations_out_path("   ")

    for result in (result_none, result_empty, result_spaces):
        assert result.name == _DEFAULT_STATIONS_PATH.name


# ============================================================================
# AST inventory invariant: walk scripts/fetch_google_places_stations.py
# and assert no ``LOGGER.warning(..., text)`` shape survives without
# routing through ``sanitize_log_arg``. A future refactor that
# re-introduces the bare ``%s`` shape fails this guard at PR-review
# time instead of waiting for a follow-up Sentinel sweep.
# ============================================================================


def _read_script_source() -> str:
    repo_root = Path(__file__).resolve().parents[1]
    return (
        repo_root / "scripts" / "fetch_google_places_stations.py"
    ).read_text(encoding="utf-8")


def test_no_bare_text_log_in_resolve_outpath() -> None:
    """Inventory invariant: no log call inside
    ``_resolve_stations_out_path`` interpolates the bare ``text``
    local without :func:`sanitize_log_arg` wrapping. Mirrors the
    canonical inventory-walker shape that closes every prior
    clear-text-logging drift in the codebase.
    """
    source = _read_script_source()
    tree = ast.parse(source)

    bare_sites: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        if node.name != "_resolve_stations_out_path":
            continue
        for sub in ast.walk(node):
            if not isinstance(sub, ast.Call):
                continue
            func = sub.func
            if not isinstance(func, ast.Attribute):
                continue
            if func.attr not in {"warning", "error", "info"}:
                continue
            for arg in sub.args[1:]:
                if _is_bare_text_name(arg):
                    bare_sites.append(
                        f"line {sub.lineno}: "
                        f"{getattr(func.value, 'id', '?')}.{func.attr}"
                        f"(..., {ast.unparse(arg)})"
                    )

    assert not bare_sites, (
        "scripts/fetch_google_places_stations.py:_resolve_stations_out_path "
        "contains log call(s) that interpolate the env-controlled ``text`` "
        "local via bare %s without sanitize_log_arg. Each site MUST route "
        "the value through sanitize_log_arg at the call site (defense-in-"
        "depth — caplog and any non-SafeFormatter handler see the pre-"
        f"formatter message). Offending site(s): {bare_sites}"
    )


def _is_bare_text_name(arg: ast.expr) -> bool:
    """Return ``True`` iff *arg* is the bare ``text`` Name reference
    (i.e. NOT wrapped in ``sanitize_log_arg(...)``)."""
    return isinstance(arg, ast.Name) and arg.id == "text"
