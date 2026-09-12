"""Upstream-truncated Baustellen titles must read as shortened, not broken.

Live, 2026-09-12 — three of 22 titles in ``docs/feed.xml`` were cut off by the
Stadt-Wien WFS and landed verbatim in the German feed::

    Landstraßer Hauptstraße von … und Apostelgasse bis Schlachthausgas   (100)
    Vordere Zollamtsstraße von … bis Unbenannte Verkehrsfläche und Rad   (100)
    Kennedybrücke zwischen … auf Seite "Otto Wagner Hofpavillon           (99)

``BEZEICHNUNG`` is capped at 100 characters upstream; this project does not
truncate titles itself. The missing text cannot be recovered — but an
unannounced cut, and a quotation mark that never closes, look like a defect on
our side. That matters because the German feed drives info displays read from
a distance and without interaction (AGENTS.md, "Priorität der Ausgaben").

Detection uses two INDEPENDENT signals rather than length alone:

1. ``len >= 100`` — the hard cap is reached.
2. an unpaired ``"`` — a complete title has none. This catches the 99-char
   case: upstream cuts at 100 and strips trailing whitespace afterwards, so a
   truncated title can also land at 99, which length alone cannot tell apart
   from a genuine 99-character title.
"""

from __future__ import annotations

from typing import Any

from scripts import update_baustellen_cache
from scripts.update_baustellen_cache import _mark_upstream_truncation
from src.utils.ids import make_guid

# The three live titles, verbatim.
_AT_CAP = (
    "Landstraßer Hauptstraße von Emmerich-Teuber-Platz und Juchgasse und "
    "Apostelgasse bis Schlachthausgas"
)
_UNPAIRED_QUOTE = (
    'Kennedybrücke zwischen Schönbrunner Schloßstraße und Hadikgasse, '
    'auf Seite "Otto Wagner Hofpavillon'
)
# 94 characters — the longest title that is NOT truncated.
_INTACT_LONG = (
    "Märzstraße 49 bis Kreuzung Huglgasse sowie Kreuzung Huglgasse bis "
    "Kreuzung Hütteldorfer Straße"
)


def _feature(name: str, *, objectid: int = 1) -> dict[str, Any]:
    return {
        "properties": {
            "BEZEICHNUNG": name,
            "OBJEKT_BEGINN": "2026-05-17Z",
            "OBJEKT_ENDE": "2026-08-14Z",
            "OBJECTID": objectid,
        },
        "geometry": {},
    }


# ---------------- detection ----------------


def test_a_title_at_the_cap_is_marked() -> None:
    assert len(_AT_CAP) == 100
    assert _mark_upstream_truncation(_AT_CAP) == _AT_CAP + "…"


def test_an_unpaired_quote_is_dropped_and_marked() -> None:
    """The 99-character case — caught by the quote, not by the length."""
    assert len(_UNPAIRED_QUOTE) == 99
    out = _mark_upstream_truncation(_UNPAIRED_QUOTE)
    assert '"' not in out
    assert out.endswith("Otto Wagner Hofpavillon…")


def test_a_complete_title_is_left_alone() -> None:
    for title in (_INTACT_LONG, "Burggasse 67", "Donaufelder Straße 79 bis 137"):
        assert _mark_upstream_truncation(title) == title


def test_a_long_title_just_below_the_cap_is_left_alone() -> None:
    # 94 characters — no ellipsis may appear on a title that simply is long.
    assert len(_INTACT_LONG) == 94
    assert "…" not in _mark_upstream_truncation(_INTACT_LONG)


def test_balanced_quotes_are_not_touched() -> None:
    title = 'Kennedybrücke, auf Seite "Otto Wagner Hofpavillon"'
    assert _mark_upstream_truncation(title) == title


def test_an_empty_title_stays_empty() -> None:
    assert _mark_upstream_truncation("") == ""


def test_the_marker_is_not_doubled() -> None:
    """Idempotent: a title that already ends in an ellipsis gains no second one."""
    once = _mark_upstream_truncation(_AT_CAP)
    assert _mark_upstream_truncation(once) == once


def test_an_odd_number_of_quotes_drops_the_last_one() -> None:
    """With 3 quotes the LAST is the opener whose partner was cut off."""
    title = 'Baustelle "A" und "B'
    assert _mark_upstream_truncation(title) == 'Baustelle "A" und B…'


# ---------------- the load-bearing safety property ----------------


def test_the_guid_is_derived_from_the_raw_title() -> None:
    """Cosmetics must not move a construction site's identity.

    ``_feature_to_event`` falls back to the title when the layer offers no
    ``OGD_ID``. Deriving the guid from the REPAIRED title would make every
    truncated site look brand-new on the deploy that introduced this marker —
    resetting its ``first_seen`` and letting it dominate the
    first_seen-sorted feed.

    Pinned by reconstructing the guid the old way, from the raw title.
    """
    event = update_baustellen_cache._feature_to_event(_feature(_AT_CAP))
    assert event is not None

    expected = make_guid(
        "baustellen",
        _AT_CAP,  # RAW, not the marked-up title
        "2026-05-17T00:00:00+02:00",
        "2026-08-14T00:00:00+02:00",
    )
    assert event.guid == expected
    # …while the displayed title carries the marker.
    assert event.title == _AT_CAP + "…"
    assert event.title != _AT_CAP


def test_an_ogd_id_still_wins_over_the_title() -> None:
    """Unchanged precedence: a stable open-data id beats the title fallback."""
    feature = _feature(_AT_CAP)
    feature["properties"]["OGD_ID"] = "site-4711"
    event = update_baustellen_cache._feature_to_event(feature)
    assert event is not None
    assert event.guid == make_guid(
        "baustellen",
        "site-4711",
        "2026-05-17T00:00:00+02:00",
        "2026-08-14T00:00:00+02:00",
    )
