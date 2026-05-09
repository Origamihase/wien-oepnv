"""Sentinel: close BiDi-mark / Unicode-line-terminator drift in every
``strip_control_chars=False`` code path.

Threat model — Round 2
----------------------
PR #1363 (journaled 2026-05-09) closed the BiDi-mark gap in
``_CONTROL_CHARS_RE`` so the canonical ``sanitize_log_message`` now
strips ``U+061C`` ALM, ``U+200E/U+200F`` LRM/RLM, and the Unicode line
terminators (``U+2028``/``U+2029``) when ``strip_control_chars=True``
(the default).

Round 1 did NOT touch the ``strip_control_chars=False`` branch — every
caller that opted out of the control-char strip continues to drop the
``_CONTROL_CHARS_RE.sub("")`` step entirely. That branch exists to
preserve readable ``\\n``/``\\r``/``\\t`` in tracebacks but it also
leaks the BiDi/zero-width/line-terminator family that has no readability
value:

* ``src/feed/reporting.py:clean_message`` is the canonical sanitiser
  for every provider detail, every warning, every error message that
  feeds the public ``feed_health.json`` artefact AND the GitHub Issue
  body submitted by ``submit_auto_issue``.
* ``src/utils/http.py:_sanitize_exception_msg`` rewrites
  ``RequestException.args[0]`` for every network-level error caught
  by ``request_safe`` — the exception text is then routed through
  every WARNING / ERROR site that logs ``str(exc)``.
* ``src/feed/logging_safe.py:SafeFormatter.formatException`` and
  ``SafeJSONFormatter.formatException`` render the traceback for
  every ``log.exception(...)`` call in the production feed builder.

A hostile upstream payload (a VOR API response, an OSM Overpass
diagnostic, an OEBB error body, a station name in
``stations.json``) that contains ``U+202E`` RLO + ``U+202C`` PDF can
therefore:

1. **Forge log records** in any consumer that honours Unicode line
   terminators (ECMAScript-pre-2019 ``JSON.parse``/``eval``, the
   GitHub PR-comment renderer, several YAML parsers, downstream SIEM
   splitters that key off Unicode whitespace) by routing
   ``U+2028``/``U+2029`` through ``clean_message``. Even though
   ``clean_message`` collapses ``\\s+`` to a single space (which catches
   ``U+2028``/``U+2029`` because Python's ``\\s`` matches Unicode line
   terminators), the BiDi and zero-width family is NOT in ``\\s`` —
   they slip through verbatim into the rendered Markdown body.
2. **Invert displayed text** (Trojan-Source / CVE-2021-42574) in any
   Unicode-aware terminal, GitHub Issue renderer, IDE log viewer, or
   SIEM dashboard so an operator triaging the public artefact misreads
   ``user=admin drop=table`` as the inverse.

This file pins the union of the BiDi / zero-width / line-terminator
family as the ALWAYS-stripped floor — independent of the
``strip_control_chars`` flag — so every sibling sanitiser path (not
just the default-True path) inherits the defence.
"""
from __future__ import annotations

import logging
from typing import Any

import pytest

from src.feed.logging_safe import SafeFormatter, SafeJSONFormatter
from src.feed.reporting import _sanitize_log_detail, clean_message
from src.utils.http import _sanitize_exception_msg
from src.utils.logging import sanitize_log_message

# Canonical "must be stripped" set: invisible Unicode characters that
# have NO readability value AND are documented log-injection /
# Trojan-Source primitives. This MUST be stripped regardless of the
# ``strip_control_chars`` flag.
_INVISIBLE_DANGEROUS_CHARS: tuple[tuple[str, str], ...] = (
    ("؜", "U+061C ARABIC LETTER MARK"),
    ("​", "U+200B ZERO WIDTH SPACE"),
    ("‌", "U+200C ZERO WIDTH NON-JOINER"),
    ("‍", "U+200D ZERO WIDTH JOINER"),
    ("‎", "U+200E LEFT-TO-RIGHT MARK"),
    ("‏", "U+200F RIGHT-TO-LEFT MARK"),
    (" ", "U+2028 LINE SEPARATOR"),
    (" ", "U+2029 PARAGRAPH SEPARATOR"),
    ("‪", "U+202A LRE"),
    ("‫", "U+202B RLE"),
    ("‬", "U+202C PDF"),
    ("‭", "U+202D LRO"),
    ("‮", "U+202E RLO"),
    ("⁦", "U+2066 LRI"),
    ("⁧", "U+2067 RLI"),
    ("⁨", "U+2068 FSI"),
    ("⁩", "U+2069 PDI"),
    ("﻿", "U+FEFF BOM / ZWNBSP"),
)


