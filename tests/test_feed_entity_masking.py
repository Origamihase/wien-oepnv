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

import pytest

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


def test_mask_entities_protects_unicode_route_separators() -> None:
    """Regression: Unicode glyphs that Marian's SentencePiece tokenizer
    treats as ``<unk>`` must be masked so they survive the round trip.

    User report (live feed.en.xml, 2026-05-22):

      DE: ``Wien Hauptbahnhof ↔ Wien Floridsdorf ↔ Wien Meidling``
      EN: ``Wien Hauptbahnhof Wien Floridsdorf Wien Meidling``  ← arrows stripped

    Without masking, the translator silently drops every preserved
    glyph (arrows, bullets, em-/en-dashes, …) and the EN feed shows
    the station names smashed together. The new fourth pass in
    :func:`_mask_entities` routes those glyphs through the same
    placeholder machinery as proper nouns.
    """
    text = "Wien Hauptbahnhof ↔ Wien Floridsdorf ↔ Wien Meidling"
    masked, mapping = build_feed._mask_entities(text)
    # The arrow must NOT appear in the masked text — if it did, the
    # translator would receive it raw and drop it.
    assert "↔" not in masked, (
        f"↔ leaked into the masked text — Marian would drop it as <unk>. "
        f"masked={masked!r}"
    )
    # The arrow IS in the mapping (so unmask can restore it).
    assert "↔" in mapping.values()
    # Repeated arrows share a single placeholder so the token count is
    # stable across mention frequency.
    arrow_placeholders = [k for k, v in mapping.items() if v == "↔"]
    assert len(arrow_placeholders) == 1
    # Round-trip restores the original glyphs verbatim.
    assert build_feed._unmask_entities(masked, mapping) == text


@pytest.mark.parametrize(
    "glyph",
    [
        "↔",   # U+2194 LEFT RIGHT ARROW (the user-reported case)
        "→",   # U+2192 RIGHTWARDS ARROW
        "←",   # U+2190 LEFTWARDS ARROW
        "⇄",   # U+21C4 RIGHTWARDS ARROW OVER LEFTWARDS ARROW
        "⇒",   # U+21D2 RIGHTWARDS DOUBLE ARROW
        "⇔",   # U+21D4 LEFT RIGHT DOUBLE ARROW
        "—",   # U+2014 EM DASH
        "–",   # U+2013 EN DASH
        "•",   # U+2022 BULLET
        "…",   # U+2026 HORIZONTAL ELLIPSIS
    ],
)
def test_mask_entities_protects_every_glyph_class(glyph: str) -> None:
    """Every preserved-symbol class registered in
    :data:`build_feed._PRESERVED_SYMBOLS_RE` must round-trip cleanly.
    """
    text = f"A {glyph} B"
    masked, mapping = build_feed._mask_entities(text)
    assert glyph not in masked, f"{glyph!r} reached the translator raw"
    assert glyph in mapping.values()
    assert build_feed._unmask_entities(masked, mapping) == text


def test_mask_entities_preserves_umlauts_and_letters() -> None:
    """Sanity guard: the preserved-symbols pass must NOT touch German
    umlauts (``ä``, ``ö``, ``ü``, ``ß``) — those MUST reach the model
    so the surrounding sentence can be translated. False positives in
    this class would break every German disruption text.
    """
    text = "Straße — Östlich Verzögerung für Reisende: Ärger über Übergänge."
    masked, mapping = build_feed._mask_entities(text)
    # Each umlaut survives the mask pass verbatim.
    for letter in ("ä", "ö", "ü", "ß", "Ä", "Ö", "Ü"):
        assert letter in masked, (
            f"umlaut {letter!r} was wrongly masked — masked={masked!r}"
        )
    # The em-dash IS protected (the user's reported failure mode).
    assert "—" not in masked
    assert "—" in mapping.values()


def test_domain_glossary_translates_betriebsstoerung() -> None:
    """Regression: ``Betriebsstörung`` was rendered as ``"Harmful
    vehicle"`` on the live feed. The glossary now maps it to the
    canonical English equivalent BEFORE the model sees the text."""
    text = "U6: Betriebsstörung"
    masked, mapping = build_feed._apply_domain_glossary(text)
    assert "Betriebsstörung" not in masked
    assert "service disruption" in mapping.values()


@pytest.mark.parametrize(
    "de_term,en_term",
    [
        ("Betriebsstörung", "service disruption"),
        ("Fahrtbehinderung", "service obstruction"),
        ("Gleisbauarbeiten", "track construction works"),
        ("Hauptfahrbahn", "main carriageway"),
        ("Schadhaftem Fahrzeug", "defective vehicle"),
        ("Schadhafter LKW", "defective truck"),
        ("Aufgelassen", "Discontinued"),
        ("Polizeieinsatz", "police operation"),
        ("Rettungseinsatz", "rescue operation"),
        ("Verkehrsunfall", "traffic accident"),
        ("Schienenersatzverkehr", "rail replacement service"),
        ("Stromausfall", "power outage"),
        ("Personen im Gleisbereich", "persons on the tracks"),
        ("Unregelmäßige Intervalle", "irregular intervals"),
    ],
)
def test_domain_glossary_round_trip(de_term: str, en_term: str) -> None:
    """Every glossary entry must map a German source token to the
    expected English equivalent and round-trip cleanly when unmasked."""
    text = f"Wegen {de_term} kommt es zu Verspätungen."
    masked, mapping = build_feed._apply_domain_glossary(text)
    assert de_term not in masked, (
        f"DE term {de_term!r} reached the translator raw. masked={masked!r}"
    )
    assert en_term in mapping.values(), (
        f"EN translation {en_term!r} missing from mapping {mapping}"
    )
    # Simulate the model preserving placeholders verbatim — the EN
    # term must surface in the unmasked output.
    restored = build_feed._unmask_entities(masked, mapping)
    assert en_term in restored


