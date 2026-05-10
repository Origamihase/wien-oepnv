"""Sentinel: close 8-bit C1-control terminal-escape drift in every
``strip_control_chars=False`` code path.

Threat model — Round 3 (8-bit C1 Drift)
---------------------------------------
Round 1 (PR #1363, journaled 2026-05-09) widened
``src/utils/logging.py:_CONTROL_CHARS_RE`` to cover the BiDi /
zero-width / line-terminator family on the
``strip_control_chars=True`` (default) path.

Round 2 (journaled 2026-05-09 *BiDi-Mark Drift Round 2*) closed the
``strip_control_chars=False`` sibling branch by lifting the BiDi /
zero-width / line-terminator union into ``_INVISIBLE_DANGEROUS_RE``
and applying it UNCONDITIONALLY (independent of the flag) inside
``sanitize_log_message``. That round explicitly named the readability
contract: ``\\n`` / ``\\r`` / ``\\t`` MUST survive
``strip_control_chars=False`` for traceback formatting.

Round 3 closes the **8-bit C1 control / terminal-escape** sibling
that survives the Round 2 fix:

* The 7-bit ANSI escape regex
  ``src/utils/logging.py:_ANSI_ESCAPE_RE`` matches ``\\x1b`` (ESC)
  followed by CSI / OSC / Fe sequences. It is anchored to ``\\x1b``
  and does NOT match the **8-bit** equivalents:
    - ``\\x9b`` (CSI, 8-bit form of ``ESC [``)
    - ``\\x9d`` (OSC, 8-bit form of ``ESC ]``)
    - ``\\x90`` (DCS, 8-bit form of ``ESC P``)
    - ``\\x9e`` (PM, 8-bit form of ``ESC ^``)
    - ``\\x9f`` (APC, 8-bit form of ``ESC _``)
  Per ECMA-48 / ISO 6429, these C1 controls are functionally
  IDENTICAL to their two-byte 7-bit forms — a terminal that honours
  8-bit C1 (xterm with ``eightBitInput`` enabled, several BSD
  consoles, embedded serial terminals, ``rxvt`` in 8-bit mode)
  interprets ``\\x9b31m`` exactly as ``\\x1b[31m`` (set foreground
  red).

* ``_INVISIBLE_DANGEROUS_RE`` (the always-strip floor) covers the
  BiDi / zero-width / line-terminator family but NOT
  ``\\x7f-\\x9f`` (DEL + the 32 C1 controls). The narrow regex
  ``_CONTROL_CHARS_RE.sub("")`` step that DOES strip them is gated
  by ``strip_control_chars=True`` — so every
  ``strip_control_chars=False`` sibling path lets them through:

    - ``src/feed/reporting.py:clean_message`` (canonical sanitiser
      for every provider detail / warning / error / exception
      message rendered into ``docs/feed_health.json`` AND the
      GitHub Issue body submitted by ``submit_auto_issue``)
    - ``src/feed/reporting.py:_sanitize_log_detail`` (provider
      diagnostic strings posted to the issue body)
    - ``src/utils/http.py:_sanitize_exception_msg`` (rewrites
      ``RequestException.args[0]`` for every network-level error
      caught by ``request_safe``; the exception text is then routed
      through every WARNING/ERROR site that logs ``str(exc)``)
    - ``src/feed/logging_safe.py:SafeFormatter.formatException``
      (renders the traceback for every ``log.exception(...)`` call
      in the production feed builder)
    - ``src/feed/logging_safe.py:SafeJSONFormatter.formatException``
      (same drift on the JSON log channel)

**Exploit shape:** A hostile upstream (compromised provider, MITM,
DNS hijack, planted cache file) returns an error response carrying
the 8-bit CSI primitive ``\\x9b...m`` (a SGR colour command) or
``\\x9d...\\x07`` (an OSC sequence that, e.g., changes the terminal
title). The error flows through:

    HTTP fetch → request_safe.RequestException →
    _sanitize_exception_msg → exc.args[0] →
    log.error("... %s ...", str(exc)) → SafeFormatter → handler

If the operator's terminal supports 8-bit C1 (``cat
log/diagnostics.log``, ``less`` without ``-r``/``-R`` configured to
strip ANSI, ``tail -f`` over an SSH connection to a BSD jump-box,
``journalctl`` on a TTY without ``--no-pager``), the byte sequence
is interpreted as a terminal command — the standard ANSI-escape
forging primitive (CVE-style log forgery, fake colour-coded "OK"
markers, terminal-title rewrite) succeeds despite the
``_ANSI_ESCAPE_RE`` defence at the 7-bit boundary.

A second sink with the same shape is ``docs/feed_health.json`` (a
public artefact published to GitHub Pages). Operators who
``cat docs/feed_health.json`` or pipe it through a JSON pretty-
printer rendered on a C1-honouring terminal trigger the same
interpretation. The published JSON carries the C1 byte verbatim
because ``json.dumps(ensure_ascii=False)`` preserves Unicode
codepoints U+0080-U+009F.

**Severity:** MEDIUM — real exploit shape against terminal renderers
that honour 8-bit C1, low against modern UTF-8 terminals (which
treat 0x80-0x9F as continuation bytes), public artefact (``docs/
feed_health.json``) plus operator-facing log surface, defence-in-
depth gap on the documented "always-strip floor".

**Fix:** Widen ``_INVISIBLE_DANGEROUS_RE`` to include
``\\x7f-\\x9f`` (DEL + the 32 C1 controls) so the unconditional
strip step closes the 8-bit terminal-escape primitive on every
``strip_control_chars=False`` sibling path. ``\\n``/``\\r``/``\\t``
remain outside the always-strip floor (they are C0, not C1) so the
readability contract for traceback formatting is preserved. The
companion regexes ``_UNSAFE_URL_CHARS`` (URL boundary) and
``_UNSAFE_CHARS_RE`` (stations validation boundary) are widened
in the same PR to maintain the canonical-coverage invariant pinned
by ``test_unsafe_url_chars_regex_covers_canonical_invisible_dangerous_set``
and ``test_unsafe_chars_regex_covers_canonical_invisible_dangerous_set``.
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from src.feed.logging_safe import SafeFormatter, SafeJSONFormatter
from src.feed.reporting import (
    FeedHealthMetrics,
    RunReport,
    _sanitize_log_detail,
    build_feed_health_payload,
    clean_message,
)
from src.utils.http import _sanitize_exception_msg
from src.utils.logging import sanitize_log_message
from src.utils import logging as canonical_logging


def _empty_metrics() -> FeedHealthMetrics:
    """Helper: minimal ``FeedHealthMetrics`` for the end-to-end PoCs.
    The C1 byte under test rides the ``RunReport`` channel, not the
    metrics channel, so empty-zero metrics are sufficient.
    """
    return FeedHealthMetrics(
        raw_items=0,
        filtered_items=0,
        deduped_items=0,
        new_items=0,
        duplicate_count=0,
        duplicates=(),
    )


# Canonical 8-bit C1 control / DEL set: all 33 code points U+007F..U+009F.
# Each is invisible (no readability value) and is either DEL itself
# (\x7f) or a C1 control with a documented 7-bit ESC-prefixed equivalent
# under ECMA-48 / ISO 6429.
_C1_TERMINAL_ESCAPE_CHARS: tuple[tuple[str, str], ...] = (
    ("\x7f", "U+007F DEL"),
    ("\x80", "U+0080 PAD"),
    ("\x81", "U+0081 HOP"),
    ("\x82", "U+0082 BPH"),
    ("\x83", "U+0083 NBH"),
    ("\x84", "U+0084 IND"),
    # NOTE: \x85 (NEL) is intentionally omitted — Python's regex `\s`
    # matches U+0085 (it has the Whitespace property), so the
    # `re.sub(r"\s+", " ", ...)` step in `clean_message` already
    # collapses it. Including it here would pass even pre-fix and
    # dilute the PoC.
    ("\x86", "U+0086 SSA"),
    ("\x87", "U+0087 ESA"),
    ("\x88", "U+0088 HTS"),
    ("\x89", "U+0089 HTJ"),
    ("\x8a", "U+008A VTS"),
    ("\x8b", "U+008B PLD"),
    ("\x8c", "U+008C PLU"),
    ("\x8d", "U+008D RI"),
    ("\x8e", "U+008E SS2"),
    ("\x8f", "U+008F SS3"),
    ("\x90", "U+0090 DCS (8-bit ESC P)"),
    ("\x91", "U+0091 PU1"),
    ("\x92", "U+0092 PU2"),
    ("\x93", "U+0093 STS"),
    ("\x94", "U+0094 CCH"),
    ("\x95", "U+0095 MW"),
    ("\x96", "U+0096 SPA"),
    ("\x97", "U+0097 EPA"),
    ("\x98", "U+0098 SOS"),
    ("\x99", "U+0099 SGCI"),
    ("\x9a", "U+009A SCI"),
    ("\x9b", "U+009B CSI (8-bit ESC [)"),
    ("\x9c", "U+009C ST"),
    ("\x9d", "U+009D OSC (8-bit ESC ])"),
    ("\x9e", "U+009E PM (8-bit ESC ^)"),
    ("\x9f", "U+009F APC (8-bit ESC _)"),
)


# ---------------------------------------------------------------------------
# Per-code-point bypass tests — pre-fix every assert FAILS.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("char,name", _C1_TERMINAL_ESCAPE_CHARS)
def test_sanitize_log_message_strips_c1_with_strip_control_chars_disabled(
    char: str, name: str
) -> None:
    """``sanitize_log_message(strip_control_chars=False)`` MUST strip
    every 8-bit C1 control / DEL — the unconditional always-strip
    floor must cover the 8-bit terminal-escape primitive set so the
    five sibling sinks (``clean_message``, ``_sanitize_log_detail``,
    ``_sanitize_exception_msg``, ``SafeFormatter.formatException``,
    ``SafeJSONFormatter.formatException``) inherit the defence in
    one cut.
    """
    payload = f"prefix{char}suffix"
    sanitized = sanitize_log_message(payload, strip_control_chars=False)
    assert char not in sanitized, (
        f"{name} ({char!r}) leaked through "
        f"sanitize_log_message(strip_control_chars=False): {sanitized!r}"
    )


@pytest.mark.parametrize("char,name", _C1_TERMINAL_ESCAPE_CHARS)
def test_clean_message_strips_c1_terminal_escape(char: str, name: str) -> None:
    """``clean_message`` is the canonical sanitiser for every provider
    detail / warning / error / exception text rendered into the public
    ``docs/feed_health.json`` artefact AND the GitHub Issue body. It
    MUST strip the 8-bit C1 / DEL set — pre-fix the
    ``sanitize_log_message(strip_control_chars=False)`` delegation
    leaks them verbatim.
    """
    payload = f"VOR error: {char}terminal-injected"
    cleaned = clean_message(payload)
    assert char not in cleaned, (
        f"{name} ({char!r}) survived clean_message: {cleaned!r}"
    )


@pytest.mark.parametrize("char,name", _C1_TERMINAL_ESCAPE_CHARS)
def test_sanitize_log_detail_strips_c1_terminal_escape(
    char: str, name: str
) -> None:
    """``_sanitize_log_detail`` cleans provider-supplied diagnostic
    strings before posting them to the GitHub Issue body. Pre-fix the
    narrow ``_CONTROL_CHARS_RE = re.compile(r"[\\x00-\\x1f\\x7f]")``
    in ``src/feed/reporting.py:30`` covers neither the C1 controls
    nor the wider canonical set; the delegation to ``clean_message``
    inherits the same drift so the C1 byte slips through.
    """
    payload = f"OSM diagnostic: {char} done"
    sanitized = _sanitize_log_detail(payload)
    assert char not in sanitized, (
        f"{name} ({char!r}) survived _sanitize_log_detail: {sanitized!r}"
    )


@pytest.mark.parametrize("char,name", _C1_TERMINAL_ESCAPE_CHARS)
def test_http_sanitize_exception_msg_strips_c1_terminal_escape(
    char: str, name: str
) -> None:
    """``_sanitize_exception_msg`` rewrites
    ``RequestException.args[0]`` for every network-level error
    surfaced by ``request_safe``. Pre-fix the
    ``sanitize_log_message(strip_control_chars=False)`` delegation
    leaks the 8-bit C1 byte into every downstream
    ``logger.error("... %s ...", str(exc))`` site.
    """
    payload = (
        "ConnectionError: failed to fetch "
        f"https://example.com/path?q={char}injected"
    )
    sanitized = _sanitize_exception_msg(payload)
    assert char not in sanitized, (
        f"{name} ({char!r}) survived _sanitize_exception_msg: {sanitized!r}"
    )


@pytest.mark.parametrize("char,name", _C1_TERMINAL_ESCAPE_CHARS)
def test_safe_formatter_format_exception_strips_c1_terminal_escape(
    char: str, name: str
) -> None:
    """The traceback rendered by ``SafeFormatter.formatException`` is
    appended to every log record carrying ``exc_info``. Pre-fix the
    formatter passes ``strip_control_chars=False`` to preserve
    readable newlines — but that also leaks the C1 family so a
    hostile exception text containing ``\\x9b31m`` smuggles a SGR
    colour command into operator-facing log output.
    """
    formatter = SafeFormatter("%(message)s")
    try:
        raise ValueError(f"VOR error: {char}injected")
    except ValueError:
        import sys

        ei: Any = sys.exc_info()
        rendered = formatter.formatException(ei)

    assert char not in rendered, (
        f"{name} ({char!r}) survived "
        f"SafeFormatter.formatException: {rendered!r}"
    )


@pytest.mark.parametrize("char,name", _C1_TERMINAL_ESCAPE_CHARS)
def test_safe_json_formatter_format_exception_strips_c1_terminal_escape(
    char: str, name: str
) -> None:
    """``SafeJSONFormatter.formatException`` shares the same drift —
    the JSON-formatted log line carries the rendered traceback
    verbatim (``ensure_ascii=False`` preserves Unicode), so a C1
    primitive in an upstream exception slips through into structured
    logs ingested by SIEM / observability stacks that may render
    them on a C1-honouring terminal.
    """
    formatter = SafeJSONFormatter()
    try:
        raise RuntimeError(f"Hostile: {char}forged")
    except RuntimeError:
        import sys

        ei: Any = sys.exc_info()
        rendered = formatter.formatException(ei)

    assert char not in rendered, (
        f"{name} ({char!r}) survived "
        f"SafeJSONFormatter.formatException: {rendered!r}"
    )


# ---------------------------------------------------------------------------
# End-to-end PoC — the public ``feed_health.json`` artefact must NOT
# carry an 8-bit C1 / DEL byte even when a hostile upstream plants one
# in an exception message.
# ---------------------------------------------------------------------------


def test_feed_health_json_does_not_carry_8bit_csi() -> None:
    """End-to-end PoC: a hostile upstream raises an exception whose
    text contains the 8-bit CSI primitive ``\\x9b31m`` (a SGR colour
    command). The exception is routed through
    ``RunReport.record_exception`` -> ``clean_message`` -> the public
    ``docs/feed_health.json`` artefact via
    ``build_feed_health_payload`` -> ``json.dumps``.

    Pre-fix: the C1 byte survives ``clean_message`` and lands in the
    JSON. Operators who ``cat docs/feed_health.json`` on a 8-bit-C1
    -honouring terminal trigger the SGR command — the standard ANSI-
    escape forging primitive succeeds despite the ``_ANSI_ESCAPE_RE``
    defence at the 7-bit boundary.

    Post-fix: the unconditional always-strip floor in
    ``_INVISIBLE_DANGEROUS_RE`` removes the byte, so the published
    JSON carries only printable / readable text.
    """
    report = RunReport(statuses=[])
    report.record_exception(
        RuntimeError("VOR error: \x9b31mFAKE OK\x9b0m injected via 8-bit CSI")
    )

    payload = build_feed_health_payload(report, _empty_metrics())
    rendered = json.dumps(payload, ensure_ascii=False)

    assert "\x9b" not in rendered, (
        "8-bit CSI (U+009B) leaked into the published feed_health.json — "
        f"rendered payload: {rendered!r}"
    )


def test_feed_health_json_does_not_carry_8bit_osc() -> None:
    """End-to-end PoC: a hostile upstream emits an error message
    containing the 8-bit OSC primitive ``\\x9d0;HACKED\\x07`` (a
    terminal-title rewrite). The same RunReport -> feed_health.json
    pipeline as the CSI PoC. Post-fix the OSC byte is stripped at
    the always-strip floor.
    """
    report = RunReport(statuses=[])
    report.add_error_message(
        clean_message(
            "OEBB error: \x9d0;HACKED\x07 terminal-title rewrite primitive"
        )
    )

    payload = build_feed_health_payload(report, _empty_metrics())
    rendered = json.dumps(payload, ensure_ascii=False)

    assert "\x9d" not in rendered, (
        "8-bit OSC (U+009D) leaked into the published feed_health.json — "
        f"rendered payload: {rendered!r}"
    )


# ---------------------------------------------------------------------------
# Inventory invariant — every 8-bit C1 / DEL code point in
# ``_INVISIBLE_DANGEROUS_RE`` is also covered by the canonical
# ``_CONTROL_CHARS_RE`` (the latter is the strip_control_chars=True
# path; both paths must agree on the C1 floor).
# ---------------------------------------------------------------------------


def test_invisible_dangerous_re_covers_8bit_c1_and_del() -> None:
    """Inventory invariant: ``_INVISIBLE_DANGEROUS_RE`` MUST cover the
    full ``\\x7f-\\x9f`` set (DEL + 32 C1 controls).

    The 8-bit terminal-escape primitives U+0080..U+009F are the
    canonical bypass shape against the 7-bit ``_ANSI_ESCAPE_RE``
    defence. Pinning the always-strip floor to include them is the
    closing-checklist for the BiDi-Mark / Terminal-Escape Drift
    family.

    A regression here means a future PR has narrowed the canonical
    floor — either intentionally (the readability contract was
    misread to require C1 preservation) or accidentally (a
    code-formatting tool collapsed the character class).
    """
    canonical = canonical_logging._INVISIBLE_DANGEROUS_RE
    missing: list[int] = []
    for cp in range(0x7F, 0xA0):
        if not canonical.fullmatch(chr(cp)):
            missing.append(cp)
    assert not missing, (
        "_INVISIBLE_DANGEROUS_RE is narrower than the 8-bit C1 / DEL "
        f"canonical floor; missing {len(missing)} code point(s): "
        + ", ".join(f"U+{cp:04X}" for cp in missing)
        + "\nThe always-strip floor must cover the 8-bit terminal-escape "
        "primitive set so the strip_control_chars=False sibling paths "
        "(clean_message, _sanitize_log_detail, _sanitize_exception_msg, "
        "SafeFormatter.formatException, SafeJSONFormatter.formatException) "
        "inherit the defence."
    )


def test_invisible_dangerous_re_subset_of_control_chars_re() -> None:
    """Inventory invariant: every code point matched by
    ``_INVISIBLE_DANGEROUS_RE`` (the always-strip floor) must also
    match ``_CONTROL_CHARS_RE`` (the strip_control_chars=True full
    set). The two paths must agree on the strip set so widening one
    cannot drift apart from the other.
    """
    canonical_invisible = canonical_logging._INVISIBLE_DANGEROUS_RE
    canonical_control = canonical_logging._CONTROL_CHARS_RE

    canonical_code_points: list[int] = []
    for cp in range(0x110000):
        if canonical_invisible.fullmatch(chr(cp)):
            canonical_code_points.append(cp)

    assert canonical_code_points, (
        "Canonical _INVISIBLE_DANGEROUS_RE matches nothing — likely a "
        "regression in the canonical regex itself"
    )

    missing: list[int] = []
    for cp in canonical_code_points:
        if not canonical_control.fullmatch(chr(cp)):
            missing.append(cp)

    assert not missing, (
        "_CONTROL_CHARS_RE is narrower than _INVISIBLE_DANGEROUS_RE; "
        f"missing {len(missing)} code point(s): "
        + ", ".join(f"U+{cp:04X}" for cp in missing[:20])
        + (" …" if len(missing) > 20 else "")
        + "\nThe two regexes must stay in sync: any code point in the "
        "always-strip floor must also be in the full strip_control_chars=True "
        "set so the two paths cannot drift apart."
    )


# ---------------------------------------------------------------------------
# Regression: existing readability contract preserved.
# ---------------------------------------------------------------------------


def test_sanitize_log_message_strip_disabled_still_preserves_newlines() -> None:
    """Round 2 readability contract: ``\\n`` / ``\\r`` / ``\\t`` MUST
    survive ``strip_control_chars=False`` for traceback formatting.
    Round 3 widens ``_INVISIBLE_DANGEROUS_RE`` to add the C1 / DEL
    set but MUST NOT regress on the C0 readability contract.
    """
    payload = "line1\nline2\rline3\tindented"
    sanitized = sanitize_log_message(payload, strip_control_chars=False)
    assert "\n" in sanitized
    assert "\r" in sanitized
    assert "\t" in sanitized


def test_sanitize_log_message_strip_disabled_round2_charset_still_stripped() -> None:
    """Round 2 invariant: the BiDi / zero-width / line-terminator
    family MUST continue to be stripped on the
    ``strip_control_chars=False`` path. Round 3 is additive — it
    widens the always-strip floor without narrowing the existing
    set.
    """
    round2_chars = (
        "؜", "​", "‌", "‍", "‎", "‏",
        " ", " ", "‪", "‫", "‬", "‭",
        "‮", "⁦", "⁧", "⁨", "⁩", "﻿",
    )
    for char in round2_chars:
        payload = f"prefix{char}suffix"
        sanitized = sanitize_log_message(payload, strip_control_chars=False)
        assert char not in sanitized, (
            f"Round 2 char {hex(ord(char))} regressed on strip_control_chars=False"
        )


def test_sanitize_log_message_default_path_still_strips_c1() -> None:
    """Pre-existing invariant: the strip_control_chars=True (default)
    path strips the full canonical set including C1 / DEL via
    ``_CONTROL_CHARS_RE``. Round 3 must not regress this — the
    default path should still strip C1 / DEL (now via either the
    always-strip floor OR the explicit ``_CONTROL_CHARS_RE`` step,
    both of which cover the set).
    """
    for char, name in _C1_TERMINAL_ESCAPE_CHARS:
        payload = f"prefix{char}suffix"
        sanitized = sanitize_log_message(payload, strip_control_chars=True)
        assert char not in sanitized, (
            f"{name} ({char!r}) regressed on strip_control_chars=True path: "
            f"{sanitized!r}"
        )
