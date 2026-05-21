"""Regression tests for proper-noun preservation in the EN feed pipeline.

The Helsinki opus-mt-de-en translator routinely mistranslates Vienna
transit proper nouns ("Stephansplatz" → "Stephen's Square",
"Wiener Linien" → "Vienna Lines"). :func:`src.build_feed._mask_entities`
replaces every known brand / station / line identifier with a stable
``XENT<n>X`` placeholder before inference, and
:func:`src.build_feed._unmask_entities` restores the original surface
form afterwards. These tests pin the mask/unmask contract so future
refactors cannot silently break the round-trip.
"""
from __future__ import annotations

from typing import Any

from src import build_feed


def test_mask_entities_protects_brands() -> None:
    text = "Wiener Linien melden Störung; ÖBB betroffen."
    masked, mapping = build_feed._mask_entities(text)
    assert "Wiener Linien" not in masked
    assert "ÖBB" not in masked
    # Mapping is non-empty and contains the originals.
    assert "Wiener Linien" in mapping.values()
    assert "ÖBB" in mapping.values()


def test_mask_entities_protects_line_identifiers() -> None:
    text = "U6 verspätet; S40 entfällt; 5B fährt Umweg."
    masked, mapping = build_feed._mask_entities(text)
    assert "U6" not in masked
    assert "S40" not in masked
    assert "5B" not in masked
    assert set(mapping.values()) >= {"U6", "S40", "5B"}


def test_mask_entities_protects_known_stations() -> None:
    # Stations are pulled from data/stations.json; ``Stephansplatz`` and
    # ``Wien Hauptbahnhof`` are canonical entries.
    text = "Zwischen Wien Hauptbahnhof und Stephansplatz Schienenersatzverkehr."
    masked, mapping = build_feed._mask_entities(text)
    assert "Wien Hauptbahnhof" not in masked
    assert "Stephansplatz" not in masked
    assert "Wien Hauptbahnhof" in mapping.values()
    assert "Stephansplatz" in mapping.values()


def test_mask_entities_protects_wien_x_aliases() -> None:
    """Regression: aliases of canonical station names whose name is a
    compound form (e.g. ``Wien Mitte-Landstraße`` carrying the alias
    ``Wien Mitte``) must be protected. Pre-fix the EN feed showed
    titles like ``Wien Rennweg - Vienna Mitte`` because the masker
    only consumed canonical names — ``Wien Mitte`` was missing and
    the translator rewrote ``Wien`` → ``Vienna`` for that token.
    """
    text = "Wien Rennweg - Wien Mitte"
    masked, mapping = build_feed._mask_entities(text)
    assert "Wien Mitte" not in masked, (
        f"Wien Mitte leaked into the masked text; the translator would "
        f"rewrite it to Vienna Mitte. masked={masked!r}"
    )
    assert "Wien Rennweg" not in masked
    assert "Wien Mitte" in mapping.values()
    assert "Wien Rennweg" in mapping.values()


def test_mask_entities_longest_match_wins() -> None:
    """``Wien Hauptbahnhof`` must match before ``Hauptbahnhof`` so the
    placeholder covers the full compound name."""
    text = "Sperre am Wien Hauptbahnhof bis 18:00."
    masked, mapping = build_feed._mask_entities(text)
    assert "Wien Hauptbahnhof" not in masked
    assert "Hauptbahnhof" not in masked
    assert "Wien Hauptbahnhof" in mapping.values()


def test_mask_entities_deduplicates_repeats() -> None:
    """The same surface form is encoded once and reused so a sentence
    with two mentions survives the round trip with identical tokens."""
    text = "U6 verspätet. U6 fährt nicht."
    masked, mapping = build_feed._mask_entities(text)
    # Both ``U6`` occurrences map to the same placeholder.
    u6_keys = [k for k, v in mapping.items() if v == "U6"]
    assert len(u6_keys) == 1
    placeholder = u6_keys[0]
    assert masked.count(placeholder) == 2


def test_mask_entities_empty_input_returns_empty_mapping() -> None:
    masked, mapping = build_feed._mask_entities("")
    assert masked == ""
    assert mapping == {}


def test_unmask_entities_restores_originals() -> None:
    text = "U6 verspätet bei Stephansplatz; Wiener Linien informieren."
    masked, mapping = build_feed._mask_entities(text)
    assert build_feed._unmask_entities(masked, mapping) == text


