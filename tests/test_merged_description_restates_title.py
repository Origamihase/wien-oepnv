"""A merged description must not repeat what the merged title says.

When two Wiener-Linien items of one line merge, their descriptions are
stacked. A headline-only item — one whose description is its own title
restated — brings that restatement along, and after the merge it sits
under the other item's real text. Published 2026-09-19, item 4::

    T: 2: Demonstration Züge halten Steig A & Züge halten bei Linie 46
    D: Nach einer Fahrtbehinderung kommt es zu unterschiedlichen
       Intervallen. Züge halten bei Linie 46

Alone, ``Züge halten bei Linie 46`` would have been emptied at emission
by ``_summary_duplicates_title`` (#1836). That rule sees the whole
summary only, and by then the paragraphs are one line. So the merge
drops a description the merged title already states, before stacking.

Measured over 300 revisions of the published feed: every ``&``-merged
item whose description restated the title *in full* was already fixed by
#1836; the one-paragraph case is this item, in two variants.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.feed.merge import _restates_title, deduplicate_fuzzy


def _item(title: str, description: str, guid: str) -> dict[str, Any]:
    return {"title": title, "description": description, "guid": guid}


# ---------------- the published case ----------------


def test_the_published_item() -> None:
    merged = deduplicate_fuzzy(
        [
            _item(
                "2: Demonstration Züge halten Steig A",
                "Nach einer Fahrtbehinderung kommt es zu unterschiedlichen Intervallen.",
                "guid1",
            ),
            _item("2: Züge halten bei Linie 46", "Züge halten bei Linie 46", "guid2"),
        ]
    )

    assert len(merged) == 1
    assert merged[0]["title"] == ("2: Demonstration Züge halten Steig A & Züge halten bei Linie 46")
    assert merged[0]["description"] == ("Nach einer Fahrtbehinderung kommt es zu unterschiedlichen Intervallen.")


def test_the_result_does_not_depend_on_arrival_order() -> None:
    """Both sides are checked, so the first-arrived item gets no free pass."""
    merged = deduplicate_fuzzy(
        [
            _item("2: Züge halten bei Linie 46", "Züge halten bei Linie 46", "guid1"),
            _item(
                "2: Demonstration Züge halten Steig A",
                "Nach einer Fahrtbehinderung kommt es zu unterschiedlichen Intervallen.",
                "guid2",
            ),
        ]
    )

    assert len(merged) == 1
    assert merged[0]["description"] == ("Nach einer Fahrtbehinderung kommt es zu unterschiedlichen Intervallen.")
    assert "Züge halten bei Linie 46" not in merged[0]["description"]


def test_two_headlines_leave_no_description_at_all() -> None:
    """When both descriptions are the title, the title is the item."""
    merged = deduplicate_fuzzy(
        [
            _item(
                "25: Linien 25 und 26 Betrieb ab Josef-Baumann-Gasse",
                "Linien 25 und 26 Betrieb ab Josef-Baumann-Gasse",
                "guid1",
            ),
            _item(
                "25: Ersatzbus ab Josef-Baumann-Gasse",
                "Ersatzbus ab Josef-Baumann-Gasse >",
                "guid2",
            ),
        ]
    )

    assert len(merged) == 1
    assert merged[0]["description"] == ""


# ---------------- what must stay ----------------


def test_a_description_with_its_own_content_stays() -> None:
    merged = deduplicate_fuzzy(
        [
            _item("40: Störung", "Kein Betrieb zwischen Gersthof und Herbeckstraße.", "guid1"),
            _item("40: Störung", "Grund: Gleisschaden.", "guid2"),
        ]
    )

    desc = merged[0]["description"]
    assert "Kein Betrieb zwischen Gersthof und Herbeckstraße." in desc
    assert "Grund: Gleisschaden." in desc


@pytest.mark.parametrize(
    ("desc", "title_body", "expected"),
    [
        ("Züge halten bei Linie 46", "Demonstration Steig A & Züge halten bei Linie 46", True),
        # Trailing directional marker and full stop do not hide a restatement.
        ("Ersatzbus ab Josef-Baumann-Gasse >", "Ersatzbus ab Josef-Baumann-Gasse", True),
        ("Kein Betrieb.", "Demonstration Kein Betrieb", True),
        # Whole words only: a longer street name is not the same statement.
        ("Betrieb ab Gersthof", "Betrieb ab Gersthofer Straße", False),
        # Sharing words is not restating.
        ("Züge halten in beiden Richtungen", "Züge halten bei Linie 46", False),
        ("", "Störung", False),
    ],
)
def test_restates_title(desc: str, title_body: str, expected: bool) -> None:
    assert _restates_title(desc, title_body) is expected


def test_the_rule_is_wired_into_the_merge() -> None:
    """The helper alone proves nothing; this fails when the call is gone."""
    merged = deduplicate_fuzzy(
        [
            _item("D: Betrieb ab Quartier Belvedere", "Grund: Demonstration am Ring.", "guid1"),
            _item("D: Kein Betrieb ab Börse", "Kein Betrieb ab Börse", "guid2"),
        ]
    )

    assert merged[0]["description"] == "Grund: Demonstration am Ring."
