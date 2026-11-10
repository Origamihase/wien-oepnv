"""How ``WholeTermSearch`` groups characters and when it asks the flat pattern.

Under the ``re`` of Python 3.11 every character is regular (module docstring
of ``src/utils/term_search.py``), so the code that decides it is reached
here in two ways, both through the decision code itself and never by
writing its result table:

* single matchers of a finished searcher are exchanged, so that a text
  character is matched by part of a class or by members of two classes;
* the module asks a stand-in for ``re`` that answers like ``re`` except for
  chosen pairs of characters, so that the relation between characters is
  not symmetric or not transitive while the classes are built.
"""
from __future__ import annotations

import itertools
import re

import pytest

from src.utils import term_search
from src.utils.term_search import WholeTermSearch

_LOGGER = "src.utils.term_search"


def _flat(terms: list[str]) -> re.Pattern[str]:
    ordered = sorted({t for t in terms if t}, key=len, reverse=True)
    body = "|".join(re.escape(term) for term in ordered)
    return re.compile(r"(?<!\w)(?:" + body + r")(?!\w)", re.IGNORECASE)


def _fall_back_warnings(caplog: pytest.LogCaptureFixture) -> int:
    return len([r for r in caplog.records if "flache Muster" in r.getMessage()])


def test_text_character_matched_by_part_of_a_class_is_irregular(
    caplog: pytest.LogCaptureFixture,
) -> None:
    terms = ["gasse", "gaſſe"]
    searcher = WholeTermSearch(terms)
    assert searcher._class_members["s"] == frozenset("sſ")
    # From here on the long s matches only itself, so "s" and "S" are matched
    # by one of the two members of their class.
    searcher._matchers["ſ"] = re.compile("ſ")
    flat = _flat(terms)
    with caplog.at_level("WARNING", logger=_LOGGER):
        # A text of regular characters is still answered by the index.
        assert searcher._class_of("ſ") == "s"
        assert searcher.search("Die Gaſſe.") is True
        assert vars(searcher)["_flat"] is None
        assert _fall_back_warnings(caplog) == 0

        assert searcher._class_of("s") is None
        assert searcher._class_of("S") is None
        assert searcher._class_of("s") is None  # the remembered answer is the same
        for text in ("Gasse", "GASSE", "Die Gasse.", "Gassen", "xgasse", "s", "gaſse", "gas se"):
            assert searcher.search(text) is (flat.search(text) is not None), text
        assert searcher.search("Die Gasse.") is True
        assert searcher.search("Gassen") is False
    assert vars(searcher)["_flat"] is not None
    assert _fall_back_warnings(caplog) == 1


def test_text_character_matched_by_two_classes_is_irregular(
    caplog: pytest.LogCaptureFixture,
) -> None:
    terms = ["straße", "gasse"]
    searcher = WholeTermSearch(terms)
    assert searcher._class_members["a"] == frozenset("a")
    assert searcher._class_members["g"] == frozenset("g")
    # From here on "q" is matched by the pattern of "a" and by that of "g".
    searcher._matchers["a"] = re.compile("[aq]", re.IGNORECASE)
    searcher._matchers["g"] = re.compile("[gq]", re.IGNORECASE)
    flat = _flat(terms)
    with caplog.at_level("WARNING", logger=_LOGGER):
        assert searcher.search("Gasse") is True
        assert vars(searcher)["_flat"] is None
        assert _fall_back_warnings(caplog) == 0

        assert searcher._class_of("q") is None
        assert searcher._class_of("Q") is None
        # The flat pattern knows nothing of the exchanged matchers: "q" is
        # neither "a" nor "g" there.
        for text in ("q Straße", "q Straßen", "qgasse", "gqsse", "qasse", "q", "Q GASSE", "q STRAẞE"):
            assert searcher.search(text) is (flat.search(text) is not None), text
        assert searcher.search("q Straße") is True
        assert searcher.search("q STRAẞE") is True
        assert searcher.search("q Straßen") is False
        assert searcher.search("qgasse") is False
        assert searcher.search("gqsse") is False
        assert searcher.search("qasse") is False
    assert vars(searcher)["_flat"] is not None
    assert _fall_back_warnings(caplog) == 1


