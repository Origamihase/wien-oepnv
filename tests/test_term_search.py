"""``WholeTermSearch`` answers like the flat pattern it replaces.

The reference in every test is the pattern ``_vienna_stations_regex`` built
until 2026-11-10: all terms escaped, joined by ``|``, between ``(?<!\\w)``
and ``(?!\\w)``, compiled with ``re.IGNORECASE``. Empty terms are left out
of it, as ``WholeTermSearch`` ignores them. How characters are grouped into
classes and when the flat pattern is asked after all is pinned in
``tests/test_term_search_classes.py``.
"""
from __future__ import annotations

import random
import re
import sys
from unittest.mock import patch

import pytest

from src.utils.term_search import WholeTermSearch


def _flat(terms: list[str]) -> re.Pattern[str]:
    ordered = sorted({t for t in terms if t}, key=len, reverse=True)
    if not ordered:
        return re.compile(r"(?!x)x")
    body = "|".join(re.escape(term) for term in ordered)
    return re.compile(r"(?<!\w)(?:" + body + r")(?!\w)", re.IGNORECASE)


def _same(terms: list[str], texts: list[str]) -> None:
    searcher = WholeTermSearch(terms)
    flat = _flat(terms)
    for text in texts:
        assert searcher.search(text) is (flat.search(text) is not None), (terms, text)


STATION_TERMS = [
    "wien mitte", "wien mitte-landstraße", "karlsplatz", "praterstern",
    "wien hbf (u)", "st. marx", "floridsdorf", "wien 10., favoriten",
    "währinger straße-volksoper", "meidling", "matzleinsdorfer platz", "u-bahn",
    "simmering (wl)", "a.k.h.", "rochusgasse", "wien", "erdberg", "enkplatz",
]


@pytest.mark.parametrize(
    "text",
    [
        "Sperre bei Wien Mitte.",
        "WIEN MITTE-LANDSTRASSE",
        "Wien Mitte-Landstraße gesperrt",
        "wien mitte-landstrasse",
        "Umbau bei Karlsplatz.",
        "Karlsplatzes",
        "amKarlsplatz",
        "Karlsplatz9",
        "9Karlsplatz",
        "Karlsplatz_",
        # Word characters that are numeric but no digits, and digits of
        # another script.
        "Karlsplatz½",
        "½Karlsplatz",
        "²Karlsplatz",
        "Karlsplatz²",
        "Karlsplatz٣",
        "ⅧKarlsplatz",
        "KarlsplatzⅧ",
        "Karlsplatz ½",
        # A combining mark is no word character.
        "Karlsplatz\u0301",
        "(Karlsplatz)",
        "Karlsplatz/Praterstern",
        "Wien Hbf (U)",
        "Wien Hbf (U)x",
        "Wien Hbf (U",
        "St. Marx",
        "St Marx",
        "st.  marx",
        "Wien 10., Favoriten",
        "A.K.H.",
        "A.K.H.x",
        "xA.K.H.",
        "Simmering (WL) und Enkplatz",
        "Währinger Straße-Volksoper",
        "WÄHRINGER STRASSE-VOLKSOPER",
        "WÄHRINGER STRAẞE-VOLKSOPER",
        "Meidling\nMeidlinger",
        "wien",
        "Wiener",
        "U-Bahn",
        "",
        " ",
        "Erd­berg",
        "Erdberg Enkplatz",
        "Floridſdorf",
        "FLORIDSDORF",
        "Mat",
        "Ma",
    ],
)
def test_station_terms_answer_like_the_flat_pattern(text: str) -> None:
    _same(STATION_TERMS, [text])


def test_terms_shorter_than_the_prefix_and_of_mixed_length() -> None:
    terms = ["a", "ab", "abc", "abcd", "abcde", "b c", "ß", "é"]
    texts = [
        "a", "A", "ab", "aB", "abc", "abcd", "ABCDE", "abcdef", "xa", "a b c",
        "b  c", "B C", "ss", "ß", "ẞ", "É", "é", "é", "a.b", "-a-", "aa",
    ]
    _same(terms, texts)


