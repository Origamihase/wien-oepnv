"""Check of 2026-10-09: the feed from the fetch to the item, on real data.

Three findings, each pinned below with the real WL and ÖBB texts it was
found on (``cache/wl_9d709a/events.json`` and ``cache/oebb_c40d21/events.json``
of that day):

* WL: display texts of planned works without a cause joined a newer
  incident of their line and led it. "25: Verspätungen" (Grund: Schadhaftes
  Fahrzeug, 09:36) read the works' stops first, and when the delay ended
  the stops kept its GUID and stood as a new disruption until 20:30.
* ÖBB: the second sentence, what is still going on, was cut in 59 of 106
  German texts, because the first one repeated the stations of the title.
* EN: "Ersatzbus 26E hält …" read "holds"; ÖBB's templated sentence was
  split after "St."; the follow-up sentences reach the model now.

Mutations checked against this file (each one caught, by the test named):

* a causeless group joins an incident that began later → ``test_old_stops_follow_the_delay_and_are_no_member``.
* an item in the feed first takes a later item's GUID → ``test_stops_in_the_feed_first_keep_their_own_guid``.
* the courtesy sentence stays → ``test_the_second_oebb_sentence_fits_without_the_stations``.
* the place goes although the title does not name it → ``test_a_place_the_title_does_not_name_stays``.
* the "St." split stays → ``test_st_poelten_is_one_templated_sentence``.
* "hält an" at the end of a clause is glossed → ``test_haelt_an_at_the_end_of_a_clause_is_no_stop``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast
from zoneinfo import ZoneInfo

import pytest

import src.build_feed as bf
from src.feed.merge import deduplicate_fuzzy
from src.feed_types import FeedItem

pytestmark = pytest.mark.usefixtures("time_line_today")

_OEBB = "ÖBB"
_WL = "Wiener Linien"
_VIENNA = ZoneInfo("Europe/Vienna")


def _at(text: str) -> datetime:
    return datetime.fromisoformat(text).astimezone(UTC)


def _wl(title: str, description: str, guid: str, start: str, end: str) -> FeedItem:
    return {
        "source": _WL,
        "category": "Störung",
        "title": title,
        "description": description,
        "link": "https://www.wienerlinien.at/ogd_realtime",
        "guid": guid,
        "pubDate": _at(start),
        "starts_at": _at(start),
        "ends_at": _at(end),
        "_identity": f"wl|{guid}",
    }


def _built(items: list[FeedItem]) -> list[FeedItem]:
    """What ``main`` makes of *items*: read, deduplicated, fuzzy-merged, merged."""
    read = cast(list[FeedItem], bf._normalize_item_datetimes(bf._post_filter_wl(list(items))))
    fuzzy = deduplicate_fuzzy(cast(list[dict[str, Any]], bf._dedupe_items(read)))
    return bf._merge_wl_ticker_clusters(cast(list[FeedItem], fuzzy))


# ---------------- WL: a display text older than the incident ----------------

# WL re-issued them on 07.10. at 00:00; the feed state has them since 23.09.
BUS_STOP = _wl(
    "25: Ersatzbus 26E hält Karl-Waldbrunner-Platz vor Schloßhofer Straße",
    "Ersatzbus 26E\nhält Karl-Waldbrunner-Platz vor Schloßhofer Straße",
    "92b3e152", "2026-10-07T00:00:18+02:00", "2026-10-09T23:59:59+02:00",
)
TRAIN_STOP = _wl(
    "25: Züge halten Donaufelder Straße 175-177",
    "Züge halten\nDonaufelder Straße 175-177",
    "b1de3360", "2026-10-07T00:00:18+02:00", "2026-10-09T23:59:59+02:00",
)
DELAY = _wl(
    "25: Verspätungen",
    "Linie 25: Unregelmäßige Intervalle in beiden Richtungen. Grund: Schadhaftes Fahrzeug.",
    "1fada761", "2026-10-09T09:36:00+02:00", "2026-10-09T23:55:00+02:00",
)


def test_old_stops_follow_the_delay_and_are_no_member() -> None:
    (item,) = _built([BUS_STOP, TRAIN_STOP, DELAY])
    assert item["title"] == "25: Verspätungen"
    # Was: "Ersatzbus 26E hält …; Züge halten …. Unregelmäßige Intervalle …".
    assert item["description"] == (
        "Unregelmäßige Intervalle in beiden Richtungen. Grund: Schadhaftes Fahrzeug. "
        "Ersatzbus 26E hält Karl-Waldbrunner-Platz vor Schloßhofer Straße; "
        "Züge halten Donaufelder Straße 175-177."
    )
    assert item["guid"] == "1fada761"
    # Not members: when the delay ends, the stops keep their own GUID and begin.
    assert item.get("_members") == ["1fada761"]


def test_without_the_incident_the_stops_are_their_own_item() -> None:
    (item,) = _built([BUS_STOP, TRAIN_STOP])
    assert item["guid"] == "92b3e152"
    assert item["description"] == "Züge halten Donaufelder Straße 175-177."


def test_a_stop_ticker_of_the_incident_still_joins_it() -> None:
    # Four minutes ahead of the message is how WL's tickers come (65A, 06.10.).
    stop = _wl(
        "25: Züge halten Donaufelder Straße 175-177",
        "Züge halten\nDonaufelder Straße 175-177",
        "early", "2026-10-09T09:32:00+02:00", "2026-10-09T23:55:00+02:00",
    )
    (item,) = _built([stop, DELAY])
    assert item["title"] == "25: Verspätungen"
    assert sorted(cast(list[str], item.get("_members"))) == ["1fada761", "early"]


def _stops_state(stops_first_seen: str) -> dict[str, dict[str, Any]]:
    """The feed state of 09.10. 21:30: the stops noted under the delay's GUID since 12:01."""
    return {
        "1fada761": {
            "first_seen": "2026-10-09T07:36:00+00:00",
            "earliest_published": "2026-10-06T22:00:18+00:00",
            "members": ["92b3e152", "b1de3360"],
            "members_seen": "2026-10-09T19:30:46+00:00",
        },
        "92b3e152": {
            "first_seen": stops_first_seen,
            "members": ["92b3e152", "b1de3360"],
            "members_seen": "2026-10-09T07:30:56+00:00",
        },
    }


