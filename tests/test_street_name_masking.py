"""Street names must reach the English feed as street names.

Live regression, 2026-09-17 (``docs/feed.en.xml``, audit finding C.4)::

    DE: 1: Veranstaltung Umleitung ab Hintere Zollamtsstr. über O und 18
    EN: 1: Event diversion from rear customs office via O and 18

A street name became the name of a building type. Someone searching the
English feed for "Hintere Zollamtsstraße" does not find it, and someone
reading "customs office" looks for a customs office.

Two gaps in ``_STREET_SUFFIX_RE`` produced it, and they fail differently:

* **Abbreviated suffix.** WL shortens ``Straße`` to ``Str.`` routinely. The
  abbreviation was not in the alternation, so ``Hintere Zollamtsstr.`` was
  not masked **at all** and went through the model in one piece.
* **Leading adjective.** The pattern required the suffix to sit on the same
  word, so ``Vordere Zollamtsstraße`` masked only ``Zollamtsstraße`` and
  left ``Vordere`` loose — "Front Zollamtsstraße". Same for ``Kleine
  Marxerbrücke`` → "Small Marxerbrücke".

**Why these tests drive the masker and not the feed.** ``Hintere
Zollamtsstraße`` — spelled out — came through the old code unharmed, but
not because the street shield worked: a stop called ``Wien Hintere
Zollamtsstraße (WL)`` exists, so pass 2 (station directory) caught it by
coincidence. A test written against the rendered feed would have passed on
that coincidence and proved nothing about the shield. ``_mask_entities`` is
therefore called directly, and the cases below deliberately mix names that
ARE in the directory with names that are not.

The negative half matters as much as the positive one: a shield that grabs
ordinary German leaves it untranslated in the English feed, which is the
same defect pointing the other way.
"""

from __future__ import annotations

import pytest

from src.build_feed import (
    _drop_article_before_street,
    _mask_entities,
    _street_names_nominative,
    _unmask_entities,
)


def _unprotected(text: str) -> str:
    """The part of *text* the masker did NOT shield, placeholders as ``□``."""
    masked, mapping = _mask_entities(text)
    for placeholder in mapping:
        masked = masked.replace(placeholder, "□")
    return masked


@pytest.mark.parametrize(
    "name",
    [
        # The live breakages.
        "Hintere Zollamtsstr.",
        "Vordere Zollamtsstraße",
        "Kleine Marxerbrücke",
        # Same shapes, other adjectives — all real Viennese streets.
        "Obere Augartenstraße",
        "Untere Viaduktgasse",
        "Große Neugasse",
        "Neue Weltgasse",
        "Alte Donaustraße",
        "Linke Wienzeile",
        "Rechte Wienzeile",
        # Plain compounds must keep working.
        "Zollamtsstraße",
        "Marxerbrücke",
        "Landstraße",
        "Pasettistraße",
        # Compounds on -gürtel/-damm (Baustellen audit 2026-10-06):
        # "Wien Westbahnhof: Neubaugürtel 6-12" went out as "New belt".
        "Neubaugürtel",
        "Rollerdamm",
        "Kaisermühlendamm",
    ],
)
def test_street_names_are_shielded_whole(name: str) -> None:
    """Nothing of the name may be left outside the placeholder."""
    assert _unprotected(name) == "□", f"{name!r} leaks: {_unprotected(name)!r}"


def test_the_shield_does_not_lean_on_the_station_directory() -> None:
    """``Vordere Zollamtsstraße`` is no stop — only the pattern can save it.

    Its sibling ``Hintere Zollamtsstraße`` IS a stop and was shielded by
    pass 2 even before the fix. Pinning the pair together is what keeps a
    future refactor from removing the street pattern and still passing.
    """
    assert _unprotected("Vordere Zollamtsstraße") == "□"
    assert _unprotected("Kleine Marxerbrücke") == "□"
    assert _unprotected("Hintere Zollamtsstraße") == "□"


