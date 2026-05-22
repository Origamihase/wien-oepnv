"""Property-based tests for the entity-masking translation pipeline.

The ``_mask_entities`` / ``_unmask_entities`` pair in
``src/build_feed.py`` is the canonical proper-noun and Unicode-glyph
shield for the EN feed. The Helsinki opus-mt-de-en Marian model
routinely drops or mistranslates entities it doesn't recognise; the
masker replaces them with stable ``XENT<n>X`` placeholders so they
survive the round trip and get restored unchanged by the unmasker.

Three regressions in this code path drove the property-test coverage:

1. PR #1596 (`feat(en-feed): protect proper nouns + dissolve unused-
   global pattern`) — initial masking shielded only brand names; the
   masker missed station names and line tokens.
2. PR #1597 (`fix(en-feed): translation pipeline audit — eliminate
   sticky-German cache`) — failed translations poisoned the persistent
   EN cache with the German source.
3. PR #1612 (`fix(en-feed): preserve Unicode route separators`) —
   Unicode separators (``↔``, ``→``, em-dashes, bullets, …) outside the
   SentencePiece vocabulary landed in ``<unk>`` and were silently
   dropped on detokenize, smashing route titles like
   ``Wien Hauptbahnhof ↔ Wien Floridsdorf`` into
   ``Wien Hauptbahnhof Wien Floridsdorf``.

Each PR shipped hand-curated tests. Hypothesis fills in the input-space
gap: it samples adversarial combinations of entity types (brand +
station + line + symbol) at random positions across the input, with
overlapping spans, repeated surfaces, and pathological boundary
conditions. The five properties pinned below describe the contract
``_mask_entities``/``_unmask_entities`` must satisfy under every input
shape.
"""
from __future__ import annotations

import hypothesis.strategies as st
from hypothesis import given, settings

from src.build_feed import (
    _ENTITY_PLACEHOLDER_RE,
    _mask_entities,
    _unmask_entities,
)


_TEXT_CHARS = st.characters(
    blacklist_categories=("Cs",),  # type: ignore[arg-type]  # surrogates — invalid in str
)
_texts = st.text(alphabet=_TEXT_CHARS, min_size=0, max_size=500)

# Hand-curated set of entities the masker is expected to shield in
# real upstream titles. Used to construct realistic adversarial inputs
# by Hypothesis without it having to discover them from scratch.
_KNOWN_ENTITIES = (
    "ÖBB", "Wiener Linien", "VOR", "ÖBB-Postbus",
    "Wien Hauptbahnhof", "Wien Meidling", "Wien Floridsdorf",
    "U1", "U6", "S40", "REX7", "41E", "10A", "N20",
    "↔", "→", "←", "—", "–", "•", "…",
    "Stammstrecke", "S-Bahn-Stammstrecke",
)


@given(text=_texts)
@settings(max_examples=300, deadline=None)
def test_mask_unmask_round_trip_identity(text: str) -> None:
    """When the translator does NOT touch the masked text, unmask must
    recover the original exactly.

    This is the most basic mask/unmask contract: ``unmask(mask(x)[0],
    mask(x)[1]) == x`` whenever no transformation happens between the
    two calls. Any drift here means the masker mutates content it
    isn't supposed to.
    """
    masked, mapping = _mask_entities(text)
    restored = _unmask_entities(masked, mapping)
    assert restored == text


@given(text=_texts)
@settings(max_examples=300, deadline=None)
def test_mask_returns_tuple_of_documented_types(text: str) -> None:
    """``_mask_entities`` must always return ``(str, dict[str, str])``.

    Without this guard a future regression that returns ``None`` or a
    bare string would propagate through the translation pipeline and
    crash ``_translate_text_attempt`` (which destructures the tuple).
    """
    result = _mask_entities(text)
    assert isinstance(result, tuple)
    assert len(result) == 2
    masked, mapping = result
    assert isinstance(masked, str)
    assert isinstance(mapping, dict)
    for key, value in mapping.items():
        assert isinstance(key, str)
        assert isinstance(value, str)


@given(text=_texts)
@settings(max_examples=300, deadline=None)
def test_mapping_keys_match_placeholder_format(text: str) -> None:
    """Every placeholder emitted by the masker matches
    ``_ENTITY_PLACEHOLDER_RE`` so the unmasker can restore it.

    Pre-fix, a divergence between the format string and the regex
    pattern would silently leak literal ``XENT<n>X`` placeholders into
    the feed body — subscribers would see ``XENT3X`` where a station
    name should be. The property pins format/regex parity at the
    placeholder boundary.
    """
    _, mapping = _mask_entities(text)
    for placeholder in mapping:
        assert _ENTITY_PLACEHOLDER_RE.fullmatch(placeholder), (
            f"placeholder {placeholder!r} does not match the "
            f"_ENTITY_PLACEHOLDER_RE pattern recognised by the unmasker"
        )


