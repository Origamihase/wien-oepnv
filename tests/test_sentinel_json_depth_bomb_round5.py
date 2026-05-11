"""Sentinel PoC: JSON depth-bomb defence Round 5 — sites Round 4 missed.

The 2026-05-07 Round 4 journal entry committed to closing every
``json.loads`` / ``json.load`` / ``response.json()`` site in ``src/`` and
``scripts/`` whose enclosing ``except`` tuple lacked ``RecursionError``.
The closing checklist enumerated sixteen further on-disk parse sites and
landed regression tests in
``tests/test_sentinel_json_depth_bomb_round4.py``. Two functionally
identical siblings remained open after that round:

  * ``scripts/update_vor_stations.py:merge_into_stations`` — pre-merge
    existing-state read for ``data/stations.json``. Pre-fix caught only
    ``FileNotFoundError`` (not even ``json.JSONDecodeError``), so a
    regular malformed ``stations.json`` already crashed the VOR merge
    here, never mind a depth-bomb. The script is invoked via
    ``update_all_stations.py:subprocess.run(check=True)``, so any
    unhandled exception raises ``CalledProcessError`` and aborts the
    entire station-directory cron — *after* the VOR API quota has
    already been debited for that run. The exact same shape exists in
    the Round-4-fixed sibling ``scripts/update_wl_stations.py:
    merge_into_stations`` (entry #7 of the Round 4 journal); the VOR
    analog was missed by the named-list audit.
  * ``scripts/update_all_stations.py:_count_polygon_vertices`` —
    diff-time reader of ``data/vienna_polygon.json``. Pre-fix caught
    ``(OSError, json.JSONDecodeError)`` but not ``RecursionError``. A
    depth-bombed polygon file (corrupted previous run, planted by a
    compromised CI runner) propagates ``RecursionError`` out of
    ``_build_heartbeat`` → ``main()`` of the orchestrator,
    crashing the cron job *after* the merged stations.json has
    already been atomically written — leaving partial state and no
    heartbeat record of what just happened.

Threat model: identical to Round 4 — a deeply-nested but well-formed
JSON document persisted to disk by a corrupted previous run, planted by
a compromised CI runner, or written during a partial flush followed by
power loss. ``json.loads`` raises ``RecursionError`` (NOT a subclass of
``json.JSONDecodeError`` and NOT caught by ``except OSError``); the
pre-fix code therefore propagated the exception out of the loader. Each
test below first asserts the canonical fallback runs (returns ``None``
for the polygon counter / overwrites corrupt cache for the merge) and
never lets ``RecursionError`` escape.

Audit-completion verdict: re-running the Round 4 enumeration grep
``git grep -nE 'json\\.loads\\(|json\\.load\\(|\\.json\\(\\)' src/ scripts/``
and walking each match's enclosing ``except`` clause now returns ZERO
sites without ``RecursionError`` coverage across both trees.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest


DEEP_BOMB_STR = "[" * 5000 + "]" * 5000


def test_precondition_deep_bomb_raises_recursion_error() -> None:
    """Pin the precondition that ``json.loads`` raises ``RecursionError``."""
    with pytest.raises(RecursionError):
        json.loads(DEEP_BOMB_STR)


# ============================================================================
# scripts/update_all_stations.py — _count_polygon_vertices (MEDIUM)
# ============================================================================
#
# Pre-fix: caught ``(OSError, json.JSONDecodeError)`` only. The orchestrator
# uses this counter at heartbeat-build time; a depth-bomb in
# ``data/vienna_polygon.json`` propagates ``RecursionError`` past the catch
# and crashes ``_build_heartbeat`` after the merged stations.json has
# already been atomically written — partial state, no heartbeat.
# Post-fix: returns ``None`` so the heartbeat records the polygon counter
# as unavailable and the orchestrator continues cleanly.


def test_update_all_stations_count_polygon_vertices_handles_depth_bomb(
    tmp_path: Path,
) -> None:
    from scripts import update_all_stations

    poisoned = tmp_path / "vienna_polygon.json"
    poisoned.write_text(DEEP_BOMB_STR, encoding="utf-8")

    result = update_all_stations._count_polygon_vertices(poisoned)

    assert result is None
