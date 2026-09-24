"""``Haltestellenauflassung`` resolves through the glossary, like its sister.

Published 2026-09-24 16:30 in ``docs/feed.en.xml`` (item 3, N31)::

    Stop stop on line N31 towards Schwedenplatz U Stop: Stammersdorf …

The German prose is "Haltestellenauflassung der Linie N31 in Richtung
Schwedenplatz U". "Haltestellenverlegung" has been in the glossary as
"stop relocation" for a while; the sister compound for a stop withdrawn for
good was not, and Marian takes it apart. 4 of 563 WL items in the cache
history carry the word, 1 the plural.

Mutations checked against this file (each one caught, by the test named):

* the two entries are removed → ``test_the_compound_resolves_to_stop_closure``.
* the epoch is left at 16 → ``test_translation_cache_epoch_was_bumped``.
"""

from __future__ import annotations

import pytest

from src import build_feed
from src.build_feed import _apply_domain_glossary, _unmask_entities


def _glossed(text: str) -> str:
    out, mapping = _apply_domain_glossary(text, source="Wiener Linien", category="Hinweis")
    return _unmask_entities(out, mapping)


@pytest.mark.parametrize(
    ("german", "english"),
    [
        ("Haltestellenauflassung der Linie N31 in Richtung Schwedenplatz U", "stop closure"),
        ("Haltestellenauflassungen der Linien 25 und 26", "stop closures"),
    ],
)
def test_the_compound_resolves_to_stop_closure(german: str, english: str) -> None:
    out = _glossed(german)
    assert english in out, out
    assert "auflassung" not in out.casefold()


def test_the_sister_entry_is_untouched() -> None:
    assert "stop relocation" in _glossed("Haltestellenverlegung der Linie 72A")


def test_translation_cache_epoch_was_bumped() -> None:
    assert build_feed._TRANSLATION_CACHE_EPOCH >= 17
