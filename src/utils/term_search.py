"""Search a text for any of very many literal terms, each as a whole term.

:class:`WholeTermSearch` answers the same question as::

    re.compile(r"(?<!\\w)(?:" + "|".join(map(re.escape, terms)) + r")(?!\\w)",
               re.IGNORECASE).search(text) is not None

but through an index instead of one pattern. With the 159,754 station names
and aliases of Vienna the pattern took 7 s to compile and about 1 s on
average to search one ÖBB text (measured 2026-11-10); the index is built
in half a second and a search takes well under a millisecond.

Empty terms are ignored. The answer is the same by construction, not by a
reimplementation of the regex engine's rules: which character of a term
matches which character of a text, and what counts as a word character, is
asked of the ``re`` module itself, one character at a time
(:meth:`WholeTermSearch._class_of`, :func:`_is_word`).

One known difference lies in the pattern, not in the index: CPython 3.11
turns alternatives that differ in one character into a character set, and
in such a set an upper-case letter beyond U+FFFF (Deseret, Osage, Adlam …)
matches nothing, while the same letter as a single literal matches. The
index follows the single literal. The terms of the station directory are
lower-cased, so the case cannot occur there.

* The characters of all terms are grouped into classes of characters that
  match each other in both directions under ``re.IGNORECASE``.
* A character of a text belongs to a class when the characters of the terms
  that match it are exactly that class. Then "term matches here" is the same
  as "same classes, position by position", and a set lookup decides it.
* A character whose matches are no whole class makes the text *irregular*;
  for such a text the flat pattern above is compiled and asked after all.
  No such character is known: with the station directory every code point
  of Unicode was asked on Python 3.11 (2026-11-10), none is irregular. The
  branch keeps the answer right should a later ``re`` relate characters
  differently, and it logs a warning because it costs the seconds the index
  saves.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from functools import lru_cache

__all__ = ["WholeTermSearch"]

log = logging.getLogger(__name__)

_PREFIX_LENGTH = 4


@lru_cache(maxsize=4096)
def _is_word(char: str) -> bool:
    return re.fullmatch(r"\w", char) is not None


class WholeTermSearch:
    """Whether a text holds one of *terms* as a whole term, ignoring case."""

    def __init__(self, terms: Iterable[str]) -> None:
        self._raw_terms = sorted({t for t in terms if t}, key=len, reverse=True)
        alphabet = sorted({char for term in self._raw_terms for char in term})
        # Stands for a text character no term character matches: the last
        # character of the Basic Multilingual Plane that no term contains,
        # so it names no class.
        used = set(alphabet)
        self._no_class = next(chr(cp) for cp in range(0xFFFF, 0, -1) if chr(cp) not in used)
        self._matchers = {char: re.compile(re.escape(char), re.IGNORECASE) for char in alphabet}
        # Classes of term characters that match each other both ways; each
        # class is named by its first character.
        self._class_members: dict[str, frozenset[str]] = {}
        name_of: dict[str, str] = {}
        for char in alphabet:
            if char in name_of:
                continue
            members = frozenset(
                other for other in alphabet
                if other not in name_of
                and self._matchers[char].fullmatch(other)
                and self._matchers[other].fullmatch(char)
            )
            self._class_members[char] = members
            for member in members:
                name_of[member] = char
        self._text_class: dict[str, str | None] = {}
        table = str.maketrans(name_of)
        self._prefix_length = min(
            [_PREFIX_LENGTH] + [len(term) for term in self._raw_terms]
        )
        self._terms: set[str] = set()
        lengths: dict[str, set[int]] = {}
        for term in self._raw_terms:
            folded = term.translate(table)
            self._terms.add(folded)
            lengths.setdefault(folded[: self._prefix_length], set()).add(len(folded))
        self._lengths = {prefix: tuple(sorted(found)) for prefix, found in lengths.items()}
        self._flat: re.Pattern[str] | None = None

    def _class_of(self, char: str) -> str | None:
        """The class a text character belongs to; ``None`` when it is irregular."""
        try:
            return self._text_class[char]
        except KeyError:
            pass
        matching = frozenset(c for c, matcher in self._matchers.items() if matcher.fullmatch(char))
        found: str | None = None
        if not matching:
            found = self._no_class
        else:
            for name, members in self._class_members.items():
                if members == matching:
                    found = name
                    break
        self._text_class[char] = found
        return found

    def _flat_pattern(self) -> re.Pattern[str]:
        if self._flat is None:
            log.warning(
                "Begriffssuche: Zeichen ohne eindeutige Klasse im Text – "
                "das flache Muster mit %d Begriffen wird kompiliert.",
                len(self._raw_terms),
            )
            body = "|".join(re.escape(term) for term in self._raw_terms)
            self._flat = re.compile(r"(?<!\w)(?:" + body + r")(?!\w)", re.IGNORECASE)
        return self._flat

    def search(self, text: str) -> bool:
        """``True`` when *text* holds one of the terms as a whole term."""
        if not self._terms or not text:
            return False
        classes = [self._class_of(char) for char in text]
        if None in classes:
            return self._flat_pattern().search(text) is not None
        folded = "".join(cls for cls in classes if cls is not None)
        size = len(text)
        width = self._prefix_length
        for start in range(size):
            if start and _is_word(text[start - 1]):
                continue
            lengths = self._lengths.get(folded[start:start + width])
            if not lengths:
                continue
            for length in lengths:
                end = start + length
                if end > size:
                    break
                if folded[start:end] in self._terms and (end == size or not _is_word(text[end])):
                    return True
        return False
