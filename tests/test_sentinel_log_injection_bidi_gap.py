"""Sentinel: close the BiDi-mark / Unicode-line-terminator gap in
``sanitize_log_message``.

Threat model
------------
``sanitize_log_message`` is the canonical log-injection / Trojan-Source
defence shared across every WARNING/ERROR site in ``src/`` and
``scripts/`` — the audit walker in
``test_sentinel_clear_text_logging_drift_utils`` enforces that every bound
exception flows through it. Pre-fix the underlying ``_CONTROL_CHARS_RE``
covers ASCII C0/C1, the CVE-2021-42574 BiDi formatting controls
(LRE/RLE/PDF/LRO/RLO and LRI/RLI/FSI/PDI), the zero-width family
(ZWSP/ZWNJ/ZWJ) and the BOM, but leaves five high-impact code points
unhandled:

* ``U+2028`` LINE SEPARATOR — splits a single sanitised log entry into
  two records in any consumer that honours Unicode line terminators
  (ECMAScript-pre-2019 ``JSON.parse``/``eval``, the GitHub PR-comment
  renderer, several YAML parsers, downstream SIEM splitters that key off
  Unicode whitespace). The forged second record can carry a
  ``ts=…`` line, a fake ``level=ERROR`` marker, anything the operator
  triages on.
* ``U+2029`` PARAGRAPH SEPARATOR — same family as U+2028, also matched
  by ``\\s`` in Python's regex but missed by ``_CONTROL_CHARS_RE``.
* ``U+200E`` LRM / ``U+200F`` RLM — invisible BiDi marks. Same Trojan-
  Source primitive as the already-stripped ``U+202A-U+202E``: a hostile
  payload prepends LRM/RLM to invert displayed text in a Unicode-aware
  terminal so an operator skimming a log misreads ``user=admin
  drop=table`` as ``drop=table user=admin`` (or the inverse).
* ``U+061C`` ARABIC LETTER MARK — the post-Unicode-6.3 BiDi control
  character. Same display-confusion blast radius as LRM/RLM and missing
  from every prior round.

The vulnerable code path is independent of the existing ``\\n``/``\\r``
escape: those are replaced *after* ANSI stripping but *before*
``_CONTROL_CHARS_RE.sub("")``, so they never fall through. The five
characters above are NOT in the escape list and NOT in the regex, so they
slip through verbatim.

The companion regex in ``src/utils/stations_validation.py``
(``_UNSAFE_CHARS_RE``) already covers ``\\u2028-\\u202e``, so the
codebase has divergent BiDi-defence shapes between the station validator
and the canonical log sanitiser. This file pins the union as the
canonical floor.
"""

from __future__ import annotations

import pytest

from src.utils.logging import sanitize_log_message

# ---------------------------------------------------------------------------
# Canonical "must be stripped" characters
# ---------------------------------------------------------------------------

_BIDI_AND_LINE_TERMINATOR_CHARS: tuple[tuple[str, str], ...] = (
    ("؜", "U+061C ARABIC LETTER MARK"),
    ("‎", "U+200E LEFT-TO-RIGHT MARK"),
    ("‏", "U+200F RIGHT-TO-LEFT MARK"),
    (" ", "U+2028 LINE SEPARATOR"),
    (" ", "U+2029 PARAGRAPH SEPARATOR"),
)

# ---------------------------------------------------------------------------
# PoC: each unhandled character slips past sanitize_log_message
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("char,name", _BIDI_AND_LINE_TERMINATOR_CHARS)
def test_sanitize_log_message_strips_bidi_and_line_terminators(
    char: str, name: str
) -> None:
    """Each unhandled BiDi / line-terminator code point must be removed.

    Pre-fix this asserts FAIL — the canonical sanitiser leaves the code
    point in place, so a hostile upstream payload can forge log lines
    (U+2028/U+2029) or visually invert operator-readable content
    (U+061C / U+200E / U+200F). Post-fix the union of all five is
    stripped from the output.
    """
    payload = f"prefix{char}suffix"
    sanitized = sanitize_log_message(payload)

    assert char not in sanitized, (
        f"{name} ({char!r}) leaked through sanitize_log_message: {sanitized!r}"
    )


def test_sanitize_log_message_strips_compound_bidi_payload() -> None:
    """A multi-character compound payload must be fully neutralised.

    Mirrors the realistic shape an attacker would inject — a chain of
    BiDi marks plus a Unicode line terminator — to confirm the regex
    treats the whole family as a single class rather than punching
    holes per character.
    """
    payload = (
        "user=victim "
        "level=ERROR ts=hostile‮payload"
        "‎injected؜"
    )
    sanitized = sanitize_log_message(payload)

    for char, name in _BIDI_AND_LINE_TERMINATOR_CHARS:
        assert char not in sanitized, (
            f"{name} survived in compound payload: {sanitized!r}"
        )


# ---------------------------------------------------------------------------
# Regression: pre-existing behaviour must keep working
# ---------------------------------------------------------------------------


def test_sanitize_log_message_preserves_newline_escape_contract() -> None:
    """Real ``\\n``/``\\r``/``\\t`` continue to be escaped (not stripped).

    The fix tightens ``_CONTROL_CHARS_RE`` only — the two-step pipeline
    (ANSI strip → newline ESCAPE → control-char strip) MUST remain
    intact so existing tests (``test_json_log_leak``, the audit walker)
    keep passing.
    """
    sanitized = sanitize_log_message("a\nb\rc\td")
    assert sanitized == "a\\nb\\rc\\td"


def test_sanitize_log_message_preserves_existing_bidi_strip_contract() -> None:
    """The existing CVE-2021-42574 BiDi / zero-width strip MUST persist.

    Anchors the families covered by the prior regex so a future regex
    refactor cannot accidentally narrow what's already protected.
    """
    payload = (
        "x‪y‫z‬A‭B‮C"  # LRE/RLE/PDF/LRO/RLO
        "⁦D⁧E⁨F⁩G"          # LRI/RLI/FSI/PDI
        "​H‌I‍J"                 # ZWSP/ZWNJ/ZWJ
        "﻿K"                                # BOM
    )
    sanitized = sanitize_log_message(payload)

    for already_covered in (
        "‪", "‫", "‬", "‭", "‮",
        "⁦", "⁧", "⁨", "⁩",
        "​", "‌", "‍",
        "﻿",
    ):
        assert already_covered not in sanitized, (
            f"Pre-existing BiDi/ZW strip regressed: {already_covered!r} "
            f"survived in {sanitized!r}"
        )


def test_sanitize_log_message_with_strip_disabled_still_redacts_secrets() -> None:
    """``strip_control_chars=False`` (traceback path) keeps redaction.

    Even when control-char stripping is disabled (e.g. for traceback
    readability), the secret-redaction patterns must still fire — the
    fix only touches the strip path, not the redact path.
    """
    payload = 'config: api_key = "ABCDEFGHIJKLMNOPQRST"'
    sanitized = sanitize_log_message(payload, strip_control_chars=False)
    assert "ABCDEFGHIJKLMNOPQRST" not in sanitized
    assert "***" in sanitized