@pytest.mark.parametrize(
    ("terms", "texts"),
    [
        # Characters whose case-insensitive match is special in ``re``.
        (["straße"], ["STRASSE", "Straße", "STRAẞE", "straẞe", "strasse"]),
        (["straẞe"], ["Straße", "STRAẞE", "strasse"]),
        (["straße", "straẞe"], ["Straße", "STRAẞE"]),
        (["gasse"], ["GASSE", "gaſſe", "GAſSE", "gasze"]),
        (["gaſſe"], ["GASSE", "gasse", "gaſſe"]),
        (["kilo"], ["KILO", "Kilo", "kılo", "kİlo", "KİLO"]),
        (["Kilo"], ["kilo", "KILO", "Kilo"]),
        (["ılı", "ili"], ["ILI", "ili", "ılı", "İLİ", "ıLi"]),
        (["µm", "μm"], ["µm", "μm", "ΜM", "Μm"]),
        (["ǆ", "ǅ", "Ǆ"], ["ǆ", "ǅ", "Ǆ"]),
        (["σ", "ς"], ["Σ", "σ", "ς"]),
        (["ω"], ["Ω", "Ω", "ω"]),
        (["å"], ["Å", "Å", "å"]),
        (["ΐ"], ["ΐ", "ΐ"]),
        (["ﬅ", "ﬆ"], ["ﬅ", "ﬆ", "st", "ST"]),
        (["ṡ"], ["Ṡ", "ẛ", "ṡ"]),
        (["θ"], ["ϑ", "Θ", "ϴ", "θ"]),
    ],
)
def test_special_case_pairs_answer_like_the_flat_pattern(
    terms: list[str], texts: list[str]
) -> None:
    _same(terms, texts)


@pytest.mark.parametrize("neighbour", ["½", "⅓", "Ⅷ", "²", "٣", "五"])
def test_numeric_word_character_next_to_a_term_is_part_of_the_word(neighbour: str) -> None:
    # ``\w`` of ``re`` is wider than letters and digits: fractions and Roman
    # numerals count as well ("½".isalpha() and "½".isdigit() are both False).
    assert re.fullmatch(r"\w", neighbour) is not None
    searcher = WholeTermSearch(["karlsplatz"])
    assert searcher.search(f"Karlsplatz{neighbour}") is False
    assert searcher.search(f"{neighbour}Karlsplatz") is False
    assert searcher.search(f"Karlsplatz {neighbour}") is True
    assert searcher.search(f"{neighbour} Karlsplatz") is True
    _same(
        ["karlsplatz"],
        [f"Karlsplatz{neighbour}", f"{neighbour}Karlsplatz", f"Karlsplatz {neighbour}", f"{neighbour} Karlsplatz"],
    )


def test_characters_of_one_class_are_folded_together() -> None:
    searcher = WholeTermSearch(["straße", "STRAẞE", "Kilo", "gaſse"])
    for same in ("ßẞ", "kK\u212A", "sSſ"):
        assert len({searcher._class_of(char) for char in same}) == 1, same
    assert searcher._class_of("x") == searcher._no_class
    assert None not in {searcher._class_of(chr(cp)) for cp in range(0x250)}


def test_random_texts_answer_like_the_flat_pattern() -> None:
    rng = random.Random(20261110)  # noqa: S311 - test data, no secret
    alphabet = "abcdeßäöüſ .-()1Ee"
    terms = sorted({
        "".join(rng.choice(alphabet) for _ in range(rng.randint(1, 7))).strip() or "x"
        for _ in range(400)
    })
    text_alphabet = alphabet + "ABCDẞÄÖÜS_,\n/éİıKK"
    texts = ["".join(rng.choice(text_alphabet) for _ in range(rng.randint(0, 40))) for _ in range(3000)]
    # Texts built from the terms themselves: hits at the start, the end and
    # next to every kind of neighbour.
    glue = [" ", "", "-", "x", "9", "_", ". ", "(", ")", "\n", "ß"]
    for _ in range(3000):
        parts: list[str] = []
        for _ in range(rng.randint(1, 3)):
            term = rng.choice(terms)
            parts.append(rng.choice([term, term.upper(), term.title(), term[:-1], term + "s"]))
            parts.append(rng.choice(glue))
        texts.append("".join(parts))
    _same(terms, texts)
    flat = _flat(terms)
    hits = sum(1 for text in texts if flat.search(text))
    assert 500 < hits < len(texts) - 500, hits


def test_no_terms_and_an_empty_text_never_match() -> None:
    assert WholeTermSearch([]).search("Wien  Mitte") is False
    assert WholeTermSearch([]).search("") is False
    assert WholeTermSearch(["wien"]).search("") is False