def test_stops_in_the_feed_first_keep_their_own_guid() -> None:
    # Live at 22:00: the stops took the delay's GUID again, place 2, "[Heute]".
    (item,) = _built([BUS_STOP, TRAIN_STOP])
    state = _stops_state("2026-09-22T22:00:28+00:00")
    (carried,) = bf._carry_item_identity([item], state, _at("2026-10-09T22:00:57+02:00"))
    assert carried["guid"] == "92b3e152"


def test_a_message_of_the_incident_still_carries_its_guid() -> None:
    # "43: Verkehrsunfall": what is left of an incident keeps its GUID and begin.
    (item,) = _built([BUS_STOP, TRAIN_STOP])
    state = _stops_state("2026-10-09T07:36:00+00:00")
    (carried,) = bf._carry_item_identity([item], state, _at("2026-10-09T22:00:57+02:00"))
    assert carried["guid"] == "1fada761"


# ---------------- ÖBB: the second sentence ----------------

LEOPOLDAU: FeedItem = {
    "source": _OEBB,
    "category": "Störung",
    "title": "Wien Leopoldau ↔ Wien Süßenbrunn",
    "description": (
        "09.10.2026<br/><br/>Wegen einer Stellwerkstörung am Bahnhof <b>waren</b> zwischen "
        "Wien Leopoldau Bahnhst (U) und Wien Süßenbrunn Bahnhst <b>bis </b><b>20:18 Uhr</b> "
        "keine Fahrten möglich. Planen Sie derzeit noch bis zu<b> 10 Minuten </b>mehr "
        "Reisezeit ein.<br>Wir bitten um Entschuldigung."
    ),
    "link": "https://fahrplan.oebb.at/bin/help.exe/dn?L=vs_scotty&tpl=showmap_external&",
    "guid": "https://fahrplan.oebb.at/bin/query.exe/dn?ujm=1&mapType=TRACKINFO&918244",
}


