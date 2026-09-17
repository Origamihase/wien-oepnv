"""WL publishes one disruption twice; only the informative copy may reach the feed.

``_fetch_traffic_infos`` asks the OGD endpoint for two feeds at once::

    params = [("name", "stoerunglang"), ("name", "stoerungkurz")]

``stoerunglang`` carries the written-out notice, ``stoerungkurz`` the short
texts of the platform displays — and the short feed emits one entry per
branch. Live regression, 2026-09-17, line 49::

    49: Gleisschaden                                <- stoerunglang
    49: Gleisschaden Betrieb ab Hütteldorfer Straße  <- stoerungkurz
    49: Gleisschaden Betrieb ab Urban-Loritz-Platz   <- stoerungkurz

The two short ones reached the feed with NO body at all. Their description
(``"Gleisschaden\\nBetrieb ab Hütteldorfer Straße >"``) merely restates their
own title, so ``_summary_duplicates_title`` in ``build_feed`` empties it — a
headline over nothing. Two of the ten slots in the German feed carried zero
information while the full notice stood right beside them saying everything:
"Kein Betrieb zwischen Hütteldorfer Straße U und Urban-Loritz-Platz. …"

The bucketing over ``topic_key`` cannot join them: without a hit in
``TITLE_TOPIC_TOKENS`` the key falls back to the whole title core, which
differs per branch. Adding ``gleisschaden`` to that set would have been the
third round of the same game (``demonstration`` and ``feuerwehreinsatz`` were
added for the two before it), and the next cause word — Oberleitungsschaden,
Weichenstörung, Fahrzeuggebrechen — would start round four.

The rule under test needs no cause word. It asks the same question twice:
does this text say anything that one does not already say?

1. the description adds nothing to its OWN title  -> a bare headline;
2. its title already appears in full inside another notice for the same
   lines, same category, overlapping in time     -> that one says it all.

Only when BOTH hold does the headline move into the other notice. That
asymmetry is what keeps the rule honest, and both halves are pinned below:
the 49A/50B street pair fails (1), the solo 12A ticker fails (2), and each
must survive.
"""

from __future__ import annotations

from datetime import UTC, datetime, tzinfo
from typing import Any

import pytest

from src.providers import wl_fetch

# Absolute timestamps, verbatim from the live incident — that is what makes
# them worth citing. ``fetch_events`` filters through ``_is_active(start,
# end, datetime.now(UTC))``, so the clock has to stand still or these windows
# expire during the day and the tests start failing with no code change (the
# sibling suite in ``test_wl_dedupe_distinct_disruptions.py`` learned that the
# hard way, CI run 34706586476).
#
# Frozen to 2026-09-17 12:00 Vienna: after every ``start`` and before every
# ``end`` in this file.
class _FrozenDatetime(datetime):
    """``datetime`` with a stopped ``now()``; everything else unchanged.

    ``now`` returns the subclass rather than ``datetime`` — ``datetime.now``
    is typed ``Self``, and a wider return type breaks the Liskov contract
    (mypy ``[override]``).
    """

    @classmethod
    def now(cls, tz: tzinfo | None = None) -> _FrozenDatetime:
        return _FROZEN_NOW if tz is None else _FROZEN_NOW.astimezone(tz)


