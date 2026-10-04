"""The WL news gate reads plain text and knows the measures as verbs.

A WL news item (``newsList`` POI) reaches the feed only if its text names a
restriction (``KW_RESTRICTION``). The raw data the Update run keeps since
2026-10-04 (``data/raw/wl/verworfen.json``) showed two real notices dropped
there, each the only notice with a line for its measure:

* "18: LCC-Herbstmarathon am 11.10.2026": "wird die Linie 18 kurz
  gef&uuml;hrt". The description is HTML with the umlauts as entities, so
  no keyword with an umlaut could ever match in a description; and the
  noun root ``kurzführung`` misses the verb "kurz geführt" anyway.
* "U6: Neue Donau, kein Halt Richtung Floridsdorf ab 14.09.2026": "wird in
  Richtung Floridsdorf U durchfahren". The WL disruption for the same
  closure ("Sperre Bahnsteig Richtung Floridsdorf") carries no line and
  stays out of the feed by the operator decision of 2026-10-03.

Over all 78 news items of the eight raw snapshots, exactly these two change.
"57A: Errichtung einer Haltestelle" (a new stop) stays out.

Mutations checked against this file (each one caught, by the test named):

* ``_gate_text`` returns its input unescaped → ``test_the_marathon_reaches_the_cache``.
* the verb alternatives removed from ``KW_RESTRICTION`` →
  ``test_the_marathon_reaches_the_cache``, ``test_the_u6_closure_reaches_the_cache``.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from types import TracebackType
from typing import Any, Literal
from zoneinfo import ZoneInfo

import pytest

from src.providers.wl_text import KW_RESTRICTION

VIENNA = ZoneInfo("Europe/Vienna")


class _Session:
    headers: dict[str, str] = {}

    def __enter__(self) -> _Session:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> Literal[False]:
        return False


def _fetch(monkeypatch: pytest.MonkeyPatch, news: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from src.providers import wl_fetch

    monkeypatch.setattr(wl_fetch, "_fetch_traffic_infos", lambda *a, **kw: [])
    monkeypatch.setattr(wl_fetch, "_fetch_news", lambda *a, **kw: list(news))
    monkeypatch.setattr(wl_fetch, "session_with_retries", lambda *a, **kw: _Session())
    return wl_fetch.fetch_events()


def _poi(title: str, description: str, line: str) -> dict[str, Any]:
    """A POI shaped like ``data/raw/wl/newsList.json``, dated around today."""
    now = datetime.now(VIENNA).replace(microsecond=0)
    return {
        "description": description,
        "id": "N20260928-6209",
        "location": {"properties": {"attributes": {}, "coordName": "WGS84", "type": "news"}, "type": "Feature"},
        "name": "N20260928-6209",
        "refPoiCategoryId": 13296,
        "relatedLines": [line],
        "time": {
            "start": (now - timedelta(days=5)).isoformat(),
            "end": (now + timedelta(days=40)).isoformat(),
        },
        "title": title,
    }


# Verbatim from the raw snapshot of 2026-10-04 13:01 UTC, without the
# "Zeitraum" paragraphs (their dates would let the text end the item).
_MARATHON = (
    "<h2>Laufveranstaltung</h2>\r\n<p>Wegen des LCC-Herbstmarathons in der Prater "
    "Hauptallee wird die Linie 18 kurz gef&uuml;hrt.</p>\r\n<p><span style=\"text-decoration: "
    "underline;\"><strong>Ma&szlig;nahmen:</strong></span><br /><strong>Linie 18:</strong><br />"
    "Betrieb nur zwischen Burggasse, Stadthalle U und Schlachthausgasse U.</p>"
)
_U6 = (
    "<h2>Bahnsteigsanierung</h2>\r\n<p>Wegen Sanierung des Bahnsteigs h&auml;lt die Linie U6 "
    "die Station Neue Donau U nur in Richtung Siebenhirten U ein.<br />Sie erreichen die Station "
    "Neue Donau mit der U6 von Floridsdorf.</p>\r\n<p><span style=\"text-decoration: "
    "underline;\"><strong>Ma&szlig;nahmen:</strong></span><br />Die Station Neue Donau U wird in "
    "Richtung Floridsdorf U durchfahren.</p>"
)
_NEW_STOP = (
    "<h2>Netz&auml;nderung</h2>\r\n<p>Die Linie 57A bekommt eine neue Haltestelle.&nbsp;</p>\r\n"
    "<p><span style=\"text-decoration: underline;\"><strong>Ma&szlig;nahmen:</strong></span><br />"
    "<strong>Linie 57A:</strong></p>"
)


def test_the_marathon_reaches_the_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    events = _fetch(monkeypatch, [_poi("18: LCC-Herbstmarathon am 11.10.2026", _MARATHON, "18")])
    assert [e["title"] for e in events] == ["18: LCC-Herbstmarathon am 11.10.2026"]


def test_the_u6_closure_reaches_the_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    events = _fetch(monkeypatch, [_poi("U6: Neue Donau, kein Halt Richtung Floridsdorf", _U6, "U6")])
    assert len(events) == 1


def test_a_new_stop_stays_out(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _fetch(monkeypatch, [_poi("57A: Errichtung einer Haltestelle", _NEW_STOP, "57A")]) == []


def test_gate_text_resolves_entities_and_tags() -> None:
    from src.providers.wl_fetch import _gate_text

    assert _gate_text("Titel", "<h2>Sperre</h2><p>Einschr&auml;nkung</p>") == "Titel Sperre Einschränkung"


@pytest.mark.parametrize(
    "text",
    [
        "wird die Linie 18 kurz geführt",
        "Linie 5 wird kurzgeführt",
        "Die Station wird in Richtung Floridsdorf durchfahren",
        "Züge durchfahren die Station",
        "kein Halt in Neue Donau",
        "Kein Betrieb zwischen Karlsplatz und Praterstern",
        "Die Linie 13A wird umgeleitet",
        "Der Betrieb ist eingestellt",
        "Die Haltestelle entfällt",
    ],
)
def test_measure_verbs_pass_the_gate(text: str) -> None:
    assert KW_RESTRICTION.search(text) is not None


@pytest.mark.parametrize(
    "text",
    [
        "Die Linie 57A bekommt eine neue Haltestelle.",
        "Linie 2 fährt wieder planmäßig",
        "Kurz vor dem Ziel geht es los",
        "Kein Halteverbot",
    ],
)
def test_plain_service_texts_stay_out(text: str) -> None:
    assert KW_RESTRICTION.search(text) is None
