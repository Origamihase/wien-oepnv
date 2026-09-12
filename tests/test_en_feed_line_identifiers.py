"""Line identifiers must survive translation byte-for-byte.

Live regression, 2026-09-12 — ``docs/feed.en.xml`` item 10::

    DE  43A/44A/844/N43/44B: Gleisbauarbeiten (Phase 2)
    EN  43A/44A844/N43/44B: track construction works (phase 2)

The slash between ``44A`` and ``844`` is gone and two lines have merged into
an invented ``44A844``. Cause: ``_LINE_ENTITY_RE`` masks line tokens so the
NMT model never sees them, and its old shape
``U[1-6]|S[0-9]+|[1-9][0-9]?[A-Z]?`` covered only 58 of the 71 line tokens
present in the live caches. ``844`` went to the model unprotected, and the
model reshaped the text around it.

An unprotected token is not reliably broken — it is reliably *unprotected*.
On the audited run ``86AR``, ``N71`` and ``N31`` came through intact while
``844`` did not. That is why these tests assert on the MASKING, not on
translated output: the defect is the missing protection, and a test that
only checked one model's output would go green on a lucky day.

A line number is a name, not a word. This affects ``docs/feed.en.xml`` only
— the German feed is built from the untranslated text.
"""

from __future__ import annotations

import re

import pytest

from src import build_feed
from src.build_feed import _LINE_ENTITY_RE, _TRAM_LETTER_LINE_RE, _mask_entities

# Every line token appearing as a title prefix in the live caches on
# 2026-09-12, verbatim. 71 of them; the old pattern protected 58.
_LIVE_LINE_TOKENS = (
    # tram / bus, one or two digits — protected before and after
    "1", "2", "11", "18", "25", "26", "27", "31", "40", "41", "44", "46",
    "49", "52", "62", "71",
    "1A", "2A", "3A", "4A", "5A", "5B", "11A", "12A", "16A", "17A", "26A",
    "28A", "29A", "31A", "37A", "43A", "44A", "44B", "49A", "50B", "56A",
    "56B", "57A", "58A", "58B", "60A", "65A", "66A", "71E", "74A", "76A",
    "76B", "86A", "87A",
    "U1", "U4", "S1", "S2", "S3", "S4", "S7", "S80",
    # …and the thirteen the old pattern missed
    "844",    # three-digit regional bus — only two digits were allowed
    "86AR",   # two-letter suffix — only one letter was allowed
    "U6E",    # U-Bahn Verstärker — no suffix allowed after U<n>
    "N8", "N20", "N29", "N43", "N46", "N49", "N65", "N66", "N71",  # night buses
)

# The shape in force before this fix. Kept verbatim so the superset property
# below is checked against the real thing, not against a paraphrase.
_OLD_LINE_ENTITY_RE = re.compile(r"\b(U[1-6]|S[0-9]+|[1-9][0-9]?[A-Z]?)\b")


def _unmasked_remainder(text: str) -> str:
    """``text`` with every placeholder blanked, so leftovers stand out."""
    masked, mapping = _mask_entities(text)
    for placeholder in mapping:
        masked = masked.replace(placeholder, "·")
    return masked


# ---------------- the live regression ----------------


def test_the_live_regression_title_is_masked_completely() -> None:
    """``844`` and ``N43`` reached the model; that is what merged the lines."""
    title = "43A/44A/844/N43/44B: Gleisbauarbeiten (Phase 2)"
    remainder = _unmasked_remainder(title)

    assert remainder.startswith("·/·/·/·/·:"), remainder
    for token in ("43A", "44A", "844", "N43", "44B"):
        assert token not in remainder, f"{token} ging ungeschützt ans Modell"


def test_the_live_regression_round_trips() -> None:
    """Masking is only useful if the unmasker puts the lines back unchanged."""
    title = "43A/44A/844/N43/44B: Gleisbauarbeiten (Phase 2)"
    masked, mapping = _mask_entities(title)
    assert build_feed._unmask_entities(masked, mapping) == title


@pytest.mark.parametrize("token", _LIVE_LINE_TOKENS)
def test_every_live_line_token_is_protected(token: str) -> None:
    assert _LINE_ENTITY_RE.fullmatch(token), f"{token} ist ungeschützt"


