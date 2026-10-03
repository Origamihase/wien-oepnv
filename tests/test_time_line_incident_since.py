"""A disruption's time line says since when it runs (operator wish 2026-10-03).

Examples are the German feed of 03.10.2026, 11:20 Vienna time: "86A/87A/95A:
Fahrtbehinderung wegen Rettungseinsatz" read "[Heute]", although the reader
wants to see how old the incident is ("Störung bitte mit Zeitangabe").
Planned measures keep "[Heute]".
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

import pytest
from zoneinfo import ZoneInfo

import src.build_feed as bf
from src.feed_types import FeedItem

VIENNA = ZoneInfo("Europe/Vienna")
NOW = datetime(2026, 10, 3, 11, 20, tzinfo=VIENNA)
NNBSP = " "


def _at(day: int, hour: int = 0, minute: int = 0, second: int = 0) -> datetime:
    return datetime(2026, 10, day, hour, minute, second, tzinfo=VIENNA)


def _wl(title: str, pub: datetime | None, start: datetime | None, **extra: Any) -> dict[str, Any]:
    item: dict[str, Any] = {
        "source": "Wiener Linien",
        "category": "Störung",
        "title": title,
        "description": "Nach einer Fahrtbehinderung kommt es zu unterschiedlichen Intervallen.",
        "pubDate": pub,
        "starts_at": start,
        "ends_at": _at(3, 23, 55),
    }
    item.update(extra)
    return item


def _line(item: dict[str, Any], now: datetime = NOW) -> str:
    start, end = item.get("starts_at"), item.get("ends_at")
    since = bf._incident_since(cast(FeedItem, item), start)
    return bf.format_local_times(start, end, now, since=since).replace(NNBSP, " ")


def test_the_earliest_message_of_the_incident_is_its_begin() -> None:
    # 87A on 03.10.: WL entered the incident at 10:37, the line's display
    # ticker followed at 10:54:13 and is the item's starts_at.
    item = _wl("87A: Fahrtbehinderung wegen Rettungseinsatz", _at(3, 10, 37), _at(3, 10, 54, 13))
    assert _line(item) == "Seit 10:37"


def test_a_single_message_begins_at_its_start() -> None:
    item = _wl("9: Fahrtbehinderung Falschparker", _at(3, 10, 42), _at(3, 10, 42))
    assert _line(item) == "Seit 10:42"


def test_an_open_end_also_says_since_when() -> None:
    item = _wl("U3: Stellwerksstörung", _at(3, 5, 17), _at(3, 5, 17), ends_at=None)
    assert _line(item) == "Seit 05:17"


@pytest.mark.parametrize(
    "title",
    [
        "12: Fahrtbehinderung Veranstaltung",
        "47B: Laufveranstaltung",
        "44: Demonstration",
        "1A: Staatsbesuch",
        "D: Fahrtbehinderung Gleisbauarbeiten",
        "U2: Arbeiten am Stellwerk",
        "N29: Polizeiübung",
    ],
)
def test_a_planned_measure_keeps_today(title: str) -> None:
    assert _line(_wl(title, _at(3, 9, 38), _at(3, 9, 38))) == "Heute"


def test_a_planned_cause_in_the_description_counts_too() -> None:
    item = _wl(
        "71: Betrieb ab Schwarzenbergplatz",
        _at(3, 9, 38),
        _at(3, 9, 38),
        description="Wegen einer Demonstration im Bereich Schwarzenbergplatz und Ring.",
    )
    assert _line(item) == "Heute"


def test_a_measure_switched_on_at_the_full_hour_keeps_today() -> None:
    # "12: Betrieb ab Franz-Josefs-Bahnhof" 13.07. 04:00:16: WL switches
    # pre-entered measures on at the full hour; no cause names the works.
    item = _wl("12: Betrieb ab Franz-Josefs-Bahnhof", _at(3, 4, 0, 16), _at(3, 4, 0, 16))
    assert _line(item) == "Heute"


def test_a_stale_full_hour_message_does_not_date_the_incident_back() -> None:
    # A reused ticker brought 00:00:25 into the item; the incident began 10:27.
    item = _wl("10A: Fahrtbehinderung Fremder Verkehrsunfall", _at(3, 0, 0, 25), _at(3, 10, 27, 20))
    assert _line(item) == "Seit 10:27"


def test_a_message_from_an_earlier_day_does_not_date_the_incident_back() -> None:
    item = _wl("10A: Fahrtbehinderung Fremder Verkehrsunfall", _at(1, 17, 3), _at(3, 10, 27, 20))
    assert _line(item) == "Seit 10:27"


def test_an_incident_from_an_earlier_day_keeps_its_line() -> None:
    item = _wl("U6: Stellwerksstörung", _at(2, 22, 10), _at(2, 22, 10))
    assert _line(item) == "Heute"
    assert _line(dict(item, ends_at=None)) == "Seit 02.10."


def test_an_incident_lasting_beyond_today_keeps_its_end() -> None:
    item = _wl("1: Verunreinigung", _at(3, 9, 28), _at(3, 9, 28), ends_at=_at(4, 6))
    assert _line(item) == "Bis So 04.10."


def test_a_begin_still_ahead_is_not_since() -> None:
    item = _wl("9: Fahrtbehinderung Falschparker", _at(3, 11, 40), _at(3, 11, 40))
    assert _line(item) == "Heute"


@pytest.mark.parametrize(
    ("source", "category"),
    [("Wiener Linien", "Hinweis"), ("Stadt Wien – Baustellen", "Baustelle")],
)
def test_only_disruptions_say_since_when(source: str, category: str) -> None:
    item = _wl("Verkehrsunfall", _at(3, 9, 28), _at(3, 9, 28), source=source, category=category)
    assert _line(item) == "Heute"


def _oebb(description: str, start: datetime, title: str = "Wien Meidling ↔ Wien Liesing") -> dict[str, Any]:
    return {
        "source": "ÖBB",
        "category": "Störung",
        "title": title,
        "description": description,
        "pubDate": start,
        "starts_at": start,
        "ends_at": None,
    }


_OEBB_RESCUE = (
    "03.10.2026<br/><br/>Wegen eines Rettungseinsatzes sind zwischen <b>Wien Meidling "
    "Bahnhof (U)</b> und <b>Wien Liesing Bahnhof</b> derzeit keine Fahrten möglich."
)


def test_an_oebb_disruption_says_since_when() -> None:
    item = _oebb(_OEBB_RESCUE, _at(3, 10, 59, 12))
    assert _line(item) == "Seit 10:59"


def _oebb_as_fetched(pub: datetime) -> dict[str, Any]:
    # Since 2026-09-12 the provider takes start and end from the period ÖBB
    # puts in front of the text ("03.10.2026"): 00:00 to 23:59:59.
    item = _oebb(_OEBB_RESCUE, _at(3, 0))
    item.update(pubDate=pub, ends_at=_at(3, 23, 59, 59))
    return item


def _oebb_line(item: dict[str, Any], entry: dict[str, Any] | None, now: datetime = NOW) -> str:
    since = bf._incident_since(
        cast(FeedItem, item), item["starts_at"], bf._first_published(cast(FeedItem, item), entry)
    )
    return bf.format_local_times(item["starts_at"], item["ends_at"], now, since=since).replace(NNBSP, " ")


def test_an_oebb_disruption_dated_by_its_period_says_since_when() -> None:
    # The provider's start is the date (00:00); the clock is the publication.
    # Before the fix every ÖBB disruption since 12.09.2026 read "Heute".
    assert _oebb_line(_oebb_as_fetched(_at(3, 10, 59, 12)), None) == "Seit 10:59"


def test_an_oebb_update_keeps_the_first_publication() -> None:
    # "Wien Floridsdorf ↔ Wien Praterstern" (27.09.2026): ÖBB re-published
    # the message at 15:48, 17:26 and 20:07 under one GUID.
    entry: dict[str, Any] = {"first_seen": _at(3, 9, 0, 30).isoformat()}
    first = _oebb_as_fetched(_at(3, 8, 48, 11))
    assert _oebb_line(first, entry, _at(3, 9, 0, 30)) == "Seit 08:48"
    update = _oebb_as_fetched(_at(3, 10, 26, 29))
    assert _oebb_line(update, entry) == "Seit 08:48"
    assert entry["first_published"] == _at(3, 8, 48, 11).astimezone(UTC).isoformat()


def test_an_oebb_entry_from_before_the_field_starts_at_its_first_sight() -> None:
    # The build first saw the message at 09:31; it now carries an update.
    entry: dict[str, Any] = {"first_seen": _at(3, 9, 31, 4).isoformat()}
    assert _oebb_line(_oebb_as_fetched(_at(3, 10, 26, 29)), entry) == "Seit 09:31"


def test_an_oebb_message_from_an_earlier_day_keeps_its_line() -> None:
    entry: dict[str, Any] = {"first_published": _at(2, 22, 10).isoformat()}
    assert _oebb_line(_oebb_as_fetched(_at(3, 10, 26, 29)), entry) == "Heute"


def test_other_sources_keep_no_first_publication() -> None:
    entry: dict[str, Any] = {"first_seen": _at(3, 9).isoformat()}
    item = _wl("87A: Rettungseinsatz", _at(3, 10, 37), _at(3, 10, 54, 13))
    assert bf._first_published(cast(FeedItem, item), entry) is None
    assert "first_published" not in entry


def test_the_rendered_oebb_item_keeps_its_first_publication(monkeypatch: pytest.MonkeyPatch) -> None:
    from functools import partial

    monkeypatch.setattr(bf, "format_local_times", partial(bf.format_local_times, now=NOW))
    state: dict[str, dict[str, Any]] = {"oebb": {"first_seen": _at(3, 9, 0, 30).isoformat()}}
    for pub in (_at(3, 8, 48, 11), _at(3, 10, 26, 29)):
        item = _oebb_as_fetched(pub)
        content = bf._format_item_content(
            cast(FeedItem, item), "oebb", item["starts_at"], item["ends_at"], state=state
        )
        assert f"[Seit{NNBSP}08:48]" in content.desc_html


@pytest.mark.parametrize(
    "description",
    [
        (
            "Wegen Reparaturarbeiten nach einem Unfall sind zwischen Wien Hbf (U) und "
            "Gramatneusiedl Bahnhof Zugfahrten nur eingeschränkt möglich."
        ),
        (
            "Der Treppenabgang Webgasse ist gesperrt. Grund dafür sind dringende "
            "Reperaturarbeiten an den Treppen."
        ),
    ],
)
def test_repairs_after_an_incident_are_not_planned(description: str) -> None:
    item = _wl("U3: Betriebsstörung", _at(3, 10, 57), _at(3, 10, 57), description=description)
    assert _line(item) == "Seit 10:57"


@pytest.mark.parametrize(
    "description",
    [
        # Published 19:57 about a closure that ended 19:55 (04.07.2026).
        (
            "04.07.2026<br/><br/>Wegen eines Polizeieinsatzes <b>waren</b> in Mödling Bahnhof "
            "<b>bis </b><b>19:55 Uhr</b> keine Fahrten möglich."
        ),
        # The abbreviation's period does not end the search.
        "Wegen eines Polizeieinsatzes waren in Wr.Neustadt Hbf bis 21:18 Uhr keine Fahrten möglich.",
    ],
)
def test_an_oebb_report_of_a_past_disruption_keeps_its_line(description: str) -> None:
    assert _line(_oebb(description, _at(3, 9, 57, 38))) == "Seit heute"


def test_an_all_clear_keeps_its_line() -> None:
    item = _oebb(
        "Wegen eines Polizeieinsatzes sind wieder alle Fahrten möglich.",
        _at(3, 9, 29, 4),
        title="Aufhebung Verkehrseinschränkung: Flughafen Wien",
    )
    assert _line(item) == "Seit heute"


def test_oebb_works_keep_their_line() -> None:
    item = _oebb(
        "03.10.2026 - 05.10.2026<br/><br/>Wegen Bauarbeiten können zwischen Wien Hbf (U) "
        "und Gramatneusiedl keine Züge fahren.",
        _at(3, 0),
    )
    item["ends_at"] = _at(5, 23, 59, 59)
    assert _line(item) == "Bis Mo 05.10."
    # A date-only start (00:00) is no clock time, and neither is a
    # publication on the full hour.
    item["description"] = "Wegen eines Schadens am Gleis sind Zugfahrten eingeschränkt."
    item["ends_at"] = _at(3, 23, 59, 59)
    assert _line(item) == "Heute"


def test_the_stammstrecke_episode_says_since_when_even_at_the_full_hour() -> None:
    # Its start is the first delayed departure measured, 16:00 is no schedule.
    from src.feed import stammstrecke

    assert stammstrecke.EVENT_SOURCE in bf._MEASURED_SOURCES
    item = {
        "source": stammstrecke.EVENT_SOURCE,
        "category": stammstrecke.EVENT_CATEGORY,
        "title": stammstrecke.EVENT_TITLE,
        "description": "Durchschnittliche Verspätung von 12 min in Richtung Meidling",
        "pubDate": _at(3, 11, 0, 55),
        "starts_at": _at(3, 10, 0),
        "ends_at": None,
    }
    assert _line(item) == "Seit 10:00"


def test_the_clock_is_vienna_time() -> None:
    # 08:37 UTC is 10:37 in Vienna (CEST).
    pub = datetime(2026, 10, 3, 8, 37, tzinfo=UTC)
    item = _wl("86A: Fahrtbehinderung wegen Rettungseinsatz", pub, _at(3, 10, 42, 44))
    assert _line(item) == "Seit 10:37"


def test_the_english_line_reads_since() -> None:
    assert bf._translate_time_line_en(f"[Seit{NNBSP}10:37]") == f"[Since{NNBSP}10:37]"


def test_the_rendered_item_carries_the_line(monkeypatch: pytest.MonkeyPatch) -> None:
    # The whole path of the German feed: _format_item_content builds the line.
    from functools import partial

    monkeypatch.setattr(bf, "format_local_times", partial(bf.format_local_times, now=NOW))
    item = _wl("87A: Fahrtbehinderung wegen Rettungseinsatz", _at(3, 10, 37), _at(3, 10, 54, 13))
    content = bf._format_item_content(cast(FeedItem, item), "ident", item["starts_at"], item["ends_at"])
    assert f"[Seit{NNBSP}10:37]" in content.desc_html
