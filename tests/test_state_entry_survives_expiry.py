"""A message that leaves the feed keeps its state entry.

The entry (``data/first_seen.json``) is what the feed remembers of a
message: since when it stands in the feed and when it was last seen. Until
2026-11-12 a build that itself saw a message run out deleted the entry
(``_save_state(state, deletions=…)``). A scheduled run hardly ever sees
that, because the fetch drops an ended message first; a rebuild between two
runs reads it from the cache once more.

Replayed from the history of the feed: on 11.11.2026 the rebuild at 01:24
Vienna time read "66A: Busse halten Salvatorianerplatz" (a works measure WL
re-issues every day from 04:40 to 01:00, in the feed since 03.10.) from the
cache of 01:02 and deleted its entry. At 05:00 the re-issued measure stood
as new on place 2, where it had stood around place 23 the days before.

Pinned here through the real ``main``:

* the entry of a message that ran out stays, with its ``first_seen``;
* the measure WL re-issues after its nightly pause keeps that
  ``first_seen``, so it keeps its place behind the newer notices;
* a message past ``MAX_ITEM_AGE_DAYS`` stays out of the next build too
  instead of returning as new.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

import src.build_feed as bf
from src.feed_build import item_history
from src.utils.cache import write_cache
from src.utils.serialize import serialize_for_cache
from tests._conftest.build_feed_bindings import setattr_all

MEASURE = "66a-salvatorianerplatz"
OLD_NOTICE = "wl-notice-without-end"


def _wl(guid: str, title: str, start: datetime, end: datetime | None, **fields: Any) -> dict[str, Any]:
    """A WL item with the keys of ``cache/wl/events.json``."""
    item: dict[str, Any] = {
        "source": "Wiener Linien",
        "category": "Störung",
        "title": title,
        "description": "Bauarbeiten\nBusse halten Salvatorianerplatz",
        "link": "https://www.wienerlinien.at/betriebsinfo",
        "guid": guid,
        "pubDate": start.isoformat(),
        "starts_at": start.isoformat(),
        "ends_at": end.isoformat() if end else None,
        "_identity": f"wl|störung|{guid}",
    }
    item.update(fields)
    return item


def _incident(now: datetime) -> dict[str, Any]:
    """A running disruption, so that every build has something to show."""
    return _wl(
        "wl-incident", "U1: Rettungseinsatz", now - timedelta(minutes=20), now + timedelta(hours=8),
        description="Wegen eines Rettungseinsatzes kommt es zu längeren Wartezeiten.",
    )


def _redirect_build(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Path, Path]:
    """Point every file ``main`` writes at *tmp_path*; the paths of the feed and of the state."""
    out_file, state_file = tmp_path / "feed.xml", tmp_path / "state.json"
    setattr_all(monkeypatch, bf, "validate_path", lambda path, name: path)
    monkeypatch.setattr(bf.feed_config, "OUT_PATH", out_file)
    monkeypatch.setattr(bf.feed_config, "STATE_FILE", state_file)
    monkeypatch.setattr(bf.feed_config, "FEED_HEALTH_PATH", tmp_path / "feed-health.md")
    monkeypatch.setattr(bf.feed_config, "FEED_HEALTH_JSON_PATH", tmp_path / "feed-health.json")
    monkeypatch.setattr(bf, "refresh_from_env", lambda: None)
    monkeypatch.setenv("KI_ENTSCHEIDUNG", "0")
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    return out_file, state_file


def _build(items: list[dict[str, Any]]) -> None:
    """One ``main`` over a WL cache that holds *items*."""
    write_cache("wl", [serialize_for_cache(item) for item in items])
    assert bf.main() == 0


def _titles(out_file: Path) -> list[str]:
    root = ET.fromstring(out_file.read_text(encoding="utf-8"))
    return [str(node.text) for node in root.findall("./channel/item/title")]


def _entry(state_file: Path, key: str) -> dict[str, Any] | None:
    entry = json.loads(state_file.read_text(encoding="utf-8")).get(key)
    return entry if isinstance(entry, dict) else None


def _seed(state_file: Path, entries: dict[str, dict[str, Any]]) -> None:
    state_file.write_text(json.dumps(entries), encoding="utf-8")


def test_entry_of_a_message_that_ran_out_stays(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    out_file, state_file = _redirect_build(monkeypatch, tmp_path)
    now = datetime.now(UTC).replace(microsecond=0)
    first_seen = (now - timedelta(days=39)).isoformat()
    _seed(state_file, {MEASURE: {"first_seen": first_seen, "last_seen": (now - timedelta(minutes=22)).isoformat()}})
    # A rebuild: the cache still holds the measure that ended 24 minutes ago.
    ended = _wl(MEASURE, "66A: Busse halten Salvatorianerplatz", now - timedelta(hours=20), now - timedelta(minutes=24))

    _build([ended, _incident(now)])

    assert _titles(out_file) == ["U1: Rettungseinsatz"]
    entry = _entry(state_file, MEASURE)
    assert entry is not None, "the build deleted the entry of a message that ran out"
    assert entry["first_seen"] == first_seen


def test_reissued_measure_keeps_its_place_after_a_rebuild_saw_its_end(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    out_file, state_file = _redirect_build(monkeypatch, tmp_path)
    now = datetime.now(UTC).replace(microsecond=0)
    first_seen = (now - timedelta(days=39)).isoformat()
    # The night of 11.11.2026 seen from the re-issue: end 3 h 40 min ago,
    # the last run that showed the measure shortly before it. The pause is
    # longer than ``_OCCURRENCE_GAP``; only the gap of a planned measure
    # (``_PLANNED_OCCURRENCE_GAP``) makes the re-issue the same message.
    pause = timedelta(hours=3, minutes=40)
    assert item_history._OCCURRENCE_GAP < pause < item_history._PLANNED_OCCURRENCE_GAP
    last_run = (now - pause - timedelta(minutes=2)).isoformat()
    _seed(state_file, {MEASURE: {"first_seen": first_seen, "last_seen": last_run}})
    ended = _wl(MEASURE, "66A: Busse halten Salvatorianerplatz", now - timedelta(hours=24), now - pause)
    _build([ended, _incident(now)])

    entry = _entry(state_file, MEASURE)
    assert entry is not None
    assert (entry["first_seen"], entry["last_seen"]) == (first_seen, last_run)

    # WL re-issues the measure with a new validity window.
    again = _wl(MEASURE, "66A: Busse halten Salvatorianerplatz", now - timedelta(minutes=1), now + timedelta(hours=20))
    # A notice that entered the feed two days ago: newer than the measure.
    notice = _wl(
        "wl-notice", "5B: Bauarbeiten", now - timedelta(days=2), now + timedelta(days=20), category="Hinweis",
        description="Wegen Straßenbauarbeiten in der Wexstraße werden die Busse umgeleitet.",
    )
    state = json.loads(state_file.read_text(encoding="utf-8"))
    state["wl-notice"] = {"first_seen": (now - timedelta(days=2)).isoformat(), "last_seen": now.isoformat()}
    _seed(state_file, state)

    _build([again, notice, _incident(now)])

    entry = _entry(state_file, MEASURE)
    assert entry is not None
    assert entry["first_seen"] == first_seen, "the re-issued measure counts as new"
    titles = _titles(out_file)
    assert titles.index("5B: Bauarbeiten") < titles.index("66A: Busse halten Salvatorianerplatz")


def test_message_past_the_age_limit_stays_out_of_the_next_build(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    out_file, state_file = _redirect_build(monkeypatch, tmp_path)
    now = datetime.now(UTC).replace(microsecond=0)
    age = timedelta(days=bf.feed_config.MAX_ITEM_AGE_DAYS + 1)
    first_seen = (now - age).isoformat()
    _seed(state_file, {OLD_NOTICE: {"first_seen": first_seen, "last_seen": (now - timedelta(minutes=30)).isoformat()}})
    old = _wl(
        OLD_NOTICE, "31: Wexstraße", now - age, None, category="Hinweis",
        description="Wegen Bauarbeiten wird die Haltestelle Wexstraße in Richtung Stammersdorf verlegt.",
    )

    _build([old, _incident(now)])
    assert _titles(out_file) == ["U1: Rettungseinsatz"]

    _build([old, _incident(now)])

    assert _titles(out_file) == ["U1: Rettungseinsatz"], "the aged-out message returned as new"
    entry = _entry(state_file, OLD_NOTICE)
    assert entry is not None
    assert entry["first_seen"] == first_seen


def test_rebuild_does_not_stamp_a_message_that_ran_out(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # "U2: Schadhaftes Fahrzeug", 03.11.2026: ended 09:35, last scheduled run
    # 09:30, rebuild 09:59, next incident of that name from 11:53, more than
    # ``_OCCURRENCE_GAP`` after the last run that showed the first one. The
    # new incident is a new occurrence, as on a morning without the rebuild.
    out_file, state_file = _redirect_build(monkeypatch, tmp_path)
    now = datetime.now(UTC).replace(microsecond=0)
    last_run = (now - timedelta(hours=2, minutes=30)).isoformat()
    _seed(state_file, {"u2": {"first_seen": (now - timedelta(hours=5)).isoformat(), "last_seen": last_run}})
    text = "Die Linie U2 fährt in unregelmäßigen Abständen. Grund: Schadhaftes Fahrzeug."
    ended = _wl(
        "u2", "U2: Schadhaftes Fahrzeug", now - timedelta(hours=5), now - timedelta(hours=2, minutes=25), description=text
    )

    _build([ended, _incident(now)])

    entry = _entry(state_file, "u2")
    assert entry is not None
    assert entry["last_seen"] == last_run, "the build stamped a message after its end"

    begin = now - timedelta(minutes=2)
    again = _wl("u2", "U2: Schadhaftes Fahrzeug", begin, now + timedelta(hours=1), description=text)

    _build([again, _incident(now)])

    entry = _entry(state_file, "u2")
    assert entry is not None
    assert datetime.fromisoformat(entry["first_seen"]) == begin, "the new incident inherited the place of the one before"
    assert "U2: Schadhaftes Fahrzeug" in _titles(out_file)


@pytest.mark.parametrize(("ended_minutes_ago", "stamped"), [(5, True), (11, False)])
def test_stamp_follows_the_bound_of_the_age_filter(ended_minutes_ago: int, stamped: bool) -> None:
    # Within ``ENDS_AT_GRACE_MINUTES`` after its end a message is still in
    # the feed and is stamped; past them ``_drop_old_items`` drops it, and
    # its entry is left as it was. Ends at noon: the bound of the Vienna
    # midnight (``_drop_after``) is far from both.
    now = datetime(2026, 11, 10, 11, 0, tzinfo=UTC)
    assert bf.feed_config.ENDS_AT_GRACE_MINUTES == 10
    end = now - timedelta(minutes=ended_minutes_ago)
    item: Any = {
        "source": "Wiener Linien", "category": "Störung", "title": "U2: Schadhaftes Fahrzeug",
        "description": "Unregelmäßige Intervalle.", "guid": "u2", "_identity": "wl|störung|u2",
        "pubDate": now - timedelta(hours=2), "starts_at": now - timedelta(hours=2), "ends_at": end,
    }
    before = (now - timedelta(minutes=30)).isoformat()
    state = {"u2": {"first_seen": (now - timedelta(hours=2)).isoformat(), "last_seen": before}}

    bf._restart_recurring_occurrences([item], state, now)
    kept, dropped = bf._drop_old_items([item], now, state)

    assert (state["u2"]["last_seen"] == now.isoformat()) is stamped
    assert bool(kept) is stamped
    assert (dropped == {"u2"}) is not stamped
    if not stamped:
        assert state["u2"]["last_seen"] == before