def test_the_second_oebb_sentence_fits_without_the_stations() -> None:
    formatted = bf._format_item_content(
        LEOPOLDAU, "918244",
        datetime(2026, 10, 9, 0, 0, tzinfo=_VIENNA), datetime(2026, 10, 9, 23, 59, 59, tzinfo=_VIENNA),
    )
    # Was: "Wegen … waren zwischen Wien Leopoldau Bahnhst (U) und Wien Süßenbrunn
    # Bahnhst bis 20:18 Uhr keine Fahrten möglich." and nothing about now.
    assert formatted.desc_text_truncated.startswith(
        "Wegen einer Stellwerkstörung am Bahnhof waren bis 20:18 Uhr keine Fahrten möglich. "
        "Planen Sie derzeit noch bis zu 10 Minuten mehr Reisezeit ein."
    )
    assert "Entschuldigung" not in formatted.desc_text_truncated


@pytest.mark.parametrize(
    ("sentence", "title", "shorter"),
    [
        (
            "Wegen einer Weichenstörung sind in Wien Hbf (U) derzeit keine Fahrten möglich.",
            "Wien Hauptbahnhof",
            "Wegen einer Weichenstörung sind derzeit keine Fahrten möglich.",
        ),
        (
            "Wegen eines Schadens am Gleis waren zwischen Wien Meidling Bahnhof (U) und "
            "Wien Liesing Bahnhof bis 20:37 Uhr nur eingeschränkt Fahrten möglich.",
            "S 1: Wien Meidling ↔ Wien Liesing",
            "Wegen eines Schadens am Gleis waren bis 20:37 Uhr nur eingeschränkt Fahrten möglich.",
        ),
    ],
)
def test_the_place_the_title_names_leaves(sentence: str, title: str, shorter: str) -> None:
    assert bf._oebb_sentence_without_place(sentence, title) == shorter


@pytest.mark.parametrize(
    ("sentence", "title"),
    [
        # only one of the two stations is in the title
        (
            "Wegen einer Stellwerkstörung sind zwischen Wien Floridsdorf Bahnhof (U) und "
            "Wien Leopoldau Bahnhst (U) derzeit keine Fahrten möglich.",
            "Wien Floridsdorf ↔ Gänserndorf",
        ),
        # "Wien" alone names no station
        ("Wegen einer Weichenstörung sind in Wien Hbf (U) derzeit keine Fahrten möglich.", "Wien"),
        # no templated sentence
        ("Die Fernverkehrszüge werden umgeleitet.", "Wien Hauptbahnhof"),
    ],
)
def test_a_place_the_title_does_not_name_stays(sentence: str, title: str) -> None:
    assert bf._oebb_sentence_without_place(sentence, title) is None


# ---------------- EN ----------------


@pytest.fixture
def cause_stub(monkeypatch: Any) -> list[str]:
    """The model as far as ÖBB templates need it: only the cause reaches it."""
    seen: list[str] = []

    def model(text: str, **_kwargs: Any) -> list[dict[str, str]]:
        seen.append(text)
        if text.startswith("Wegen einer "):
            return [{"translation_text": "Due to a " + text[len("Wegen einer "):]}]
        return [{"translation_text": text}]

    monkeypatch.setattr(bf, "_get_translation_pipeline", lambda: model)
    return seen


def test_the_shortened_oebb_sentence_renders_without_a_place(cause_stub: list[str]) -> None:
    out = bf._translate_text_attempt(
        "Wegen einer Stellwerkstörung am Bahnhof waren bis 20:18 Uhr keine Fahrten möglich. "
        "Planen Sie derzeit noch bis zu 10 Minuten mehr Reisezeit ein.",
        source=_OEBB,
    )
    assert out == (
        "Due to an interlocking failure at the station, no trains could run until 20:18. "
        "For now, allow up to 10 minutes of extra travel time."
    )
    # Only the cause reaches the model (the glossary masks it).
    assert len(cause_stub) == 1 and cause_stub[0].startswith("Wegen einer ")