def test_unmask_entities_tolerates_missing_placeholders() -> None:
    """Translator output may drop a placeholder; unmask must not leak
    a literal ``XENTnX`` token into the published feed."""
    mapping = {"XENT0X": "Wien Hauptbahnhof", "XENT1X": "U6"}
    # Translator returned an output that kept XENT0X but dropped XENT1X
    # AND introduced a stray XENT9X that isn't in the mapping.
    output = "Closure at XENT0X due to XENT9X works"
    restored = build_feed._unmask_entities(output, mapping)
    assert "Wien Hauptbahnhof" in restored
    # Stray placeholder is stripped, not left in the user-visible text.
    assert "XENT9X" not in restored
    assert "XENT" not in restored


def test_unmask_entities_no_mapping_returns_input_unchanged() -> None:
    assert build_feed._unmask_entities("plain text XENT0X", {}) == "plain text XENT0X"


def test_translate_text_round_trip_preserves_entities_when_pipeline_missing(
    monkeypatch: Any,
) -> None:
    """Even without a translation pipeline, ``_translate_text`` must
    return the original text (with entities intact) so the EN feed
    degrades gracefully."""
    monkeypatch.setattr(build_feed, "_get_translation_pipeline", lambda: None)
    text = "U6: Verspätung zwischen Wien Hauptbahnhof und Stephansplatz."
    assert build_feed._translate_text(text) == text


def test_translate_text_pipes_masked_text_through_pipeline(
    monkeypatch: Any,
) -> None:
    """The pipeline receives the MASKED text (not the original) so a
    real Helsinki model cannot mistranslate proper nouns."""
    seen: dict[str, Any] = {}

    def fake_pipeline(text: str, **kwargs: Any) -> list[dict[str, str]]:
        seen["input"] = text
        seen["kwargs"] = kwargs
        # The fake model echoes the input as the translation so we can
        # verify the unmask step preserves the original entities.
        return [{"translation_text": text}]

    monkeypatch.setattr(build_feed, "_get_translation_pipeline", lambda: fake_pipeline)
    text = "U6 verspätet bei Stephansplatz; Wiener Linien informieren."
    out = build_feed._translate_text(text)
    # The masked input given to the pipeline must NOT contain the
    # original proper nouns.
    assert "Stephansplatz" not in seen["input"]
    assert "Wiener Linien" not in seen["input"]
    assert "U6" not in seen["input"]
    # ``truncation=True`` is forwarded so long inputs don't crash Marian.
    assert seen["kwargs"].get("truncation") is True
    # The final output must restore the proper nouns verbatim.
    assert out == text


def test_translate_text_handles_dropped_placeholder_gracefully(
    monkeypatch: Any,
) -> None:
    """If the translator drops a placeholder the rest of the output is
    still restored and no stray ``XENT…`` token leaks to subscribers."""
    def fake_pipeline(text: str, **kwargs: Any) -> list[dict[str, str]]:
        # Strip every placeholder from the "translation" — simulates a
        # model that aggressively rewrites unfamiliar tokens.
        return [{"translation_text": build_feed._ENTITY_PLACEHOLDER_RE.sub("", text)}]

    monkeypatch.setattr(build_feed, "_get_translation_pipeline", lambda: fake_pipeline)
    text = "Wiener Linien informieren über U6."
    out = build_feed._translate_text(text)
    # No stray placeholders survive into the published feed.
    assert "XENT" not in out
    # The non-entity remainder is still present.
    assert "informieren" in out


def test_translation_state_is_a_single_dict() -> None:
    """CodeQL flagged the dual-global pattern as an unused variable;
    the refactor consolidates state into a single module-level dict
    so the analyser does not emit a false positive on the circuit
    breaker assignment."""
    assert isinstance(build_feed._TRANSLATION_STATE, dict)
    assert "pipeline" in build_feed._TRANSLATION_STATE
    assert "load_failed" in build_feed._TRANSLATION_STATE


def test_get_translation_pipeline_short_circuits_after_failure() -> None:
    """After a failed load the function must short-circuit on the
    ``load_failed`` flag (CodeQL flagged the dual-global precursor as
    an unused-global write; the consolidated dict makes the circuit
    breaker visible to the analyser AND to this test)."""
    # Reset to a known clean state and simulate a previously-failed load.
    saved_pipeline = build_feed._TRANSLATION_STATE["pipeline"]
    saved_load_failed = build_feed._TRANSLATION_STATE["load_failed"]
    try:
        build_feed._TRANSLATION_STATE["pipeline"] = None
        build_feed._TRANSLATION_STATE["load_failed"] = True
        # Must short-circuit and return None without re-attempting import.
        assert build_feed._get_translation_pipeline() is None
        # The load_failed flag is unchanged by the short-circuit.
        assert build_feed._TRANSLATION_STATE["load_failed"] is True
    finally:
        build_feed._TRANSLATION_STATE["pipeline"] = saved_pipeline
        build_feed._TRANSLATION_STATE["load_failed"] = saved_load_failed
