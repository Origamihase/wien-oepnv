"""Regression tests for ``_extract_prefix_lines`` false-positive guards.

The Wiener Linien title parser at
``src/providers/wl_lines.py:_extract_prefix_lines`` historically
matched anything of the shape ``<alphanumeric>: <body>`` as a line
prefix. Two pathological inputs slipped through:

1. **Time-shaped prefix** — ``17:30 Uhr Verspätung`` was parsed as
   ``lines=["17"], body="30 Uhr Verspätung"``. The leading ``17``
   coincidentally matches the regex ``[A-Za-z0-9]+`` (and the
   strict line-token pattern, since ``17`` IS a valid line-code
   shape), so only the disambiguator ``:`` -> digit / ``:`` -> space
   can tell time from line prefix. The fix tightens the trailing
   ``\\s*`` to ``\\s+`` so the colon must be followed by whitespace.

2. **Generic word prefix** — ``Achtung: Sperre`` /
   ``Information: Test`` / ``Hinweis: Umleitung`` was parsed as
   ``lines=["ACHTUNG"], body="Sperre"`` etc. The fix gates the
   prefix-strip on a strict line-token pattern requiring at least one
   digit, so pure-word prefixes are now passed through unchanged.

Both bugs were latent — current WL data always starts with a real
line code, so neither has triggered in production. The guards close
the surface against future upstream format changes (or hand-edited
cache entries) that could otherwise leak into ``_post_filter_wl``'s
title rebuild and mangle ``Achtung: Sperre`` into ``ACHTUNG: Sperre``.
"""
from __future__ import annotations

from src.providers.wl_lines import _extract_prefix_lines


def test_time_prefix_not_stripped() -> None:
    """``17:30 Uhr Verspätung`` keeps the time fragment intact."""
    body, lines = _extract_prefix_lines("17:30 Uhr Verspätung")
    assert lines == [], (
        f"Time fragment was parsed as line code: lines={lines}"
    )
    assert body == "17:30 Uhr Verspätung", (
        f"Time-prefixed title was mangled: body={body!r}"
    )


def test_word_prefix_not_stripped() -> None:
    """``Achtung: Sperre`` and siblings stay intact."""
    for title in (
        "Achtung: Sperre wegen Bauarbeiten",
        "Information: Umleitung der Linie",
        "Hinweis: Verspätung erwartet",
        "Linie: 40 wird umgeleitet",
    ):
        body, lines = _extract_prefix_lines(title)
        assert lines == [], (
            f"Generic word was parsed as line code in {title!r}: lines={lines}"
        )
        assert body == title, (
            f"Word-prefixed title was mangled: body={body!r}"
        )


def test_valid_line_prefixes_still_extracted() -> None:
    """Real Vienna/ÖBB line prefixes still round-trip correctly."""
    cases = [
        ("40A: Umleitung", ["40A"], "Umleitung"),
        ("U1: Verspätung", ["U1"], "Verspätung"),
        ("S40: Test", ["S40"], "Test"),
        ("40+41: Betrieb ab Gersthof", ["40", "41"], "Betrieb ab Gersthof"),
        ("41E/10A: Ersatzbus", ["41E", "10A"], "Ersatzbus"),
        ("N6/N71: Umleitung", ["N6", "N71"], "Umleitung"),
        ("27A/28A/29A: Fronleichnamsumzug", ["27A", "28A", "29A"], "Fronleichnamsumzug"),
        ("40: 40+41: Stacked title", ["40", "41"], "Stacked title"),
        ("9, 40, 41: Umleitung", ["9", "40", "41"], "Umleitung"),
    ]
    for title, expected_lines, expected_body in cases:
        body, lines = _extract_prefix_lines(title)
        assert lines == expected_lines, (
            f"Expected lines {expected_lines}, got {lines} for {title!r}"
        )
        assert body == expected_body, (
            f"Expected body {expected_body!r}, got {body!r} for {title!r}"
        )


def test_rufbus_prefix_extracts_only_strict_line_token() -> None:
    """``Rufbus N20: …`` keeps N20; ``Rufbus Achtung: …`` is left alone."""
    body, lines = _extract_prefix_lines("Rufbus N20: Betriebshinweis")
    assert lines == ["N20"]
    assert body == "Betriebshinweis"

    # Synthetic edge case: if a Rufbus block ever carries a non-line
    # token, the strict-token gate must reject the strip rather than
    # surface a mangled prefix.
    body, lines = _extract_prefix_lines("Rufbus Achtung: Hinweis")
    assert lines == [], (
        f"Non-line Rufbus token leaked through strict gate: lines={lines}"
    )
    assert body == "Rufbus Achtung: Hinweis"


def test_multiword_prefix_not_stripped() -> None:
    """``Strecke Heiligenstadt: …`` (space inside the prefix) is left alone.

    The space inside the prefix block already fails the
    ``[A-Za-z0-9]+`` greedy match — pre-fix this still worked. The
    test pins the existing behaviour so a future widening of the
    prefix regex doesn't re-introduce the false positive.
    """
    body, lines = _extract_prefix_lines("Strecke Heiligenstadt: ab 10:00")
    assert lines == []
    assert body == "Strecke Heiligenstadt: ab 10:00"


def test_colon_without_whitespace_not_stripped() -> None:
    """``U1:Sperre`` (no whitespace after colon) is left alone.

    The strict ``:\\s+`` requirement closes the time-prefix false
    positive (``17:30 …``). The trade-off is that a hypothetical WL
    title without a space after the colon (``U1:Sperre``) is no
    longer recognised as having a line prefix. Every cached WL/ÖBB
    title in the production cache carries ``: `` with a space, so
    this is lossless in practice and the test pins the new contract.
    """
    body, lines = _extract_prefix_lines("U1:Sperre wegen Fahrzeug")
    # Without whitespace after the colon, no prefix strip happens.
    assert lines == [], (
        f"Colon-without-space was treated as line prefix: lines={lines}"
    )
    assert body == "U1:Sperre wegen Fahrzeug"


def test_empty_title_passes_through() -> None:
    body, lines = _extract_prefix_lines("")
    assert lines == []
    assert body == ""


def test_no_prefix_at_all_passes_through() -> None:
    body, lines = _extract_prefix_lines("Betrieb ab Gersthof")
    assert lines == []
    assert body == "Betrieb ab Gersthof"
