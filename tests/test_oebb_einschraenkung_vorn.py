"""ÖBB's "Wegen … sind zwischen … keine Fahrten möglich." on the TV and in English.

KI-Wächter 12. and 13.11.2026 (issue #2209), "Wien Meidling ↔ St. Pölten
Hbf": with two routes the sentence is over the 180 characters, and the cut
took what is not possible: "Wegen eines Polizeieinsatzes sind zwischen
Tullnerfeld Bahnhof und St. Pölten Hbf bzw. Tullnerfeld Bahnhof und
Moosbierbaum-Heiligeneich Bahnhof, bis voraussichtlich 13:30 …". Such a
sentence now says what is not possible, with its time, first, and the
routes follow behind a colon (``_oebb_restriction_first``), the same rule as
for "… kann … nicht fahren." The English reads it from the template
(``_OEBB_RESTRICTION_FRONT_RE``).

Mutations checked against this file (each one caught, by the test named):

* the sentence is not moved → ``test_the_meidling_notice_keeps_what_is_not_possible``.
* a sentence that fits is moved too → ``test_a_sentence_that_fits_keeps_its_order``.
* a place with the template's own words is moved → ``test_a_place_with_other_words_keeps_its_order``.
* the colon stays when the place leaves → ``test_the_place_the_title_names_leaves_with_its_colon``.
* the English head goes to the model → ``test_the_cut_sentence_reads_no_trains_can_run``.
* the bare end day goes to the model → ``test_a_bare_end_day_and_a_line_read_english``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

import pytest

from src import build_feed as bf
from src.feed_build.oebb_lead import _oebb_sentence_without_place
from src.feed_build.oebb_summary import _oebb_what_does_not_run_first
from src.feed_types import FeedItem
from tests._conftest.build_feed_bindings import setattr_all

_OEBB = "ÖBB"

# The cache's HTML of 12.11.2026 12:25 (TRACKINFO 926504), first sentence.
_MEIDLING_HTML = (
    "12.11.2026<br/><br/>Wegen eines Polizeieinsatzes sind zwischen <b>Tullnerfeld Bahnhof "
    "</b>und <b>St.Pölten Hbf </b>bzw. <b>Tullnerfeld Bahnhof </b>und <b>Moosbierbaum-"
    "Heiligeneich Bahnhof</b>, bis voraussichtlich <b>13:30 Uhr keine Fahrten</b> möglich. "
    "Einzelne <b>Fernverkehrszüge</b> werden zwischen <b>St.Pölten Hbf</b> und <b>Wien "
    "Meidling Bahnhof </b>über Rekawinkel Bahnhof <b>umgeleitet</b>."
)
_MEIDLING_SENTENCE = (
    "Wegen eines Polizeieinsatzes sind zwischen Tullnerfeld Bahnhof und St. Pölten Hbf bzw. "
    "Tullnerfeld Bahnhof und Moosbierbaum-Heiligeneich Bahnhof, bis voraussichtlich 13:30 "
    "Uhr keine Fahrten möglich."
)


def _render(title: str, raw_desc: str) -> str:
    item = cast(
        FeedItem,
        {
            "title": title,
            "description": raw_desc,
            "source": _OEBB,
            "category": "Störung",
            "guid": "test",
            "link": "",
        },
    )
    formatted = bf._format_item_content(
        item,
        ident="t",
        starts_at=datetime(2026, 11, 11, 23, 0, tzinfo=UTC),
        ends_at=datetime(2026, 11, 12, 22, 59, tzinfo=UTC),
    )
    return formatted.desc_text_truncated


@pytest.fixture
def model(monkeypatch: Any) -> list[str]:
    """An echo model with a cause rule; records what reaches it."""
    seen: list[str] = []

    def pipe(text: str, **_kwargs: Any) -> list[dict[str, str]]:
        seen.append(text)
        if text.startswith("Wegen "):
            text = "Due to " + text[len("Wegen "):]
        return [{"translation_text": text}]

    setattr_all(monkeypatch, bf, "_get_translation_pipeline", lambda: pipe)
    return seen


def test_the_meidling_notice_keeps_what_is_not_possible() -> None:
    shown = _render("Wien Meidling ↔ St. Pölten Hauptbahnhof", _MEIDLING_HTML)
    assert shown.startswith(
        "Wegen eines Polizeieinsatzes sind bis voraussichtlich 13:30 Uhr keine Fahrten möglich: "
        "zwischen Tullnerfeld Bahnhof und St. Pölten Hbf bzw. "
    )
    assert " … [" in shown


@pytest.mark.parametrize(
    ("sentence", "expected"),
    [
        (
            "Wegen einer beschädigten Brücke durch einen Verkehrsunfall sind zwischen Wien "
            "Meidling Bahnhof (U) und Wien Liesing Bahnhof (U) Zugfahrten bis voraussichtlich "
            "10:45 Uhr nur eingeschränkt möglich.",
            "Wegen einer beschädigten Brücke durch einen Verkehrsunfall sind Zugfahrten bis "
            "voraussichtlich 10:45 Uhr nur eingeschränkt möglich: zwischen Wien Meidling Bahnhof "
            "(U) und Wien Liesing Bahnhof (U).",
        ),
        (
            "Wegen eines Rettungseinsatzes sind zwischen Wien Süßenbrunn Bahnhst und Wien "
            "Leopoldau Bahnhst (U) bzw. Wien Süßenbrunn Bahnhst und Wien Stadlau Bahnhst (U), "
            "derzeit keine Fahrten möglich.",
            "Wegen eines Rettungseinsatzes sind derzeit keine Fahrten möglich: zwischen Wien "
            "Süßenbrunn Bahnhst und Wien Leopoldau Bahnhst (U) bzw. Wien Süßenbrunn Bahnhst und "
            "Wien Stadlau Bahnhst (U).",
        ),
        (
            "Wegen Verlängerung der Bauarbeiten waren zwischen Wien St. Marx Bahnhst und "
            "Flughafen Wien Bahnhof bzw. zwischen Wien Hbf und Flughafen Wien Bahnhof bis 06:54 "
            "Uhr keine Fahrten möglich.",
            "Wegen Verlängerung der Bauarbeiten waren bis 06:54 Uhr keine Fahrten möglich: "
            "zwischen Wien St. Marx Bahnhst und Flughafen Wien Bahnhof bzw. zwischen Wien Hbf "
            "und Flughafen Wien Bahnhof.",
        ),
        (
            "Wegen Verlängerung der Bauarbeiten sind zwischen Wien Penzing Bahnhst und Wien "
            "Hütteldorf Bahnhof (U) für Züge der Linie S 45 bis voraussichtlich 24.07.2026, "
            "01:00 Uhr keine Fahrten möglich.",
            "Wegen Verlängerung der Bauarbeiten sind für Züge der Linie S 45 bis "
            "voraussichtlich 24.07.2026, 01:00 Uhr keine Fahrten möglich: zwischen Wien "
            "Penzing Bahnhst und Wien Hütteldorf Bahnhof (U).",
        ),
    ],
)
def test_the_forms_of_the_sentence(sentence: str, expected: str) -> None:
    assert _oebb_what_does_not_run_first(sentence) == expected


def test_a_sentence_that_fits_keeps_its_order() -> None:
    sentence = (
        "Wegen Verlängerung der Bauarbeiten sind zwischen Gramatneusiedl Bahnhof und Wien "
        "Hbf bis voraussichtlich 05:30 Uhr keine Fahrten möglich."
    )
    assert _oebb_what_does_not_run_first(sentence) == sentence


def test_a_place_with_other_words_keeps_its_order() -> None:
    # "keine Fahrten der S 80 zwischen …" is not the template's end.
    s80 = (
        "Wegen einer Weichenstörung sind zwischen Wien Hbf und Wien Simmering Bahnhof bis "
        "voraussichtlich 17:30 Uhr keine Fahrten der S 80 zwischen Wien Hbf und Wien Aspern "
        "Nord möglich."
    )
    assert _oebb_what_does_not_run_first(s80) == s80
    # "Zugfahrten" inside the place: the slot is not a list of stations.
    odd = (
        "Wegen einer Oberleitungsstörung sind zwischen Wien Meidling Bahnhof und Tullnerfeld "
        "Bahnhof bzw. Wien Hütteldorf Bahnhof Zugfahrten und Fahrten der Nahverkehrszüge "
        "derzeit nur eingeschränkt möglich."
    )
    assert _oebb_what_does_not_run_first(odd) == odd


def test_the_place_the_title_names_leaves_with_its_colon() -> None:
    moved = (
        "Wegen eines Feuerwehreinsatzes sind Zugfahrten derzeit nur eingeschränkt möglich: "
        "zwischen Wien Stadlau Bahnhst (U) und Wien Simmering Bahnhof (U)."
    )
    assert _oebb_sentence_without_place(moved, "Wien Stadlau ↔ Wien Simmering") == (
        "Wegen eines Feuerwehreinsatzes sind Zugfahrten derzeit nur eingeschränkt möglich."
    )


def test_the_cut_sentence_reads_no_trains_can_run(model: list[str]) -> None:
    sentence = (
        "Wegen eines Polizeieinsatzes sind bis voraussichtlich 13:30 Uhr keine Fahrten möglich: "
        "zwischen Tullnerfeld Bahnhof und St. Pölten Hbf bzw. Tullnerfeld Bahnhof …"
    )
    out = bf._translate_text_attempt(sentence, "t", source=_OEBB)
    assert out == (
        "Due to eines Polizeieinsatzes, no trains can run until approx. 13:30: between "
        "Tullnerfeld station and St. Pölten Hbf or Tullnerfeld station …"
    )
    # Only the cause reached the model.
    assert len(model) == 1 and model[0].startswith("Wegen ")


def test_a_bare_end_day_and_a_line_read_english(model: list[str]) -> None:
    day = (
        "Wegen Bauarbeiten sind bis voraussichtlich 07.09.2026 keine Fahrten möglich: zwischen "
        "Wien Praterstern Bahnhof und Wien Hbf."
    )
    assert bf._translate_text_attempt(day, "t", source=_OEBB) == (
        "Due to construction works, no trains can run until approx. 07.09.2026: between "
        "Wien Praterstern station and Wien Hbf."
    )
    line = (
        "Wegen Bauarbeiten sind für Züge der Linie S 45 bis voraussichtlich 24.07.2026, 01:00 "
        "Uhr keine Fahrten möglich: zwischen Wien Penzing Bahnhst und Wien Hütteldorf …"
    )
    assert bf._translate_text_attempt(line, "t", source=_OEBB) == (
        "Due to construction works, no S 45 trains can run until approx. 24.07.2026, 01:00: "
        "between Wien Penzing station and Wien Hütteldorf …"
    )
    assert all(text.startswith("Wegen ") for text in model)


_TWO_ROUTES = (
    "Wegen einer Oberleitungsstörung sind zwischen Wien Meidling Bahnhof (U) und Tullnerfeld "
    "Bahnhof bzw. Wien Hütteldorf Bahnhof (U) und Tullnerfeld Bahnhof"
)


@pytest.mark.parametrize(
    "tail",
    [
        " Ein- und Ausstiege nicht möglich.",
        " erneut keine Fahrten möglich.",
        " wieder nur eingeschränkt möglich.",
        " mit Regionalzügen keine Fahrten möglich.",
        " in beiden Richtungen bis voraussichtlich 13:30 Uhr nur eingeschränkt Fahrten möglich.",
        " vorübergehend keine Fahrten möglich.",
    ],
)
def test_a_place_that_is_no_station_list_keeps_its_order(tail: str) -> None:
    # Independent check 2026-11-13: these words would have moved behind the
    # colon, where the cut takes them.
    sentence = _TWO_ROUTES + tail
    assert len(sentence) > 180
    assert _oebb_what_does_not_run_first(sentence) == sentence


def test_a_stop_with_a_line_behind_the_article_keeps_its_name() -> None:
    from src.build_feed import _render_record_table

    rendered = _render_record_table(
        "Haltestelle: Schottenring, Zugang zur U4", source="Wiener Linien", category="Hinweis"
    )
    assert rendered == "Stop: Schottenring, Zugang zur U4"
