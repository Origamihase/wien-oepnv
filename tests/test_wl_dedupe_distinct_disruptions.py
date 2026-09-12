"""Distinct WL disruptions on one line on one day must all reach the feed.

Live regression, 2026-09-12 (``python -m src.cli feed lint``)::

    Nach Deduplizierung: 79 (entfernte Duplikate: 4)

    - 3x Schluessel wl|stoerung|L=44|D=2026-09-11:
        44: Veranstaltung Zuege halten Rosensteingasse ...
        44: Veranstaltung Betrieb ab Johann-Nepomuk-Berger-Platz
        44: Fahrtbehinderung Veranstaltung
    - 2x Schluessel wl|hinweis|L=49A,50B|D=2026-08-25:
        49A/50B: Mondweg
        49A/50B: Huettergasse

Four of 83 items were dropped as "duplicates" although they describe
different disruptions at different places. ``_wl_identity`` folded the
``topic_key`` into the key only when the line set or the start date was
missing; ``_dedupe_items`` keys on ``_identity`` first and never reaches the
finer per-item ``guid``, so everything the key conflated vanished silently.

These tests drive the REAL path — ``fetch_events`` (identity construction +
bucketing) and then ``_dedupe_items`` — rather than ``_wl_identity`` alone.
A unit test on the identity function would have passed both before and after
the fix for the wrong reason: the defect only becomes visible once the
provider's bucketing and the central dedupe run over the same items.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.build_feed import _dedupe_items
from src.providers import wl_fetch


def _traffic_info(title: str, *, line: str, start: str) -> dict[str, Any]:
    """One trafficInfo entry in the shape ``_fetch_traffic_infos`` returns."""
    return {
        "title": title,
        "description": f"{title} (Testbeschreibung)",
        "relatedLines": [line],
        "time": {"start": start, "end": "2026-09-30T23:59:59+02:00"},
        "attributes": {},
    }


@pytest.fixture
def _no_news(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        wl_fetch, "_fetch_news", lambda **_kwargs: [], raising=True
    )


def _run(monkeypatch: pytest.MonkeyPatch, infos: list[dict[str, Any]]) -> list[Any]:
    monkeypatch.setattr(
        wl_fetch, "_fetch_traffic_infos", lambda **_kwargs: infos, raising=True
    )
    return wl_fetch.fetch_events()


def test_three_distinct_disruptions_on_one_line_all_survive_dedupe(
    monkeypatch: pytest.MonkeyPatch, _no_news: None
) -> None:
    start = "2026-09-11T09:16:36+02:00"
    infos = [
        _traffic_info(
            "Veranstaltung Züge halten Rosensteingasse bei Linie 9",
            line="44",
            start=start,
        ),
        _traffic_info(
            "Veranstaltung Betrieb ab Johann-Nepomuk-Berger-Platz",
            line="44",
            start=start,
        ),
        _traffic_info("Fahrtbehinderung Veranstaltung", line="44", start=start),
    ]

    events = _run(monkeypatch, infos)
    assert len(events) == 3, "die drei Meldungen dürfen nicht schon im Provider verschmelzen"

    # The identities must already differ — this is what _dedupe_items keys on.
    assert len({e["_identity"] for e in events}) == 3

    deduped = _dedupe_items(list(events))
    assert len(deduped) == 3, (
        "drei verschiedene Störungen auf Linie 44 am selben Tag — keine davon "
        "darf als Duplikat verworfen werden"
    )


def test_two_locations_on_one_line_set_both_survive_dedupe(
    monkeypatch: pytest.MonkeyPatch, _no_news: None
) -> None:
    """The live 49A/50B case: same line set, same day, two different streets.

    The titles carry no word from ``TITLE_TOPIC_TOKENS``, so
    ``_topic_key_from_title`` falls back to the normalised title core and the
    two items get distinct topics — exactly as upstream published them.
    """
    start = "2026-08-25T00:00:00+02:00"
    infos = [
        _traffic_info("Mondweg", line="49A", start=start),
        _traffic_info("Hüttergasse", line="49A", start=start),
    ]

    deduped = _dedupe_items(list(_run(monkeypatch, infos)))
    titles = sorted(str(i["title"]) for i in deduped)
    assert len(deduped) == 2, f"beide Orte müssen erhalten bleiben, bekam: {titles}"


def test_a_shared_topic_token_still_aggregates_in_the_provider(
    monkeypatch: pytest.MonkeyPatch, _no_news: None
) -> None:
    """Boundary of this fix — the layer BELOW it is unchanged and intentional.

    When both titles contain the same word from ``TITLE_TOPIC_TOKENS``
    ("Umleitung"), ``_topic_key_from_title`` reduces both to that token, the
    bucketing in ``fetch_events`` merges them into ONE item and picks the
    better title/description by score (unioning stops and extras). That is a
    deliberate "one item per (category, topic, line set)" aggregation and it
    happens before ``_identity`` is ever compared — so this fix neither causes
    it nor removes it. Pinned here so the two layers are not confused: a future
    reader seeing one item for two streets should look at the bucketing, not
    at ``_dedupe_items``.
    """
    start = "2026-08-25T00:00:00+02:00"
    infos = [
        _traffic_info("Umleitung Mondweg", line="49A", start=start),
        _traffic_info("Umleitung Hüttergasse", line="49A", start=start),
    ]

    events = _run(monkeypatch, infos)
    assert len(events) == 1, "das Bucketing fasst gleiches Topic + Linienset zusammen"
    assert len(_dedupe_items(list(events))) == 1


def test_a_genuine_repeat_still_dedupes(
    monkeypatch: pytest.MonkeyPatch, _no_news: None
) -> None:
    """Over-splitting guard.

    The same disruption reported twice shares category + line set + topic, so
    the provider's own bucketing merges it long before ``_dedupe_items`` runs.
    Without this guard the fix could trade dropped items for duplicated ones.
    """
    start = "2026-09-11T09:16:36+02:00"
    infos = [
        _traffic_info("Gleisbauarbeiten Hernalser Hauptstraße", line="44", start=start),
        _traffic_info("Gleisbauarbeiten Hernalser Hauptstraße", line="44", start=start),
    ]

    deduped = _dedupe_items(list(_run(monkeypatch, infos)))
    assert len(deduped) == 1


def test_identity_refines_the_bucket_key(
    monkeypatch: pytest.MonkeyPatch, _no_news: None
) -> None:
    """Two items from different buckets can never share an ``_identity``.

    ``fetch_events`` buckets raw events by ``(category, topic_key, line set)``
    and ``_identity`` now carries all three (plus the start day), so it is a
    refinement of the bucket key. That is the structural property behind the
    two tests above: ``_dedupe_items`` can no longer drop anything the
    provider's own bucketing did not already merge.
    """
    start = "2026-09-11T09:16:36+02:00"
    infos = [
        _traffic_info("Fahrtbehinderung Veranstaltung", line="44", start=start),
        _traffic_info("Gleisbauarbeiten Hernalser Hauptstraße", line="44", start=start),
        _traffic_info("Aufzug defekt Station Schottentor", line="44", start=start),
    ]

    events = _run(monkeypatch, infos)
    identities = [e["_identity"] for e in events]
    guids = [e["guid"] for e in events]
    assert len(set(identities)) == len(identities) == len(set(guids))
