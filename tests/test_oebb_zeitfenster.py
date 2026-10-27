"""ÖBB's time window "(jeweils …)" survives the cut (operator decision 2026-10-26).

"Fenster statt Strecke und Zeitraum": when a "kann … nicht fahren" sentence
with its window does not fit in 180 characters, the route goes first (the
title names it), then the period when the time line ends on its last day;
the window stays. "(an einigen Tagen)" goes with the trains like "an
einzelnen Tagen".

Mutations checked against this file (each one caught, by the test named):

* the period goes although the time line does not end on its day →
  ``test_period_stays_without_matching_end``.
* the route goes although the title does not name it →
  ``test_route_stays_when_the_title_does_not_name_it``.
* the window goes with the cut again → ``test_weekend_nights_stay_visible``.
"""

from __future__ import annotations

from datetime import date

import pytest

from src import build_feed as bf

GRAMATNEUSIEDL = (
    "Wegen Bauarbeiten können zwischen Wien Hbf (U) und Gramatneusiedl Bahnhof von "
    "31.10.2026 bis 30.11.2026 (jeweils von Samstag 23:15 Uhr bis Sonntag 04:40 Uhr "
    "und von Sonntag 23:15 Uhr bis Montag 04:40 Uhr) keine Nahverkehrszüge fahren."
)
TITLE = "Wien Hauptbahnhof ↔ Gramatneusiedl"


def test_weekend_nights_stay_visible() -> None:
    out = bf._oebb_what_does_not_run_first(GRAMATNEUSIEDL, title=TITLE, shown_end=date(2026, 11, 30))
    assert out == (
        "Wegen Bauarbeiten können keine Nahverkehrszüge fahren: jeweils von Samstag 23:15 Uhr "
        "bis Sonntag 04:40 Uhr und von Sonntag 23:15 Uhr bis Montag 04:40 Uhr."
    )
    assert len(out) <= 180


def test_period_stays_without_matching_end() -> None:
    out = bf._oebb_what_does_not_run_first(GRAMATNEUSIEDL, title=TITLE, shown_end=None)
    assert "von 31.10.2026 bis 30.11.2026" in out


def test_route_stays_when_the_title_does_not_name_it() -> None:
    out = bf._oebb_what_does_not_run_first(
        GRAMATNEUSIEDL, title="Wien Meidling ↔ Ebenfurth", shown_end=date(2026, 11, 30)
    )
    assert "zwischen Wien Hbf (U) und Gramatneusiedl Bahnhof" in out


def test_route_goes_first_when_that_is_enough() -> None:
    sentence = (
        "Wegen Bauarbeiten können zwischen Wien Floridsdorf Bahnhof (U) und Flughafen Wien "
        "Bahnhof von 01.06.2026 bis 19.06.2026 (jeweils Samstag, Sonntag und Feiertag) "
        "einige Nahverkehrszüge nicht fahren."
    )
    out = bf._oebb_what_does_not_run_first(sentence, title="Wien Floridsdorf ↔ Flughafen Wien")
    assert out == (
        "Wegen Bauarbeiten können einige Nahverkehrszüge nicht fahren: von 01.06.2026 bis "
        "19.06.2026 (jeweils Samstag, Sonntag und Feiertag)."
    )


def test_some_days_go_with_the_trains() -> None:
    sentence = (
        "Wegen Bauarbeiten können zwischen Wien Hbf und Bruck/Mur Bahnhof/Graz Hbf von "
        "14.03.2026 (22:00 Uhr) bis 29.05.2026 (05:00 Uhr) (an einigen Tagen) die Züge EC 206, "
        "EC 207, EC 71, EC 79, EC 533, RJ 751, RJX 637, RJX 639, IC 535, IC 850, IC 1136, "
        "IC 1138 und D 1135 nicht fahren."
    )
    out = bf._oebb_what_does_not_run_first(sentence, title="Wien Hauptbahnhof Graz")
    assert out == (
        "Wegen Bauarbeiten können an einigen Tagen 13 Züge (EC, RJ, RJX, IC und D) nicht "
        "fahren: von 14.03.2026 (22:00 Uhr) bis 29.05.2026 (05:00 Uhr)."
    )
    parts = bf._oebb_cannot_run_parts(out)
    assert parts is not None and parts.head.endswith("cannot run on certain days")


@pytest.mark.parametrize(
    ("model", "published"),
    [
        # The model's rendering of "(jeweils Samstag, Sonntag und Feiertag)".
        ("from 01.06.2026 to 19.06.2026 (saturday, Sunday and holiday).",
         "from 01.06.2026 to 19.06.2026 (Saturday, Sunday and holiday)."),
        ("from monday to friday", "from Monday to Friday"),
        # Lower-case months are English words; they stay.
        ("trains may run", "trains may run"),
    ],
)
def test_english_weekdays_take_a_capital(model: str, published: str) -> None:
    assert bf._english_weekdays_capitalised(model) == published
