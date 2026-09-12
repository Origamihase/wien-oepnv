"""Distinct WL disruptions on one line on one day must all reach the feed.

Live regression, 2026-09-12 (``python -m src.cli feed lint``)::

    Nach Deduplizierung: 79 (entfernte Duplikate: 4)

    - 2x Schluessel wl|hinweis|L=49A,50B|D=2026-08-25:
        49A/50B: Mondweg
        49A/50B: Huettergasse        <- andere Strasse, verworfen

``_wl_identity`` folded the ``topic_key`` into the key only when the line set
or the start date was missing; ``_dedupe_items`` keys on ``_identity`` first
and never reaches the finer per-item ``guid``, so everything the coarse
line+day key conflated vanished silently.

That blind drop was never a merge. It hit genuinely distinct messages — two
different streets above — just as hard as it hit repeated reports of ONE
incident. Both failure modes are pinned here:

* distinct messages must all survive (``..._survive_dedupe``);
* several messages about one incident must join into a single item, and they
  must do so in the PROVIDER'S bucketing, where the better title and the
  better description win (``..._merge_into_one``) — not by dropping one at
  random further downstream.

Correction to the original write-up: the line-44 ``Veranstaltung`` trio was
first cited as three *distinct* disruptions. It is not — it is one event
reported from three angles, and it now belongs to the merge tests. The
49A/50B pair is the genuine "distinct" case.

These tests drive the REAL path — ``fetch_events`` (identity construction +
bucketing) and then ``_dedupe_items`` — rather than ``_wl_identity`` alone.
A unit test on the identity function would have passed both before and after
the fix for the wrong reason: the defect only becomes visible once the
provider's bucketing and the central dedupe run over the same items.
"""

from __future__ import annotations

from datetime import UTC, datetime, tzinfo
from typing import Any

import pytest

from src.build_feed import _dedupe_items
from src.providers import wl_fetch

# Die Zeitstempel unten stehen wörtlich so in den Live-Daten vom 2026-09-12 —
# genau das macht sie als Beleg wertvoll. Sie sind aber absolut, und
# ``fetch_events`` filtert über ``_is_active(start, end, datetime.now(UTC))``.
# Ohne eingefrorene Uhr laufen die Fenster im Lauf des Tages ab und die Tests
# fangen an zu scheitern, ohne dass sich am Code etwas geändert hätte:
#
#   Der Demonstrations-Test lief bis 19:00 Wiener Zeit grün und kippte dann —
#   die informativere der beiden Meldungen endet um 19:00, wurde ab da als
#   inaktiv verworfen, und übrig blieb ausgerechnet der dürftige Titel, dessen
#   Verdrängung der Test verhindern soll (CI-Lauf 34706586476).
#
# Eingefroren auf 2026-09-12 15:00 Wiener Zeit: nach jedem ``start`` und vor
# jedem ``end`` in dieser Datei. Bitte nicht durch relative Zeitstempel
# ersetzen — die Tages-Komponente geht in ``_wl_identity`` ein (``D=…``), und
# ein Lauf kurz vor Mitternacht würde Meldungen auf zwei Tage verteilen.
class _FrozenDatetime(datetime):
    """``datetime`` mit stehengebliebenem ``now()``; alles andere unverändert.

    ``now`` gibt den Subtyp zurück, nicht ``datetime`` — ``datetime.now`` ist
    als ``Self`` typisiert, und ein breiterer Rückgabetyp verletzt den
    Liskov-Vertrag (mypy ``[override]``).
    """

    @classmethod
    def now(cls, tz: tzinfo | None = None) -> _FrozenDatetime:
        return _FROZEN_NOW if tz is None else _FROZEN_NOW.astimezone(tz)