@pytest.mark.parametrize("terms", [[""], ["", ""], ["", "karlsplatz"]])
@pytest.mark.parametrize("text", ["Wien  Mitte", "Linz - Wels", " ", ".", ""])
def test_empty_terms_are_ignored(terms: list[str], text: str) -> None:
    """Documented behaviour: an empty term is no term.

    The formula in the module docstring, taken literally, keeps an empty
    alternative, and that matches wherever two non-word characters meet and
    in an empty text. ``WholeTermSearch`` leaves empty terms out instead
    ("Empty terms are ignored"), so the formula holds for non-empty terms.
    """
    literal = re.compile(r"(?<!\w)(?:" + "|".join(map(re.escape, terms)) + r")(?!\w)", re.IGNORECASE)
    assert literal.search(text) is not None
    assert WholeTermSearch(terms).search(text) is False


def test_an_empty_term_does_not_hide_the_other_terms() -> None:
    searcher = WholeTermSearch(["", "wien", ""])
    assert searcher.search("Wien  Mitte") is True
    assert searcher.search("Wiener  Mitte") is False
    _same(["", "wien", "a"], ["Wien  Mitte", "Wiener  Mitte", "a", " a ", "  ", ""])


_U_FFFF_TEXTS = [
    "wien\uffff", "WIEN\uffff", "Sperre bei Wien\uffff.", "wien", "wien\uffffx", "xwien\uffff",
    "wien\uffff\uffff", "wien\ufffe", "wien\ufffd", "wien?", "wien.", "wienx", "\uffff", "\ufffe",
    "\ufffd", "mitte", "mitte\ufffe", "mitte\uffff", "mitte\ufffd", "mitte!", "wien\uffff mitte\ufffe",
]


@pytest.mark.parametrize(
    "terms",
    [["wien\uffff"], ["wien\uffff", "mitte"], ["wien\uffff", "mitte\ufffe"], ["\uffff"], ["\uffff", "\ufffe", "\ufffd"]],
)
def test_a_term_with_u_ffff_answers_like_the_flat_pattern(terms: list[str]) -> None:
    _same(terms, _U_FFFF_TEXTS)


def test_a_term_with_u_ffff_is_searched_like_any_other_term() -> None:
    searcher = WholeTermSearch(["wien\uffff", "mitte"])
    assert searcher.search("Sperre bei Wien\uffff.") is True
    assert searcher.search("WIEN\uffff") is True
    assert searcher.search("Wien Mitte") is True
    # U+FFFF is a character of the term, not a stand-in for any character.
    for text in ("wien", "wien?", "wien.", "wienx", "wien\ufffe", "wien\ufffd", "\uffff"):
        assert searcher.search(text) is False, text
    # The neighbours count as for any other term; U+FFFF is no word character.
    assert searcher.search("wien\uffffx") is False
    assert searcher.search("xwien\uffff") is False
    assert searcher.search("wien\uffff\uffff") is True
    assert searcher.search("\uffffmitte\uffff") is True


@pytest.mark.parametrize(
    ("terms", "expected"),
    [
        (["wien", "mitte"], "\uffff"),
        (["wien\uffff"], "\ufffe"),
        (["wien\ufffe"], "\uffff"),
        (["wien\uffff", "mitte\ufffe"], "\ufffd"),
        (["\ufffe\uffff\ufffd", "\ufffc"], "\ufffb"),
        ([], "\uffff"),
    ],
)
def test_the_no_class_sign_is_the_last_bmp_character_no_term_contains(
    terms: list[str], expected: str
) -> None:
    searcher = WholeTermSearch(terms)
    assert searcher._no_class == expected
    assert searcher._no_class not in set("".join(terms))
    # It names no class either, so no folded term can contain it.
    assert searcher._no_class not in searcher._class_members
    assert all(searcher._no_class not in folded for folded in searcher._terms)


@pytest.mark.parametrize(
    "terms",
    [["wien", "mitte"], ["wien\uffff", "mitte"], ["wien\uffff", "mitte\ufffe"], ["wien\ufffd", "mitte\uffff\ufffe"]],
)
def test_the_no_class_sign_in_a_text_matches_nothing(terms: list[str]) -> None:
    searcher = WholeTermSearch(terms)
    sign = searcher._no_class
    # In a text the sign is a character like "?": no term character matches it.
    assert searcher._class_of(sign) == searcher._class_of("?") == sign
    texts = [sign, sign * 4, sign * 5]
    for term in terms:
        texts.append(term[:-1] + sign)
        texts.append(sign + term[1:])
        texts.extend(term.replace(char, sign) for char in set(term))
    for text in texts:
        assert searcher.search(text) is False, (terms, text)
    # Next to a term it is an ordinary non-word character.
    for term in terms:
        assert searcher.search(f"{sign}{term}{sign}") is True, (terms, term)
    _same(terms, texts + [f"{sign}{term}{sign}" for term in terms] + ["wien " + sign + " mitte"])


