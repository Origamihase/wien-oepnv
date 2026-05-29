"""Regression test: ``deduplicate_fuzzy`` must be order-independent.

Pre-fix the merge iterated ``items`` in arrival order and used
first-match-wins on the inner loop (``break`` after the merge). Three
items with a transitive overlap graph — ``A`` overlaps ``C``,
``B`` overlaps ``C``, but ``A`` and ``B`` do NOT directly overlap —
produced different groupings depending on the upstream concatenation
order::

    order [A, B, C] → [AC, B]   # C merged with A (the first existing item it saw)
    order [B, A, C] → [BC, A]   # C merged with B (the first existing item it saw)

Why this matters: cache loaders feed ``deduplicate_fuzzy`` after a
``_dedupe_items`` pass that may itself reshuffle items by dedup key.
Upstream provider order is also incidental (network race, plugin
registration order). The merge result therefore drifted between
runs / between deployments, taking the survivor's ``guid`` and
``_identity`` (post-fix preserved by the peer-merge branch) along with
it — every drift event reset ``first_seen`` for the affected merged
disruption.

Fix sorts ``items`` at the top of ``deduplicate_fuzzy`` by the per-item
stable identity tuple ``(_identity, guid, title)``, so survivor
selection is deterministic across runs regardless of input order.
"""

from __future__ import annotations

import copy
from typing import Any

from src.feed.merge import deduplicate_fuzzy


def _signature(items: list[dict[str, Any]]) -> list[tuple[str, str, str]]:
    """Return an order-insensitive equality signature for a merge result."""
    return sorted(
        (
            str(it.get("title") or ""),
            str(it.get("_identity") or ""),
            str(it.get("guid") or ""),
        )
        for it in items
    )


def test_three_items_transitive_overlap_is_order_independent() -> None:
    """A↔C, B↔C, but NOT A↔B — pre-fix the result swapped with input order."""
    items_base = [
        {
            "title": "U1/U2: ottakring umleitung",
            "guid": "g_A",
            "_identity": "wl|hinweis|L=U1,U2|D=2026-05-29",
        },
        {
            "title": "U3/U4: ottakring umleitung",
            "guid": "g_B",
            "_identity": "wl|hinweis|L=U3,U4|D=2026-05-29",
        },
        {
            "title": "U1/U3: ottakring umleitung",
            "guid": "g_C",
            "_identity": "wl|hinweis|L=U1,U3|D=2026-05-29",
        },
    ]

    results: list[list[tuple[str, str, str]]] = []
    for permutation in ([0, 1, 2], [1, 0, 2], [2, 1, 0], [2, 0, 1], [0, 2, 1]):
        inp = [copy.deepcopy(items_base[i]) for i in permutation]
        results.append(_signature(deduplicate_fuzzy(inp)))

    for idx, sig in enumerate(results[1:], start=1):
        assert sig == results[0], (
            f"Permutation #{idx} produced a different merge result than "
            f"the canonical one:\n  canonical: {results[0]}\n  this run:  {sig}"
        )


def test_vor_oebb_priority_already_order_independent_still_holds() -> None:
    """Independence guarantee must hold for the VOR/ÖBB branches too."""
    vor = {
        "title": "S1/S2: Weichenstörung",
        "description": "Short VOR text.",
        "guid": "vor_guid_1",
        "provider": "vor",
        "source": "vor",
    }
    oebb = {
        "title": "S1/S2: Weichenstörung",
        "description": "Details from ÖBB.",
        "guid": "oebb_guid_1",
        "provider": "oebb",
        "source": "oebb",
    }
    a = deduplicate_fuzzy([copy.deepcopy(vor), copy.deepcopy(oebb)])
    b = deduplicate_fuzzy([copy.deepcopy(oebb), copy.deepcopy(vor)])
    # Both orderings collapse to the same merged item; VOR wins.
    assert _signature(a) == _signature(b)
    assert a[0]["guid"] == "vor_guid_1"


def test_repeat_runs_on_identical_input_produce_byte_identical_output() -> None:
    """Same input twice must produce the same ``_identity`` / ``guid``.

    The repeat-run invariant is what makes ``first_seen`` survive
    upstream re-publishes of the same disruption: every cycle the
    deduped + merged item must collapse to the same persistent key.
    """
    items = [
        {
            "title": "U6/U4: stoerung praterstern",
            "guid": "wl_aaa",
            "_identity": "wl|hinweis|L=U4,U6|D=2026-05-29",
        },
        {
            "title": "U6: stoerung praterstern",
            "guid": "wl_bbb",
            "_identity": "wl|hinweis|L=U6|D=2026-05-29",
        },
    ]
    a = deduplicate_fuzzy(copy.deepcopy(items))
    b = deduplicate_fuzzy(copy.deepcopy(items))
    assert len(a) == 1
    assert len(b) == 1
    assert a[0]["_identity"] == b[0]["_identity"]
    assert a[0]["guid"] == b[0]["guid"]
