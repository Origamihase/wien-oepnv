"""The feed chain is one function that ``main`` and every replay call.

Decided 2026-10-15 (Michael, after Jules' modularisation audit of
2026-10-14): ``build_feed`` stays one module, but the steps between fetching
and rendering live only in ``_assemble_feed_items``. Replay probes that copied
the steps by hand drifted from the real build (they missed the Baustellen
twin step); a probe that calls the function cannot.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

import src.build_feed as bf
from src.feed_types import FeedItem

# Every step of the chain. ``main`` must reach them only through
# ``_assemble_feed_items``, so the chain exists exactly once.
_CHAIN_STEPS = {
    "_drop_test_messages",
    "_restart_recurring_occurrences",
    "_note_announced_starts",
    "_drop_old_items",
    "_summarize_duplicates",
    "_dedupe_items",
    "deduplicate_fuzzy",
    "_merge_wl_ticker_clusters",
    "_absorb_works_tickers",
    "_yield_notices_to_incidents",
    "_drop_baustellen_twins",
    "_carry_item_identity",
    "_recency_sort_key",
    "_defer_repeated_route_titles",
    "_apply_topic_budget",
    "_defer_upcoming_items",
    "_defer_all_clear_items",
}


def _called_names(func: Any) -> set[str]:
    tree = (
        func
        if isinstance(func, ast.AST)
        else ast.parse(textwrap.dedent(inspect.getsource(func)))
    )
    return {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }


def _item(guid: str, title: str, now: datetime) -> FeedItem:
    return {
        "guid": guid,
        "title": title,
        "description": "",
        "link": "",
        "source": "Wiener Linien",
        "category": "Störung",
        "pubDate": now - timedelta(minutes=5),
        "ends_at": now + timedelta(hours=6),
    }


def test_main_runs_no_chain_step_itself() -> None:
    called = _called_names(bf.main)
    assert "_assemble_feed_items" in called
    assert not called & _CHAIN_STEPS


def test_no_other_function_copies_the_chain() -> None:
    # ``lint`` once carried its own copy (without identity carry-over,
    # restarts and announced starts), so its counts could differ from the
    # health report of a build. One chain step in a helper is fine; two or
    # more mean a second chain.
    tree = ast.parse(inspect.getsource(bf))
    copies = {
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name != "_assemble_feed_items"
        and len(_called_names(node) & _CHAIN_STEPS) > 1
    }
    assert not copies
    assert "_assemble_feed_items" in _called_names(bf.lint)


def test_assemble_runs_every_chain_step() -> None:
    assert _CHAIN_STEPS <= _called_names(bf._assemble_feed_items)


def test_main_renders_what_assemble_returns() -> None:
    now = datetime.now(UTC)
    items = [_item("g1", "16A: Rettungseinsatz", now), _item("g2", "U4: Stromstörung", now)]
    assembled: list[list[str]] = []
    rendered: list[list[str]] = []
    real_assemble = bf._assemble_feed_items

    def spy(*args: Any, **kwargs: Any) -> list[FeedItem]:
        result = real_assemble(*args, **kwargs)
        assembled.append([it["title"] for it in result])
        return result

    def fake_make_rss(feed_items: list[FeedItem], *args: Any, **kwargs: Any) -> str:
        rendered.append([it["title"] for it in feed_items])
        return ""

    with patch.object(bf, "_invoke_collect_items", return_value=items), \
         patch.object(bf, "_assemble_feed_items", side_effect=spy), \
         patch.object(bf, "_load_state", return_value={}), \
         patch.object(bf, "_make_rss", side_effect=fake_make_rss), \
         patch.object(bf, "_save_state", MagicMock()), \
         patch.object(bf, "atomic_write", MagicMock()), \
         patch.object(bf, "validate_path", MagicMock()), \
         patch.object(bf, "write_feed_health_report", MagicMock()), \
         patch.object(bf, "write_feed_health_json", MagicMock()):
        assert bf.main() == 0

    assert len(assembled) == 1
    # DE and EN feed both render exactly the assembled list.
    assert rendered == [assembled[0], assembled[0]]
    assert sorted(assembled[0]) == ["16A: Rettungseinsatz", "U4: Stromstörung"]


def test_stats_keep_the_counts_reached_before_a_failure() -> None:
    now = datetime.now(UTC)
    items = [_item("g1", "16A: Rettungseinsatz", now), _item("g2", "U4: Stromstörung", now)]
    stats = bf.FeedAssemblyStats()
    with patch.object(bf, "_merge_wl_ticker_clusters", side_effect=RuntimeError("boom")):
        with pytest.raises(RuntimeError):
            bf._assemble_feed_items(items, {}, now, stats=stats)
    # The age filter ran, the merge did not: main reports what was counted.
    assert stats.filtered_count == 2
    assert stats.deduped_count == 0
