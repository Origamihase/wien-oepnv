"""One message, one GUID while it runs (2026-10-06).

Live on 2026-10-06, ``docs/feed.xml``: "43: Verkehrsunfall" stood in the
feed from 00:30 Vienna time under the GUID of its first display ticker
(``cc281c65…``, 00:03:28) as "[Seit 00:03]". The tickers ran out at 01:03;
the rebuild at 01:14 read the same cache, the long message (``de57d7fc…``,
start 00:04) stood alone, and the feed showed it under its GUID as "[Seit
00:04]". "1: Verkehrsunfall" (11:30, long message resolved, tickers left),
"41/42: Verkehrsunfall" (05.10. 01:30, WL replaced the incident by one
follow-up per line) and "9: Falschparker" (05.10. 19:01) changed their GUID
the same way. The shapes below are the cache entries of those builds
(``cache/wl_9d709a/events.json``), on today's clock.

Mutations checked against this file (each one caught, by the test named):

* no carry in ``main`` → ``test_43_keeps_its_guid_and_begin_when_its_tickers_run_out``.
* the begin is not kept → ``test_the_begin_stays_when_the_earliest_message_leaves``.
* the carried GUID's entry is deleted with its run-out message →
  ``test_43_keeps_its_guid_and_begin_when_its_tickers_run_out`` (third build).
* a carried item forgets its own message → the same test (third build).
* any shared message carries → ``test_a_new_incident_that_takes_up_a_running_one_keeps_its_guid``.
* the time rule is dropped → ``test_a_long_message_dated_back_continues_its_tickers``.
* old notes count → ``test_notes_older_than_the_gap_carry_nothing``.
* a newer message of a recurring GUID keeps the old begin →
  ``test_a_recurring_guid_with_a_newer_message_keeps_its_own_time``.
* an item leaves out its WL number → ``test_an_item_stands_for_its_message_and_its_wl_number``.
* a GUID is carried onto two items → ``test_one_guid_goes_to_one_item``.
* a combined entry counts every incident → ``test_a_combined_entry_counts_its_newest_incident``.
* the provider drops the WL number → ``test_the_provider_keeps_the_wl_number``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, tzinfo
from typing import Any, cast

import pytest

import src.build_feed as bf
from src.feed.merge import member_guids
from src.feed_types import FeedItem
from src.providers import wl_fetch

G_TICKER = "cc281c6508ba062820ee6ec97a11514a795c1ba829397c43d3f8f6719026c771"
G_LONG = "de57d7fc844121db03d0e532febfd7d62faf7f8cc5ced1951f3e1470fcd4d6c8"
STOCK = "Nach einer Fahrtbehinderung kommt es zu unterschiedlichen Intervallen."


def _wl(
    title: str,
    description: str,
    guid: str,
    published: datetime,
    *,
    starts_at: datetime | None = None,
    ends_at: datetime | None = None,
    numbers: list[str] | None = None,
) -> dict[str, Any]:
    """A WL cache entry as ``wl_fetch`` writes it."""
    item: dict[str, Any] = {
        "source": "Wiener Linien",
        "category": "Störung",
        "title": title,
        "description": description,
        "link": "https://www.wienerlinien.at/ogd_realtime",
        "guid": guid,
        "pubDate": published.isoformat(),
        "starts_at": (starts_at or published).isoformat(),
        "ends_at": ends_at.isoformat() if ends_at else None,
        "_identity": f"wl|störung|{title}",
    }
    if numbers:
        item["_wl_ids"] = numbers
    return item


def _line_43(now: datetime, *, tickers_end: datetime) -> list[dict[str, Any]]:
    ticker = now - timedelta(minutes=27)  # 00:03:28 at the 00:30 build
    return [
        _wl(
            "43: Verkehrsunfall",
            f"Linie 43: {STOCK}",
            G_LONG,
            ticker + timedelta(seconds=32),
            ends_at=now + timedelta(hours=23),
            numbers=["I20261005-0041"],
        ),
        _wl(
            "43: Fahrtbehinderung Verkehrsunfall",
            "Fahrtbehinderung\nVerkehrsunfall",
            G_TICKER,
            ticker,
            starts_at=ticker + timedelta(seconds=65),
            ends_at=tickers_end,
        ),
    ]


def _build(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any, state: dict[str, Any], cache: list[dict[str, Any]]
) -> list[tuple[str, str, str]]:
    """``main`` over *cache*, reading and writing *state*; (guid, title, description) per item."""
    import xml.etree.ElementTree as ET

    def save(saved: dict[str, Any], deletions: set[str] | None = None) -> None:
        state.clear()
        state.update(saved)
        for key in deletions or ():
            state.pop(key, None)

    out_file = tmp_path / "feed.xml"
    monkeypatch.setattr(bf, "read_cache", lambda provider: cache if provider == "wl" else [])
    monkeypatch.setattr(bf, "validate_path", lambda path, name: path)
    monkeypatch.setattr(bf.feed_config, "OUT_PATH", out_file)
    monkeypatch.setattr(bf.feed_config, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(bf.feed_config, "FEED_HEALTH_PATH", tmp_path / "feed-health.md")
    monkeypatch.setattr(bf.feed_config, "FEED_HEALTH_JSON_PATH", tmp_path / "feed-health.json")
    monkeypatch.setattr(bf, "_load_state", lambda: {key: dict(value) for key, value in state.items()})
    monkeypatch.setattr(bf, "_save_state", save)
    monkeypatch.setattr(bf, "refresh_from_env", lambda: None)
    assert bf.main() == 0
    channel = ET.parse(out_file).getroot().find("channel")
    assert channel is not None
    return [
        (it.findtext("guid") or "", it.findtext("title") or "", it.findtext("description") or "")
        for it in channel.findall("item")
    ]


def test_43_keeps_its_guid_and_begin_when_its_tickers_run_out(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    now = datetime.now(UTC)
    state: dict[str, Any] = {}
    running = _build(monkeypatch, tmp_path, state, _line_43(now, tickers_end=now + timedelta(minutes=30)))
    assert [(guid, title) for guid, title, _ in running] == [(G_TICKER, "43: Verkehrsunfall")]

    # 01:14: the same cache, the tickers' end (01:03) has passed.
    ran_out = _line_43(now, tickers_end=now - timedelta(hours=1))
    assert _build(monkeypatch, tmp_path, state, ran_out) == running
    # And the build after: the run-out ticker's entry is still there to carry.
    assert _build(monkeypatch, tmp_path, state, ran_out) == running


def _entry(members: list[str], noted: datetime, begin: datetime | None = None, **extra: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "first_seen": (noted - timedelta(hours=1)).isoformat(),
        "members": members,
        "members_seen": noted.isoformat(),
        **extra,
    }
    if begin is not None:
        entry["earliest_published"] = begin.isoformat()
    return entry


def _item(guid: str, published: datetime, members: list[str] | None = None, title: str = "1: Verkehrsunfall") -> FeedItem:
    item = cast(FeedItem, _wl(title, STOCK, guid, published))
    item["pubDate"] = published
    item["starts_at"] = published
    if members:
        item["_members"] = members
    return item


NOW = datetime(2026, 10, 6, 9, 30, 57, tzinfo=UTC)  # 11:30:57 in Vienna


def test_a_long_message_dated_back_continues_its_tickers() -> None:
    """``I20261006-0012-F01``: created 10:54 with start 10:40, tickers from 10:43."""
    tickers = datetime(2026, 10, 6, 8, 43, 51, tzinfo=UTC)
    long_message = datetime(2026, 10, 6, 8, 40, tzinfo=UTC)
    state = {"t": _entry(["t", "t2"], NOW - timedelta(minutes=30), tickers)}
    (item,) = bf._carry_item_identity([_item("l", long_message, ["l", "t", "t2"])], state, NOW)
    assert item["guid"] == "t"
    assert item["pubDate"] == long_message


def test_the_begin_stays_when_the_earliest_message_leaves() -> None:
    """11:30: the long message (10:40) resolved, its tickers (10:43) left."""
    begin = datetime(2026, 10, 6, 8, 40, tzinfo=UTC)
    tickers = datetime(2026, 10, 6, 8, 43, 51, tzinfo=UTC)
    state = {"l": _entry(["l", "t", "t2", "wl:I20261006-0012"], NOW - timedelta(minutes=30), begin)}
    (item,) = bf._carry_item_identity([_item("t", tickers, ["t", "t2"])], state, NOW)
    assert (item["guid"], item["pubDate"]) == ("l", begin)


def test_a_new_incident_that_takes_up_a_running_one_keeps_its_guid() -> None:
    """"52: Rettungseinsatz" (2026-10-05 17:50) with a ticker of 30.09. on its line."""
    new = datetime(2026, 10, 5, 15, 50, tzinfo=UTC)
    now = new + timedelta(minutes=11)
    state = {"old": _entry(["old"], now - timedelta(minutes=30), datetime(2026, 9, 30, 22, 0, tzinfo=UTC))}
    (item,) = bf._carry_item_identity([_item("new", new, ["new", "old"], "52: Rettungseinsatz")], state, now)
    assert (item["guid"], item["pubDate"]) == ("new", new)


def test_follow_ups_of_one_incident_continue_it() -> None:
    """"6/18: Schadhaftes Fahrzeug": ``I20261004-0019`` became ``-F01`` (6) and ``-F02`` (18)."""
    begin = datetime(2026, 10, 4, 15, 42, tzinfo=UTC)
    now = datetime(2026, 10, 4, 16, 30, 58, tzinfo=UTC)
    state = {"6/18": _entry(["6/18", "wl:I20261004-0019"], now - timedelta(minutes=30), begin)}
    (item,) = bf._carry_item_identity(
        [_item("6", begin, ["18", "6", "wl:I20261004-0019"], "6/18: Schadhaftes Fahrzeug")], state, now
    )
    assert item["guid"] == "6/18"


def test_notes_older_than_the_gap_carry_nothing() -> None:
    tickers = datetime(2026, 10, 6, 8, 43, 51, tzinfo=UTC)
    state = {"l": _entry(["l", "t"], NOW - bf._OCCURRENCE_GAP - timedelta(minutes=1), tickers - timedelta(minutes=3))}
    (item,) = bf._carry_item_identity([_item("t", tickers, ["t"])], state, NOW)
    assert (item["guid"], item["pubDate"]) == ("t", tickers)


def test_a_recurring_guid_with_a_newer_message_keeps_its_own_time() -> None:
    """WL gives the next "43: Verkehrsunfall" the GUID of the last one."""
    earlier = NOW - timedelta(hours=1, minutes=50)
    noted = NOW - timedelta(minutes=40)
    new = NOW - timedelta(minutes=10)
    state = {"g": _entry(["g"], noted, earlier)}
    (item,) = bf._carry_item_identity([_item("g", new)], state, NOW)
    assert (item["guid"], item["pubDate"]) == ("g", new)
    bf._remember_item_identity([item], state, NOW)
    assert state["g"]["earliest_published"] == new.isoformat()


def test_one_guid_goes_to_one_item() -> None:
    """The 41/42 follow-ups as two items: the one sharing more messages carries the GUID."""
    begin = datetime(2026, 10, 4, 22, 47, tzinfo=UTC)
    now = datetime(2026, 10, 4, 23, 30, 56, tzinfo=UTC)
    state = {"41/42": _entry(["41/42", "t41", "wl:I20261004-0026"], now - timedelta(minutes=29), begin)}
    items = [
        _item("42", begin, ["42", "wl:I20261004-0026"], "42: Verkehrsunfall"),
        _item("41", begin, ["41", "t41", "wl:I20261004-0026"], "41: Verkehrsunfall"),
    ]
    assert [it["guid"] for it in bf._carry_item_identity(items, state, now)] == ["42", "41/42"]


def test_a_guid_in_use_is_not_carried() -> None:
    tickers = datetime(2026, 10, 6, 8, 43, 51, tzinfo=UTC)
    state = {"l": _entry(["l", "t"], NOW - timedelta(minutes=30), tickers)}
    items = [_item("t", tickers, ["t"]), _item("l", tickers, ["l"])]
    assert [it["guid"] for it in bf._carry_item_identity(items, state, NOW)] == ["t", "l"]


def test_without_notes_nothing_changes() -> None:
    """The first build after the change writes the notes and changes no item."""
    tickers = datetime(2026, 10, 6, 8, 43, 51, tzinfo=UTC)
    state: dict[str, dict[str, Any]] = {"l": {"first_seen": NOW.isoformat()}, "t": {"first_seen": NOW.isoformat()}}
    items = [_item("t", tickers, ["l", "t"])]
    assert bf._carry_item_identity(items, state, NOW) == items
    bf._remember_item_identity(items, state, NOW)
    assert state["t"]["members"] == ["l", "t"]
    assert state["t"]["members_seen"] == NOW.isoformat()
    assert state["t"]["earliest_published"] == tickers.isoformat()
    assert "members" not in state["l"]


def test_a_combined_entry_counts_its_newest_incident() -> None:
    """"66A: Rettungseinsatz, Bauarbeiten": the works keep their own GUID when the rescue ends."""
    start = datetime(2026, 10, 3, 14, 0, tzinfo=UTC)
    works = _item("works", start - timedelta(days=1), title="66A: Bauarbeiten Busse halten Salvatorianerplatz")
    works["description"] = "Bauarbeiten\nBusse halten Salvatorianerplatz"
    works["ends_at"] = start + timedelta(days=1)
    rescue = _item("rescue", start, title="66A: Rettungseinsatz")
    rescue["description"] = "Linie 66A: Unregelmäßige Intervalle in beiden Richtungen."
    rescue["ends_at"] = start + timedelta(hours=1)
    (entry,) = bf._merge_wl_ticker_clusters([works, rescue])
    assert entry["title"] == "66A: Rettungseinsatz, Bauarbeiten"
    assert (entry["guid"], member_guids(entry)) == ("rescue", ["rescue"])


def test_fuzzy_merge_keeps_both_messages() -> None:
    from src.feed.merge import deduplicate_fuzzy

    first = {"title": "40/41: Betrieb ab Gersthof", "guid": "a", "source": "Wiener Linien", "category": "Störung"}
    second = {"title": "40: Falschparker Betrieb ab Gersthof", "guid": "b", "source": "Wiener Linien", "category": "Störung"}
    (merged,) = deduplicate_fuzzy([first, second])
    assert member_guids(merged) == ["a", "b"]


class _FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz: tzinfo | None = None) -> _FrozenDatetime:
        frozen = _FrozenDatetime(2026, 10, 5, 22, 30, tzinfo=UTC)  # 00:30 in Vienna
        return frozen if tz is None else frozen.astimezone(tz)


def _info(name: str, title: str, description: str, start: str, end: str, category: int) -> dict[str, Any]:
    """A trafficInfo of the 00:30 fetch, as WL sent it (``data/raw/wl/trafficInfoList.json``)."""
    return {
        "name": name,
        "title": title,
        "description": description,
        "relatedLines": ["43"],
        "relatedStops": [],
        "refTrafficInfoCategoryId": category,
        "time": {"start": start, "end": end},
        "attributes": {},
        **({"status": "active"} if category == 2 else {}),
    }


def test_the_provider_keeps_the_wl_number(monkeypatch: pytest.MonkeyPatch) -> None:
    infos = [
        _info(
            "I20261005-0041-F01",
            "43: Verkehrsunfall",
            f"Linie 43: {STOCK}",
            "2026-10-06T00:04:00.000+0200",
            "2026-10-06T23:55:00.000+0200",
            2,
        ),
        _info(
            "R1348-143",
            "Fahrtbehinderung\nVerkehrsunfall",
            "Fahrtbehinderung\nVerkehrsunfall",
            "2026-10-06T00:03:28.000+0200",
            "2026-10-06T01:03:00.000+0200",
            3,
        ),
    ]
    monkeypatch.setattr(wl_fetch, "datetime", _FrozenDatetime, raising=True)
    monkeypatch.setattr(wl_fetch, "_fetch_news", lambda **_kwargs: [], raising=True)
    monkeypatch.setattr(wl_fetch, "_fetch_traffic_infos", lambda **_kwargs: infos, raising=True)
    numbers = {str(event["title"]): event.get("_wl_ids") for event in wl_fetch.fetch_events()}
    assert numbers == {"43: Verkehrsunfall": ["I20261005-0041"], "43: Fahrtbehinderung Verkehrsunfall": None}


@pytest.mark.parametrize(
    ("name", "numbers"),
    [
        ("I20261004-0019", {"I20261004-0019"}),
        ("I20261004-0019-F02", {"I20261004-0019"}),
        ("R1345-143", set()),
        ("", set()),
    ],
)
def test_incident_numbers(name: str, numbers: set[str]) -> None:
    assert wl_fetch._incident_ids({"name": name}) == numbers


def test_an_item_stands_for_its_message_and_its_wl_number() -> None:
    item = {"guid": "de57", "_wl_ids": ["I20261005-0041"]}
    assert member_guids(item) == ["de57", "wl:I20261005-0041"]