@pytest.mark.parametrize(
    ("german", "english"),
    [
        ("Planen Sie derzeit noch bis zu 10 Minuten mehr Reisezeit ein.",
         "For now, allow up to 10 minutes of extra travel time."),
        ("Planen Sie bis zu 15 Minuten mehr Reisezeit ein.", "Allow up to 15 minutes of extra travel time."),
        ("Planen Sie in diesem Bereich bis zu 5 Minuten mehr Reisezeit ein.",
         "Allow up to 5 minutes of extra travel time in this area."),
        ("Ihre Reisezeit verlängert sich um bis zu 20 Minuten.", "Your journey takes up to 20 minutes longer."),
        ("Es kommt noch zu vereinzelten Zugausfällen und Verzögerungen.",
         "There are still occasional train cancellations and delays."),
        ("Es kommt zu vereinzelten Zugausfällen und Verspätungen bis zu 10 Minuten.",
         "There are occasional train cancellations and delays of up to 10 minutes."),
        ("Über die Dauer der Unterbrechung kann derzeit noch keine Angabe gemacht werden.",
         "It is not yet known how long the interruption will last."),
        ("Die Züge warten die Sperre vorerst ab.", "For now, trains are waiting until the line reopens."),
        ("Fahrgäste mit ÖBB-Tickets können in diesem Bereich die Wiener Linien benutzen.",
         "Passengers with ÖBB tickets can use Wiener Linien in this area."),
    ],
)
def test_oebb_follow_up_sentences_need_no_model(german: str, english: str) -> None:
    assert bf._render_oebb_follow_up(german) == english


def test_another_follow_up_takes_the_model(cause_stub: list[str]) -> None:
    assert bf._render_oebb_follow_up("Fernverkehrszüge und CJX Züge werden umgeleitet.") == ""


def test_st_poelten_is_one_templated_sentence(cause_stub: list[str]) -> None:
    out = bf._translate_text_attempt(
        "Wegen einer Weichenstörung sind in St. Pölten Hbf derzeit keine Fahrten möglich.",
        source=_OEBB,
    )
    # Was: "in St." and "Pölten Hbf …" through the model as two sentences.
    assert out == "Due to a switch fault, no trains can run at St. Pölten Hbf at present."
    assert len(cause_stub) == 1 and cause_stub[0].startswith("Wegen einer ")


def _rendered(text: str) -> str:
    glossed, mapping = bf._apply_domain_glossary(
        bf._normalise_for_translation(text), source=_WL, category="Störung"
    )
    return bf._unmask_entities(glossed, mapping)


@pytest.mark.parametrize(
    ("german", "english"),
    [
        # 09.10.2026: "replacement bus 26E holds Karl-Waldbrunner-Platz ahead of …"
        ("Ersatzbus 26E hält Karl-Waldbrunner-Platz vor Schloßhofer Straße",
         "replacement bus 26E stops at Karl-Waldbrunner-Platz vor Schloßhofer Straße"),
        ("Ersatzbus 41E hält bei Währinger Str 200", "replacement bus 41E stops at Währinger Str 200"),
        ("Ersatzbus 26E hält bei Linie 25", "replacement bus 26E stops at the stop of line 25"),
        ("Linie 26E hält Donaufelder Straße 148", "Linie 26E stops at Donaufelder Straße 148"),
        ("Ersatzverkehr hält Kagran", "replacement service stops at Kagran"),
    ],
)
def test_a_stop_with_its_line_is_rendered_with_its_preposition(german: str, english: str) -> None:
    assert _rendered(german) == english


def test_haelt_an_at_the_end_of_a_clause_is_no_stop() -> None:
    assert _rendered("Die Störung hält an.") == "Die Störung dauert an."


def test_translation_cache_epoch_was_bumped() -> None:
    assert bf._TRANSLATION_CACHE_EPOCH >= 25
