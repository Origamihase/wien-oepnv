"""The Stadt-Wien referral that cost a display slot and said nothing.

Every Stadt-Wien roadworks description that touches a bus or tram route
carries a sentence telling the reader to ask somebody else::

    Nähere Informationen zu den betroffenen öffentlichen Verkehrsmittel
    sind der Auskunft der Wiener Linien GmbH & Co KG zu entnehmen.

It is 130 characters. The feed summary budget is 180, and the summary is
built from the first sentence plus — only if the two together still fit —
the second. So whenever the referral came first, it *was* the summary:
the sentence that says what actually happens on the street never fit.

Published 2026-09-18, item 8 of ten in ``docs/feed.xml``::

    Ruthnergasse Kreuzung Justgasse
    Nähere Informationen zu den betroffenen öffentlichen Verkehrsmittel
    sind der Auskunft der Wiener Linien GmbH & Co KG zu entnehmen.
    [20.09.2026 – 16.10.2026]

The feed caps at ``MAX_ITEMS`` = 10 and rotates on EasySignage displays,
so that slot did not merely read badly — it displaced a disruption that
would have been shown instead.

Sizing, honestly: 5 of 22 cached Baustellen descriptions carry the phrase,
and only 2 of those carry it first. It is not systemic. It was live on a
public display, in the German feed, which ``AGENTS.md`` ranks first.

The phrase is not a fixed string. Three wordings appear in the cache and
they differ only in the middle, which is why the pattern anchors on the
two invariant ends rather than matching a literal.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

import pytest

from src import build_feed
from src.build_feed import _WL_REFERRAL_BOILERPLATE_RE
from src.feed_types import FeedItem

# The three wordings, verbatim from ``cache/baustellen_d438c3/events.json``.
_ZU_DEN = (
    "Nähere Informationen zu den betroffenen öffentlichen Verkehrsmittel "
    "sind der Auskunft der Wiener Linien GmbH & Co KG zu entnehmen."
)
_ZUR_UMLEITUNG = (
    "Nähere Informationen zur Umleitung sowie Haltestellenverlegung der "
    "betroffenen öffentlichen Verkehrsmittel sind der Auskunft der Wiener "
    "Linien GmbH & Co KG zu entnehmen."
)
_ZUR_VERLEGUNG = (
    "Nähere Informationen zur Haltestellenverlegungen der betroffenen "
    "öffentlichen Verkehrsmittel sind der Auskunft der Wiener Linien "
    "GmbH & Co KG zu entnehmen."
)


def _render(raw_desc: str, *, title: str = "Stub") -> str:
    """Return the published German summary for *raw_desc*."""
    item = cast(
        FeedItem,
        {
            "title": title,
            "description": raw_desc,
            "source": "Stadt Wien",
            "category": "Baustelle",
            "guid": "stub-1",
            "link": "https://example.test/",
        },
    )
    formatted = build_feed._format_item_content(
        item,
        ident="stub-1",
        starts_at=datetime(2026, 9, 20, tzinfo=UTC),
        ends_at=None,
    )
    return formatted.desc_text_truncated


# --------------------------------------------------------------------------
# The pattern
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("wording", "seen_in"),
    [
        (_ZU_DEN, "Ruthnergasse Kreuzung Justgasse"),
        (_ZUR_UMLEITUNG, "Erzherzog-Karl-Straße Kreuzung Polgarstraße bis Smolagasse"),
        (_ZUR_VERLEGUNG, "Frauenstiftgasse von Baumergasse bis Brünner Straße"),
    ],
)
def test_every_live_wording_is_matched_whole(wording: str, seen_in: str) -> None:
    """*seen_in* names the cached item the wording was taken from."""
    match = _WL_REFERRAL_BOILERPLATE_RE.search(wording)
    assert match is not None, f"not matched ({seen_in})"
    assert match.group(0) == wording, f"matched only part of it ({seen_in})"


@pytest.mark.parametrize(
    "keeper",
    [
        # A referral to somewhere the reader can actually go.
        "Nähere Informationen finden Sie auf www.wien.gv.at.",
        "Nähere Informationen zur Umleitung entnehmen Sie dem Plan.",
        # Same subject, but it states something instead of deferring.
        "Die Fahrpläne der betroffenen öffentlichen Verkehrsmittel ändern sich.",
        # Wiener Linien as the actor of a real disruption, not as a hotline.
        "Die Wiener Linien führen Gleisbauarbeiten durch.",
    ],
)
def test_sentences_that_carry_information_are_not_touched(keeper: str) -> None:
    assert _WL_REFERRAL_BOILERPLATE_RE.search(keeper) is None


def test_the_match_cannot_run_across_a_sentence_boundary() -> None:
    """The variable middle is a dot-free run, so a runaway match is bounded.

    Without that, an opening ``Nähere Informationen zu …`` could reach
    forward past its own full stop into a later sentence's tail and delete
    everything in between.
    """
    text = (
        "Nähere Informationen zur Umleitung folgen. Die Fahrpläne der "
        "betroffenen öffentlichen Verkehrsmittel sind der Auskunft der "
        "Wiener Linien GmbH & Co KG zu entnehmen."
    )
    match = _WL_REFERRAL_BOILERPLATE_RE.search(text)
    assert match is None or "folgen." not in match.group(0)


# --------------------------------------------------------------------------
# What the feed shows
# --------------------------------------------------------------------------


def test_the_published_ruthnergasse_summary_says_what_happens() -> None:
    """The exact cached description behind the 2026-09-18 feed item."""
    out = _render(
        _ZU_DEN + " ImBaustellenbereich wird ein Fahrstreifen freigehalten und "
        "der Verkehr wechselweise mittels Personal während der Spitzenzeiten "
        "oder Verkehrszeichen durchgeschleust. Beginn: 20.09.2026 00:00 Uhr "
        "Geplant bis: 16.10.2026 00:00 Uhr Maßnahme: Straßenbau Bezirk: 21",
        title="Ruthnergasse Kreuzung Justgasse",
    )
    assert "Auskunft der Wiener Linien" not in out
    assert out.startswith("Im Baustellenbereich wird ein Fahrstreifen freigehalten")


def test_the_published_frauenstiftgasse_summary_says_what_happens() -> None:
    out = _render(
        _ZUR_VERLEGUNG + " Bauphase 1 (14.07.2026 bis 20.07.2026): Ein "
        "Fahrstreifen je Fahrtrichtung bleibt aufrecht. DerFußgängerverkehr "
        "kann aufrecht gehalten werden.",
        title="Frauenstiftgasse von Baumergasse bis Brünner Straße",
    )
    assert "Auskunft der Wiener Linien" not in out
    assert out.startswith("Bauphase 1")


def test_a_referral_in_second_place_frees_the_slot_behind_it() -> None:
    """Not only the leading case: the referral ate sentence two as well.

    Cached item "Erzherzog-Karl-Straße Kreuzung Polgarstraße bis
    Smolagasse" — pre-fix the summary stopped after its first sentence,
    because the second was the referral and the third never got a turn.
    """
    out = _render(
        "Die Zufahrt zur Sport & Fun Halle Donaustadt ist möglich. "
        + _ZUR_UMLEITUNG
        + " Die Arbeiten werden in zwei Bauabschnitten durchgeführt.",
        title="Erzherzog-Karl-Straße Kreuzung Polgarstraße bis Smolagasse",
    )
    assert "Auskunft der Wiener Linien" not in out
    assert "Die Zufahrt zur Sport & Fun Halle Donaustadt ist möglich." in out
    assert "Die Arbeiten werden in zwei Bauabschnitten durchgeführt." in out
    # The seam where the referral was removed must not lose or double a space.
    assert "möglich. Die Arbeiten" in out


def test_a_description_that_is_only_the_referral_keeps_it() -> None:
    """A useless sentence beats a headline with nothing underneath it.

    On a Full-HD display an empty description is a visibly broken item,
    so the removal stands down when it would empty the summary.
    """
    out = _render(_ZU_DEN, title="Irgendeine Gasse")
    assert "Auskunft der Wiener Linien" in out


def test_descriptions_without_the_referral_are_unchanged() -> None:
    raw = "Die Fahrbahn wird in beide Richtungen gesperrt."
    assert raw in _render(raw)