class _OneCharacter:
    """What the stand-in compiles for one term character."""

    def __init__(self, relation: _PairwiseRe, char: str) -> None:
        self._relation = relation
        self._char = char

    def fullmatch(self, string: str) -> bool | None:
        return True if self._relation.matches(self._char, string) else None


class _Reference:
    """What the stand-in compiles for the flat pattern: the same relation, term by term."""

    def __init__(self, relation: _PairwiseRe) -> None:
        self._relation = relation

    def search(self, text: str) -> bool | None:
        self._relation.reference_searches += 1
        return True if self._relation.holds(text) else None


class _PairwiseRe:
    """Stands in for ``re`` inside ``src.utils.term_search``.

    ``chosen[(term_char, text_char)]`` says whether the pattern of
    ``term_char`` matches ``text_char``; every other pair, the escaping and
    the word characters are asked of ``re``. :meth:`holds` is the reference:
    the flat pattern under this relation, spelled out position by position.
    """

    IGNORECASE = re.IGNORECASE

    def __init__(self, terms: list[str], chosen: dict[tuple[str, str], bool]) -> None:
        self._terms = [term for term in terms if term]
        self._chosen = chosen
        self.flat_compiled = 0
        self.reference_searches = 0

    def escape(self, pattern: str) -> str:
        return re.escape(pattern)

    def fullmatch(self, pattern: str, string: str, flags: int = 0) -> re.Match[str] | None:
        return re.fullmatch(pattern, string, flags)

    def compile(self, pattern: str, flags: int = 0) -> _OneCharacter | _Reference:
        assert flags == re.IGNORECASE
        if pattern.startswith(r"(?<!\w)"):
            self.flat_compiled += 1
            return _Reference(self)
        char = pattern[-1]
        assert pattern == re.escape(char)
        return _OneCharacter(self, char)

    def matches(self, term_char: str, text_char: str) -> bool:
        chosen = self._chosen.get((term_char, text_char))
        if chosen is not None:
            return chosen
        return re.fullmatch(re.escape(term_char), text_char, re.IGNORECASE) is not None

    def holds(self, text: str) -> bool:
        for term in self._terms:
            for start in range(len(text) - len(term) + 1):
                end = start + len(term)
                if start and re.fullmatch(r"\w", text[start - 1]):
                    continue
                if end < len(text) and re.fullmatch(r"\w", text[end]):
                    continue
                if all(self.matches(a, b) for a, b in zip(term, text[start:end], strict=True)):
                    return True
        return False


# The pattern of "a" matches "b", the pattern of "b" does not match "a".
_ONE_WAY = {("a", "b"): True}
# "a" and "b" match each other, "b" and "c" match each other, "a" and "c" do not.
_CHAIN = {("a", "b"): True, ("b", "a"): True, ("b", "c"): True, ("c", "b"): True}
# "a" and "b" match each other; "d" is matched by the pattern of "a" alone.
_HALF_A_CLASS = {("a", "b"): True, ("b", "a"): True, ("a", "d"): True}


def test_a_one_way_match_forms_no_class(monkeypatch: pytest.MonkeyPatch) -> None:
    terms = ["ab", "c"]
    fake = _PairwiseRe(terms, _ONE_WAY)
    monkeypatch.setattr(term_search, "re", fake)
    searcher = WholeTermSearch(terms)
    assert searcher._class_members == {"a": frozenset("a"), "b": frozenset("b"), "c": frozenset("c")}
    # "a" is matched by the pattern of "a" alone, a whole class: the index answers.
    assert searcher._class_of("a") == "a"
    assert searcher.search("a c") is True
    assert searcher.search("a") is False
    assert fake.flat_compiled == 0
    # "b" is matched by the patterns of "a" and of "b": members of two classes.
    assert searcher._class_of("b") is None
    for text in ("bb", "ab", "ba", "b", "b c", "bc", "xbb"):
        assert searcher.search(text) is fake.holds(text), text
    # "ab" is found in "bb" because the pattern of "a" matches "b" ...
    assert searcher.search("bb") is True
    # ... and not in "ba", because the pattern of "b" does not match "a".
    assert searcher.search("ba") is False
    assert fake.flat_compiled == 1


