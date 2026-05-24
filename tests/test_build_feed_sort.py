import sys
import importlib
import pytest
import types
from pathlib import Path
from datetime import datetime, UTC
from typing import Any

def _import_build_feed(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    module_name = "src.build_feed"
    # Ensure we can import src modules
    root = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(root))
    monkeypatch.syspath_prepend(str(root / "src"))

    # Mock providers to avoid import errors
    providers = types.ModuleType("providers")
    wl = types.ModuleType("providers.wiener_linien")
    setattr(wl, "fetch_events", lambda: [])
    oebb = types.ModuleType("providers.oebb")
    setattr(oebb, "fetch_events", lambda: [])
    monkeypatch.setitem(sys.modules, "providers", providers)
    monkeypatch.setitem(sys.modules, "providers.wiener_linien", wl)
    monkeypatch.setitem(sys.modules, "providers.oebb", oebb)

    sys.modules.pop(module_name, None)
    module = importlib.import_module(module_name)
    module.refresh_from_env()
    return module

def test_sort_key_handles_none_guid(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """
    Verify that _recency_sort_key handles cases where 'guid' is explicitly
    None / missing / a string in the item dict (tiebreak part is index 1).
    """

    monkeypatch.setenv("OUT_PATH", "docs/feed.xml")
    monkeypatch.setenv("LOG_DIR", "log")

    # Create necessary directories to pass validation if needed (though validate_path checks parents existence mostly)
    (tmp_path / "docs").mkdir()
    (tmp_path / "log").mkdir()

    build_feed = _import_build_feed(monkeypatch)
    now_utc = datetime.now(UTC)
    state: dict[str, dict[str, Any]] = {}

    # Create an item with explicitly None guid
    item_none_guid = {
        "title": "None Guid",
        "guid": None,
        "pubDate": datetime.now(UTC)
    }

    # Create an item with no guid key
    item_missing_guid = {
        "title": "Missing Guid",
        "pubDate": datetime.now(UTC)
    }

    # Create an item with string guid
    item_str_guid = {
        "title": "String Guid",
        "guid": "some-guid",
        "pubDate": datetime.now(UTC)
    }

    # This should not raise TypeError
    key1 = build_feed._recency_sort_key(item_none_guid, state, now_utc)
    key2 = build_feed._recency_sort_key(item_missing_guid, state, now_utc)
    key3 = build_feed._recency_sort_key(item_str_guid, state, now_utc)

    # Verify that the final-tiebreak (guid) part of the key is a string.
    # Key shape: (-first_seen, category_rank, -pubDate, guid) → guid is last.
    assert isinstance(key1[-1], str)
    assert len(key1[-1]) > 0

    assert isinstance(key2[-1], str)
    assert len(key2[-1]) > 0

    assert isinstance(key3[-1], str)
    assert key3[-1] == "some-guid"

def test_deterministic_sorting_fallback(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:

    build_feed = _import_build_feed(monkeypatch)

    now = datetime(2024, 1, 1, 12, 0, tzinfo=UTC)
    state: dict[str, dict[str, Any]] = {}
    # No state → identical first_seen (now); no guids → identity tiebreak.
    items = [
        {"title": "Item C", "pubDate": now},
        {"title": "Item A", "pubDate": now},
        {"title": "Item B", "pubDate": now},
    ]

    sorted_items = sorted(items, key=lambda it: build_feed._recency_sort_key(it, state, now))

    # Ensure they don't error out, and order is deterministic.
    # The _sort_key will generate strings via _identity_for_item
    # For these items, identity includes "T=Item C|F=...", "T=Item A|F=...", etc.
    # Check that sorting relies on _identity_for_item

    ident_a = build_feed._identity_for_item(items[1])
    ident_b = build_feed._identity_for_item(items[2])
    ident_c = build_feed._identity_for_item(items[0])

    # Construct expected sorted order based on identities
    expected_order = sorted([
        {"item": items[0], "ident": ident_c},
        {"item": items[1], "ident": ident_a},
        {"item": items[2], "ident": ident_b},
    ], key=lambda x: x["ident"])

    # We assert that the sorted result matches the one ordered by identities directly
    assert [it["title"] for it in sorted_items] == [x["item"]["title"] for x in expected_order]


def test_stoerung_ranks_above_baustelle_on_first_seen_tie(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """On a first_seen tie (same build), Störung outranks Hinweis outranks Baustelle."""
    build_feed = _import_build_feed(monkeypatch)
    now = datetime(2026, 5, 24, 12, 0, tzinfo=UTC)
    state: dict[str, dict[str, Any]] = {}  # empty → all first_seen == now
    items: list[dict[str, Any]] = [
        {"title": "Bau", "category": "Baustelle", "guid": "b", "pubDate": now},
        {"title": "Stoer", "category": "Störung", "guid": "s", "pubDate": now},
        {"title": "Hint", "category": "Hinweis", "guid": "h", "pubDate": now},
    ]
    ordered = sorted(items, key=lambda it: build_feed._recency_sort_key(it, state, now))
    assert [it["title"] for it in ordered] == ["Stoer", "Hint", "Bau"]


def test_newer_pubdate_first_within_same_category_and_first_seen(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Same category + first_seen → newer pubDate leads (sinks old-start Baustellen)."""
    build_feed = _import_build_feed(monkeypatch)
    now = datetime(2026, 5, 24, 12, 0, tzinfo=UTC)
    state: dict[str, dict[str, Any]] = {}
    items: list[dict[str, Any]] = [
        {"title": "old-start", "category": "Baustelle", "guid": "a",
         "pubDate": datetime(2021, 2, 28, tzinfo=UTC)},
        {"title": "new-start", "category": "Baustelle", "guid": "b",
         "pubDate": datetime(2026, 4, 1, tzinfo=UTC)},
    ]
    ordered = sorted(items, key=lambda it: build_feed._recency_sort_key(it, state, now))
    assert [it["title"] for it in ordered] == ["new-start", "old-start"]


def test_missing_pubdate_sorts_last_within_group(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A null/unparseable pubDate sorts last within its (first_seen, category) group."""
    build_feed = _import_build_feed(monkeypatch)
    now = datetime(2026, 5, 24, 12, 0, tzinfo=UTC)
    state: dict[str, dict[str, Any]] = {}
    items: list[dict[str, Any]] = [
        {"title": "no-pub", "category": "Störung", "guid": "a", "pubDate": None},
        {"title": "has-pub", "category": "Störung", "guid": "b", "pubDate": now},
    ]
    ordered = sorted(items, key=lambda it: build_feed._recency_sort_key(it, state, now))
    assert [it["title"] for it in ordered] == ["has-pub", "no-pub"]