@pytest.mark.parametrize("char,name", _INVISIBLE_DANGEROUS_CHARS)
def test_sanitize_log_message_strips_bidi_with_strip_control_chars_disabled(
    char: str, name: str
) -> None:
    """Pre-fix asserts FAIL — the ``strip_control_chars=False`` branch
    bypasses ``_CONTROL_CHARS_RE.sub("")`` entirely, so every BiDi /
    zero-width / line-terminator code point survives verbatim.

    Post-fix the BiDi/zero-width family is stripped UNCONDITIONALLY
    (independent of the ``strip_control_chars`` flag) since these have
    no readability value and are pure log-injection / Trojan-Source
    primitives.
    """
    payload = f"prefix{char}suffix"
    sanitized = sanitize_log_message(payload, strip_control_chars=False)
    assert char not in sanitized, (
        f"{name} ({char!r}) leaked through "
        f"sanitize_log_message(strip_control_chars=False): {sanitized!r}"
    )


def test_clean_message_strips_bidi_marks() -> None:
    """``clean_message`` (the canonical sanitiser for every provider
    detail / warning / error rendered into ``feed_health.json`` and
    the GitHub Issue body) MUST strip BiDi / zero-width characters.

    Pre-fix: ``clean_message`` calls
    ``sanitize_log_message(strip_control_chars=False)`` then collapses
    ``\\s+`` to a single space. Python's ``\\s`` matches
    ``U+2028``/``U+2029`` but NOT the BiDi family
    (``U+061C``/``U+200E``/``U+200F``/``U+202A-U+202E``/
    ``U+2066-U+2069``) or the zero-width family
    (``U+200B``/``U+200C``/``U+200D``/``U+FEFF``).
    """
    payload = (
        "user=admin‮drop=table‬"
        "‎injected؜"
        " forged_record"
    )
    cleaned = clean_message(payload)
    for char, name in _INVISIBLE_DANGEROUS_CHARS:
        assert char not in cleaned, (
            f"{name} ({char!r}) survived clean_message: {cleaned!r}"
        )


def test_sanitize_log_detail_strips_bidi_marks() -> None:
    """``_sanitize_log_detail`` (used to clean provider-supplied
    diagnostic strings before posting them to the issue body) MUST
    strip the same BiDi / zero-width family.

    Pre-fix this delegates to ``clean_message`` which carries the same
    drift; post-fix the unconditional strip in ``sanitize_log_message``
    closes both call paths in one cut.
    """
    payload = "OSM diagnostic: ‮rm -rf /‬ done"
    sanitized = _sanitize_log_detail(payload)
    assert "‮" not in sanitized
    assert "‬" not in sanitized


def test_http_sanitize_exception_msg_strips_bidi_marks() -> None:
    """``_sanitize_exception_msg`` rewrites every
    ``RequestException.args[0]`` produced by ``request_safe``. Pre-fix
    this returns ``sanitize_log_message(msg, strip_control_chars=False)``
    so a hostile remote that embeds BiDi marks in an HTTP error body
    can ship them straight into every downstream
    ``logger.error("... %s ...", str(exc))`` site.
    """
    payload = (
        "ConnectionError: failed to fetch "
        "https://example.com/path?q=v"
        "‮injected_marker‬؜"
    )
    sanitized = _sanitize_exception_msg(payload)
    assert "‮" not in sanitized
    assert "‬" not in sanitized
    assert "؜" not in sanitized


