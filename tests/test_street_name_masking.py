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

from src.build_feed import _mask_entities


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


def test_free_standing_street_names_are_a_documented_gap() -> None:
    """``Mariahilfer Straße`` stays unshielded, on purpose.

    Shielding it needs a branch for a bare suffix after an attribute —
    ``<Wort> Straße`` — because the suffix does not sit on the name's own
    word. Any head broad enough to catch ``Mariahilfer``, ``Donaufelder``
    and ``Schloßhofer`` (they share only the ``-er`` ending, and the set is
    productive) also catches German determiners: ``Dieser Platz``, ``Jeder
    Weg``. That branch would need an exclusion list of its own.

    The trade is not worth it here. These names are unshielded today and
    demonstrably NOT broken — the model passes them through unchanged
    because it does not know the attribute, and no item in either feed
    shows one altered. Closing a latent hole by opening a live
    over-capture is the wrong direction; the case belongs in the station
    directory (which already covers ``Hütteldorfer Straße`` and
    ``Matzleinsdorfer Platz``), not in this heuristic.
    """
    assert _unprotected("Mariahilfer Straße") == "Mariahilfer Straße"
    assert _unprotected("Donaufelder Straße") == "Donaufelder Straße"


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


def test_translation_cache_epoch_was_bumped() -> None:
    """Cached English outlives the bug unless the epoch moves.

    ``_cached_translation`` only retries when the cached value equals the
    German source, so "rear customs office" — wrong but not German — would
    be served for the item's lifetime. The U4 notice carrying it runs until
    30.11.2026.
    """
    from src.build_feed import _TRANSLATION_CACHE_EPOCH

    assert _TRANSLATION_CACHE_EPOCH >= 7