_FROZEN_NOW = _FrozenDatetime(2026, 9, 12, 13, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _frozen_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hält ``fetch_events`` auf 2026-09-12 15:00 Wiener Zeit fest."""
    monkeypatch.setattr(wl_fetch, "datetime", _FrozenDatetime, raising=True)


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
    """Three unrelated incidents on one line on one day must all survive.

    Originally written with the live line-44 ``Veranstaltung`` trio, which
    turned out to be ONE event described three times — see the module
    docstring. Three genuinely unrelated causes make the point without
    relying on that misreading.
    """
    start = "2026-09-11T09:16:36+02:00"
    infos = [
        _traffic_info("Weichenstörung Schottentor", line="44", start=start),
        _traffic_info("Gleisbauarbeiten Hernalser Hauptstraße", line="44", start=start),
        # NB: kein "Aufzug …" — ``_is_facility_only`` verwirft reine
        # Aufzugs-/Rolltreppenmeldungen, bevor sie ein Item werden.
        _traffic_info("Falschparker Kreuzgasse", line="44", start=start),
    ]

    events = _run(monkeypatch, infos)
    assert len(events) == 3, "drei verschiedene Ursachen dürfen nicht verschmelzen"

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
        _traffic_info("Weichenstörung Schottentor", line="44", start=start),
        _traffic_info("Gleisbauarbeiten Hernalser Hauptstraße", line="44", start=start),
        # NB: kein "Aufzug …" — ``_is_facility_only`` verwirft reine
        # Aufzugs-/Rolltreppenmeldungen, bevor sie ein Item werden.
        _traffic_info("Falschparker Kreuzgasse", line="44", start=start),
    ]

    events = _run(monkeypatch, infos)
    identities = [e["_identity"] for e in events]
    guids = [e["guid"] for e in events]
    assert len(set(identities)) == len(identities) == len(set(guids))


def test_two_messages_about_one_demonstration_merge_into_one(
    monkeypatch: pytest.MonkeyPatch, _no_news: None
) -> None:
    """Live regression 2026-09-12: the same closure, published twice.

        38A: Demonstration
        38A: Demonstration Haltestelle Kahlenberg wird nicht eingehalten

    Both describe the Kahlenberg stop being skipped. Before the dedupe fix
    this stayed hidden — ``_dedupe_items`` blindly dropped one of them via
    the coarse line+day ``_identity``. That was masking, not merging: it hit
    genuinely distinct messages just as hard (see the 49A/50B test above).

    The right place to join them is the topic: with ``demonstration`` in
    ``TITLE_TOPIC_TOKENS`` both land in one bucket, and the bucketing picks
    the better title AND the better description — so the single item beats
    either input.
    """
    infos = [
        {
            "title": "Demonstration",
            "description": (
                "Linie 38A: Die Haltestelle Kahlenberg kann derzeit in beiden "
                "Fahrtrichtungen nicht eingehalten werden. Busse mit Fahrziel "
                "Kahlenberg werden Cobenzl-Parkplatz kurzgeführt."
            ),
            "relatedLines": ["38A"],
            "time": {
                "start": "2026-09-12T11:47:00+02:00",
                "end": "2026-09-12T23:55:00+02:00",
            },
            "attributes": {},
        },
        {
            "title": "Demonstration Haltestelle Kahlenberg wird nicht eingehalten",
            "description": "Demonstration\nHaltestelle Kahlenberg wird nicht eingehalten",
            "relatedLines": ["38A"],
            "time": {
                "start": "2026-09-12T12:25:15+02:00",
                "end": "2026-09-12T19:00:00+02:00",
            },
            "attributes": {},
        },
    ]

    events = _run(monkeypatch, infos)
    assert len(events) == 1, [e["title"] for e in events]

    item = events[0]
    # The informative title wins …
    assert "Kahlenberg" in str(item["title"])
    # … and so does the readable description, not the terse one.
    assert "Cobenzl-Parkplatz" in str(item["description"])


def test_one_event_reported_in_several_facets_merges(
    monkeypatch: pytest.MonkeyPatch, _no_news: None
) -> None:
    """The line-44 trio: one Veranstaltung, three upstream messages.

    ``tests`` above keep genuinely distinct messages apart; this one keeps
    the other error in check. Upstream describes a single event from three
    angles, and ``veranstaltung`` in ``TITLE_TOPIC_TOKENS`` folds them into
    one item instead of three near-identical feed entries.
    """
    start = "2026-09-11T09:16:36+02:00"
    infos = [
        _traffic_info(
            "Veranstaltung Züge halten Rosensteingasse bei Linie 9", line="44", start=start
        ),
        _traffic_info(
            "Veranstaltung Betrieb ab Johann-Nepomuk-Berger-Platz", line="44", start=start
        ),
        _traffic_info("Fahrtbehinderung Veranstaltung", line="44", start=start),
    ]

    assert len(_run(monkeypatch, infos)) == 1


def test_one_fire_brigade_callout_does_not_take_two_feed_slots(
    monkeypatch: pytest.MonkeyPatch, _no_news: None
) -> None:
    """Live 2026-09-12: one 64A callout occupied two of the ten feed slots.

        64A: Fahrtbehinderung wegen Feuerwehreinsatz
        64A: Feuerwehreinsatz Betrieb ab Gregorygasse

    Same gap as the 38A ``Demonstration`` case — ``feuerwehreinsatz`` was
    missing from ``TITLE_TOPIC_TOKENS`` while its siblings ``polizeieinsatz``
    and ``rettungseinsatz`` were already there.

    The German feed is the priority output (AGENTS.md): it drives info
    displays with a hard item cap, so a duplicate is not just noise, it
    displaces a different disruption entirely.
    """
    start = "2026-09-12T09:00:00+02:00"
    infos = [
        _traffic_info("Fahrtbehinderung wegen Feuerwehreinsatz", line="64A", start=start),
        _traffic_info("Feuerwehreinsatz Betrieb ab Gregorygasse", line="64A", start=start),
    ]

    events = _run(monkeypatch, infos)
    assert len(events) == 1, [e["title"] for e in events]
    assert "Gregorygasse" in str(events[0]["title"]), "der informative Titel gewinnt"


def test_the_same_callout_on_different_lines_stays_separate(
    monkeypatch: pytest.MonkeyPatch, _no_news: None
) -> None:
    """Over-merge guard for the token above.

    Three lines were hit by fire-brigade callouts on 2026-09-12. They are
    separate disruptions for a reader waiting at a specific stop, and the
    line set keeps them apart — the new token must not change that.
    """
    start = "2026-09-12T09:00:00+02:00"
    infos = [
        _traffic_info("Feuerwehreinsatz Betrieb ab Carlbergergasse", line="60A", start=start),
        _traffic_info(
            "Feuerwehreinsatz Betrieb ab Atzgersdorf, Kirchenplatz", line="66A", start=start
        ),
        _traffic_info("Feuerwehreinsatz Betrieb ab Gregorygasse", line="64A", start=start),
    ]

    assert len(_run(monkeypatch, infos)) == 3
