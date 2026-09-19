"""One event, all ten slots — the notice that explained it was on place 12.

On 2026-09-19 Wiener Linien announced a demonstration on the Ring as ONE
full notice for seven lines (``1/2/2A/3A/4A/71/D: Demonstration am
19.09.2026``, category ``Hinweis``, one measure per line) and, on the day,
one display ticker per line (``1: Demonstration Betrieb ab Hintere
Zollamtsstraße``, category ``Störung``, WL's boilerplate underneath). The
published feed carried ten tickers of that one event and not the notice.

Three gates said no, each correct on its own:

* the ticker's line set was not EQUAL to the notice's — it is a subset;
* the categories differed — ``Störung`` versus ``Hinweis``;
* the boilerplate „Nach einer Fahrtbehinderung kommt es zu
  unterschiedlichen Intervallen." counted as content.

All three are relaxed here, and nothing else: the word check still
requires every word of the ticker's title to appear in the notice, with
HTML entities resolved because the notice sits in the bucket as HTML.
Payloads below are the cache's, shortened.
"""

from __future__ import annotations

from datetime import UTC, datetime, tzinfo
from typing import Any

import pytest

from src.providers import wl_fetch
from src.providers.wl_fetch import (
    _categories_compatible,
    _content_tokens,
    _is_headline_only,
)


class _FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz: tzinfo | None = None) -> _FrozenDatetime:
        return _FROZEN_NOW if tz is None else _FROZEN_NOW.astimezone(tz)


