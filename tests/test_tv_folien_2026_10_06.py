"""EasySignage preview of 06.10.2026: what the ten slides showed wrong.

* Two stop relocations ended "… und N62 in Richtung …" and "… bzw. N66 in
  Richtung …": the 180-character cut left a word that only leads into what
  it took away.
* 40A read "Hartäckerstraße 65 → Ersatzlos aufgelassen": a closed stop
  behind the arrow that means "moved to".
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

import pytest

from src import build_feed
from src.build_feed import _truncate_summary_180
from src.feed_types import FeedItem


def _format(raw_title: str, raw_desc: str, *, lang: str = "de") -> str:
    item = cast(
        FeedItem,
        {
            "title": raw_title,
            "description": raw_desc,
            "source": "Wiener Linien",
            "category": "Hinweis",
            "guid": "t",
            "link": "",
        },
    )
    formatted = build_feed._format_item_content(
        item,
        ident="t",
        starts_at=datetime(2026, 10, 5, 11, 0, tzinfo=UTC),
        ends_at=datetime(2026, 10, 26, 21, 0, tzinfo=UTC),
        lang=lang,
        state={},
    )
    return formatted.desc_text_truncated


def _stop_notice(heading: str, stop: str, origin: str, target: str) -> str:
    return (
        f"<p><strong><u>{heading}</u></strong></p>"
        f"<p><strong><u>Haltestelle:</u></strong> {stop}</p>"
        f"<p><strong><u>Von:</u></strong> {origin}</p>"
        f"<p><strong><u>Nach:</u></strong> {target}</p>"
        "<p><strong><u>Dauer:</u></strong> Ab 05. Oktober 2026, etwa 13:00 Uhr f&uuml;r "
        "etwa zwei Wochen</p><p><strong><u>Grund:</u></strong> Stra&szlig;enbau</p>"
    )


# ---------------- the cut never ends on a leading word ----------------

_EIBESBRUNNERGASSE = (
    "Wienerbergstraße 27b-27c → Wienerbergstraße 27a. Haltestellenverlegung der "
    "Linien 7A in Richtung Reumannplatz U, 15A in Richtung Enkplatz U, Grillgasse "
    "und N62 in Richtung Oper, Karlsplatz (U)."
)


def test_cut_direction_list_ends_on_the_last_line_not_on_in_richtung() -> None:
    out = _truncate_summary_180(_EIBESBRUNNERGASSE)
    assert out.endswith("Grillgasse und N62 …")
    assert len(out) <= 180


def _cut_at(head: str, tail: str) -> str:
    """A summary whose 180-character cut falls right behind *head*."""
    filler = "Fahrtbehinderung in beiden Richtungen. " * 5
    room = 173 - len(head)
    lead = filler[: filler.rfind(" ", 0, room - 3) + 1]
    lead += "Wien"[: room - len(lead) - 1].ljust(room - len(lead) - 1, "a") + " "
    summary = f"{lead}{head} {tail} {filler}"
    assert summary[:175].rsplit(" ", 1)[0].endswith(head)
    return summary


@pytest.mark.parametrize(
    ("head", "tail", "ending"),
    [
        # Real cuts since July (relocations, incident reasons, measures).
        ("bzw. N66 in Richtung", "Oper, Karlsplatz.", "bzw. N66 …"),
        ("Grund: Rettungseinsatz im", "Haltestellenbereich Praterstern.",
         "Grund: Rettungseinsatz …"),
        ("Linie 29A: Umleitung in", "beiden Richtungen.", "Linie 29A: Umleitung …"),
        ("Grinzinger Straße über", "Grinzinger Allee.", "Grinzinger Straße …"),
        # A label in front of the word goes with it.
        ("44B und N43. Maßnahmen: Linie", "44A in Richtung Dornbach.", "44B und N43. …"),
    ],
)
def test_cut_drops_the_leading_word_and_what_it_announces(
    head: str, tail: str, ending: str
) -> None:
    out = _truncate_summary_180(_cut_at(head, tail))
    assert out.endswith(ending), out
    assert len(out) <= 180


def test_numbers_in_front_of_the_dropped_word_stay() -> None:
    # The generic tail clean-up drops bare numbers; a list of lines in front
    # of a dropped "und" is whole and keeps them.
    out = _truncate_summary_180(_cut_at("auf die Linien U3, 5, 12, 46, 52 und", "49 aus."))
    assert out.endswith("U3, 5, 12, 46, 52 …"), out


def test_a_closed_bracket_in_front_of_the_dropped_word_stays() -> None:
    out = _truncate_summary_180(
        _cut_at("Michelbeuern-AKH U (Strecke Linien 42 und 9); Linie", "41: Umleitung.")
    )
    assert out.endswith("(Strecke Linien 42 und 9) …"), out


def test_a_sentence_in_front_of_the_dropped_word_keeps_its_last_word() -> None:
    out = _truncate_summary_180(_cut_at("Ersatz: U1, U2, U3, U4, D, 2 und 71. Die", "Haltestellen."))
    assert out.endswith("D, 2 und 71. …"), out


def test_a_cut_on_a_meaningful_word_is_unchanged() -> None:
    out = _truncate_summary_180(_cut_at("in Richtung Praterstern", "S U."))
    assert out.endswith("in Richtung Praterstern …"), out


# ---------------- a closed stop gets no arrow ----------------


def test_closed_stop_reads_closed_not_moved() -> None:
    desc = _format(
        "40A: Döblinger Friedhof, Felix-Dahn-Straße",
        _stop_notice(
            "Haltestellenauflassung der Linie 40A in Richtung Schottentor",
            "Döblinger Friedhof", "Hartäckerstraße 65", "Ersatzlos aufgelassen",
        ),
    )
    assert desc.startswith(
        "Hartäckerstraße 65: ersatzlos aufgelassen. Haltestellenauflassung der Linie 40A"
    )
    assert "→" not in desc


def test_moved_stop_keeps_its_arrow() -> None:
    desc = _format(
        "40A: Döblinger Friedhof",
        _stop_notice(
            "Haltestellenverlegung der Linie 40A in Richtung Schottentor",
            "Döblinger Friedhof", "Hartäckerstraße 65", "etwa 50 Meter in Richtung Gersthof",
        ),
    )
    assert desc.startswith("Hartäckerstraße 65 → etwa 50 Meter in Richtung Gersthof.")


def test_closed_stop_line_is_rendered_to_english_without_the_model() -> None:
    assert build_feed._render_wl_sentence(
        "Hartäckerstraße 65: ersatzlos aufgelassen.", "t", "Wiener Linien", None
    ) == "Hartäckerstraße 65: closed without replacement."


def test_relocation_to_another_stop_is_english_behind_the_arrow() -> None:
    assert build_feed._render_wl_sentence(
        "Hartäckerstraße 65 → zur Haltestelle Döblinger Friedhof.", "t", "Wiener Linien", None
    ) == "Hartäckerstraße 65 → to the stop Döblinger Friedhof."
