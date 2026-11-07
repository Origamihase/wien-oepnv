"""WL network changes ("Netzänderung") enter the feed as notices (operator decision 2026-11-07).

"9A: Errichtung einer Haltestelle" (new stop from 07.09.2026) and "57A:
Errichtung einer Haltestelle" (from 05.09.) name no restriction word and fell
at the keyword gate of ``wl_fetch`` in every fetch of 04.–07.10.2026. WL marks
the kind of a long message in its lead ("<h2>Netzänderung</h2>"); the gate
reads that mark (``wl_news_kind.is_network_change``). WL's ``time.end`` of
such a message is the notice's end, a month after the change, so the item
carries no end; its start is the date under WL's "Zeitpunkt:" heading. "N67: Geänderte
Linienführung ab 05.12.2026" (route change, listed 04.11.–15.12.) names "entfällt" and
passed the gate already; it loses WL's end the same way.
"""

from __future__ import annotations

from datetime import UTC, datetime, tzinfo
from pathlib import Path
from typing import Any

import pytest

from src.providers import wl_fetch
from src.providers.wl_news_kind import is_network_change, lead_text
from src.providers.wl_text import extract_start_from_description, is_stop_notice
from src.utils import raw_capture

# Real WL newsList record of 04.10.2026 (N20260806-6072), description verbatim.
_NETZAENDERUNG_9A = (
    "<h2>Netz&auml;nderung</h2>\r\n<p>Die Linie 9A bekommt eine neue Haltestelle.&nbsp;</p>\r\n"
    '<p><span style="text-decoration: underline;"><strong>Zeitpunkt:</strong></span><br />Ab 07. September 2026.</p>\r\n'
    '<p><span style="text-decoration: underline;"><strong>Ma&szlig;nahmen:</strong></span><br /><strong>Linie 9A:</strong></p>\r\n'
    '<p><span style="text-decoration: underline;">Haltestelle Richtung Bahnhof Meidling S U, Eichenstra&szlig;e:</span>'
    "<br />- Bahnhof Meidling S U, Eichenstra&szlig;e (Eichenstra&szlig;e 72-74, End- und Anfangshaltestelle)</p>"
)

# Real WL newsList record of 04.11.2026 (N20261104-0002), first paragraphs verbatim: a network change
# whose text names a restriction word ("entfällt"), so it passed the gate before; WL lists it until 15.12.
_NETZAENDERUNG_N67 = (
    "<h2>Netzänderung</h2><p>Die Linienführung der Linie N67 wird im Bereich Jochen-Rindt-Straße angepasst.</p>"
    "<p><strong><u>Zeitpunkt:</u></strong><br>Ab Samstag, 05. Dezember 2026, 00:00 Uhr.</p>"
    "<p><strong><u>Neue Fahrstrecke:</u></strong><br>Die Busse fahren in beiden Richtungen direkt von der "
    "Jochen-Rindt-Straße über die Tenschertstraße. Die Fahrt über Inzersdorf, Biróstraße entfällt.</p>"
)


def _news(name: str, title: str, description: str, lines: list[str], start: str, end: str) -> dict[str, Any]:
    return {
        "alertCause": "unknown",
        "alertCauseId": 0,
        "description": description,
        "id": name,
        "location": {"properties": {"attributes": {}, "coordName": "WGS84", "type": "news"}, "type": "Feature"},
        "name": name,
        "refPoiCategoryId": 13296,
        "relatedLines": lines,
        "relatedStops": [500, 3207],
        "time": {"end": end, "start": start, "validFrom": start},
        "title": title,
    }


def _fetch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, pois: list[dict[str, Any]]) -> list[dict[str, Any]]:
    monkeypatch.setattr(raw_capture, "RAW_ROOT", tmp_path / "raw")
    monkeypatch.setenv(raw_capture.RAW_CAPTURE_ENV, "1")
    responses = {
        "trafficInfoList": {"data": {"trafficInfos": []}, "message": {"value": "OK"}},
        "newsList": {"data": {"pois": pois}, "message": {"value": "OK"}},
    }
    monkeypatch.setattr(wl_fetch, "_get_json", lambda path, **kwargs: responses[path])
    return wl_fetch.fetch_events()


def test_network_change_is_read_from_the_lead() -> None:
    assert is_network_change(_NETZAENDERUNG_9A) is True
    # The older format without a heading leads with the word itself.
    assert is_network_change("<p>Netzänderung der Linie 13A ab 01.12.2026: neue Linienführung …</p>") is True
    assert is_network_change("<h2>Liniennetzänderungen</h2><p>Ab 07. September 2026 …</p>") is True
    # Works and stop relocations are no network change, and a network change is no stop notice.
    assert is_network_change("<h2>Gleisbauarbeiten</h2><p>Wegen Gleisbauarbeiten … Netzänderung folgt.</p>") is False
    assert is_network_change("<p><strong>Haltestellenverlegung der Linie 68A</strong></p>") is False
    assert is_network_change("") is False
    assert is_stop_notice(_NETZAENDERUNG_9A) is False
    assert lead_text("<h2>Netz&auml;nderung</h2>\r\n<p>Die Linie&nbsp;9A</p>") == "Netzänderung Die Linie 9A"