_FROZEN_NOW = _FrozenDatetime(2026, 9, 17, 10, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _frozen_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Holds ``fetch_events`` at 2026-09-17 12:00 Vienna time."""
    monkeypatch.setattr(wl_fetch, "datetime", _FrozenDatetime, raising=True)


@pytest.fixture(autouse=True)
def _no_news(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wl_fetch, "_fetch_news", lambda **_kwargs: [], raising=True)


def _run(monkeypatch: pytest.MonkeyPatch, infos: list[dict[str, Any]]) -> list[Any]:
    monkeypatch.setattr(
        wl_fetch, "_fetch_traffic_infos", lambda **_kwargs: infos, raising=True
    )
    return wl_fetch.fetch_events()


def _info(
    title: str,
    description: str,
    *,
    lines: list[str],
    start: str = "2026-09-17T08:14:00+02:00",
    end: str = "2026-09-17T23:59:59+02:00",
) -> dict[str, Any]:
    """One trafficInfo entry in the shape ``_fetch_traffic_infos`` returns."""
    return {
        "title": title,
        "description": description,
        "relatedLines": lines,
        "relatedStops": [],
        "time": {"start": start, "end": end},
        "attributes": {},
    }


# ---- the live payloads, verbatim from cache/wl_9d709a/events.json ----------

_NOTICE = _info(
    "49: Gleisschaden",
    "Linie 49: Kein Betrieb zwischen Hütteldorfer Straße U und "
    "Urban-Loritz-Platz. Die Züge fahren ab Hütteldorfer Straße bis "
    "Joachimsthalerplatz. Die Züge fahren ab Urban Loritz Platz zum "
    "Westbahnhof. Weichen Sie ersatzweise auf die Linien U3, 9, 52, 12A und "
    "48A aus. Grund: Gleisschaden im Bereich Märzstraße 62.",
    lines=["49"],
    end="2026-09-18T00:55:00+02:00",
)
_TICKER_WEST = _info(
    "49: Gleisschaden Betrieb ab Hütteldorfer Straße",
    "Gleisschaden\nBetrieb ab Hütteldorfer Straße >",
    lines=["49"],
    start="2026-09-17T08:14:13+02:00",
)
_TICKER_EAST = _info(
    "49: Gleisschaden Betrieb ab Urban-Loritz-Platz",
    "Gleisschaden\nBetrieb ab Urban-Loritz-Platz",
    lines=["49"],
    start="2026-09-17T08:18:13+02:00",
)


def test_display_tickers_fold_into_the_full_notice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The live case: three upstream entries, one incident, one feed item."""
    events = _run(monkeypatch, [_NOTICE, _TICKER_WEST, _TICKER_EAST])

    assert len(events) == 1, [e["title"] for e in events]
    (item,) = events
    assert item["title"] == "49: Gleisschaden"
    assert "Kein Betrieb zwischen Hütteldorfer Straße U" in item["description"]
    # The surviving item is the one that says something. A fold that kept a
    # ticker instead would still show one item and pass a bare count check.
    assert "Joachimsthalerplatz" in item["description"]


def test_fold_is_independent_of_the_upstream_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Whichever entry arrives first, the informative one must win.

    The WL feed order is not contractual. Pinned because a fold driven by
    dict insertion order would pass the test above and silently keep a
    blank headline whenever the short feed happened to answer first.
    """
    for order in (
        [_TICKER_WEST, _TICKER_EAST, _NOTICE],
        [_TICKER_EAST, _NOTICE, _TICKER_WEST],
    ):
        events = _run(monkeypatch, list(order))
        assert [e["title"] for e in events] == ["49: Gleisschaden"]


def test_folded_ticker_does_not_narrow_the_notice_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The notice runs past midnight; the tickers stop at 23:59:59.

    The full notice is the authoritative one, so absorbing a shorter
    display text must not cut its window back to the end of the day.
    """
    events = _run(monkeypatch, [_NOTICE, _TICKER_WEST, _TICKER_EAST])

    (item,) = events
    assert item["ends_at"] == datetime.fromisoformat("2026-09-18T00:55:00+02:00")


def test_stops_from_the_tickers_reach_the_surviving_item(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fold is a merge, so the branches' stops count towards the notice.

    The live tickers carried no ``relatedStops``, so today's feed is
    unaffected — but when they do, section D turns the union into the
    ``(n Halte)`` suffix exactly as it does for a merge inside one bucket.
    Pinned so the changed title reads as the intended consequence it is,
    and not as a surprise the next time WL fills the field.
    """
    west = dict(_TICKER_WEST, relatedStops=[{"name": "Hütteldorfer Straße"}])
    east = dict(_TICKER_EAST, relatedStops=[{"name": "Urban-Loritz-Platz"}])

    events = _run(monkeypatch, [_NOTICE, west, east])

    (item,) = events
    assert item["title"] == "49: Gleisschaden (2 Halte)"
    assert "Kein Betrieb zwischen Hütteldorfer Straße U" in item["description"]


def test_a_headline_without_a_notice_beside_it_survives(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Condition (2): nothing else covers it, so it is the only information.

    ``12A: Betrieb ab Johnstraße U`` is a display ticker like the line-49
    ones, but no written-out notice accompanies it. Dropping it would leave
    the line with no entry at all — the rule must fold, never simply delete.
    """
    solo = _info(
        "12A: Betrieb ab Johnstraße U",
        "Gleisbauarbeiten\nBetrieb ab Johnstraße U",
        lines=["12A"],
        start="2026-09-16T04:00:00+02:00",
    )
    second = _info(
        "12A: Betrieb ab Schweglerstraße 19-21",
        "Gleisbauarbeiten\nBetrieb ab Schweglerstraße 19-21",
        lines=["12A"],
        start="2026-09-16T04:00:00+02:00",
    )

    events = _run(monkeypatch, [solo, second])

    titles = sorted(e["title"] for e in events)
    assert titles == [
        "12A: Betrieb ab Johnstraße U",
        "12A: Betrieb ab Schweglerstraße 19-21",
    ], "two bare headlines cannot cover each other — neither says more"


def test_distinct_notices_with_their_own_text_never_fold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Condition (1): two streets, one line pair, both carry real text.

    This is the pair the earlier blanket ``_identity`` dedupe destroyed (see
    ``test_wl_dedupe_distinct_disruptions.py``). Each description says far
    more than its own title, so neither is a headline and the fold never
    starts — no cause-word list required to tell them apart.
    """
    mondweg = _info(
        "49A/50B: Mondweg",
        "<p>Haltestellenverlegung der Linien 49A und 50B in Richtung "
        "Wolfersberg</p> <p>Haltestelle: Mondweg</p> <p>Von: Anzbachgasse "
        "130</p> <p>Grund: Grabungsarbeiten</p>",
        lines=["49A", "50B"],
        start="2026-09-16T07:00:00+02:00",
    )
    huettergasse = _info(
        "49A/50B: Hüttergasse",
        "<p>Haltestellenverlegung der Linien 49A und 50B in Richtung "
        "Wolfersberg</p> <p>Haltestelle: Hüttergasse</p> <p>Von: "
        "Anzengruberstraße 77a</p> <p>Grund: Rohrleitungsarbeiten</p>",
        lines=["49A", "50B"],
        start="2026-09-16T08:00:00+02:00",
    )

    events = _run(monkeypatch, [mondweg, huettergasse])

    assert sorted(e["title"] for e in events) == [
        "49A/50B: Hüttergasse",
        "49A/50B: Mondweg",
    ]


def test_a_notice_that_does_not_cover_the_headline_leaves_it_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Condition (2) is the whole safeguard against a blind merge.

    Two disruptions can share a line and a day and still be unrelated. The
    notice below is about an overhead-line fault at the other end of the
    route and names none of the ticker's words, so the ticker is still the
    only source for its own branch and has to stay.
    """
    unrelated = _info(
        "49: Oberleitungsschaden",
        "Linie 49: Unregelmäßige Intervalle in beiden Richtungen. Grund: "
        "Oberleitungsschaden im Bereich Dr.-Karl-Renner-Ring.",
        lines=["49"],
    )

    events = _run(monkeypatch, [unrelated, _TICKER_WEST])

    assert sorted(e["title"] for e in events) == [
        "49: Gleisschaden Betrieb ab Hütteldorfer Straße",
        "49: Oberleitungsschaden",
    ]


def test_a_hinweis_never_absorbs_a_stoerung_headline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Categories are kept apart, even when the texts line up perfectly.

    The Hinweis below repeats every word of the ticker's title, so only the
    category check separates them. Folding a live disruption into a planned
    notice would file it under the wrong heading and, downstream, under the
    wrong ranking — the feed treats the two categories differently.
    """
    hinweis = {
        "title": "49: Ersatzverkehr",
        "description": "Grund: Gleisschaden. Betrieb ab Hütteldorfer Straße "
                       "eingestellt. Ersatzverkehr mit Bussen eingerichtet.",
        "relatedLines": ["49"],
        "relatedStops": [],
        "time": {"start": "2026-09-17T08:00:00+02:00",
                 "end": "2026-09-18T00:55:00+02:00"},
        "attributes": {},
    }
    monkeypatch.setattr(wl_fetch, "_fetch_news", lambda **_kwargs: [hinweis], raising=True)

    events = _run(monkeypatch, [_TICKER_WEST])

    by_category = {e["category"]: e["title"] for e in events}
    assert by_category == {
        "Störung": "49: Gleisschaden Betrieb ab Hütteldorfer Straße",
        "Hinweis": "49: Ersatzverkehr",
    }


def test_a_headline_is_not_absorbed_by_an_unrelated_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A full notice on another line must not swallow the ticker.

    Constructed so the notice's text happens to contain every word of the
    ticker's title: only the line-set check stands between them.
    """
    other_line = _info(
        "13A: Gleisschaden",
        "Linie 13A: Gleisschaden, Betrieb ab Hütteldorfer Straße bis auf "
        "Weiteres eingestellt. Grund: Gleisschaden im Bereich Märzstraße.",
        lines=["13A"],
    )

    events = _run(monkeypatch, [other_line, _TICKER_WEST])

    assert sorted(e["title"] for e in events) == [
        "13A: Gleisschaden",
        "49: Gleisschaden Betrieb ab Hütteldorfer Straße",
    ]


# ---- the guards end-to-end tests cannot reach ------------------------------
#
# ``_is_active`` drops anything starting after ``now`` and anything that
# ended before it, so two entries that survive ``fetch_events`` both span the
# current moment and therefore always overlap. The time guard is only
# reachable one level down, on the bucket dicts themselves.


def _bucket(
    title: str,
    desc: str,
    *,
    lines: list[str],
    starts: datetime,
    ends: datetime,
    category: str = "Störung",
) -> dict[str, Any]:
    """A bucket in the shape section C of ``fetch_events`` builds."""
    return {
        "category": category,
        "title": title,
        "desc_base": desc,
        "lines_pairs": [(ln.casefold(), ln) for ln in lines],
        "stop_names": set(),
        "extras": [],
        "pubDate": starts,
        "starts_at": starts,
        "ends_at": ends,
    }


_SEPTEMBER = (
    datetime(2026, 9, 17, 8, 0, tzinfo=UTC),
    datetime(2026, 9, 17, 22, 0, tzinfo=UTC),
)
_OCTOBER = (
    datetime(2026, 10, 20, 8, 0, tzinfo=UTC),
    datetime(2026, 10, 20, 22, 0, tzinfo=UTC),
)


def test_fold_requires_overlapping_time() -> None:
    """A notice from another month describes another incident.

    Both texts match word for word — only the calendar separates them.
    Without the overlap check an advance notice for October would absorb
    today's disruption and today's entry would vanish from the feed.
    """
    ticker = _bucket(
        "49: Gleisschaden Betrieb ab Hütteldorfer Straße",
        "Gleisschaden Betrieb ab Hütteldorfer Straße",
        lines=["49"],
        starts=_SEPTEMBER[0],
        ends=_SEPTEMBER[1],
    )
    far_off = _bucket(
        "49: Gleisschaden",
        "Kein Betrieb, Gleisschaden, Betrieb ab Hütteldorfer Straße "
        "eingestellt. Ersatzverkehr eingerichtet.",
        lines=["49"],
        starts=_OCTOBER[0],
        ends=_OCTOBER[1],
    )
    buckets = {"ticker": ticker, "notice": far_off}

    wl_fetch._fold_display_tickers(buckets)

    assert set(buckets) == {"ticker", "notice"}

    # Same pair, now overlapping: the fold does happen, so the test above
    # fails for the time check and not because the texts stopped matching.
    overlapping = {
        "ticker": dict(ticker),
        "notice": _bucket(
            far_off["title"],
            far_off["desc_base"],
            lines=["49"],
            starts=_SEPTEMBER[0],
            ends=_SEPTEMBER[1],
        ),
    }
    wl_fetch._fold_display_tickers(overlapping)
    assert set(overlapping) == {"notice"}


def test_fold_does_not_depend_on_the_line_number_in_the_body() -> None:
    """The line belongs to the line check, not to the text comparison.

    The live notice happens to open with "Linie 49: Kein Betrieb …", so an
    implementation that compared the ticker's whole title — line prefix
    included — against the notice body passed the end-to-end case by
    coincidence. WL does not always repeat the number in prose, and the
    first draft of this rule left the duplicate standing whenever it did
    not. Which lines are meant is already settled by the line-set
    comparison; repeating that question in the prose only adds a way to
    get it wrong.
    """
    ticker = _bucket(
        "49: Gleisschaden Betrieb ab Hütteldorfer Straße",
        "Gleisschaden Betrieb ab Hütteldorfer Straße",
        lines=["49"],
        starts=_SEPTEMBER[0],
        ends=_SEPTEMBER[1],
    )
    # Deliberately never names the line in the body.
    notice = _bucket(
        "49: Gleisschaden",
        "Kein Betrieb zwischen Hütteldorfer Straße und Urban-Loritz-Platz. "
        "Grund: Gleisschaden. Betrieb ab Hütteldorfer Straße eingestellt.",
        lines=["49"],
        starts=_SEPTEMBER[0],
        ends=_SEPTEMBER[1],
    )
    assert "49" not in notice["desc_base"]
    buckets = {"ticker": ticker, "notice": notice}

    wl_fetch._fold_display_tickers(buckets)

    assert set(buckets) == {"notice"}


def test_a_headline_never_absorbs_another_headline() -> None:
    """Two contentless entries cannot cover each other.

    Without the "target must say more than its own title" check the pair
    below would collapse to whichever the iteration reached first, and the
    surviving item would still be blank — a silent loss dressed as a merge.
    """
    first = _bucket(
        "49: Gleisschaden Betrieb ab Hütteldorfer Straße",
        "Gleisschaden Betrieb ab Hütteldorfer Straße",
        lines=["49"],
        starts=_SEPTEMBER[0],
        ends=_SEPTEMBER[1],
    )
    second = _bucket(
        "49: Gleisschaden Betrieb",
        "Gleisschaden Betrieb",
        lines=["49"],
        starts=_SEPTEMBER[0],
        ends=_SEPTEMBER[1],
    )
    buckets = {"a": first, "b": second}

    wl_fetch._fold_display_tickers(buckets)

    assert set(buckets) == {"a", "b"}


def test_folding_carries_stops_and_extras_over() -> None:
    """A fold is a merge, not a delete — whatever the ticker knew is kept."""
    ticker = _bucket(
        "49: Gleisschaden Betrieb ab Hütteldorfer Straße",
        "Gleisschaden Betrieb ab Hütteldorfer Straße",
        lines=["49"],
        starts=datetime(2026, 9, 17, 7, 0, tzinfo=UTC),
        ends=datetime(2026, 9, 17, 23, 0, tzinfo=UTC),
    )
    ticker["stop_names"] = {"Hütteldorfer Straße"}
    ticker["extras"] = ["Reason: Gleisschaden"]
    notice = _bucket(
        "49: Gleisschaden",
        "Kein Betrieb zwischen Hütteldorfer Straße und Urban-Loritz-Platz. "
        "Grund: Gleisschaden. Betrieb ab Hütteldorfer Straße eingestellt.",
        lines=["49"],
        starts=_SEPTEMBER[0],
        ends=_SEPTEMBER[1],
    )
    buckets = {"ticker": ticker, "notice": notice}

    wl_fetch._fold_display_tickers(buckets)

    assert set(buckets) == {"notice"}
    assert notice["stop_names"] == {"Hütteldorfer Straße"}
    assert notice["extras"] == ["Reason: Gleisschaden"]
    assert notice["pubDate"] == datetime(2026, 9, 17, 7, 0, tzinfo=UTC), (
        "the earliest sighting dates the incident"
    )
    assert notice["ends_at"] == datetime(2026, 9, 17, 23, 0, tzinfo=UTC), (
        "the ticker outlasts the notice here, so the window widens"
    )


def test_an_open_ended_ticker_does_not_blur_a_known_end() -> None:
    """``None`` means "not stated", not "runs forever".

    The notice carries a concrete end; the ticker does not. Treating the
    missing value as an open end (the shape section C uses when merging
    within one bucket) would erase the one hard fact of the pair.
    """
    ticker = _bucket(
        "49: Gleisschaden Betrieb",
        "Gleisschaden Betrieb",
        lines=["49"],
        starts=_SEPTEMBER[0],
        ends=_SEPTEMBER[1],
    )
    ticker["ends_at"] = None
    notice = _bucket(
        "49: Gleisschaden",
        "Kein Betrieb, Gleisschaden im Bereich Märzstraße, Betrieb "
        "eingestellt bis auf Weiteres.",
        lines=["49"],
        starts=_SEPTEMBER[0],
        ends=_SEPTEMBER[1],
    )
    buckets = {"ticker": ticker, "notice": notice}

    wl_fetch._fold_display_tickers(buckets)

    assert set(buckets) == {"notice"}
    assert notice["ends_at"] == _SEPTEMBER[1]


@pytest.mark.parametrize(
    ("text", "beyond", "expected"),
    [
        ("Gleisschaden\nBetrieb ab Hütteldorfer Straße >",
         "49: Gleisschaden Betrieb ab Hütteldorfer Straße", True),
        # WL writes the same place both ways; punctuation must not decide.
        ("Urban-Loritz-Platz",
         "Die Züge fahren ab Urban Loritz Platz zum Westbahnhof", True),
        ("Gleisbauarbeiten Betrieb ab Johnstraße U",
         "12A: Betrieb ab Johnstraße U", False),
        ("Mondweg", "Haltestelle Hüttergasse", False),
        # Empty text is missing information, never redundancy.
        ("", "irgendein Text", False),
        ("   \n  ", "irgendein Text", False),
    ],
)
def test_says_nothing_new(text: str, beyond: str, expected: bool) -> None:
    """The one predicate both halves of the rule are built from."""
    assert wl_fetch._says_nothing_new(text, beyond=beyond) is expected
