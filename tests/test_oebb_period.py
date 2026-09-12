"""ÖBB items must carry the disruption period, not the publication date.

Live regression, 2026-09-12 — three simultaneous closures on the same route
were indistinguishable in ``docs/feed.xml``::

    Wien Hauptbahnhof ↔ Gramatneusiedl   [Seit 24.08.2026]
    Wien Hauptbahnhof ↔ Gramatneusiedl   [Seit 24.08.2026]
    Wien Hauptbahnhof ↔ Gramatneusiedl   [Seit 10.09.2026]

Their only difference — the construction window — sat unparsed at the start
of the description (``03.10.2026 - 05.10.2026<br/><br/>Wegen Bauarbeiten …``).
``build_feed`` recognised that prefix as metadata but only ever *stripped* it
(``_DATE_RANGE_PREFIX_RE`` / ``_DATE_SINGLE_PREFIX_RE``), so:

* ``starts_at`` stayed the PUBLICATION date and ``ends_at`` stayed ``None``;
* the rendered time line claimed ``[Seit 10.09.2026]`` for a closure that
  starts on 05.12.2026 — not merely uninformative but wrong;
* nothing ever retired an item whose construction work had finished, because
  ``_drop_old_items`` rule 1 needs an ``ends_at``.

The German feed is the priority output (AGENTS.md) and drives info displays
that show exactly this bracket, read from a distance and without interaction.

Parsing the prefix into ``starts_at``/``ends_at`` fixes all three at once —
``format_local_times`` already renders ranges, single days and future starts.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import UTC, datetime

from src.build_feed import format_local_times
from src.providers.oebb import _build_item_from_xml, _parse_period


def _item(description: str, *, title: str = "Bauarbeiten: Wien Hbf - Gramatneusiedl") -> ET.Element:
    """One ``<item>`` in the shape the ÖBB RSS delivers."""
    el = ET.Element("item")
    for tag, text in (
        ("title", title),
        ("description", description),
        ("link", "https://fahrplan.oebb.at/bin/help.exe/dn?L=vs_scotty"),
        ("guid", "https://fahrplan.oebb.at/bin/query.exe/dn?ujm=1&mapType=TRACKINFO&900001"),
        ("pubDate", "Mon, 24 Aug 2026 08:43:40 +0200"),
    ):
        child = ET.SubElement(el, tag)
        child.text = text
    return el


# ---------------- _parse_period ----------------


def test_parse_period_reads_a_range() -> None:
    start, end = _parse_period("03.10.2026 - 05.10.2026<br/><br/>Wegen Bauarbeiten …")
    assert start is not None and end is not None
    assert (start.year, start.month, start.day) == (2026, 10, 3)
    assert (end.year, end.month, end.day) == (2026, 10, 5)
    # The end must cover the whole final day, not expire at midnight.
    assert (end.hour, end.minute, end.second) == (23, 59, 59)
    assert (start.hour, start.minute) == (0, 0)


def test_parse_period_reads_a_single_day() -> None:
    start, end = _parse_period("01.11.2026<br/><br/>Wegen Bauarbeiten …")
    assert start is not None and end is not None
    assert start.date() == end.date()
    # A single day renders as "Am …" — see format_local_times.
    assert format_local_times(start, end) == "Am 01.11.2026"


def test_parse_period_returns_nothing_without_a_prefix() -> None:
    assert _parse_period("Wegen Bauarbeiten kann zwischen A und B …") == (None, None)
    assert _parse_period("") == (None, None)


def test_parse_period_rejects_an_impossible_date() -> None:
    # An upstream typo must not abort the whole fetch.
    assert _parse_period("31.02.2026 - 01.03.2026<br/>x") == (None, None)


def test_parse_period_rejects_a_reversed_range() -> None:
    assert _parse_period("05.12.2026 - 01.01.2026<br/>x") == (None, None)


def test_parse_period_does_not_match_a_date_inside_the_text() -> None:
    # Only the leading prefix counts; a date mid-sentence is prose.
    assert _parse_period("Wegen Bauarbeiten ab 03.10.2026 fahren …") == (None, None)


# ---------------- end to end through _build_item_from_xml ----------------


def test_three_closures_on_one_route_become_distinguishable() -> None:
    """The live defect: same title, three different construction windows."""
    windows = ["03.10.2026 - 05.10.2026", "31.10.2026 - 30.11.2026", "05.12.2026 - 07.12.2026"]
    lines = []
    for window in windows:
        built = _build_item_from_xml(
            _item(
                f"{window}<br/><br/>Wegen Bauarbeiten können <br>zwischen "
                f"<b>Wien Hbf (U)</b> und <b>Gramatneusiedl Bahnhof</b><br>"
                f"keine Nahverkehrszüge fahren."
            )
        )
        assert built is not None
        lines.append(format_local_times(built["starts_at"], built["ends_at"]))

    # ``format_local_times`` joins a range with NARROW NO-BREAK SPACE (U+202F)
    # around the en dash, on purpose: the period must not wrap mid-range on a
    # display. Spelled out here so nobody "fixes" it to a plain space.
    nnbsp = "\u202f"
    assert lines == [
        f"03.10.2026{nnbsp}–{nnbsp}05.10.2026",
        f"31.10.2026{nnbsp}–{nnbsp}30.11.2026",
        f"05.12.2026{nnbsp}–{nnbsp}07.12.2026",
    ]
    assert len(set(lines)) == 3, "die drei Sperren müssen unterscheidbar sein"


def test_the_period_replaces_the_publication_date() -> None:
    """``starts_at`` must describe the disruption, not when ÖBB announced it.

    Before the fix this item rendered "[Seit 24.08.2026]" — the pubDate — for
    work that only starts in December.
    """
    built = _build_item_from_xml(
        _item("05.12.2026 - 07.12.2026<br/><br/>Wegen Bauarbeiten können zwischen "
              "<b>Wien Hbf (U)</b> und <b>Gramatneusiedl Bahnhof</b> …")
    )
    assert built is not None

    pub = built["pubDate"]
    assert isinstance(pub, datetime)
    # pubDate keeps its RSS meaning: when the message was published.
    assert pub.astimezone(UTC).date().isoformat() == "2026-08-24"
    # starts_at now describes the disruption itself.
    starts_at = built["starts_at"]
    assert isinstance(starts_at, datetime)
    assert starts_at.date().isoformat() == "2026-12-05"
    assert "Seit" not in format_local_times(starts_at, built["ends_at"])


def test_an_end_date_lets_a_finished_closure_retire() -> None:
    """``_drop_old_items`` rule 1 needs an ``ends_at`` to retire an item.

    With ``ends_at=None`` — the old behaviour — a closure stayed in the feed
    until it aged out by ``first_seen``, long after the work had finished.
    """
    built = _build_item_from_xml(_item("03.10.2026 - 05.10.2026<br/><br/>Wegen Bauarbeiten …"))
    assert built is not None
    assert built["ends_at"] is not None


def test_without_a_period_the_previous_behaviour_is_kept() -> None:
    """Fallback contract: no prefix → publication date as start, no end."""
    built = _build_item_from_xml(
        _item("Wegen Bauarbeiten können zwischen <b>Wien Hbf (U)</b> und "
              "<b>Gramatneusiedl Bahnhof</b> keine Züge fahren.")
    )
    assert built is not None
    assert built["starts_at"] == built["pubDate"]
    assert built["ends_at"] is None