@pytest.mark.parametrize(
    "phrase",
    [
        # Bare nouns and article + noun: ordinary German, must stay
        # translatable. The adjective head is a closed list precisely so
        # these cannot be swept up.
        "Die Straße ist gesperrt",
        "Der Platz bleibt frei",
        "Eine Gasse weiter",
        "Dieser Weg ist gesperrt",
        "Jeder Platz ist besetzt",
        # Ordinary words that merely end in a street-ish syllable.
        "Entschuldigung.",
        # NB: no place names here. ``Mitterberg.`` looks like a good
        # negative case and is not one — ``Wien Mitterberg (WL)`` is a real
        # stop, so pass 2 masks it on purpose. Only words that are nobody's
        # name belong in this list.
        # Disruption vocabulary that must reach the glossary untouched.
        "Gleisschaden",
        "Veranstaltung",
        "Fahrtbehinderung",
        "Betrieb",
    ],
)
def test_ordinary_german_is_not_swallowed(phrase: str) -> None:
    """A shield that grabs prose leaves it German in the English feed."""
    assert "□" not in _unprotected(phrase), (
        f"{phrase!r} was (partly) shielded: {_unprotected(phrase)!r}"
    )


def test_free_standing_street_names_are_shielded() -> None:
    """``Mariahilfer Straße`` is shielded since the EN audit of 2026-10-03.

    This was once a documented gap, on the claim that such names pass the
    model unchanged. The audit of every published DE/EN pair refuted it:
    "Südtiroler Platz" → "South Tyrolean square", "Währinger Gürtel" →
    "moving belt", "Schloßhofer Straße" → "Schlosshofer Straße". The
    feared over-capture (``Dieser Platz``, ``Jeder Weg``) is a closed word
    class and excluded by name; ``test_a_bare_suffix_never_stands_on_its_own``
    below still guards it.
    """
    assert _unprotected("Mariahilfer Straße") == "□"
    assert _unprotected("Donaufelder Straße") == "□"
    assert _unprotected("Südtiroler Platz") == "□"
    assert _unprotected("Währinger Gürtel") == "□"


def test_a_bare_suffix_never_stands_on_its_own() -> None:
    """The guard that makes the gap above safe to leave open.

    What keeps ``Dieser Platz`` and ``Die Straße`` out of the shield is not
    the adjective list — it is that the name body must be a compound
    (``Zollamtsstraße``) or an abbreviation (``Zollamtsstr.``). A bare
    suffix is never a name by itself, whatever precedes it.

    Pinned because that is exactly the invariant a future attempt at the
    free-standing case would break: adding a ``<head> <bare suffix>``
    branch makes these assertions fail, which is the signal to bring an
    exclusion list with it.
    """
    for phrase in ("Dieser Platz", "Die Straße", "Jeder Weg", "Große Straße"):
        assert "□" not in _unprotected(phrase), (
            f"{phrase!r} was shielded: {_unprotected(phrase)!r}"
        )


def test_the_abbreviation_branch_survives_a_following_word() -> None:
    """``str.`` is followed by a space, where ``\\b`` does not hold.

    Between ``.`` and ``` ``` there is no word boundary — both are
    non-word characters — so a trailing ``\\b`` would make the branch never
    match in running text, which is exactly where it has to work. Pinned
    because the unit case ``"Hintere Zollamtsstr."`` ends at the string and
    would pass either way.
    """
    sentence = "Umleitung ab Hintere Zollamtsstr. über O und 18"
    assert "Hintere Zollamtsstr" not in _unprotected(sentence)
    assert "□" in _unprotected(sentence)


def test_known_gap_bahnsteig_is_shielded_as_if_it_were_a_street() -> None:
    """Documented limitation, unchanged by this fix — not a claim it is right.

    ``Bahnsteig`` ends in ``…steig``, which the suffix list carries for
    real streets (``Rudolfstieg``). The shield therefore preserves the
    common noun verbatim and the English feed keeps the German word. No
    current item is affected — ``Bahnsteig`` appears in 0 of the 10 items
    of either feed — so this is latent, and narrowing it means either a
    hand-kept exception list or dropping a genuine street suffix.

    Pinned so the behaviour is visible rather than buried in a regex, and
    so a future fix has somewhere to land: flip this test when the noun
    stops being shielded.
    """
    assert _unprotected("Bahnsteig") == "□"