_FROZEN_NOW = _FrozenDatetime(2026, 9, 19, 15, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _frozen_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Holds ``fetch_events`` at 2026-09-19 17:00 Vienna time."""
    monkeypatch.setattr(wl_fetch, "datetime", _FrozenDatetime, raising=True)


def _run(
    monkeypatch: pytest.MonkeyPatch,
    tickers: list[dict[str, Any]],
    notices: list[dict[str, Any]],
) -> list[Any]:
    monkeypatch.setattr(wl_fetch, "_fetch_traffic_infos", lambda **_kwargs: tickers, raising=True)
    monkeypatch.setattr(wl_fetch, "_fetch_news", lambda **_kwargs: notices, raising=True)
    return wl_fetch.fetch_events()


def _entry(
    title: str,
    description: str,
    *,
    lines: list[str],
    start: str,
    end: str,
) -> dict[str, Any]:
    return {
        "title": title,
        "description": description,
        "relatedLines": lines,
        "relatedStops": [],
        "time": {"start": start, "end": end},
        "attributes": {},
    }


_BOILERPLATE = "Nach einer Fahrtbehinderung kommt es zu unterschiedlichen Intervallen."

_NOTICE = _entry(
    "Demonstration am 19.09.2026",
    "<h2>Demonstration</h2> <p>Wegen einer Demonstration im Bereich "
    "Schwarzenbergplatz und Ring kommt es zu folgenden "
    "Verkehrsma&szlig;nahmen.</p> <p><strong>Zeitraum:</strong><br />"
    "Samstag, 19. September 2026, ab ca. 12:00 Uhr bis ca. 21:30 Uhr.</p> "
    "<p><strong>Ma&szlig;nahmen:</strong><br /><strong>Linie D:</strong> "
    "Derzeit kein Betrieb zwischen B&ouml;rse und Quartier Belvedere.<br />"
    "<strong>Linie 1:</strong> Umleitung in beiden Richtungen zwischen "
    "Kliebergasse und Hintere Zollamtsstra&szlig;e &uuml;ber "
    "Landstra&szlig;e S U und Hauptbahnhof S U (Strecke Linien O und 18)."
    "<br /><strong>Linie 2:</strong> Kein Betrieb zwischen Parlament, U "
    "Volkstheater und Heinestra&szlig;e.<br /><strong>Linie 71:</strong> "
    "Betrieb nur zwischen Kaiserebersdorf und Schlachthausgasse.<br />"
    "<strong>Linie 2A:</strong> Kein Betrieb m&ouml;glich.<br />"
    "<strong>Linie 3A:</strong> Betrieb nur zwischen Oper, Karlsplatz und "
    "Hoher Markt.<br /><strong>Linie 4A:</strong> Betrieb nur zwischen "
    "Lisztstra&szlig;e und Wittelsbachstra&szlig;e.</p>",
    lines=["1", "2", "2A", "3A", "4A", "71", "D"],
    start="2026-09-18T00:00:00+02:00",
    end="2026-09-19T21:30:00+02:00",
)


def _ticker(title: str, line: str, description: str | None = None) -> dict[str, Any]:
    return _entry(
        title,
        f"Linie {line}: {_BOILERPLATE}" if description is None else description,
        lines=[line],
        start="2026-09-19T13:23:49+02:00",
        end="2026-09-19T23:55:00+02:00",
    )


_TICKER_1 = _ticker("Demonstration Betrieb ab Hintere Zollamtsstraße", "1")
_TICKER_2A = _ticker("Demonstration Kein Betrieb", "2A")
# 31 is not one of the notice's lines.
_TICKER_31 = _ticker("Demonstration Betrieb ab Wallensteinstraße", "31")
# ``Steig A`` is not in the notice's text.
_TICKER_2_STEIG = _ticker("Demonstration Züge halten Steig A", "2")
# A headline of its own, on a notice line, with words the notice lacks.
_TICKER_LINIE_O = _ticker("Züge halten bei Linie O", "1", "Züge halten bei Linie O")


# ---------------- the published day, end to end ----------------


def test_tickers_on_the_notice_lines_fold_into_the_notice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events = _run(
        monkeypatch,
        [_TICKER_1, _TICKER_2A, _TICKER_31, _TICKER_2_STEIG, _TICKER_LINIE_O],
        [_NOTICE],
    )

    assert sorted(e["title"] for e in events) == [
        "1/2/2A/3A/4A/71/D: Demonstration am 19.09.2026",
        "1: Züge halten bei Linie O",
        "2: Demonstration Züge halten Steig A",
        "31: Demonstration Betrieb ab Wallensteinstraße",
    ]


def test_the_notice_keeps_its_category_and_widens_to_the_tickers_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events = _run(monkeypatch, [_TICKER_1], [_NOTICE])

    assert len(events) == 1
    notice = events[0]
    assert notice["category"] == "Hinweis"
    assert notice["ends_at"].isoformat() == "2026-09-19T23:55:00+02:00"


def test_fold_is_independent_of_the_upstream_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    forward = _run(monkeypatch, [_TICKER_1, _TICKER_2A], [_NOTICE])
    backward = _run(monkeypatch, [_TICKER_2A, _TICKER_1], [_NOTICE])

    assert [e["title"] for e in forward] == [e["title"] for e in backward]
    assert len(forward) == 1


# ---------------- what still must not fold ----------------


def test_a_line_outside_the_notice_keeps_its_ticker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events = _run(monkeypatch, [_TICKER_31], [_NOTICE])

    assert sorted(e["title"] for e in events) == [
        "1/2/2A/3A/4A/71/D: Demonstration am 19.09.2026",
        "31: Demonstration Betrieb ab Wallensteinstraße",
    ]


def test_a_ticker_with_words_the_notice_lacks_stays(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Subset lines are necessary, never sufficient — the words decide."""
    events = _run(monkeypatch, [_TICKER_2_STEIG, _TICKER_LINIE_O], [_NOTICE])

    assert len(events) == 3


def test_a_ticker_with_its_own_text_stays(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only the boilerplate counts as nothing; a reason of its own is content."""
    ticker = _ticker(
        "Demonstration Kein Betrieb",
        "2A",
        "Linie 2A: Kein Betrieb. Grund: Polizeieinsatz im Bereich Oper.",
    )

    events = _run(monkeypatch, [ticker], [_NOTICE])

    assert len(events) == 2


def test_a_hinweis_headline_never_folds_into_a_stoerung_notice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The category gate opens one way only.

    The notice below covers every word of the headline's title, so only
    the direction of the categories keeps them apart. (``Umleitung`` is
    in the title because a news item has to pass ``KW_RESTRICTION``.)
    """
    stoerung_notice = _entry(
        "Umleitung",
        "Linie 49: Umleitung ab Hütteldorfer Straße U über Märzstraße bis "
        "Urban-Loritz-Platz. Grund: Gleisschaden im Bereich Märzstraße 62.",
        lines=["49"],
        start="2026-09-19T08:00:00+02:00",
        end="2026-09-19T23:55:00+02:00",
    )
    hinweis_headline = _entry(
        "Umleitung ab Hütteldorfer Straße",
        "Umleitung ab Hütteldorfer Straße",
        lines=["49"],
        start="2026-09-19T08:00:00+02:00",
        end="2026-09-19T23:55:00+02:00",
    )

    events = _run(monkeypatch, [stoerung_notice], [hinweis_headline])

    assert sorted((e["category"], e["title"]) for e in events) == [
        ("Hinweis", "49: Umleitung ab Hütteldorfer Straße"),
        ("Störung", "49: Umleitung"),
    ]


# ---------------- the three relaxed gates, one by one ----------------


@pytest.mark.parametrize(
    ("src", "cand", "expected"),
    [
        ("Störung", "Störung", True),
        ("Hinweis", "Hinweis", True),
        ("Störung", "Hinweis", True),
        ("Hinweis", "Störung", False),
        ("Störung", "Baustelle", False),
    ],
)
def test_categories_compatible(src: str, cand: str, expected: bool) -> None:
    assert _categories_compatible(src, cand) is expected


def test_the_boilerplate_counts_as_nothing() -> None:
    bucket = {
        "title": "Demonstration Betrieb ab Hintere Zollamtsstraße",
        "desc_base": f"Linie 1: {_BOILERPLATE}",
        "lines_pairs": [("1", "1")],
    }
    assert _is_headline_only(bucket)

    bucket["desc_base"] = "Linie 1: Kein Betrieb. Grund: Polizeieinsatz."
    assert not _is_headline_only(bucket)


def test_entities_and_tags_are_resolved_before_the_word_check() -> None:
    tokens = _content_tokens("<p><strong>Linie 1:</strong> Umleitung bis Hintere Zollamtsstra&szlig;e &uuml;ber Landstra&szlig;e S U.</p>")

    # ``casefold`` spells ß as ss on both sides of the comparison, so the
    # entity has to become that same ``ss`` — never a stray ``szlig``.
    assert "zollamtsstrasse" in tokens
    assert "zollamtsstrasse" in _content_tokens("Hintere Zollamtsstraße")
    assert "über" in tokens
    assert "strong" not in tokens
    assert "szlig" not in tokens
