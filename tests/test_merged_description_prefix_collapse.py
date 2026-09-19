"""The title collapsed the shared prefix; the description repeated it.

When two Wiener-Linien items of the same line merge, the TITLE has long
factored out the part both say and joined only the differing tails —
``_join_merged_names`` → :func:`~src.feed.merge._collapse_common_prefix`.
The description did not. It concatenated both source texts verbatim,
shared prefix and all, and the two stood side by side in the feed::

    T: O: Schadhafter Bus Betrieb ab Praterstern, Quartier Belvedere
    D: Schadhafter Bus Betrieb ab Praterstern
       Schadhafter Bus Betrieb ab Quartier Belvedere

Measured over the published German feed: at least 6 of 250 items. The
fix is not a new rule — it is the rule the title already follows,
applied one field over, with the same constraints (≥10 shared characters
ending on a word boundary, incoming suffix ≤60 characters, no ÖBB ``↔``
chain).

Where the collapse declines, the legacy blank-line join stands. Two of
the six measured cases decline on purpose and belong to different
mechanisms:

* ``Fahrtbehinderung PKW im Gleis`` + ``PKW im Gleis Betrieb ab
  Raxstraße`` — the overlap is a *suffix* of the first and a *prefix* of
  the second, not a shared opening.
* ``Linien 25 und 26 Betrieb ab Josef-Baumann-Gasse`` + ``Ersatzbus ab
  Josef-Baumann-Gasse`` — the shared part is a *suffix* of both.

Both are left alone here rather than bolted onto the same pass.

One boundary the title never needed: a description written as a
**sentence** keeps the blank-line join. The comma the collapse inserts
would land behind the full stop — ``Details about Lauf., Pfad.`` — and
a text of several sentences would see two different second sentences
glued into one statement. The measured cases are clause-form and carry
no terminator.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.feed.merge import (
    _collapse_common_prefix,
    _collapse_description_prefix,
    deduplicate_fuzzy,
)


def _item(title: str, description: str, guid: str) -> dict[str, Any]:
    return {"title": title, "description": description, "guid": guid}


# ---------------- what the fix collapses ----------------


@pytest.mark.parametrize(
    ("first", "second", "expected"),
    [
        (
            "Schadhafter Bus Betrieb ab Praterstern",
            "Schadhafter Bus Betrieb ab Quartier Belvedere",
            "Schadhafter Bus Betrieb ab Praterstern, Quartier Belvedere",
        ),
        (
            "Fremdunfall Züge halten bei den Linien 1,18,62 WLB",
            "Fremdunfall Züge halten bei der Linie O",
            "Fremdunfall Züge halten bei den Linien 1, 18, 62 WLB, der Linie O",
        ),
        (
            "Busse halten bei Taborstraße 35-37 Gleisbauarbeiten",
            "Busse halten bei Taborstraße 62",
            "Busse halten bei Taborstraße 35-37 Gleisbauarbeiten, 62",
        ),
    ],
)
def test_each_published_case(first: str, second: str, expected: str) -> None:
    """Descriptions taken verbatim from the published feed."""
    merged = deduplicate_fuzzy(
        [
            _item("11A: Verkehrsstörung", first, "guid1"),
            _item("11A: Verkehrsstörung", second, "guid2"),
        ]
    )

    assert len(merged) == 1
    assert merged[0]["description"] == expected


def test_the_repeat_is_gone_not_just_shortened() -> None:
    """The point: the shared opening appears once, not twice."""
    merged = deduplicate_fuzzy(
        [
            _item(
                "O: Schadhafter Bus",
                "Schadhafter Bus Betrieb ab Praterstern",
                "guid1",
            ),
            _item(
                "O: Schadhafter Bus",
                "Schadhafter Bus Betrieb ab Quartier Belvedere",
                "guid2",
            ),
        ]
    )
    desc = merged[0]["description"]

    assert desc.count("Schadhafter Bus Betrieb ab") == 1, desc
    assert "Praterstern" in desc
    assert "Quartier Belvedere" in desc


# ---------------- what must keep the blank-line join ----------------


def test_unrelated_descriptions_still_stack() -> None:
    """Two sentences with nothing in common read better stacked.

    Comma-joining them would produce exactly the wall of text the
    collapse exists to prevent, so the legacy join has to survive.
    """
    merged = deduplicate_fuzzy(
        [
            _item("1/2: Event", "Details about Lauf.", "guid1"),
            _item("1/2: Event", "Etwas völlig anderes.", "guid2"),
        ]
    )
    desc = merged[0]["description"]

    assert "Details about Lauf." in desc
    assert "Etwas völlig anderes." in desc
    assert "\n\n" in desc, desc


@pytest.mark.parametrize(
    ("first", "second"),
    [
        # Overlap is a suffix of the first and a prefix of the second —
        # a different mechanism, deliberately not handled here.
        (
            "Fahrtbehinderung PKW im Gleis",
            "PKW im Gleis Betrieb ab Raxstraße",
        ),
        # Shared part is a suffix of BOTH.
        (
            "Linien 25 und 26 Betrieb ab Josef-Baumann-Gasse",
            "Ersatzbus ab Josef-Baumann-Gasse",
        ),
        # Nothing in common at all.
        ("Kein Betrieb.", "Umleitung."),
        # A short shared opening is a coincidence of German grammar, not
        # a shared meaning. ``Kein `` is 5 characters; the threshold is
        # 10. Collapsing here would produce "Kein Betrieb ab Praterstern,
        # Halt in Floridsdorf" — two unrelated facts glued into one.
        ("Kein Betrieb ab Praterstern", "Kein Halt in Floridsdorf"),
        ("Wegen Bauarbeiten gesperrt", "Wegen Schnee eingestellt"),
    ],
)
def test_the_collapse_declines_where_it_should(first: str, second: str) -> None:
    assert _collapse_common_prefix(first, second) is None


def test_a_short_shared_opening_stays_stacked_end_to_end() -> None:
    """The threshold has to hold through the merge, not just in the helper.

    Two sentences that happen to open with the same German function word
    must not end up comma-joined into a single false statement.
    """
    merged = deduplicate_fuzzy(
        [
            _item("U6: Störung", "Kein Betrieb ab Praterstern", "guid1"),
            _item("U6: Störung", "Kein Halt in Floridsdorf", "guid2"),
        ]
    )
    desc = merged[0]["description"]

    assert "\n\n" in desc, desc
    assert desc.count("Kein ") == 2, desc


@pytest.mark.parametrize(
    ("first", "second"),
    [
        # Single sentences: the comma would land behind the full stop.
        ("Details about Lauf.", "Details about Pfad."),
        # Several sentences: two different second sentences would be
        # glued into one statement.
        (
            "Kein Betrieb ab Praterstern. Grund: Unfall.",
            "Kein Betrieb ab Praterstern. Ersatzbus fährt.",
        ),
        ("Umleitung über den Ring! Bitte umsteigen.", "Umleitung über den Gürtel!"),
    ],
)
def test_a_sentence_keeps_the_blank_line_join(first: str, second: str) -> None:
    """Sentence-form descriptions decline — even with a long shared prefix.

    Below the helper is asked directly, then the same texts go through
    the merge, so a lost guard fails here whichever way it is lost.
    """
    assert _collapse_description_prefix(first, second) is None

    merged = deduplicate_fuzzy(
        [
            _item("1/2: Event", first, "guid1"),
            _item("1/2: Event", second, "guid2"),
        ]
    )
    desc = merged[0]["description"]

    assert desc == f"{first}\n\n{second}", desc


def test_a_full_stop_inside_a_word_is_not_a_terminator() -> None:
    """Only ``.`` before whitespace or the end ends a sentence.

    ``Linien 1,18,62`` and ``35-37`` have to keep collapsing — and so
    would a decimal — so the guard must not fire on every dot.
    """
    assert (
        _collapse_description_prefix(
            "Busse halten bei Taborstraße 35-37 Gleisbauarbeiten",
            "Busse halten bei Taborstraße 62",
        )
        == "Busse halten bei Taborstraße 35-37 Gleisbauarbeiten, 62"
    )
    assert (
        _collapse_description_prefix(
            "Betriebsstörung Haltestelle Km 3.5 Nord",
            "Betriebsstörung Haltestelle Km 3.5 Süd",
        )
        == "Betriebsstörung Haltestelle Km 3.5 Nord, Süd"
    )


def test_an_oebb_chain_route_is_never_collapsed() -> None:
    """``↔`` joins a route, it does not separate two alternatives.

    Collapsing on a shared prefix would mangle the chain, so the guard
    in ``_collapse_common_prefix`` has to keep holding for descriptions
    too — the field this fix newly routes through it.
    """
    first = "Streckensperre Wien Meidling ↔ Wien Hütteldorf"
    second = "Streckensperre Wien Meidling ↔ Wien Penzing"

    assert _collapse_common_prefix(first, second) is None

    merged = deduplicate_fuzzy(
        [
            _item("S45: Streckensperre", first, "guid1"),
            _item("S45: Streckensperre", second, "guid2"),
        ]
    )
    assert "↔ Wien Hütteldorf" in merged[0]["description"]
    assert "↔ Wien Penzing" in merged[0]["description"]


def test_a_contained_description_still_wins_outright() -> None:
    """Containment is checked before the collapse and must stay first.

    When one description already holds the other word for word, the
    longer one is the answer — not a comma-joined hybrid.
    """
    merged = deduplicate_fuzzy(
        [
            _item("40: Störung", "Betrieb ab Gersthof.", "guid1"),
            _item(
                "40: Störung",
                "Betrieb ab Gersthof. Grund: Gleisschaden.",
                "guid2",
            ),
        ]
    )
    assert merged[0]["description"] == "Betrieb ab Gersthof. Grund: Gleisschaden."


# ---------------- the rule must actually run ----------------


def test_the_collapse_is_wired_into_the_description_merge() -> None:
    """Calling the helper directly proves nothing about the merge path.

    Dropping the call from ``deduplicate_fuzzy`` leaves every direct
    ``_collapse_common_prefix`` assertion above passing — the same gap
    that slipped through in PR #1842. This is the test that fails.
    """
    merged = deduplicate_fuzzy(
        [
            _item(
                "72A: Haltestellenverlegung",
                "Ersatzbus hält Karl-Waldbrunner-Platz vor Schloßhofer Straße",
                "guid1",
            ),
            _item(
                "72A: Haltestellenverlegung",
                "Ersatzbus hält Kurt-Waldbrunner-Platz vor Schloßhofer Straße",
                "guid2",
            ),
        ]
    )
    desc = merged[0]["description"]

    assert "\n\n" not in desc, desc
    assert desc.count("Ersatzbus hält") == 1, desc
