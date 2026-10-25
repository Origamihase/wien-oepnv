"""ÖBB's "Wegen … kann … der S1-Zug 23626 nicht fahren." on the TV and in English.

Jules audit 2026-10-25 (``docs/archive/audits/audit-2026-10-25.md``), the
feed of commit 10ec488:

* DE: ÖBB says what does not run at the end of the sentence, behind the
  routes and days. The 180-character cut took it: "… an einzelnen Tagen der
  S1-Zug … [Bis 28.11.]" (45 ÖBB notices since December 2025 ended like
  that). A sentence over the limit now names the trains and the verb first
  (``_oebb_what_does_not_run_first``); one that fits keeps ÖBB's order.
* EN: the model lost the verb behind the routes: "… the S2 train can't
  drive 23360." and "Construction works allows between … of the S1 train
  …". The sentence is rendered from its parts (``_oebb_cannot_run_parts``).

Mutations checked against this file (each one caught, by the test named):

* the trains are not moved → ``test_the_s1_keeps_what_does_not_run``.
* a sentence that fits is moved too → ``test_a_sentence_that_fits_keeps_its_order``.
* a sentence with two subjects is moved → ``test_two_subjects_keep_their_order``.
* the train list is not counted → ``test_a_long_train_list_reads_as_its_count``.
* "an einzelnen Tagen" stays behind the routes → ``test_the_s1_keeps_what_does_not_run``.
* "keine … fahren" reads "cannot run" → ``test_no_trains_can_run``.
* "bzw." between two stations reads "and" → ``test_alternative_stations_read_or``.
* the cached S2 English is served on → ``test_the_cached_s2_english_is_translated_again``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

import pytest

from src import build_feed as bf
from src.feed_types import FeedItem

_OEBB = "ÖBB"

# The cache's HTML of 25.10.2026 (TRACKINFO 907513 and 907728), up to the
# second sentence.
_S1_HTML = (
    "24.10.2026 - 28.11.2026<br/><br/>Wegen Bauarbeiten kann<br>zwischen <b>Deutsch "
    "Wagram Bahnhof</b> und <b>Gänserndorf Bahnhof </b><br>von <b>2</b><b>4.10.2026 "
    "</b>bis <span><b>26.10.2026<br></b>von <b>31.10.2026</b> bis <b>01.11.2026<br>"
    "</b>von <b>14.11.2026</b> bis <b>15.11.2026<br></b>von <b>21.11.2026</b> bis "
    "<b>22.11.2026</b><b><br></b>am <b>28.11.2026 </b>und<b><br></b>zwischen <b>Wien "
    "Leopoldau Bahnhst </b>und<b> Deutsch Wagram Bahnhof <br></b>am <b>25.10.2026</b> "
    "und <b>01.11.2026</b></span><b><br></b>der S1-Zug 23626 nicht fahren.<br>Ein "
    "Schienenersatzverkehr mit Autobussen wird für Sie eingerichtet."
)
_S2_SENTENCE = (
    "Wegen Bauarbeiten kann zwischen Wien Leopoldau Bahnhst (U) und Mistelbach/Zaya "
    "Bahnhof am 25.10.2026 und 01.11.2026 der S2-Zug 23360 nicht fahren."
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
        starts_at=datetime(2026, 10, 24, 0, 0, tzinfo=UTC),
        ends_at=datetime(2026, 11, 28, 22, 0, tzinfo=UTC),
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

    monkeypatch.setattr(bf, "_get_translation_pipeline", lambda: pipe)
    return seen


def test_the_s1_keeps_what_does_not_run() -> None:
    shown = _render("S 1: Wien Leopoldau ↔ Deutsch Wagram", _S1_HTML)
    assert shown.startswith(
        "Wegen Bauarbeiten kann der S1-Zug 23626 an einzelnen Tagen nicht fahren: zwischen "
        "Deutsch Wagram Bahnhof und Gänserndorf Bahnhof und zwischen "
    )
    assert " … [Bis" in shown


def test_a_sentence_that_fits_keeps_its_order() -> None:
    assert bf._oebb_what_does_not_run_first(_S2_SENTENCE) == _S2_SENTENCE


def test_no_trains_first_when_too_long() -> None:
    sentence = (
        "Wegen Bauarbeiten können zwischen Wien Hbf (U) und Gramatneusiedl Bahnhof von "
        "31.10.2026 bis 30.11.2026 (jeweils von Samstag 23:15 Uhr bis Sonntag 04:40 Uhr "
        "und von Sonntag 23:15 Uhr bis Montag 04:40 Uhr) keine Nahverkehrszüge fahren."
    )
    assert bf._oebb_what_does_not_run_first(sentence).startswith(
        "Wegen Bauarbeiten können keine Nahverkehrszüge fahren: zwischen Wien Hbf (U) und "
        "Gramatneusiedl Bahnhof von 31.10.2026 bis 30.11.2026 (jeweils"
    )


def test_two_subjects_keep_their_order() -> None:
    sentence = (
        "Wegen Bauarbeiten können zwischen Wien Hbf (U) und Gramatneusiedl Bahnhof von "
        "03.10.2026 bis 04.10.2026 der Zug REX64(2679) von 03. auf 04.10.2026 bis 04. auf "
        "05.10.2026 die Züge REX6(7777), S60(26085), S60(26086) und REX6(7776) nicht fahren."
    )
    assert bf._oebb_what_does_not_run_first(sentence) == sentence
    rerouted = (
        "Wegen Bauarbeiten werden zwischen Wien Hbf (U) und Deutsch Wagram Bahnhof von "
        "28.03.2026 bis 02.05.2026 einige REX 1-Züge umgeleitet und können in Wien Quartier "
        "Belvedere, Wien Rennweg, Wien Mitte-Landstraße und Wien Praterstern nicht fahren."
    )
    assert bf._oebb_what_does_not_run_first(rerouted) == rerouted


def test_a_long_train_list_reads_as_its_count() -> None:
    sentence = (
        "Wegen Bauarbeiten können zwischen Wien Westbahnhof (U) und Wien Hütteldorf "
        "Bahnhof (U) von 03.06.2026 (23:00 Uhr) bis 08.06.2026 (04:00 Uhr) die Züge IC "
        "1110, IC 1113, IC 1115, IC 1118, IC 1119, IC 1142, IC 1143 und D 1141 nicht fahren."
    )
    assert bf._oebb_what_does_not_run_first(sentence) == (
        "Wegen Bauarbeiten können 8 Züge nicht fahren: zwischen Wien Westbahnhof (U) und "
        "Wien Hütteldorf Bahnhof (U) von 03.06.2026 (23:00 Uhr) bis 08.06.2026 (04:00 Uhr)."
    )


def test_the_s2_reads_cannot_run(model: list[str]) -> None:
    out = bf._translate_text_attempt(_S2_SENTENCE, "t", source=_OEBB)
    assert out == (
        "Due to construction works, the S2 train 23360 cannot run between Wien Leopoldau "
        "station (U) and Mistelbach/Zaya station on 25.10.2026 and 01.11.2026."
    )
    # Only the cause reached the model.
    assert len(model) == 1 and model[0].startswith("Wegen ")


def test_the_cut_s1_reads_cannot_run(model: list[str]) -> None:
    sentence = (
        "Wegen Bauarbeiten kann der S1-Zug 23626 an einzelnen Tagen nicht fahren: zwischen "
        "Deutsch Wagram Bahnhof und Gänserndorf Bahnhof und zwischen Wien Leopoldau Bahnhst …"
    )
    out = bf._translate_text_attempt(sentence, "t", source=_OEBB)
    assert out == (
        "Due to construction works, the S1 train 23626 cannot run on certain days between "
        "Deutsch Wagram station and Gänserndorf station and between Wien Leopoldau station …"
    )


def test_no_trains_can_run(model: list[str]) -> None:
    sentence = (
        "Wegen Bauarbeiten können zwischen Flughafen Wien Bahnhof und Wolfsthal Bahnhof an "
        "einzelnen Tagen (jeweils 08:45 Uhr - 14:45 Uhr) keine REX 7 Züge fahren."
    )
    out = bf._translate_text_attempt(sentence, "t", source=_OEBB)
    assert out == (
        "Due to construction works, no REX 7 trains can run between Flughafen Wien station "
        "and Wolfsthal station on certain days (each day 08:45 - 14:45)."
    )


def test_alternative_stations_read_or(model: list[str]) -> None:
    sentence = (
        "Wegen Bauarbeiten können zwischen Wien Hbf (U) bzw. Wien Meidling Bahnhof (U) und "
        "Wien Hütteldorf (U) von 04.07.2026 (ca. 02:00 Uhr) bis voraussichtlich Ende 2027 "
        "keine Züge fahren."
    )
    out = bf._translate_text_attempt(sentence, "t", source=_OEBB)
    assert out == (
        "Due to construction works, no trains can run between Wien Hbf (U) or Wien Meidling "
        "station (U) and Wien Hütteldorf (U) from 04.07.2026 (approx. 02:00) until probably "
        "the end of 2027."
    )


def test_unknown_days_go_to_the_model_behind_the_colon(model: list[str]) -> None:
    sentence = (
        "Wegen Bauarbeiten kann der Zug RJ 650 nicht fahren: von Bruck/Mur Bahnhof nach "
        "Graz Hbf in der Nacht von 14.02. auf 15.02.2026 …"
    )
    out = bf._translate_text_attempt(sentence, "t", source=_OEBB)
    assert out is not None
    assert out.startswith("Due to construction works, train RJ 650 cannot run: von Bruck/Mur")
    # The cause, then the routes and days alone.
    assert len(model) == 2 and model[1].startswith("von Bruck/Mur Bahnhof nach ")


def test_other_sentences_keep_their_path() -> None:
    labelled = (
        "Von 18.10.2026 bis 19.10.2026 kann der Zug RJ 263 zwischen Wien Hütteldorf "
        "Bahnhof (U) und Wien Hbf (U) nicht fahren."
    )
    assert bf._oebb_cannot_run_parts(labelled) is None
    assert bf._oebb_cannot_run_parts("Wegen Bauarbeiten können keine Züge halten.") is None


def test_the_cached_s2_english_is_translated_again() -> None:
    cached = (
        "Construction works means that between Wien Leopoldau station (U) and "
        "Mistelbach/Zaya station at 25.10.2026 and 01.11.2026 the S2 train can't drive 23360."
    )
    assert bf._oebb_cannot_run_defect(_S2_SENTENCE, cached) == "trains sentence"
    fixed = (
        "Due to construction works, the S2 train 23360 cannot run between Wien Leopoldau "
        "station (U) and Mistelbach/Zaya station on 25.10.2026 and 01.11.2026."
    )
    assert bf._oebb_cannot_run_defect(_S2_SENTENCE, fixed) is None