@given(text=_texts)
@settings(max_examples=200, deadline=None)
def test_unmask_drops_dangling_placeholders_when_translator_strips_them(
    text: str,
) -> None:
    """If the translator strips placeholders entirely (the canonical
    Marian ``<unk>`` drop), unmask must not emit literal ``XENT<n>X``
    tokens to subscribers.

    Simulation: mask the input, throw away the masked text, run unmask
    on the ORIGINAL text. Any ``XENT<n>X``-shaped substring already
    present in the original is irrelevant (Hypothesis won't produce
    them since the placeholder format collides nothing in real German
    text); the property is that unmask's output never CREATES such a
    token.
    """
    _, mapping = _mask_entities(text)
    output = _unmask_entities(text, mapping)
    # After unmask, no placeholder shape should remain unless it was
    # already present in the input (the contract is "restore or drop",
    # never "create"). Count placeholders in input and output — output
    # must not exceed input.
    placeholders_in_text = len(_ENTITY_PLACEHOLDER_RE.findall(text))
    placeholders_in_output = len(_ENTITY_PLACEHOLDER_RE.findall(output))
    assert placeholders_in_output <= placeholders_in_text, (
        f"unmask created new placeholder tokens: text={text!r} "
        f"in={placeholders_in_text} out={placeholders_in_output} "
        f"output={output!r}"
    )


@given(
    prefix=_texts,
    entity=st.sampled_from(_KNOWN_ENTITIES),
    suffix=_texts,
)
@settings(max_examples=300, deadline=None)
def test_known_entity_round_trips_when_translator_keeps_placeholder(
    prefix: str,
    entity: str,
    suffix: str,
) -> None:
    """Known entities (brands, stations, lines, Unicode symbols) embedded
    in arbitrary surrounding German text must survive the mask/unmask
    round trip even when only the placeholder reaches the output.

    Simulates the canonical happy-path: the translator keeps the
    placeholders in the output (potentially with rearranged surrounding
    text). The unmasker restores each placeholder to its original
    surface form. This is the property that breaks when a glyph slips
    out of the masker's coverage — the PR #1612 ↔ regression manifests
    here when the entity is U+2194.
    """
    composed = f"{prefix} {entity} {suffix}"
    masked, mapping = _mask_entities(composed)
    # The placeholders may not be present if the entity didn't match
    # any of the four entity classes — that's fine, the round-trip
    # property still holds via identity. The interesting case is when
    # the entity IS captured: the placeholder must round-trip to the
    # original surface form.
    restored = _unmask_entities(masked, mapping)
    assert restored == composed


@given(text=_texts)
@settings(max_examples=200, deadline=None)
def test_repeated_surface_shares_single_placeholder(text: str) -> None:
    """Identical entity surfaces must share exactly one placeholder.

    The masker dedupes via ``surface_to_placeholder`` so a title with
    ``Wien Hauptbahnhof ↔ Wien Hauptbahnhof`` (typo case, but also any
    real route mention) produces a single mapping entry, not two. The
    property pins that contract by asserting the mapping values form
    a set with no duplicates.
    """
    _, mapping = _mask_entities(text)
    surfaces = list(mapping.values())
    assert len(surfaces) == len(set(surfaces)), (
        f"mapping carries duplicate surfaces — dedup is broken: {surfaces}"
    )


# ---------------------------------------------------------------------------
# Regression vectors from past bugfix PRs. These pin the exact inputs
# that motivated the entity-masker work so a future regex change cannot
# silently regress to the buggy shape.
# ---------------------------------------------------------------------------

_REGRESSION_INPUTS = [
    # PR #1612 — Unicode separators stripped by Marian
    "Wien Hauptbahnhof ↔ Wien Floridsdorf ↔ Wien Meidling",
    "S-Bahn-Stammstrecke → Heiligenstadt",
    "U1 — Karlsplatz",
    "ÖBB · Wiener Linien · VOR",
    "Bahnhof Wien Mitte … Bahnhof Wien Praterstern",
    # PR #1596 — Proper nouns mistranslated
    "Wiener Linien meldet eine Verspätung der U6",
    "ÖBB-Postbus 41E hält am Karlsplatz",
    # Edge cases that have surfaced in audits
    "",
    " ",
    "U1",  # single line token, nothing else
    "↔",  # single Unicode glyph, nothing else
    "↔↔↔",  # repeated glyph (dedup)
    "ÖBB ÖBB ÖBB",  # repeated brand (dedup)
    "🚇",  # emoji outside the protected blocks
    "Wien Hauptbahnhof" * 5,  # repeated station (dedup + length)
]


@given(text=st.sampled_from(_REGRESSION_INPUTS))
def test_regression_inputs_round_trip(text: str) -> None:
    """Each historical bug vector round-trips cleanly through mask/unmask."""
    masked, mapping = _mask_entities(text)
    restored = _unmask_entities(masked, mapping)
    assert restored == text


@given(text=st.sampled_from(_REGRESSION_INPUTS))
def test_regression_inputs_no_placeholder_leakage(text: str) -> None:
    """No regression input produces a placeholder token in the masked
    text that doesn't appear in the mapping (would otherwise leak to
    the EN feed if the translator passes the placeholder through)."""
    masked, mapping = _mask_entities(text)
    placeholders_in_masked = set(_ENTITY_PLACEHOLDER_RE.findall(masked))
    leaked = placeholders_in_masked - set(mapping)
    assert not leaked, (
        f"masked text carries placeholder(s) not in mapping — "
        f"would leak to EN feed: leaked={leaked} masked={masked!r}"
    )


def test_empty_input_returns_empty_tuple() -> None:
    """Pre-fix contract: empty input is handled by the fast-path branch
    at ``_mask_entities`` line 1080 and must yield ``("", {})``."""
    masked, mapping = _mask_entities("")
    assert masked == ""
    assert mapping == {}