@pytest.mark.parametrize(
    ("phrase", "name"),
    [
        # Live, Jules audits 26. and 27.10.2026: "Due to an ordered line
        # closure of the algae path, line 89A is redirected."
        ("Streckensperre des Algenwegs", "Algenwegs"),
        ("im Bereich des Tesarekplatzes", "Tesarekplatzes"),
        ("Sperre des Aumannplatzes", "Aumannplatzes"),
        ("Nebenfahrbahn des Margaretengürtels", "Margaretengürtels"),
        ("eine Sperre des Universitätsrings", "Universitätsrings"),
        ("Sperre des Währinger Gürtels", "Währinger Gürtels"),
        ("im Zuge des Floridsdorfer Marktes", "Floridsdorfer Marktes"),
    ],
)
def test_genitive_street_names_are_shielded_whole(phrase: str, name: str) -> None:
    """German declines the name; the ``\\b`` after the suffix let it through."""
    masked, mapping = _mask_entities(phrase)
    assert name in mapping.values(), f"{name!r} leaks: {_unprotected(phrase)!r}"


@pytest.mark.parametrize(
    "phrase",
    [
        # Common nouns in the genitive always reached the model and read
        # right ("renovation of the platform", "a flea market").
        "Wegen Sanierung des Bahnsteigs",
        "Wegen eines Flohmarktes",
        "im Bereich des Gehsteiges",
        "im Bereich des Ersatzgehsteiges",
        "Sperre des Bahnhofsvorplatzes",
        "Sperre des Radweges",
        # Second check of #2075 (27.10.2026): words WL writes in the base
        # form, and frequent common nouns; shielded, the English kept the
        # German word ("closure of Einstieg", "edge of Spielplatz").
        "Wegen einer Sperre des Einstiegs",
        "Sperre des Zustiegs",
        "im Bereich des Bussteigs B",
        "am Rand des Spielplatzes",
        "im Bereich des Arbeitsplatzes",
        "entlang des Bahndamms",
        "Sperre des Rettungsweges",
        "vor dem Eingang des Supermarktes",
        "Unterwegs",
    ],
)
def test_genitive_common_nouns_stay_translatable(phrase: str) -> None:
    assert "□" not in _unprotected(phrase), (
        f"{phrase!r} was (partly) shielded: {_unprotected(phrase)!r}"
    )


@pytest.mark.parametrize(
    ("surface", "base"),
    [
        ("Algenwegs", "Algenweg"),
        ("Algenweges", "Algenweg"),
        ("Tesarekplatzes", "Tesarekplatz"),
        ("Margaretengürtels", "Margaretengürtel"),
        ("Universitätsrings", "Universitätsring"),
        ("Handelskais", "Handelskai"),
        ("Rollerdammes", "Rollerdamm"),
        ("Währinger Gürtels", "Währinger Gürtel"),
        ("Floridsdorfer Marktes", "Floridsdorfer Markt"),
        # Nominatives stay as they are.
        ("Margaretengürtel", "Margaretengürtel"),
        ("Hintere Zollamtsstr.", "Hintere Zollamtsstr."),
        ("Ottakring", "Ottakring"),
    ],
)
def test_english_restores_the_base_form(surface: str, base: str) -> None:
    """The English text names the street as its sign does: "of Algenweg"."""
    assert _street_names_nominative({"XENT0X": surface}) == {"XENT0X": base}


def test_the_article_before_the_restored_name_drops() -> None:
    """Model output "of the XENT0X" for "des Algenwegs" ends as "of Algenweg"."""
    masked, mapping = _mask_entities("des Algenwegs")
    (placeholder,) = mapping
    restored = _unmask_entities(
        f"Due to an ordered line closure of the {placeholder}, line 89A is redirected.",
        _street_names_nominative(mapping),
    )
    assert _drop_article_before_street(restored) == (
        "Due to an ordered line closure of Algenweg, line 89A is redirected."
    )


def test_translation_cache_epoch_was_bumped() -> None:
    """Cached English outlives the bug unless the epoch moves.

    ``_cached_translation`` only retries when the cached value equals the
    German source, so "rear customs office" — wrong but not German — would
    be served for the item's lifetime. The U4 notice carrying it runs until
    30.11.2026.
    """
    from src.build_feed import _TRANSLATION_CACHE_EPOCH

    assert _TRANSLATION_CACHE_EPOCH >= 7
