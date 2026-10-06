"""Der Beginn einer Stadt-Wien-Baustelle steht im WFS einen Tag zu früh.

``OBJEKT_BEGINN`` und ``OBJEKT_ENDE`` kommen als UTC-Datum eines Wiener
Zeitpunkts (``2026-06-22Z``). Der Beginn, Mitternacht in Wien, landet dabei
auf dem Vortag; das Ende bleibt der letzte Tag. Belegt an den Texten der
Baustellen selbst (Prüfung vom 2026-10-06, 13 von 13 Beginn-Angaben). Die
Felder unten sind die echten aus ``data/raw/baustellen/BAUSTELLENLINOGD.json``
(Stand 2026-10-05).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from scripts import update_baustellen_cache as ubc
from src.utils.ids import make_guid

VIENNA = ZoneInfo("Europe/Vienna")


def _neilreichgasse(**overrides: Any) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "ANSPRECHPERSON": "Poststelle Wien Straßenbau",
        "ANTRAGSTELLER": None,
        "BEHINDERUNGSART": "Straßenbau",
        "BEZEICHNUNG": "Neilreichgasse von Gudrunstraße bis Davidgasse",
        "BEZIRK": 10,
        "GISCODE": 1,
        "GISCODE_TXT": "P90 Baustellen",
        "OBJEKT_BEGINN": "2026-06-22Z",
        "OBJEKT_ENDE": "2026-10-13Z",
        "PRESSETEXT": (
            "Bauphase 1 von 2 (23.06.2026 bis 28.08.2026):Die Arbeiten erfolgen bei "
            "Sperre der Neilreichgasse von Gudrunstraße bis Buchengasse. Bauphase 2 von 2 "
            "(31.08.2026 bis 13.10.2026): Die Buslinie 7A wird in diesen Zeitraum über die "
            "Rothenhofgasse Sonnleithengasse - Davidgasse umgeleitet."
        ),
        "SE_ANNO_CAD_DATA": None,
    }
    properties.update(overrides)
    return {
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": [[16.3712, 48.1728], [16.372, 48.173]]},
        "properties": properties,
    }


def test_start_is_the_day_after_the_delivered_date() -> None:
    event = ubc._feature_to_event(_neilreichgasse())
    assert event is not None
    assert event.starts_at == datetime(2026, 6, 23, tzinfo=VIENNA)
    assert event.pub_date == event.starts_at
    assert "Beginn: 23.06.2026" in event.description


def test_end_stays_the_last_day() -> None:
    event = ubc._feature_to_event(_neilreichgasse())
    assert event is not None
    assert event.ends_at == datetime(2026, 10, 13, tzinfo=VIENNA)


def test_guid_keeps_the_delivered_date() -> None:
    """``first_seen`` and the translation hang on the guid: no site may look new."""
    event = ubc._feature_to_event(_neilreichgasse())
    assert event is not None
    expected = make_guid(
        "baustellen",
        "Neilreichgasse von Gudrunstraße bis Davidgasse",
        datetime(2026, 6, 22, tzinfo=VIENNA).isoformat(),
        datetime(2026, 10, 13, tzinfo=VIENNA).isoformat(),
    )
    assert event.guid == expected


def test_start_crosses_month_year_and_time_change() -> None:
    cases = {
        "2021-02-28Z": datetime(2021, 3, 1, tzinfo=VIENNA),
        "2026-12-31Z": datetime(2027, 1, 1, tzinfo=VIENNA),
        # Night of the change to winter time: the day after is still a day.
        "2026-10-24Z": datetime(2026, 10, 25, tzinfo=VIENNA),
    }
    for raw, expected in cases.items():
        event = ubc._feature_to_event(_neilreichgasse(OBJEKT_BEGINN=raw, OBJEKT_ENDE="2027-02-01Z"))
        assert event is not None
        assert event.starts_at == expected, raw
        assert event.starts_at.utcoffset() == expected.utcoffset(), raw


def test_start_never_after_the_last_day() -> None:
    event = ubc._feature_to_event(_neilreichgasse(OBJEKT_BEGINN="2026-10-13Z"))
    assert event is not None
    assert event.starts_at == datetime(2026, 10, 13, tzinfo=VIENNA)


def test_a_start_with_a_clock_time_is_taken_as_written() -> None:
    event = ubc._feature_to_event(_neilreichgasse(OBJEKT_BEGINN="2026-06-23T06:00:00+02:00"))
    assert event is not None
    assert event.starts_at == datetime(2026, 6, 23, 6, tzinfo=VIENNA)


def test_a_site_without_start_keeps_none() -> None:
    event = ubc._feature_to_event(_neilreichgasse(OBJEKT_BEGINN=None))
    assert event is not None
    assert event.starts_at is None
