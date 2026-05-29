"""Regression test: ``LINE_CODE_RE`` must not extract a standalone
non-D/non-O uppercase letter as a phantom line from a body scan.

Pre-fix ``LINE_CODE_RE`` carried a bare ``[A-Z]`` alternative — added
to support WL's real letters-only tram lines ``D`` (Wien Hauptbahnhof
— Nußdorf) and ``O`` (Praterstern — Raxstraße) — but the alternative
was unconstrained. Any standalone uppercase letter at a word boundary
matched, so realistic title shapes produced phantom line pairs that
poisoned the downstream identity bucket AND the rendered title prefix:

* ``"Information zu S-Bahn-Verkehr in Wien"`` →
  ``[('S', 'S')]``. ``\\bS\\b`` matches the ``S`` in ``S-Bahn`` because
  ``-`` is a non-word boundary.
* ``"A bis Karlsplatz"`` → ``[('A', 'A')]``. Sentence-start
  preposition ``"A"`` matched as a bus / tram letter.

The phantom then flowed into ``_wl_identity`` (wrong bucket / wrong
``first_seen`` key) AND into the rendered title via
``_ensure_line_prefix`` (``"S: Information zu S-Bahn-Verkehr…"``).

Scope of the fix
================
Only the BODY-SCANNER regex ``LINE_CODE_RE`` is tightened to ``[DO]``.
``_STRICT_LINE_TOKEN_RE`` (the prefix-strip gate) keeps the broader
``[A-Z]`` alternative because ``_extract_prefix_lines`` is anchored on
the colon-line-prefix shape ``LINE_PREFIX_STRIP_RE`` and the existing
``test_single_letter_line_codes.py`` pins acceptance of ``J``/``O``/``A``
as a defensive "future-proof for hypothetical lines" surface. The
concrete repro is body-scan only.
"""

from __future__ import annotations

from src.providers.wl_lines import (
    LINE_CODE_RE,
    _detect_line_pairs_from_text,
)


# ---- 1. Phantom single-letter extraction is rejected --------------------


def test_s_in_s_bahn_compound_is_not_a_phantom_line() -> None:
    """``"S-Bahn"`` must not yield ``('S', 'S')`` as a phantom line pair."""
    pairs = _detect_line_pairs_from_text("Information zu S-Bahn-Verkehr in Wien")
    assert pairs == [], (
        f"Phantom ``S`` extracted from ``S-Bahn`` compound: {pairs}"
    )


def test_sentence_start_uppercase_letter_is_not_a_phantom_line() -> None:
    """Standalone non-D/non-O uppercase letters are not WL line codes."""
    for title in (
        "A bis Karlsplatz",
        "B zum Hauptbahnhof",
        "X-Mal verspätet",
        "E wegen Bauarbeiten",
        "I am Stephansplatz",
    ):
        pairs = _detect_line_pairs_from_text(title)
        assert pairs == [], (
            f"Phantom single-letter extracted from {title!r}: {pairs}"
        )


def test_line_code_regex_rejects_non_do_single_letter() -> None:
    """Direct ``LINE_CODE_RE`` regression: ``\\bX\\b`` must not match."""
    # Realistic compound-word context.
    assert LINE_CODE_RE.findall("Verkehr in S-Bahn-Zonen") == []
    assert LINE_CODE_RE.findall("A bis Karlsplatz") == []
    # Multiple phantoms in one title.
    assert LINE_CODE_RE.findall("X und Y unbekannt") == []


# ---- 2. Real single-letter tram lines D and O still extract -------------


def test_tram_d_is_still_extracted() -> None:
    """``D`` is a real WL tram line — must still be detected."""
    pairs = _detect_line_pairs_from_text("Linie D im Bauarbeiten-Modus")
    extracted = [tok for tok, _ in pairs]
    assert "D" in extracted, (
        "Real WL tram line ``D`` (Wien Hbf — Nußdorf) must still extract"
    )
    # Direct regex check too.
    assert "D" in LINE_CODE_RE.findall("Linie D im Bauarbeiten-Modus")


def test_tram_o_is_still_extracted() -> None:
    """``O`` is a real WL tram line — must still be detected."""
    pairs = _detect_line_pairs_from_text("O: Demonstration Praterstern")
    extracted = [tok for tok, _ in pairs]
    assert "O" in extracted, "Real WL tram line ``O`` must still extract"


# ---- 3. Multi-character lines are unaffected by the tightening ----------


def test_multi_character_line_codes_still_extract() -> None:
    """Tightening ``[A-Z]`` → ``[DO]`` must not regress digit-bearing codes."""
    for text, must_contain in (
        ("U6: Sperre", "U6"),
        ("S40 verspätet", "S40"),
        ("Bus 41E Umleitung", "41E"),
        ("Linie 10A betroffen", "10A"),
        ("Rufbus N20 Information", "N20"),
    ):
        pairs = _detect_line_pairs_from_text(text)
        extracted = [tok for tok, _ in pairs]
        assert must_contain in extracted, (
            f"Real line {must_contain!r} dropped from {text!r}: {extracted}"
        )
