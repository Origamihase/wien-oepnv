"""A "Linie Badner Bahn:" prefix leaves a WL description like "Linie 62:" does (2026-11-06).

On 05.11.2026 19:01 WL's incident "LB: Fremder Verkehrsunfall" read "Linie
Badner Bahn: Nach einer Fahrtbehinderung kommt es zu unterschiedlichen
Intervallen." The feed kept the prefix behind the title "LB: …", the sentence
missed its English template, and the English feed read "Line Badner Bahn:
After a service obstruction it comes to irregular intervals." (20:31). The
line-code shapes the prefix knew (``_WL_DESC_LINE_TOKEN``) have no room for
a name; "Badner Bahn" is WL's name for its line LB.
"""

from __future__ import annotations

from datetime import datetime
from typing import cast
from zoneinfo import ZoneInfo

import pytest

import src.build_feed as bf
from src.feed_build.wl_measures import _strip_wl_description_line_prefix
from src.feed_types import FeedItem

VIENNA = ZoneInfo("Europe/Vienna")
SENTENCE = "Nach einer Fahrtbehinderung kommt es zu unterschiedlichen Intervallen."
BADNER_BAHN_PART = "Badner Bahn: Betrieb nur zwischen Baden, Josefsplatz und Bahnhof Meidling S U, Schedifkaplatz."


@pytest.mark.parametrize(
    ("desc", "expected"),
    [
        (f"Linie Badner Bahn: {SENTENCE}", SENTENCE),
        (f"Linien 62, Badner Bahn: {SENTENCE}", SENTENCE),
        (f"Linien Badner Bahn/62: {SENTENCE}", SENTENCE),
        (f"Linie 62: {SENTENCE}", SENTENCE),
        # without "Linie" the name marks the Badner Bahn's part of a multi-line text and stays
        (BADNER_BAHN_PART, BADNER_BAHN_PART),
        # a word that is no line stays
        (f"Linie frei: {SENTENCE}", f"Linie frei: {SENTENCE}"),
    ],
)
def test_the_prefix_leaves(desc: str, expected: str) -> None:
    assert _strip_wl_description_line_prefix(desc) == expected


def test_the_feed_text_of_the_badner_bahn_starts_with_its_sentence() -> None:
    published = datetime(2026, 11, 5, 19, 1, tzinfo=VIENNA)
    item = cast(
        FeedItem,
        {
            "source": "Wiener Linien",
            "category": "Störung",
            "title": "LB: Fremder Verkehrsunfall",
            "description": f"Linie Badner Bahn: {SENTENCE}",
            "link": "https://www.wienerlinien.at/betriebsinfo",
            "guid": "52d42225f078",
            "pubDate": published,
            "starts_at": published,
            "ends_at": None,
            "_identity": "wl|störung|L=LB|D=2026-11-05|TK=lb fremder verkehrsunfall",
        },
    )
    (read,) = bf._post_filter_wl([item])
    assert read["description"] == SENTENCE
    assert read["title"] == "LB: Fremder Verkehrsunfall"
