"""Letter classes for regexes that read a place or street name.

Vienna's street signs carry more than the German letters: ``Biróstraße``,
``Bertégasse``, ``Esterházygasse``, ``Linnéplatz``, ``Lehárgasse``, and ÖBB
names stations such as ``Győr`` or ``Břeclav``. A name pattern written with
``[A-Za-zÄÖÜäöüß]`` stops at the first other letter, so the name is not
recognised at all: ``Verkehrsstörung im Bereich Biróstraße 17`` reached the
EN model unshielded and the feed read "in the area of Biró Street 17"
(KI-Wächter 03.11.2026, feed of 02.11. 14:30). Every regex that recognises a
name builds its letter classes from these constants, so one rule holds for
all of them: a name keeps every Latin letter it is written with.

The classes cover Basic Latin plus the Latin-1 Supplement, Latin
Extended-A and Latin Extended-B letters (U+00C0 to U+024F), which hold the
letters of every European language written in Latin script. ``×`` and
``÷`` sit in that block but are no letters and stay out. Python's ``re``
has no ``\\p{Lu}``, so the upper and lower case lists are spelled out from
:meth:`str.isupper` and :meth:`str.islower`; the German letters
``ÄÖÜäöüß`` are part of them.
"""

from __future__ import annotations

__all__ = ["NAME_LETTER", "NAME_LOWER", "NAME_UPPER", "latin_names"]

_EXTENDED = [chr(code) for code in range(0x00C0, 0x0250)]

#: Upper case Latin letters, for use inside ``[...]``: ``A-Z`` plus ``ÄÖÜ``, ``É``, ``Š``, ``Ő``, ...
NAME_UPPER: str = "A-Z" + "".join(ch for ch in _EXTENDED if ch.isalpha() and ch.isupper())
#: Lower case Latin letters, for use inside ``[...]``: ``a-z`` plus ``äöüß``, ``é``, ``ó``, ``ř``, ...
NAME_LOWER: str = "a-z" + "".join(ch for ch in _EXTENDED if ch.isalpha() and ch.islower())
#: Every Latin letter, for use inside ``[...]``.
NAME_LETTER: str = NAME_UPPER + NAME_LOWER


def latin_names(pattern: str) -> str:
    """*pattern* with its German letter classes widened to every Latin letter.

    The name patterns stay written as they always were (``[A-ZÄÖÜ]``,
    ``[a-zäöüß]``, ``[A-Za-zÄÖÜäöüß-]``), which keeps them readable; this
    function, applied where the pattern is compiled, swaps each of the three
    spellings for :data:`NAME_UPPER`, :data:`NAME_LOWER` or
    :data:`NAME_LETTER`. Only these exact spellings inside a class change, so
    a literal word such as ``Straße`` in the pattern stays as it is.
    """
    return (
        pattern.replace("A-Za-zÄÖÜäöüß", NAME_LETTER)
        .replace("A-ZÄÖÜ", NAME_UPPER)
        .replace("a-zäöüß", NAME_LOWER)
    )
