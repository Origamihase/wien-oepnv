"""Regression tests for four WL text/line-masking findings.

Each of these slipped through the line-detection / topic-key / stop-name
normalisation paths and surfaced as phantom WL line tokens, degraded dedup
keys, or multi-line description rows in the RSS feed. All four reproduce
deterministically before the fixes are applied.

1. ``wl_lines.ADDRESS_NO_PRE_RE`` — required ``\\d+\\b`` so a house number
   with an alpha suffix (``Stiege 12A``, ``Nr. 5B``) escaped the mask. The
   trailing ``A``/``B`` was then picked up by ``LINE_CODE_RE``'s
   ``[0-9]{1,3}[A-Z]?`` branch as a phantom WL bus line. Fix mirrors the
   alpha-suffix already supported by sibling ``ADDRESS_NO_RE``.

2. ``wl_lines.TIME_RE`` — matched only ``HH:MM``, so the ``:SS`` tail of an
   ``HH:MM:SS`` timestamp survived; the trailing 2-digit ``SS`` was then
   extracted as a phantom WL line. Fix: optional ``(?::\\d{2})?``.

3. ``wl_text._GENERIC_FILLER`` — the ``betrieb\\s+ab.*`` alternation used a
   greedy ``.*`` that swallowed every downstream token to end-of-string,
   eating legitimate topic tokens. ``Sperre Betrieb ab Karlsplatz mit
   Unfall`` collapsed to ``sperre`` (the ``unfall`` topic token was lost),
   so two unrelated incidents that happen to share a "Betrieb ab ..."
   preamble degenerated to the same ``topic_key`` and collided downstream
   in the WL identity / dedup pipeline. Fix: ``\\s+\\S+`` (one location
   word) instead of ``.*``.

4. ``wl_fetch._stop_names_from_related`` (non-canonical fallback) used
   ``\\s{2,}`` to collapse whitespace runs, so a single embedded ``\\n`` /
   ``\\t`` in a relatedStops name survived into the RSS ``Haltestelle:``
   description line verbatim, breaking the single-line render. Fix:
   ``\\s+`` — matches the canonical shape of the sibling
   ``_normalize_whitespace`` helper.
"""

from __future__ import annotations

from src.providers.wl_fetch import _stop_names_from_related
from src.providers.wl_lines import (
    ADDRESS_NO_PRE_RE,
    TIME_RE,
    _detect_line_pairs_from_text,
)
from src.providers.wl_text import _topic_key_from_title


# --------------------------------------------------------------------------
# 1. ADDRESS_NO_PRE_RE — alpha suffix on the house number
# --------------------------------------------------------------------------


def test_address_pre_keyword_with_alpha_suffix_is_masked() -> None:
    """``Stiege 12A``, ``Nr. 5B``, etc. must mask the WHOLE number+suffix."""
    for raw in ("Stiege 12A", "Nr. 5B", "Tür 7", "Top 200a", "Hausnr. 3c"):
        assert ADDRESS_NO_PRE_RE.search(raw), raw
        # And ``_detect_line_pairs_from_text`` must not surface the suffix
        # as a phantom WL line:
        assert _detect_line_pairs_from_text(f"{raw} wegen Bauarbeiten") == [], raw


def test_address_pre_bare_number_still_masked() -> None:
    """The fix must not regress the original bare-number masking."""
    for raw in ("Stiege 12", "Nr. 5", "Tür 7"):
        assert _detect_line_pairs_from_text(f"{raw} wegen Bauarbeiten") == [], raw


# --------------------------------------------------------------------------
# 2. TIME_RE — optional seconds component
# --------------------------------------------------------------------------


def test_time_with_seconds_is_masked_fully() -> None:
    """``HH:MM:SS`` must mask the WHOLE timestamp, not leave ``:SS``."""
    masked = TIME_RE.sub(" ", "Sperre 17:30:45 Ausfall")
    assert "45" not in masked, masked
    assert _detect_line_pairs_from_text("Sperre 17:30:45 Ausfall") == []


def test_time_without_seconds_still_masked() -> None:
    """The fix must not regress the original ``HH:MM`` masking."""
    assert _detect_line_pairs_from_text("Sperre 17:30 Ausfall") == []
    assert _detect_line_pairs_from_text("U6 12:00 Verspätung") == [("U6", "U6")]


def test_real_line_codes_after_time_still_detected() -> None:
    """A real line code in a title with a timestamp must still extract."""
    pairs = _detect_line_pairs_from_text("U6 verspätet ab 17:30:00 wegen Sperre")
    assert ("U6", "U6") in pairs


# --------------------------------------------------------------------------
# 3. _GENERIC_FILLER — non-greedy 'Betrieb ab <Ort>' / 'Betrieb nur <Ort>'
# --------------------------------------------------------------------------


def test_topic_key_preserves_token_after_betrieb_ab() -> None:
    """``Sperre Betrieb ab Karlsplatz mit Unfall`` -> ``sperre unfall``.

    Pre-fix the greedy ``.*`` swallowed everything past "Betrieb ab" so
    ``unfall`` was lost and the key collapsed to ``sperre``.
    """
    assert _topic_key_from_title(
        "Sperre Betrieb ab Karlsplatz mit Unfall"
    ) == "sperre unfall"


def test_topic_key_preserves_token_after_betrieb_nur() -> None:
    """Same fix for the sibling ``betrieb nur <Ort>`` alternation."""
    assert _topic_key_from_title(
        "Sperre Betrieb nur Karlsplatz mit Unfall"
    ) == "sperre unfall"


def test_topic_key_two_unrelated_betrieb_ab_titles_dont_collide() -> None:
    """Two incidents sharing only a "Betrieb ab ..." preamble must produce
    DIFFERENT topic keys (the pre-fix collision-cause)."""
    k1 = _topic_key_from_title("Sperre Betrieb ab Karlsplatz mit Unfall")
    k2 = _topic_key_from_title(
        "Sperre Betrieb ab Karlsplatz wegen Demonstration"
    )
    assert k1 != k2, (
        f"Two unrelated incidents collapsed to the same topic_key {k1!r} — "
        f"would collide in the WL dedup / identity pipeline."
    )


def test_topic_key_without_betrieb_ab_is_unaffected() -> None:
    """The fix must not change keys for titles that don't carry the phrase."""
    assert _topic_key_from_title("Sperre wegen Unfall") == "sperre unfall"


# --------------------------------------------------------------------------
# 4. _stop_names_from_related — single-char whitespace runs
# --------------------------------------------------------------------------


def test_stop_name_with_embedded_newline_collapses_to_single_line() -> None:
    """A lone ``\\n`` in a relatedStops name must collapse, not survive."""
    nl = chr(10)
    result = _stop_names_from_related([{"name": f"Karls{nl}platz"}])
    assert result == ["Karls platz"], result


def test_stop_name_with_embedded_tab_collapses_to_single_line() -> None:
    """Same for a lone ``\\t``."""
    tab = chr(9)
    result = _stop_names_from_related([{"name": f"Stephans{tab}platz"}])
    assert result == ["Stephans platz"], result


def test_stop_name_with_normal_double_space_still_collapses() -> None:
    """The original behaviour (collapse runs of >= 2 ws chars) must hold."""
    result = _stop_names_from_related([{"name": "Karlsplatz  bei  Oper"}])
    assert result == ["Karlsplatz bei Oper"], result