def test_upper_case_letter_beyond_u_ffff_among_alternatives_is_the_known_difference() -> None:
    """Pins the one difference the module docstring names; it lies in the pattern.

    This documents the pattern of CPython 3.11, not a property of the index.
    CPython 3.11 turns the alternatives U+10400 and "x" into a character
    set, and in such a set an upper-case letter beyond U+FFFF matches nothing.
    The index follows the single literal, which matches. Seen on 3.11.18 and
    3.12.3; on 3.13.18 the pattern answers like the index. The assertion on
    the flat pattern is therefore bound to 3.11: once the project leaves
    3.11 it may be dropped, together with the paragraph in the module
    docstring of ``src/utils/term_search.py``. The assertions on the index
    hold on every version.
    """
    upper, lower = "\U00010400", "\U00010428"  # DESERET CAPITAL / SMALL LETTER LONG I
    assert upper.lower() == lower
    # As a single literal the upper-case letter matches both forms.
    assert _flat([upper]).search(upper) is not None
    assert _flat([upper]).search(lower) is not None
    _same([upper], [upper, lower, "x"])
    # Among alternatives the index still answers like the single literal ...
    searcher = WholeTermSearch([upper, "x"])
    assert searcher.search(upper) is True
    assert searcher.search(lower) is True
    assert searcher.search("x") is True
    # ... while the flat pattern of CPython 3.11 does not match.
    if sys.version_info[:2] == (3, 11):
        hint = "this CPython 3.11 matches the letter in a character set: drop this assertion, the index is right"
        assert _flat([upper, "x"]).search(upper) is None, hint
        assert _flat([upper, "x"]).search(lower) is None, hint
    # With the lower-case form as term both agree, on every version.
    _same([lower, "x"], [upper, lower, "x", "X", f"a{lower}", f"{upper} x"])
    assert WholeTermSearch([lower, "x"]).search(upper) is True
    assert _flat([lower, "x"]).search(upper) is not None


def test_text_has_vienna_connection_uses_the_index() -> None:
    from src.utils import stations

    searcher = stations._vienna_stations_regex()
    assert isinstance(searcher, WholeTermSearch)
    assert stations.text_has_vienna_connection("Umbau bei Karlsplatz.") is True
    assert stations.text_has_vienna_connection("Umbau bei Karlsplatzhausen.") is False
    # The real directory needs no fall-back for an ordinary German text.
    assert searcher.search("Störung zwischen Praterstern und Floridsdorf: ẞ, é, ı, İ, –, „“") is True
    assert searcher._flat is None


def test_directory_alias_with_u_ffff_is_an_ordinary_term() -> None:
    """A name or alias of the directory may contain U+FFFF.

    Until the review of 2026-11-10 ``WholeTermSearch`` refused such a term,
    and every call of ``text_has_vienna_connection`` would have raised.
    """
    from src.utils import stations

    entries = (
        {"name": "Wien Test", "in_vienna": True, "aliases": ["Teststation\uffff", "Test Alias"]},
    )
    # Both searches are cached per process: neither may keep the result of
    # the small directory, nor start from the result of the real one.
    cached = (stations._vienna_stations_regex, stations._non_vienna_stations_regex)
    for function in cached:
        function.cache_clear()
    try:
        with patch.object(stations, "_station_entries", return_value=entries):
            assert stations.text_has_vienna_connection("Umbau bei Test Alias.") is True
            assert stations.text_has_vienna_connection("Umbau bei Teststation\uffff.") is True
            assert stations.text_has_vienna_connection("Umbau bei Teststation.") is False
            assert stations.text_has_vienna_connection("Umbau bei Teststation?") is False
            searcher = stations._vienna_stations_regex()
            assert isinstance(searcher, WholeTermSearch)
            assert searcher._no_class == "\ufffe"
    finally:
        for function in cached:
            function.cache_clear()
