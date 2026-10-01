"""A WL title that only lists lines gives way to the description's heading.

Live 2026-09-30 18:31 to 2026-10-01, 34 versions of ``docs/feed.xml``::

    D/1/2/71/1A/3A: D, 1, 2, 71, 1A, 3A

WL titled its demonstration notice with the affected lines; the line prefix
repeated them. On a display the item said nothing but line numbers. The
description opens with ``<h2>Demonstration</h2>``. Over 736 versions of the
WL cache this is the only title that lists nothing but lines.

Mutations checked against this file (each one caught, by the test named):

* the heading is not used → ``test_the_demonstration_notice_is_titled_by_its_heading``.
* any title gives way to the heading → ``test_an_informative_title_stays``.
* the heading is tidied like a title → ``test_the_heading_keeps_its_cause``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from src.providers import wl_fetch

# Verbatim from cache/wl_9d709a/events.json (2026-09-30 18:30), trimmed
# after the first measure.
_DEMO_DESCRIPTION = (
    "<h2>Demonstration</h2> <p>Wegen einer Demonstration kommt es zu Einschr&auml;nkungen beim "
    "&ouml;ffentlichen Verkehr.</p> <p><span style=\"text-decoration: underline;\"><strong>Zeitraum:"
    "</strong></span><br />Donnerstag, 01. Oktober 2026 von ca. 17:00 Uhr bis 21:00 Uhr.</p> "
    "<p><strong>Linie D:</strong><br />Umleitung in beiden Richtungen zwischen B&ouml;rse und "
    "Schwarzenbergplatz &uuml;ber Schottenring, Schwedenplatz und Stubentor.</p>"
)
_DEMO_LINES = ["D", "1", "2", "71", "1A", "3A"]


def _poi(title: str, description: str, lines: list[str]) -> dict[str, Any]:
    now = datetime.now(UTC)
    return {
        "title": title,
        "description": description,
        "time": {"start": (now - timedelta(hours=1)).isoformat(), "end": (now + timedelta(hours=3)).isoformat()},
        "relatedLines": lines,
        "relatedStops": [],
        "attributes": {},
    }


def _titles(monkeypatch: pytest.MonkeyPatch, poi: dict[str, Any]) -> list[str]:
    monkeypatch.setattr(wl_fetch, "_fetch_traffic_infos", lambda timeout=20, session=None: [])
    monkeypatch.setattr(wl_fetch, "_fetch_news", lambda timeout=20, session=None: [poi])
    return [str(item["title"]) for item in wl_fetch.fetch_events()]


def test_the_demonstration_notice_is_titled_by_its_heading(monkeypatch: pytest.MonkeyPatch) -> None:
    poi = _poi("D, 1, 2, 71, 1A, 3A", _DEMO_DESCRIPTION, _DEMO_LINES)
    assert _titles(monkeypatch, poi) == ["D/1/2/71/1A/3A: Demonstration"]


def test_an_informative_title_stays(monkeypatch: pytest.MonkeyPatch) -> None:
    poi = _poi("Demonstration am Ring", _DEMO_DESCRIPTION, _DEMO_LINES)
    assert _titles(monkeypatch, poi) == ["D/1/2/71/1A/3A: Demonstration am Ring"]


@pytest.mark.parametrize("title", ["D, 1, 2, 71, 1A, 3A", "U6", "13A und 14A", "U1/U2"])
def test_a_line_list_without_a_heading_keeps_its_title(title: str) -> None:
    assert wl_fetch._title_or_heading(title, "<p>Umleitung.</p>") == title


def test_the_heading_keeps_its_cause() -> None:
    # ``_tidy_title_wl`` strips "Gleisbauarbeiten" in front of a place as a label.
    heading = "<h2>Gleisbauarbeiten M&auml;rzstra&szlig;e</h2>"
    assert wl_fetch._title_or_heading("49", heading) == "Gleisbauarbeiten Märzstraße"


def test_an_empty_title_takes_the_heading_or_the_generic_label() -> None:
    assert wl_fetch._title_or_heading("---", _DEMO_DESCRIPTION) == "Demonstration"
    assert wl_fetch._title_or_heading("---", "") == "Meldung"