def test_zeitpunkt_heading_dates_the_change() -> None:
    start = extract_start_from_description(_NETZAENDERUNG_9A)
    assert start is not None
    assert (start.year, start.month, start.day) == (2026, 9, 7)


def _clock(moment: datetime) -> type[datetime]:
    """A ``datetime`` whose ``now`` is *moment* (``wl_fetch`` reads ``datetime.now(UTC)``)."""

    class _At(datetime):
        @classmethod
        def now(cls, tz: tzinfo | None = None) -> _At:
            when = moment.astimezone(tz) if tz is not None else moment.replace(tzinfo=None)
            return cls(*when.timetuple()[:6], tzinfo=when.tzinfo)

    return _At


def test_wl_fetch_keeps_the_network_change_as_a_notice_without_an_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Fetched 04.10.2026 11:31 Vienna time, the first raw state with the record; WL's start is the
    # publication 06.08., its end 07.10. 23:59, a month after the change.
    monkeypatch.setattr(wl_fetch, "datetime", _clock(datetime(2026, 10, 4, 9, 31, 22, tzinfo=UTC)))
    items = _fetch(
        monkeypatch,
        tmp_path,
        [
            _news(
                "N20260806-6072", "9A: Errichtung einer Haltestelle", _NETZAENDERUNG_9A, ["9A"],
                "2026-08-06T00:00:00.000+0200", "2026-10-07T23:59:00.000+0200",
            ),
            # A news item without a restriction word and without the mark still falls.
            _news(
                "N20260901-0001", "U1: Starke Nachfrage", "<p>Wegen starker Nachfrage fahren mehr Züge.</p>", ["U1"],
                "2026-09-01T00:00:00.000+0200", "2026-12-31T23:59:00.000+0100",
            ),
        ],
    )
    assert [(it["title"], it["category"]) for it in items] == [("9A: Errichtung einer Haltestelle", "Hinweis")]
    item = items[0]
    assert item["ends_at"] is None
    assert item["starts_at"].date().isoformat() == "2026-09-07"
    assert {"grund": "kein Einschränkungs-Stichwort", "titel": "U1: Starke Nachfrage"} in raw_capture.collected_drops("wl")


def test_gate_and_notice_end_helpers() -> None:
    assert wl_fetch._news_passes_gate("9A Errichtung einer Haltestelle Die Linie 9A bekommt eine neue Haltestelle", _NETZAENDERUNG_9A)
    assert not wl_fetch._news_passes_gate("U1 Starke Nachfrage mehr Züge", "<p>Wegen starker Nachfrage fahren mehr Züge.</p>")
    assert wl_fetch._news_passes_gate("12A N49 Gleisbauarbeiten Umleitung", "<h2>Gleisbauarbeiten</h2>")
    end = datetime(2026, 10, 7, 21, 59, tzinfo=UTC)
    assert wl_fetch._notice_end(_NETZAENDERUNG_9A, end) is None
    assert wl_fetch._notice_end("<h2>Gleisbauarbeiten</h2><p>Zeitraum: bis 07.10.2026</p>", end) == end


def test_a_network_change_that_names_a_restriction_word_loses_wl_end_too() -> None:
    assert is_network_change(_NETZAENDERUNG_N67) is True
    start = extract_start_from_description(_NETZAENDERUNG_N67)
    assert start is not None
    assert (start.year, start.month, start.day) == (2026, 12, 5)
    assert wl_fetch._news_passes_gate("N67 Geänderte Linienführung Die Fahrt entfällt", _NETZAENDERUNG_N67)
    assert wl_fetch._notice_end(_NETZAENDERUNG_N67, datetime(2026, 12, 15, 22, 59, tzinfo=UTC)) is None


def test_wl_fetch_keeps_the_n67_route_change_without_wl_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Fetched 07.11.2026 19:01 Vienna time; WL sends the record with an empty line list.
    monkeypatch.setattr(wl_fetch, "datetime", _clock(datetime(2026, 11, 7, 18, 1, tzinfo=UTC)))
    items = _fetch(
        monkeypatch,
        tmp_path,
        [
            _news(
                "N20261104-0002", "N67: Geänderte Linienführung ab 05.12.2026", _NETZAENDERUNG_N67, [""],
                "2026-11-04T12:39:00.000+0100", "2026-12-15T23:59:00.000+0100",
            )
        ],
    )
    assert [(it["title"], it["category"], it["ends_at"]) for it in items] == [
        ("N67: Geänderte Linienführung", "Hinweis", None)
    ]
    assert items[0]["starts_at"].date().isoformat() == "2026-12-05"
