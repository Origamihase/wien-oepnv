"""Sentinel PoC: ASCII C0 controls (except TAB/LF/CR) survive every
``strip_control_chars=False`` sibling sink — log-injection drift on the
``_INVISIBLE_DANGEROUS_RE`` always-strip floor.

Threat model — ASCII C0 Drift (Round 4 of the *Log-Injection Drift* family)
--------------------------------------------------------------------------

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

Round 3 (journaled 2026-05-10 *8-bit C1 Terminal-Escape Drift*)
widened ``_INVISIBLE_DANGEROUS_RE`` to also cover ``\\x7f-\\x9f``
(DEL + the 32 ECMA-48 C1 controls). The 7-bit ``_ANSI_ESCAPE_RE``
is anchored to ``\\x1b`` and does not match the 8-bit equivalents,
so 8-bit CSI / OSC / DCS / PM / APC primitives bypassed the
defence on every ``strip_control_chars=False`` sibling sink until
that round.

Round 4 (this PoC) closes the **last canonical-floor sibling**: the
ASCII C0 controls (``\\x00-\\x08, \\x0B, \\x0C, \\x0E-\\x1F``) — i.e.
the C0 set MINUS the readable whitespace bytes ``\\x09`` (TAB),
``\\x0A`` (LF), ``\\x0D`` (CR) which preserve traceback readability.

Among the four canonical control-byte regexes in the project, three
already cover the C0-minus-readable-whitespace set:

* ``src/utils/text.py:_MARKDOWN_NORMALISE_UNSAFE_RE`` —
  ``[\\x00-\\x08\\x0b\\x0c\\x0e-\\x1f\\x7f-\\x9f...]``
* ``src/utils/stats.py:_CSV_CONTROL_CHARS_RE`` — same shape.
* ``src/build_feed.py:_CONTROL_RE`` — same shape (Round 6 of the
  BiDi-Mark Drift family widened it to the canonical floor).

Only ``src/utils/logging.py:_INVISIBLE_DANGEROUS_RE`` was left at:

    [\\x7f-\\x9f\\u061c\\u200b-\\u200f\\u2028-\\u202e\\u2066-\\u2069\\ufeff]

— covers DEL + C1 + BiDi + zero-width + LSEP/PSEP + BOM but NOT C0.
The narrow ``_CONTROL_CHARS_RE`` step that DOES strip C0 controls is
gated by ``strip_control_chars=True`` so every
``strip_control_chars=False`` sibling sink lets them through:

* ``src/feed/reporting.py:clean_message`` — canonical sanitiser for
  every provider detail / warning / error / exception text rendered
  into the public ``docs/feed-health.md`` artefact and the GitHub
  Issue body submitted by ``submit_auto_issue``.
* ``src/feed/reporting.py:_sanitize_log_detail`` — provider
  diagnostic strings posted to the issue body.
* ``src/utils/http.py:_sanitize_exception_msg`` — rewrites
  ``RequestException.args[0]`` for every network-level error caught
  by ``request_safe``; the exception text is then routed through
  every WARNING/ERROR site that logs ``str(exc)``.
* ``src/feed/logging_safe.py:SafeFormatter.formatException`` —
  renders the traceback for every ``log.exception(...)`` call in
  the production feed builder.
* ``src/feed/logging_safe.py:SafeJSONFormatter.formatException`` —
  same drift on the JSON log channel ingested by SIEM /
  observability stacks.

**Exploit shape:** A hostile upstream (compromised provider, MITM,
DNS hijack, planted cache file) returns an error response carrying
ASCII C0 primitives:

* ``\\x00`` (NUL) — many command-line tools (``cat``, ``less``,
  ``cut`` with default delimiter) treat NUL as terminator and
  truncate the rendered output. An attacker who plants ``\\x00``
  after the first error line **hides** all subsequent error /
  warning / detail entries from operators reading the artefact via
  these tools.
* ``\\x07`` (BEL) — terminal-bell trigger. ``cat docs/feed-health.md``
  on a TTY beeps for every ``\\x07`` byte; an upstream that plants
  many ``\\x07`` bytes turns the operator's terminal into a denial-
  of-attention vector.
* ``\\x08`` (BS) — backspace. ``cat`` on a TTY moves the cursor back
  one position; combined with replacement bytes, an attacker can
  spoof what the operator sees ("ERROR" overwritten with "OK ").
* ``\\x0c`` (FF) — form feed. Some terminals clear the screen on
  FF; ``\\x0c`` flooding lets the attacker hide the rest of the
  output.
* ``\\x1b`` (ESC) — start of CSI/OSC sequences. ``_ANSI_ESCAPE_RE``
  matches ``\\x1b`` followed by specific patterns (``[``, ``]``,
  Fe, two-byte). A bare ``\\x1b`` not followed by any of these
  patterns survives the regex but still triggers terminal mode
  changes on some legacy terminals.
* ``\\x0e``-``\\x0f`` (SO/SI) — Shift Out / Shift In. Switches the
  terminal to an alternate character set on some legacy terminals
  (DEC VT-100 G1 charset). An attacker plants ``\\x0e`` to make
  subsequent text render as line-drawing characters, garbling the
  log output.

The error flows through:

    HTTP fetch → request_safe.RequestException →
    _sanitize_exception_msg → exc.args[0] →
    log.error("... %s ...", str(exc)) → SafeFormatter →
    operator log stream / docs/feed-health.md / GitHub Issue body

If the operator's terminal interprets these bytes (``cat
docs/feed-health.md``, ``less`` without ``-r``/``-R`` configured to
strip control chars, ``tail -f log/diagnostics.log`` on any TTY,
``journalctl --no-pager``), the byte sequence triggers terminal
behaviour — **content hiding via NUL**, **denial-of-attention via
BEL**, **visual spoofing via BS**, **screen wipe via FF**, **legacy
charset switch via SO/SI**.

A second sink with the same shape is the public ``docs/feed-health.md``
markdown artefact (served by GitHub Pages from
``https://origamihase.github.io/wien-oepnv/feed-health.md`` and
rendered by Jekyll). The markdown file is also fetched by every
operator who runs ``cat docs/feed-health.md`` locally, by every IDE
that opens the markdown for editing, and by every CI artefact
viewer that displays the raw markdown text. The feed_health.md
emission via ``escape_markdown`` does NOT strip control chars
(``escape_markdown`` only escapes HTML and Markdown special chars
``[]()*_`@<>``), so a C0 byte in an error / warning text propagates
into the public markdown file verbatim.

A third sink is the GitHub Issue body submitted by
``submit_auto_issue`` — same emission shape (``escape_markdown``)
and same gap. Every repo watcher / contributor sees the rendered
issue body in their browser (GitHub's renderer typically handles
control chars but the raw markdown reachable via GitHub's API is
viewable verbatim) AND in email notifications (raw markdown).

Severity
--------

MEDIUM — real exploit shape against terminal renderers and against
the documented ``cat docs/feed-health.md`` artefact-inspection
workflow, defence-in-depth gap on the documented "always-strip
floor" design contract. The C0 set was the LAST canonical-floor
character family that ``_INVISIBLE_DANGEROUS_RE`` did not cover
while every other canonical sibling regex (markdown / CSV /
build_feed) already did. Closes the four-round drift on the
canonical-floor invariant in one cut.

Fix
---

Widen ``src/utils/logging.py:_INVISIBLE_DANGEROUS_RE`` from:

    [\\x7f-\\x9f\\u061c\\u200b-\\u200f\\u2028-\\u202e\\u2066-\\u2069\\ufeff]

to:

    [\\x00-\\x08\\x0b\\x0c\\x0e-\\x1f\\x7f-\\x9f\\u061c\\u200b-\\u200f
     \\u2028-\\u202e\\u2066-\\u2069\\ufeff]

— byte-exact mirror of ``_MARKDOWN_NORMALISE_UNSAFE_RE`` /
``_CSV_CONTROL_CHARS_RE`` / ``_CONTROL_RE`` (the three canonical
sibling regexes that already cover the floor). ``\\x09`` (TAB),
``\\x0A`` (LF), ``\\x0D`` (CR) remain outside the always-strip
floor so the readability contract for traceback formatting is
preserved.

The widening is **additive-only**: every code point the pre-fix
regex matched still matches post-fix; the only delta is the
addition of the 26 C0 control bytes (``\\x00-\\x08, \\x0B, \\x0C,
\\x0E-\\x1F``). All five sibling sinks inherit the defence in one
cut without any callsite change.

Inventory invariant
-------------------

The closing-checklist for the *Log-Injection Drift* family is now
amended with the auto-discoverable invariant
``test_invisible_dangerous_re_covers_canonical_floor`` which pins
``_INVISIBLE_DANGEROUS_RE`` against the canonical floor used by
``_MARKDOWN_NORMALISE_UNSAFE_RE``, ``_CSV_CONTROL_CHARS_RE``, and
``_CONTROL_RE``. A future regression that narrows any of the four
regexes (or widens one without the others) fails the test at
PR-review time.

The companion regexes ``_UNSAFE_URL_CHARS`` (URL boundary) and
``_UNSAFE_CHARS_RE`` (stations validation boundary) already cover
the C0 set (``_UNSAFE_URL_CHARS`` includes ``\\s`` plus ``\\x00-\\x1f``;
``_UNSAFE_CHARS_RE`` includes ``\\x00-\\x08\\x0b\\x0c\\x0e-\\x1f``)
so no widening is needed at those boundaries — the existing
inventory tests
(``test_unsafe_url_chars_regex_covers_canonical_invisible_dangerous_set``
and ``test_unsafe_chars_regex_covers_canonical_invisible_dangerous_set``)
continue to pass post-fix.
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
    render_feed_health_markdown,
)
from src.utils import logging as canonical_logging
from src.utils.http import _sanitize_exception_msg
from src.utils.logging import sanitize_log_message
from src.utils.text import _MARKDOWN_NORMALISE_UNSAFE_RE
from src.utils.stats import _CSV_CONTROL_CHARS_RE
from src.build_feed import _CONTROL_RE


def _empty_metrics() -> FeedHealthMetrics:
    """Helper: minimal ``FeedHealthMetrics`` for the end-to-end PoCs.
    The C0 byte under test rides the ``RunReport`` channel, not the
    metrics channel, so empty-zero metrics are sufficient."""
    return FeedHealthMetrics(
        raw_items=0,
        filtered_items=0,
        deduped_items=0,
        new_items=0,
        duplicate_count=0,
        duplicates=(),
    )


# Canonical ASCII C0 control set MINUS readable whitespace
# (TAB \x09, LF \x0a, CR \x0d). 26 code points total —
# the same set covered by ``_MARKDOWN_NORMALISE_UNSAFE_RE`` /
# ``_CSV_CONTROL_CHARS_RE`` / ``_CONTROL_RE``.
_C0_CONTROL_CHARS: tuple[tuple[str, str], ...] = (
    ("\x00", "U+0000 NUL — content-truncation primitive"),
    ("\x01", "U+0001 SOH"),
    ("\x02", "U+0002 STX"),
    ("\x03", "U+0003 ETX"),
    ("\x04", "U+0004 EOT"),
    ("\x05", "U+0005 ENQ"),
    ("\x06", "U+0006 ACK"),
    ("\x07", "U+0007 BEL — terminal-bell denial-of-attention"),
    ("\x08", "U+0008 BS — backspace visual-spoof primitive"),
    # NOTE: \x09 (TAB), \x0a (LF), \x0d (CR) intentionally excluded
    # — they are kept readable for traceback formatting per the
    # Round 2 readability contract (test_sanitize_log_message_strip_disabled_still_preserves_newlines).
    ("\x0b", "U+000B VT — vertical tab"),
    ("\x0c", "U+000C FF — form feed (terminal screen-wipe)"),
    ("\x0e", "U+000E SO — shift out / charset switch"),
    ("\x0f", "U+000F SI — shift in"),
    ("\x10", "U+0010 DLE"),
    ("\x11", "U+0011 DC1 — XON"),
    ("\x12", "U+0012 DC2"),
    ("\x13", "U+0013 DC3 — XOFF"),
    ("\x14", "U+0014 DC4"),
    ("\x15", "U+0015 NAK"),
    ("\x16", "U+0016 SYN"),
    ("\x17", "U+0017 ETB"),
    ("\x18", "U+0018 CAN"),
    ("\x19", "U+0019 EM"),
    ("\x1a", "U+001A SUB"),
    ("\x1b", "U+001B ESC — bare-ESC primitive"),
    ("\x1c", "U+001C FS"),
    ("\x1d", "U+001D GS"),
    ("\x1e", "U+001E RS"),
    ("\x1f", "U+001F US"),
)


# ---------------------------------------------------------------------------
# Per-code-point bypass tests — pre-fix every assert FAILS.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("char,name", _C0_CONTROL_CHARS)
def test_sanitize_log_message_strips_c0_with_strip_control_chars_disabled(
    char: str, name: str
) -> None:
    """``sanitize_log_message(strip_control_chars=False)`` MUST strip
    every ASCII C0 control (except TAB/LF/CR) — the unconditional
    always-strip floor must cover the C0 primitive set so the five
    sibling sinks (``clean_message``, ``_sanitize_log_detail``,
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


@pytest.mark.parametrize("char,name", _C0_CONTROL_CHARS)
def test_clean_message_strips_c0_controls(char: str, name: str) -> None:
    """``clean_message`` is the canonical sanitiser for every provider
    detail / warning / error / exception text rendered into the public
    ``docs/feed-health.md`` artefact AND the GitHub Issue body. It
    MUST strip the ASCII C0 control set — pre-fix the
    ``sanitize_log_message(strip_control_chars=False)`` delegation
    leaks them verbatim.
    """
    payload = f"VOR error: {char}injected"
    cleaned = clean_message(payload)
    assert char not in cleaned, (
        f"{name} ({char!r}) survived clean_message: {cleaned!r}"
    )


@pytest.mark.parametrize("char,name", _C0_CONTROL_CHARS)
def test_sanitize_log_detail_strips_c0_controls(char: str, name: str) -> None:
    """``_sanitize_log_detail`` cleans provider-supplied diagnostic
    strings before posting them to the GitHub Issue body. Pre-fix the
    delegation to ``clean_message`` inherits the C0 drift so the
    control byte slips through.
    """
    payload = f"OSM diagnostic: {char} done"
    sanitized = _sanitize_log_detail(payload)
    assert char not in sanitized, (
        f"{name} ({char!r}) survived _sanitize_log_detail: {sanitized!r}"
    )


@pytest.mark.parametrize("char,name", _C0_CONTROL_CHARS)
def test_http_sanitize_exception_msg_strips_c0_controls(
    char: str, name: str
) -> None:
    """``_sanitize_exception_msg`` rewrites
    ``RequestException.args[0]`` for every network-level error
    surfaced by ``request_safe``. Pre-fix the
    ``sanitize_log_message(strip_control_chars=False)`` delegation
    leaks the C0 byte into every downstream
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


@pytest.mark.parametrize("char,name", _C0_CONTROL_CHARS)
def test_safe_formatter_format_exception_strips_c0_controls(
    char: str, name: str
) -> None:
    """The traceback rendered by ``SafeFormatter.formatException`` is
    appended to every log record carrying ``exc_info``. Pre-fix the
    formatter passes ``strip_control_chars=False`` to preserve
    readable newlines — but that also leaks the C0 family so a
    hostile exception text containing ``\\x00`` (NUL) hides the rest
    of the rendered traceback when ``cat``d on a NUL-truncating tool.
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


@pytest.mark.parametrize("char,name", _C0_CONTROL_CHARS)
def test_safe_json_formatter_format_exception_strips_c0_controls(
    char: str, name: str
) -> None:
    """``SafeJSONFormatter.formatException`` shares the same drift —
    the JSON-formatted log line carries the rendered traceback. Pre-
    fix a C0 primitive in an upstream exception slips through into
    structured logs ingested by SIEM / observability stacks.
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
# End-to-end PoC — the public ``docs/feed-health.md`` artefact must
# NOT carry an ASCII C0 byte (except TAB/LF/CR) even when a hostile
# upstream plants one in an exception / warning / error message.
# ---------------------------------------------------------------------------


def test_feed_health_md_does_not_carry_c0_control_in_warning() -> None:
    """End-to-end PoC: a hostile upstream emits a warning containing
    ASCII NUL (``\\x00``). The warning is routed through
    ``RunReport.add_warning`` -> ``clean_message`` -> the public
    ``docs/feed-health.md`` artefact via ``render_feed_health_markdown``.

    Pre-fix: the NUL byte survives ``clean_message`` and lands in the
    rendered markdown. Operators who ``cat docs/feed-health.md`` on a
    NUL-truncating tool see only the content BEFORE the planted NUL —
    the rest of the warnings / errors / details are hidden, defeating
    the operator-visibility contract of the feed-health artefact.

    Post-fix: the unconditional always-strip floor in
    ``_INVISIBLE_DANGEROUS_RE`` removes the C0 byte, so the published
    markdown carries only printable / readable text.
    """
    report = RunReport(statuses=[])
    report.add_warning(
        "VOR cache warning: \x00 NUL-injected to truncate operator view"
    )
    report.add_error_message(
        "Subsequent error that pre-fix would be hidden by the NUL truncation"
    )

    rendered = render_feed_health_markdown(report, _empty_metrics())

    assert "\x00" not in rendered, (
        "ASCII NUL leaked into the public docs/feed-health.md — "
        "operators tooling that truncates at NUL would hide subsequent "
        "warnings / errors / details from view"
    )


def test_feed_health_md_does_not_carry_bel_in_error() -> None:
    """End-to-end PoC: a hostile upstream emits an error message
    containing the ASCII BEL byte (``\\x07``). Pre-fix the BEL byte
    flows verbatim into ``docs/feed-health.md``; ``cat`` on a TTY
    triggers a terminal beep for every BEL byte. An attacker who
    floods the error log with BEL bytes turns the operator's
    terminal into a denial-of-attention vector.
    """
    report = RunReport(statuses=[])
    report.add_error_message(
        "OEBB fetch error: \x07\x07\x07 BEL-flood denial-of-attention"
    )

    rendered = render_feed_health_markdown(report, _empty_metrics())

    assert "\x07" not in rendered, (
        "ASCII BEL leaked into the public docs/feed-health.md — "
        "the rendered markdown can be weaponised as a terminal-bell "
        "denial-of-attention vector against operators using `cat`."
    )


def test_feed_health_md_does_not_carry_bs_in_provider_detail() -> None:
    """End-to-end PoC: a hostile upstream returns an HTTP error body
    that surfaces in a provider detail. The detail flows through
    ``provider_error`` -> ``clean_message`` -> ``ProviderReport.detail``
    -> ``render_feed_health_markdown`` -> ``docs/feed-health.md``.

    Pre-fix: the BS byte (``\\x08``) survives ``clean_message`` and
    lands in the markdown. Combined with replacement bytes (``ERROR
    \\x08\\x08\\x08\\x08\\x08OK ``), an attacker spoofs what the
    operator sees in the rendered terminal output — the documented
    "FAKE OK" terminal-spoof primitive.
    """
    report = RunReport(statuses=[("oebb", True)])
    report.provider_error(
        "oebb",
        "ERROR\x08\x08\x08\x08\x08OK   spoof primitive in upstream HTTP body",
    )

    rendered = render_feed_health_markdown(report, _empty_metrics())

    assert "\x08" not in rendered, (
        "ASCII BS leaked into the public docs/feed-health.md — "
        "the rendered markdown carries a documented terminal-spoof "
        "primitive that lets attacker-controlled text overwrite the "
        "operator's view of the error message."
    )


# ---------------------------------------------------------------------------
# Inventory invariant — ``_INVISIBLE_DANGEROUS_RE`` MUST cover every
# C0 control (except TAB/LF/CR) that the canonical sibling regexes
# (``_MARKDOWN_NORMALISE_UNSAFE_RE`` / ``_CSV_CONTROL_CHARS_RE`` /
# ``_CONTROL_RE``) already cover.
# ---------------------------------------------------------------------------


def test_invisible_dangerous_re_covers_c0_minus_readable_whitespace() -> None:
    """Inventory invariant: ``_INVISIBLE_DANGEROUS_RE`` MUST cover the
    full ``\\x00-\\x08, \\x0B, \\x0C, \\x0E-\\x1F`` set (the ASCII C0
    controls minus readable whitespace).

    Three canonical sibling regexes already cover this set:

    * ``src/utils/text.py:_MARKDOWN_NORMALISE_UNSAFE_RE``
    * ``src/utils/stats.py:_CSV_CONTROL_CHARS_RE``
    * ``src/build_feed.py:_CONTROL_RE``

    The always-strip floor in ``_INVISIBLE_DANGEROUS_RE`` MUST agree
    with this canonical floor so every ``strip_control_chars=False``
    sibling path (``clean_message``, ``_sanitize_log_detail``,
    ``_sanitize_exception_msg``, ``SafeFormatter.formatException``,
    ``SafeJSONFormatter.formatException``) inherits the same defence.

    A regression here means a future PR has narrowed the canonical
    floor — either intentionally (the readability contract was
    misread to require C0 preservation beyond TAB/LF/CR) or
    accidentally (a code-formatting tool collapsed the character
    class).
    """
    canonical = canonical_logging._INVISIBLE_DANGEROUS_RE
    missing: list[int] = []
    # The canonical floor is C0 controls EXCEPT readable whitespace.
    excluded = {0x09, 0x0A, 0x0D}  # TAB, LF, CR
    for cp in range(0x00, 0x20):
        if cp in excluded:
            continue
        if not canonical.fullmatch(chr(cp)):
            missing.append(cp)
    assert not missing, (
        "_INVISIBLE_DANGEROUS_RE is narrower than the C0-minus-"
        "readable-whitespace canonical floor; missing "
        f"{len(missing)} code point(s): "
        + ", ".join(f"U+{cp:04X}" for cp in missing)
        + "\nThe always-strip floor must cover the C0 control set "
        "(except TAB/LF/CR) so the strip_control_chars=False sibling "
        "paths (clean_message, _sanitize_log_detail, "
        "_sanitize_exception_msg, SafeFormatter.formatException, "
        "SafeJSONFormatter.formatException) inherit the defence."
    )


def test_invisible_dangerous_re_matches_canonical_sibling_regexes() -> None:
    """Inventory invariant: ``_INVISIBLE_DANGEROUS_RE`` MUST be at
    least as wide as the canonical sibling regexes
    ``_MARKDOWN_NORMALISE_UNSAFE_RE`` / ``_CSV_CONTROL_CHARS_RE`` /
    ``_CONTROL_RE`` on the C0 control axis.

    A regression that narrows ``_INVISIBLE_DANGEROUS_RE`` below any
    sibling's coverage of C0 controls re-opens the
    ``strip_control_chars=False`` sibling paths to log-injection via
    the bytes that the markdown / CSV / RSS-XML writer regexes
    already block.
    """
    canonical = canonical_logging._INVISIBLE_DANGEROUS_RE
    siblings = (
        ("_MARKDOWN_NORMALISE_UNSAFE_RE", _MARKDOWN_NORMALISE_UNSAFE_RE),
        ("_CSV_CONTROL_CHARS_RE", _CSV_CONTROL_CHARS_RE),
        ("_CONTROL_RE (build_feed)", _CONTROL_RE),
    )
    excluded = {0x09, 0x0A, 0x0D}
    for cp in range(0x00, 0x20):
        if cp in excluded:
            continue
        ch = chr(cp)
        for sibling_name, sibling_regex in siblings:
            if sibling_regex.fullmatch(ch):
                # If the sibling matches it, the canonical must too.
                assert canonical.fullmatch(ch), (
                    f"_INVISIBLE_DANGEROUS_RE drift: U+{cp:04X} matched by "
                    f"{sibling_name} but NOT by _INVISIBLE_DANGEROUS_RE — "
                    "the always-strip floor must be at least as wide as "
                    "every canonical sibling regex on the C0 axis."
                )


# ---------------------------------------------------------------------------
# Regression: existing readability contract preserved (TAB/LF/CR
# survive strip_control_chars=False).
# ---------------------------------------------------------------------------


def test_sanitize_log_message_strip_disabled_still_preserves_tab_lf_cr() -> None:
    """Round 2 readability contract: ``\\n`` / ``\\r`` / ``\\t`` MUST
    survive ``strip_control_chars=False`` for traceback formatting.
    Round 4 widens ``_INVISIBLE_DANGEROUS_RE`` to add the C0 set
    (except TAB/LF/CR) but MUST NOT regress the readability contract.
    """
    payload = "line1\nline2\rline3\tindented"
    sanitized = sanitize_log_message(payload, strip_control_chars=False)
    assert "\n" in sanitized
    assert "\r" in sanitized
    assert "\t" in sanitized


def test_sanitize_log_message_strip_disabled_round3_charset_still_stripped() -> None:
    """Round 3 invariant: the 8-bit C1 / DEL family MUST continue to
    be stripped on the ``strip_control_chars=False`` path. Round 4 is
    additive — it widens the always-strip floor without narrowing
    the existing set.
    """
    payload = "trace: \x9b31m\x7f\x80\x9d injected"
    sanitized = sanitize_log_message(payload, strip_control_chars=False)
    for c in "\x9b\x7f\x80\x9d":
        assert c not in sanitized, (
            f"Round 3 8-bit C1 / DEL char {c!r} regressed in Round 4: "
            f"{sanitized!r}"
        )


def test_sanitize_log_message_strip_disabled_round2_charset_still_stripped() -> None:
    """Round 2 invariant: the BiDi / zero-width / line-terminator
    family MUST continue to be stripped on the
    ``strip_control_chars=False`` path. Round 4 is additive.
    """
    payload = "trace: ‮​ ﻿ injected"
    sanitized = sanitize_log_message(payload, strip_control_chars=False)
    for c in "‮​ ﻿":
        assert c not in sanitized, (
            f"Round 2 BiDi / zero-width / line-terminator char {c!r} "
            f"regressed in Round 4: {sanitized!r}"
        )


# ---------------------------------------------------------------------------
# Round 4 inventory cross-check — ``feed_health.json`` (JSON) sink.
# JSON encodes C0 controls as \\u00xx escape sequences regardless of
# the always-strip floor (RFC 8259 disallows raw C0 in JSON strings),
# so the JSON file does NOT carry the raw byte even pre-fix. This
# regression test pins that contract — the JSON-escape protection is
# a SECOND layer of defence, but it does not protect the markdown /
# GitHub Issue body sinks which use ``escape_markdown`` (no C0
# strip). The Round 4 fix is what closes those sinks; this test
# confirms the JSON sink continues to JSON-escape the byte even
# post-fix.
# ---------------------------------------------------------------------------


def test_feed_health_json_continues_to_escape_c0_controls() -> None:
    """Sanity check: the JSON-encoded ``feed_health.json`` artefact
    JSON-escapes C0 controls regardless of the always-strip floor.
    Round 4's fix closes the markdown / GitHub-Issue-body sinks; the
    JSON sink was already protected by JSON's own escape rules. This
    test confirms the JSON sink continues to escape the byte even
    when the always-strip floor strips it earlier in the pipeline —
    the two layers of defence cooperate without conflict.
    """
    report = RunReport(statuses=[])
    report.add_warning(
        "VOR cache warning: \x00 NUL injected"
    )

    payload = build_feed_health_payload(report, _empty_metrics())
    rendered = json.dumps(payload, ensure_ascii=False)

    # Post-fix: the always-strip floor removes the byte before JSON
    # encoding, so neither raw NUL nor its JSON-escape sequence
    # appears in the output (the byte was already stripped).
    assert "\x00" not in rendered
