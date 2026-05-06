"""Regression tests for Bug 12B (two-word street names with house numbers).

Real Wiener-Linien titles sometimes include addresses like:

* ``Währinger Str 200`` (two words, abbreviated suffix without period)
* ``Mariahilfer Str. 12`` (two words, abbreviated with period)
* ``Dornbacher Straße 85`` (two words, full suffix)
* ``Breitenfurter Straße 236-238`` (two words, full suffix, range)
* ``Lerchenfelder Gürtel 12`` (two words, alternative suffix)

The previous ``ADDRESS_NO_RE`` only matched compounds where the suffix
was glued to the prefix (``Wienerstraße 12``), so the trailing house
number escaped the masking pass and ``LINE_CODE_RE`` picked it up as a
phantom transit line. The cached event #26 in
``cache/wl_9d709a/events.json`` actually rendered as
``41E/200: Ersatzbus 41E …`` with ``200`` mistaken for a line.

The fix adds a second alternation for ``<word>\\s+(?:Straße|Str\\.?|
Gasse|Platz|Allee|Weg|Steig|Ufer|Brücke|Kai|Ring|Gürtel|Lände|Damm|
Markt)\\s+\\d+(?:\\s*[-–—/]\\s*\\d+)?[A-Za-z]?`` so two-word street
names AND number ranges (``236-238``, ``12a``, ``200/2``) are correctly
masked before line detection runs.
"""

from __future__ import annotations

import re

import pytest

from src.providers.wl_lines import (
    ADDRESS_NO_RE,
    _detect_line_pairs_from_text,
)


class TestAddressRegexMatchesTwoWordStreets:
    @pytest.mark.parametrize(
        "text",
        [
            "Währinger Str 200",
            "Mariahilfer Str. 12",
            "Dornbacher Straße 85",
            "Breitenfurter Straße 236-238",
            "Lerchenfelder Gürtel 12",
            "Wienerstraße 200",
            "Pasettistraße 200",
        ],
    )
    def test_two_word_streets_match(self, text: str) -> None:
        m = ADDRESS_NO_RE.search(text)
        assert m is not None, f"Address regex must match {text!r}"
        # The match must consume the trailing number (or range).
        assert re.search(r"\d", m.group(0))


class TestLineDetectionDoesNotPickAddressNumbers:
    def test_waehringer_str_200_no_phantom_line(self) -> None:
        # The real cache item that exposed the bug.
        pairs = _detect_line_pairs_from_text(
            "Ersatzbus 41E halten bei Währinger Str 200"
        )
        line_tokens = [tok for tok, _ in pairs]
        assert "200" not in line_tokens
        assert "41E" in line_tokens

    def test_breitenfurter_strasse_range_no_phantom_lines(self) -> None:
        pairs = _detect_line_pairs_from_text(
            "Bauarbeiten Busse halten Breitenfurter Straße 236-238"
        )
        line_tokens = [tok for tok, _ in pairs]
        assert "236" not in line_tokens
        assert "238" not in line_tokens

    def test_dornbacher_strasse_no_phantom_line(self) -> None:
        pairs = _detect_line_pairs_from_text(
            "Linie 43A Dornbacher Straße 85"
        )
        line_tokens = [tok for tok, _ in pairs]
        assert "85" not in line_tokens
        assert "43A" in line_tokens

    def test_real_lines_still_detected(self) -> None:
        # Defence: actual line codes must still be found.
        pairs = _detect_line_pairs_from_text("U6 gesperrt")
        assert ("U6", "U6") in pairs

    def test_two_lines_still_detected(self) -> None:
        pairs = _detect_line_pairs_from_text("13A und U4 betroffen")
        line_tokens = [tok for tok, _ in pairs]
        assert "13A" in line_tokens
        assert "U4" in line_tokens
