from datetime import datetime, timedelta, UTC
from typing import Any

import pytest

from src.providers import wl_fetch


def _make_event(title: str, lines: list[str]) -> dict[str, Any]:
    now = datetime.now(UTC)
    start = (now - timedelta(hours=1)).isoformat()
    end = (now + timedelta(hours=1)).isoformat()
    return {
        "title": title,
        "description": "",
        "time": {"start": start, "end": end},
        "relatedLines": lines,
        "relatedStops": [],
        "attributes": {},
    }


def _run(
    monkeypatch: pytest.MonkeyPatch,
    traffic: list[dict[str, Any]],
    news: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    monkeypatch.setattr(
        wl_fetch, "_fetch_traffic_infos", lambda timeout=20, session=None: traffic
    )
    monkeypatch.setattr(
        wl_fetch, "_fetch_news", lambda timeout=20, session=None: news or []
    )
    return wl_fetch.fetch_events()


def test_aggregate_removed_when_singles_say_the_same(monkeypatch: pytest.MonkeyPatch) -> None:
    aggregate = _make_event("Verkehrsunfall", ["U1", "U2"])
    single1 = _make_event("Verkehrsunfall Betrieb ab Karlsplatz", ["U1"])
    single2 = _make_event("Verkehrsunfall Betrieb ab Schottenring", ["U2"])

    titles = [it["title"] for it in _run(monkeypatch, [aggregate, single1, single2])]

    assert "U1: Verkehrsunfall Betrieb ab Karlsplatz" in titles
    assert "U2: Verkehrsunfall Betrieb ab Schottenring" in titles
    assert "U1/U2: Verkehrsunfall" not in titles


def test_aggregate_kept_when_singles_tell_something_else(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression 2026-10-03: E fragte nur nach Linien, nie nach dem Inhalt.

    „1: Rettungseinsatz“ und „2: Falschparker“ sind keine Teilmeldungen einer
    Demonstration auf denselben Linien; die Demonstration bleibt.
    """
    aggregate = _make_event("Demonstration", ["U1", "U2"])
    single1 = _make_event("Rettungseinsatz", ["U1"])
    single2 = _make_event("Falschparker", ["U2"])

    titles = [it["title"] for it in _run(monkeypatch, [aggregate, single1, single2])]

    assert sorted(titles) == ["U1/U2: Demonstration", "U1: Rettungseinsatz", "U2: Falschparker"]


def test_subset_removed_when_aggregate_says_the_same(monkeypatch: pytest.MonkeyPatch) -> None:
    aggregate = _make_event("Verkehrsunfall", ["U1", "U2"])
    single1 = _make_event("Verkehrsunfall", ["U1"])

    items = _run(monkeypatch, [aggregate, single1])

    assert [it["title"] for it in items] == ["U1/U2: Verkehrsunfall"]


def test_subset_kept_when_it_says_something_else(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression 2026-10-03: „2A: Bauarbeiten Renngasse“ verschwand zehn Tage
    lang, solange die Regenbogenparade auf 2A angekündigt war."""
    parade = _make_event("Regenbogenparade 2026", ["1", "2", "2A"])
    works = _make_event("Bauarbeiten Renngasse", ["2A"])

    titles = sorted(it["title"] for it in _run(monkeypatch, [parade, works]))

    # Der Titel wird gekürzt („Bauarbeiten“ wandert in die Kategorie).
    assert titles == ["1/2/2A: Regenbogenparade 2026", "2A: Renngasse"]


def _make_news(title: str, lines: list[str]) -> dict[str, Any]:
    now = datetime.now(UTC)
    start = (now - timedelta(hours=1)).isoformat()
    end = (now + timedelta(hours=1)).isoformat()
    return {
        "title": title,
        "description": "",
        "time": {"start": start, "end": end},
        "relatedLines": lines,
        "relatedStops": [],
        "attributes": {},
    }


def test_aggregate_retained_when_only_other_category_singles_cover_lines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A multi-line Störung aggregate must NOT be dropped just because
    single-line items of a DIFFERENT category (Hinweis) cover its lines:
    no single-line *Störung* actually covers them, so the disruption alert
    must survive. Section E is category-aware, mirroring section F."""
    aggregate = _make_event("Signalstörung Innenstadt", ["U1", "U2"])
    hinweis1 = _make_news("U1: Umleitung wegen Veranstaltung", ["U1"])
    hinweis2 = _make_news("U2: Umleitung wegen Veranstaltung", ["U2"])

    monkeypatch.setattr(
        wl_fetch,
        "_fetch_traffic_infos",
        lambda timeout=20, session=None: [aggregate],
    )
    monkeypatch.setattr(
        wl_fetch,
        "_fetch_news",
        lambda timeout=20, session=None: [hinweis1, hinweis2],
    )

    items = wl_fetch.fetch_events()
    categories = [it["category"] for it in items]

    # The Störung aggregate survives (cross-category singles do not cover it).
    assert "Störung" in categories, categories
    assert categories.count("Hinweis") == 2
    assert len(items) == 3


def test_display_ticker_of_works_removed_beside_works_notice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Die Anzeigetafel-Kurzmeldung nennt das Thema der Baustellenmeldung
    in ihrer ersten Zeile; sie sagt mit anderen Worten dasselbe und fällt
    weiter weg, auch wenn ihr Titel Wörter enthält, die dort fehlen."""
    works = _make_event("Gleisbauarbeiten", ["5", "12", "37"])
    works["description"] = "Umleitung in beiden Richtungen über Spittelau."
    ticker = _make_event("Betrieb ab Nußdorfer Straße", ["37"])
    ticker["description"] = "Gleisbauarbeiten\nBetrieb ab Nußdorfer Straße"

    titles = [it["title"] for it in _run(monkeypatch, [works, ticker])]

    assert titles == ["5/12/37: Gleisbauarbeiten"]


def test_display_tickers_do_not_cover_a_long_incident_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression 2026-10-07 (Rohdaten 2026-10-05 09:31 MESZ): Die zwei
    Anzeigetafel-Kurzmeldungen deckten nach Wörtern die ausführliche Meldung
    „10, 60: Polizeieinsatz“ ab, und E entfernte sie samt Maßnahmen und
    Dauer. Eine Kurzmeldung deckt nie eine ausführliche Meldung ab."""
    incident = _make_event("10, 60: Polizeieinsatz", ["10", "60"])
    incident["name"] = "I20261005-0016"
    incident["description"] = (
        "Linie 10: Betrieb nur zwischen Dornbach und Linzer Straße. "
        "Linie 60: Kein Betrieb zwischen Penzinger Straße und Anschützgasse. "
        "Voraussichtliche Dauer: 09:50 Uhr. Grund: Polizeieinsatz."
    )
    ticker10 = _make_event("Fahrtbehinderung wegen Polizeieinsatz", ["10"])
    ticker10["name"] = "R1559-160"
    ticker60 = _make_event("Polizeieinsatz Betrieb ab Anschützgasse", ["60"])
    ticker60["name"] = "R572-160"

    items = _run(monkeypatch, [incident, ticker10, ticker60])

    kept = [it for it in items if it["title"] == "10/60: Polizeieinsatz"]
    assert kept, [it["title"] for it in items]
    assert "Dornbach" in kept[0]["description"]


def test_long_incident_message_not_removed_by_a_wider_display_ticker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F: Eine Kurzmeldung für mehr Linien entfernt keine ausführliche Meldung."""
    incident = _make_event("2: Rettungseinsatz", ["2"])
    incident["name"] = "I20261005-0031"
    incident["description"] = "Linie 2: Fahrtbehinderung in Richtung Dornbach. Grund: Rettungseinsatz."
    ticker = _make_event("Fahrtbehinderung wegen Rettungseinsatz", ["2", "12"])
    ticker["name"] = "R2420-118"

    titles = [it["title"] for it in _run(monkeypatch, [incident, ticker])]

    assert "2: Rettungseinsatz" in titles, titles