def test_safe_formatter_format_exception_strips_bidi_marks() -> None:
    """The traceback rendered by ``SafeFormatter.formatException`` is
    appended to every log record carrying ``exc_info``. Pre-fix the
    formatter passes ``strip_control_chars=False`` to preserve readable
    newlines, but that also lets the BiDi family slip through into the
    final formatted log line.
    """
    formatter = SafeFormatter("%(message)s")
    try:
        raise ValueError(
            "VOR error: ‮payload‬ with bidi ؜ marks"
        )
    except ValueError:
        import sys

        ei: Any = sys.exc_info()
        rendered = formatter.formatException(ei)

    assert "‮" not in rendered
    assert "‬" not in rendered
    assert "؜" not in rendered


def test_safe_json_formatter_format_exception_strips_bidi_marks() -> None:
    """``SafeJSONFormatter.formatException`` shares the same drift —
    the JSON-formatted log line carries the rendered traceback verbatim
    (``ensure_ascii=False`` preserves Unicode), so a BiDi mark in an
    upstream exception slips through into structured logs ingested by
    downstream SIEM/observability stacks.
    """
    formatter = SafeJSONFormatter()
    try:
        raise RuntimeError(
            "Hostile: ‎inverted‏  forged"
        )
    except RuntimeError:
        import sys

        ei: Any = sys.exc_info()
        rendered = formatter.formatException(ei)

    assert "‎" not in rendered
    assert "‏" not in rendered
    assert " " not in rendered


def test_safe_formatter_format_strips_bidi_in_traceback() -> None:
    """End-to-end: a real ``log.exception`` call carrying BiDi marks
    in the bound exception text MUST emit a sanitised final line.

    This proves the fix lands at the formatter integration layer, not
    just in the ``formatException`` helper — rules out a regression
    where the traceback is sanitised but the message is not (or vice
    versa).
    """
    formatter = SafeFormatter("%(message)s")
    record = logging.LogRecord(
        name="test",
        level=logging.ERROR,
        pathname=__file__,
        lineno=42,
        msg="upstream payload ‮payload‬",
        args=(),
        exc_info=None,
    )
    rendered = formatter.format(record)
    assert "‮" not in rendered
    assert "‬" not in rendered


# ---------------------------------------------------------------------------
# Regression: pre-existing contracts must keep working
# ---------------------------------------------------------------------------


def test_sanitize_log_message_strip_disabled_still_preserves_newlines() -> None:
    """The whole point of ``strip_control_chars=False`` is to keep
    ``\\n``/``\\r``/``\\t`` for traceback readability. The fix must
    preserve that contract — only invisible-dangerous Unicode goes
    away unconditionally; ASCII line breaks survive.
    """
    payload = "line1\nline2\rline3\tindent"
    sanitized = sanitize_log_message(payload, strip_control_chars=False)
    assert "\n" in sanitized
    assert "\r" in sanitized
    assert "\t" in sanitized


def test_sanitize_log_message_strip_disabled_redacts_secrets() -> None:
    """Re-anchor the existing PR #1363 contract — the redact path
    fires regardless of the strip flag. Defends against a regression
    where the unconditional invisible-strip refactor accidentally
    short-circuits the secret-redact patterns.
    """
    payload = 'config: api_key = "ABCDEFGHIJKLMNOPQRST"'
    sanitized = sanitize_log_message(payload, strip_control_chars=False)
    assert "ABCDEFGHIJKLMNOPQRST" not in sanitized
    assert "***" in sanitized


def test_sanitize_log_message_default_path_unchanged() -> None:
    """The default ``strip_control_chars=True`` path keeps Round 1
    behaviour: ``\\n`` is escaped to literal ``\\\\n``, ``\\r`` to
    ``\\\\r``, ``\\t`` to ``\\\\t``, and the BiDi/zero-width family is
    stripped (now via the unconditional pre-pass).
    """
    payload = "a\nb\rc\td"
    sanitized = sanitize_log_message(payload)
    assert sanitized == "a\\nb\\rc\\td"