@pytest.mark.parametrize("token", ["844", "86AR", "U6E", "N8", "N43", "N71"])
def test_the_tokens_the_old_pattern_missed(token: str) -> None:
    """Named individually so a regression says which shape broke."""
    assert not _OLD_LINE_ENTITY_RE.fullmatch(token), "Testdaten veraltet"
    assert _LINE_ENTITY_RE.fullmatch(token)


# ---------------- the load-bearing safety property ----------------


@pytest.mark.parametrize("token", _LIVE_LINE_TOKENS)
def test_the_pattern_is_a_strict_superset_of_the_old_one(token: str) -> None:
    """Widening must not cost protection anywhere.

    Every alternative was widened, none narrowed — in particular ``S[0-9]+``
    keeps its unbounded digit run instead of being tightened to the S1–S80
    range actually in service. Narrowing is the only direction that could
    leave a line exposed that used to be safe.
    """
    if _OLD_LINE_ENTITY_RE.fullmatch(token):
        assert _LINE_ENTITY_RE.fullmatch(token), (
            f"{token} war vorher geschützt und ist es jetzt nicht mehr"
        )


def test_a_masked_token_never_leaves_a_fragment_behind() -> None:
    """Half-masking is worse than no masking.

    ``U6E`` is the shape that invites it: the old pattern's ``U[1-6]`` would
    have matched ``U6`` and handed the bare ``E`` to the translator, had the
    trailing ``\\b`` not held it off. The same trap produced "4. Gate" from
    ``4. Tor`` once — see the ordering comment in ``_mask_entities``.
    """
    for token in ("U6E", "86AR", "844", "N43"):
        remainder = _unmasked_remainder(f"{token}: Störung")
        assert remainder == "·: Störung", f"{token} -> {remainder!r}"


def test_line_shaped_station_names_stay_out_of_the_station_pattern() -> None:
    """The widened pattern must not swallow real station names.

    ``_LINE_ENTITY_RE`` doubles as a ``fullmatch`` predicate that keeps
    line-shaped names out of the station protection regex. A name it matches
    loses ITS protection — so widening the pattern here could quietly
    unprotect stations. Measured against the live directory: none.
    """
    from src.utils.stations import _station_entries

    caught = [
        name
        for entry in _station_entries()
        if isinstance(name := entry.get("name"), str)
        and len(name.strip()) >= 4
        and not name.strip().isdigit()
        and _LINE_ENTITY_RE.fullmatch(name.strip())
    ]
    assert caught == [], f"Stationsnamen verlören ihren Schutz: {caught[:10]}"


# ---------------- trams D and O ----------------


@pytest.mark.parametrize(
    "text",
    [
        "D: Gleisbauarbeiten",
        "O: Störung",
        "43A/D: Umleitung",
        "D/O/1: Veranstaltung",
        "Linie D wird umgeleitet",
        "Linie O verkehrt nicht",
    ],
)
def test_a_tram_letter_in_line_context_is_masked(text: str) -> None:
    assert _TRAM_LETTER_LINE_RE.search(text), text


@pytest.mark.parametrize(
    "text",
    [
        "Die D-Mark war die Währung",
        "Vitamin D fehlt",
        "Gleis O steht nicht zur Verfügung",
        "Ausgang D beim Bahnhof",
        "Sektor A/B gesperrt",
    ],
)
def test_a_bare_letter_outside_line_context_is_left_alone(text: str) -> None:
    """Why the tram letters need a context gate at all.

    A single letter carries no shape a regex could recognise, and in German
    prose ``D`` is far more often a word than a tram. Masking it everywhere
    would protect nothing and hide text from the translator.
    """
    assert not _TRAM_LETTER_LINE_RE.search(text), text


def test_a_tram_letter_survives_next_to_a_masked_line() -> None:
    """The tram pass runs AFTER the line pass, so ``43A`` is a placeholder by
    then. Its slash separator has to still be there for the context gate to
    fire — that is the ordering this pins.
    """
    remainder = _unmasked_remainder("43A/D: Umleitung")
    assert remainder == "·/·: Umleitung", remainder