def test_a_chain_of_matches_forms_no_single_class(monkeypatch: pytest.MonkeyPatch) -> None:
    terms = ["ab", "c"]
    fake = _PairwiseRe(terms, _CHAIN)
    monkeypatch.setattr(term_search, "re", fake)
    searcher = WholeTermSearch(terms)
    # "c" matches "b" both ways but not "a", so it stays outside the class of "a" and "b".
    assert searcher._class_members == {"a": frozenset("ab"), "c": frozenset("c")}
    # "a" is matched by the patterns of "a" and "b", exactly their class:
    # the index answers, and it finds "ab" in "aa".
    assert searcher._class_of("a") == "a"
    assert searcher.search("aa") is True
    assert searcher.search("a") is False
    assert searcher.search("aaa") is False
    assert fake.flat_compiled == 0
    # "b" is matched by all three patterns, "c" by those of "b" and "c":
    # neither set is one class.
    assert searcher._class_of("b") is None
    assert searcher._class_of("c") is None
    for text in ("c", "ac", "ab", "bc", "cb", "b", "x c", "ca", "cc", "a c"):
        assert searcher.search(text) is fake.holds(text), text
    assert searcher.search("c") is True
    assert searcher.search("ac") is True  # "ab": the pattern of "b" matches "c"
    assert searcher.search("ca") is False
    assert fake.flat_compiled == 1


def test_a_character_matched_by_half_a_class_is_irregular(monkeypatch: pytest.MonkeyPatch) -> None:
    terms = ["b", "ca"]
    fake = _PairwiseRe(terms, _HALF_A_CLASS)
    monkeypatch.setattr(term_search, "re", fake)
    searcher = WholeTermSearch(terms)
    assert searcher._class_members == {"a": frozenset("ab"), "c": frozenset("c")}
    assert searcher.search("a") is True  # the pattern of "b" matches "a"
    assert fake.flat_compiled == 0
    # "d" is matched by the pattern of "a" but not by that of "b".
    assert searcher._class_of("d") is None
    assert searcher.search("d") is False
    assert searcher.search("cd") is True
    assert searcher.search("d") is fake.holds("d")
    assert searcher.search("cd") is fake.holds("cd")
    assert fake.flat_compiled == 1


@pytest.mark.parametrize("chosen", [_ONE_WAY, _CHAIN, _HALF_A_CLASS], ids=["one-way", "chain", "half-a-class"])
def test_every_short_text_answers_like_the_reference_of_the_same_relation(
    chosen: dict[tuple[str, str], bool], monkeypatch: pytest.MonkeyPatch
) -> None:
    texts = ["".join(chars) for size in range(1, 5) for chars in itertools.product("abcd ", repeat=size)]
    by_index = 0
    by_reference = 0
    for terms in (["ab", "c"], ["a", "bc"], ["abc"], ["b"], ["ca", "ab", "d"], ["a b", "cc"]):
        fake = _PairwiseRe(terms, chosen)
        monkeypatch.setattr(term_search, "re", fake)
        searcher = WholeTermSearch(terms)
        for text in texts:
            assert searcher.search(text) is fake.holds(text), (terms, text)
        by_index += len(texts) - fake.reference_searches
        by_reference += fake.reference_searches
    # Both ways are taken: the comparison is not only the reference with
    # itself, and the relation does make characters irregular.
    assert by_index > 100, by_index
    assert by_reference > 100, by_reference