def test_domain_glossary_case_insensitive() -> None:
    """Glossary matching is case-insensitive so a lowercase token at
    the start of a sentence still gets the canonical EN translation."""
    text = "betriebsstörung führt zu Verspätungen."
    masked, mapping = build_feed._apply_domain_glossary(text)
    assert "betriebsstörung" not in masked.lower()
    assert "service disruption" in mapping.values()


def test_domain_glossary_longest_match_wins() -> None:
    """``Schadhaftem Fahrzeug`` (multi-word) must beat the single-word
    ``Schadhaftem`` alternation so the model sees one placeholder, not
    two."""
    text = "wegen Schadhaftem Fahrzeug verzögert sich der Betrieb"
    masked, mapping = build_feed._apply_domain_glossary(text)
    # One placeholder for the compound — not two for the parts.
    assert "defective vehicle" in mapping.values()
    placeholder_count = sum(
        1 for v in mapping.values() if v == "defective vehicle"
    )
    assert placeholder_count == 1


def test_glossary_and_entity_masking_compose() -> None:
    """End-to-end pipeline composition: glossary first (DE→EN), then
    verbatim entity masking. The merged mapping must restore EN terms
    where the glossary applied and DE surfaces elsewhere."""
    text = "U6: Betriebsstörung — Wien Hauptbahnhof betroffen"
    # Pass 1: glossary
    gloss_text, gloss_mapping = build_feed._apply_domain_glossary(text)
    assert "Betriebsstörung" not in gloss_text
    assert "service disruption" in gloss_mapping.values()
    # Pass 2: entity masking (sees text WITH XGLO placeholders)
    masked, ent_mapping = build_feed._mask_entities(gloss_text)
    assert "Wien Hauptbahnhof" not in masked
    assert "Wien Hauptbahnhof" in ent_mapping.values()
    # Merge mappings — glossary uses XGLO, entities use XENT, no collision.
    combined = {**gloss_mapping, **ent_mapping}
    assert len(combined) == len(gloss_mapping) + len(ent_mapping)
    # Unmask combines both
    restored = build_feed._unmask_entities(masked, combined)
    assert "service disruption" in restored
    assert "Wien Hauptbahnhof" in restored
    assert "Betriebsstörung" not in restored


def test_street_suffix_protects_compound_names() -> None:
    """Regression: ``Pasettistraße`` was rendered as ``"Pasetti
    Street"`` and ``Landstraßer Hauptstraße`` as ``"Landstraßer main
    road"`` on the live feed. Both surface forms must now be masked
    so the translator cannot rewrite the ``-straße`` suffix."""
    cases = [
        "Pasettistraße",
        "Hauptstraße",
        "Hellwagstraße",
        "Mariahilferstraße",
        "Wipplingergasse",
        "Schwarzenbergplatz",
        "Wienerbergbrücke",
    ]
    for word in cases:
        text = f"Sperre an der {word} bis 18:00."
        masked, mapping = build_feed._mask_entities(text)
        assert word not in masked, (
            f"Street name {word!r} reached the translator raw. masked={masked!r}"
        )
        # Either the street pass picked it up (mapped verbatim) or an
        # earlier pass (stations) recognised it as a registered name.
        # Both outcomes preserve the surface form on round-trip.
        assert word in mapping.values()


def test_street_suffix_ignores_normal_words() -> None:
    """The street-suffix heuristic must not match arbitrary German
    words. A normal sentence containing ``Linie``, ``Bahn``, ``wegen``
    must pass through untouched (apart from the line ``5A`` and brand
    matches the existing passes already handle)."""
    text = "Die wegen der Bahn umgeleitete Linie nutzt eine andere Route."
    masked, mapping = build_feed._mask_entities(text)
    for word in ("Die", "wegen", "der", "umgeleitete", "Linie", "eine", "andere", "Route"):
        assert word in masked, (
            f"Normal German word {word!r} was wrongly masked. masked={masked!r}"
        )


def test_brand_pattern_is_case_sensitive() -> None:
    """Regression: a case-insensitive brand pattern matched ``vor``
    (German preposition "before") against the operator brand
    ``VOR``. The pattern must be case-sensitive so the prose around
    the brand stays translatable."""
    # Lowercase 'vor' must NOT match the VOR brand.
    text = "Pasettistraße vor Hellwagstraße"
    masked, mapping = build_feed._mask_entities(text)
    assert "vor" in masked
    assert "vor" not in mapping.values()
    # Uppercase VOR still does.
    text2 = "VOR und ÖBB informieren"
    masked2, mapping2 = build_feed._mask_entities(text2)
    assert "VOR" not in masked2
    assert "VOR" in mapping2.values()
    assert "ÖBB" in mapping2.values()


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
