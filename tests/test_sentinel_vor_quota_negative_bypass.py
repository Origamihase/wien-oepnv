"""Sentinel PoC: VOR daily-quota bypass via negative on-disk request count.

Threat model
------------
The VOR provider's ``load_request_count`` and ``save_request_count`` (in
``src/providers/vor.py``) read ``data/vor_request_count.json`` to track the
number of API requests already made today against the contractual VAO Start
tier 100/day limit. Pre-fix, both sites parsed the on-disk ``"requests"``
value via ``int(value)`` with NO lower-bound clamp:

    count = data["requests"]
    try:
        int_count = int(count)
    except (ValueError, TypeError):
        int_count = 0
    # No max(0, int_count) — negative values pass through.

A poisoned ``data/vor_request_count.json`` with a negative ``requests`` value
(planted by a compromised CI runner, partial flush + power loss, or an
operator mis-edit) would silently:

  1. Bypass the runtime quota check ``_limit_reached`` in
     ``scripts/update_vor_cache.py:87`` — that test uses
     ``todays_count >= MAX_REQUESTS_PER_DAY``, which is False for any
     negative value, so the run proceeds even when the day's quota should
     have been exhausted.

  2. Be perpetuated by ``save_request_count``: it adds the run's delta to
     the negative ``disk_count`` and writes the offset back, so the
     tampered counter survives across runs and silently consumes the
     quota for many days before the count finally crosses ``MAX``.

The defence-in-depth contract is that the quota tracker MUST clamp the
parsed value at zero — a counter cannot legitimately go below zero, so any
negative on-disk value is by definition tampered/corrupt and must be
ignored. The fix lands at both parse sites
(``load_request_count`` + ``save_request_count``) so the in-memory cache
and on-disk file cannot diverge on the lower bound.

Tests below pin the pre-fix leak, prove the post-fix clamp, and assert
the canonical schema is restored on the next save (self-healing).
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import src.providers.vor as vor


@pytest.fixture(autouse=True)
def _isolate_quota_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Point REQUEST_COUNT_FILE at tmp_path and reset the in-memory cache."""
    count_file = tmp_path / "vor_request_count.json"
    monkeypatch.setattr(vor, "REQUEST_COUNT_FILE", count_file)
    monkeypatch.setattr(vor, "_QUOTA_CACHE", {"date": None, "count": 0, "unsaved_delta": 0})
    return count_file


def _today_iso() -> str:
    return datetime.now(ZoneInfo("Europe/Vienna")).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Site 1: load_request_count must clamp negative on-disk counts at zero
# ---------------------------------------------------------------------------


def test_load_request_count_clamps_negative_to_zero(
    _isolate_quota_state: Path,
) -> None:
    """A poisoned counter file with a negative ``requests`` must be clamped.

    Pre-fix: ``int(-1000)`` survives unchecked; the in-memory cache and
    return value carry the negative offset, letting subsequent
    ``_limit_reached`` checks pass even when 100 requests have already
    been made.

    Post-fix: ``max(0, int_count)`` floors the value at 0, so the runtime
    quota check sees a fresh-start counter and the file's next save
    rewrites the canonical schema.
    """
    today = _today_iso()
    poisoned = {"date": today, "requests": -1000}
    _isolate_quota_state.write_text(json.dumps(poisoned), encoding="utf-8")

    stored_date, count = vor.load_request_count(bypass_cache=True)

    assert stored_date == today, "date should round-trip"
    assert count >= 0, (
        f"negative count must be clamped to 0; got {count}. A negative "
        "count would let the runtime quota check pass even when the "
        "VAO Start 100/day limit has been exceeded — defense-in-depth "
        "violation against poisoned counter files."
    )
    # Specifically: the clamp must produce 0, not some other defaulted value.
    assert count == 0, f"clamped count must be exactly 0, got {count}"


def test_load_request_count_preserves_legitimate_count(
    _isolate_quota_state: Path,
) -> None:
    """Sanity: legitimate counts in [0, MAX] must round-trip unchanged."""
    today = _today_iso()
    _isolate_quota_state.write_text(
        json.dumps({"date": today, "requests": 42}), encoding="utf-8"
    )

    stored_date, count = vor.load_request_count(bypass_cache=True)
    assert stored_date == today
    assert count == 42, "legitimate count must pass through clamp untouched"


def test_load_request_count_huge_value_naturally_blocks(
    _isolate_quota_state: Path,
) -> None:
    """A huge positive count (≥ MAX) naturally triggers the limit check.

    The clamp's lower-bound-only behaviour preserves the ``>= MAX`` fail-
    closed semantics: a tampered file claiming 99999 requests still blocks
    the run via the existing ``_limit_reached`` check.
    """
    today = _today_iso()
    huge = vor.MAX_REQUESTS_PER_DAY * 100
    _isolate_quota_state.write_text(
        json.dumps({"date": today, "requests": huge}), encoding="utf-8"
    )

    stored_date, count = vor.load_request_count(bypass_cache=True)
    assert stored_date == today
    assert count >= vor.MAX_REQUESTS_PER_DAY, (
        "huge count must trigger fail-closed limit check via >= MAX"
    )


# ---------------------------------------------------------------------------
# Site 2: save_request_count must also clamp the disk-read negative count
# ---------------------------------------------------------------------------


def test_save_request_count_clamps_negative_disk_value(
    _isolate_quota_state: Path,
) -> None:
    """save_request_count's under-lock disk read must also clamp negative values.

    Without clamping the disk read inside the file_lock block, even a
    fixed ``load_request_count`` is bypassed: ``save_request_count``
    re-reads the file under lock, so a tampered file with negative
    ``requests`` would leak the offset into ``new_total`` and overwrite
    the on-disk file with the negative offset + delta — perpetuating
    the bypass across runs.
    """
    today = _today_iso()
    _isolate_quota_state.write_text(
        json.dumps({"date": today, "requests": -500}), encoding="utf-8"
    )

    # Force a flush by setting WIEN_OEPNV_TEST_QUOTA_BATCH=1 (used by VOR
    # internally to flush after every save call in tests).
    import os
    os.environ["WIEN_OEPNV_TEST_QUOTA_BATCH"] = "1"
    try:
        # Bump the in-memory delta so save_request_count actually flushes.
        vor._QUOTA_CACHE["unsaved_delta"] = 0  # re-init
        result = vor.save_request_count()
    finally:
        os.environ.pop("WIEN_OEPNV_TEST_QUOTA_BATCH", None)

    # After the save, the on-disk count must be non-negative — proving
    # the clamp happened during the under-lock disk re-read.
    persisted = json.loads(_isolate_quota_state.read_text(encoding="utf-8"))
    assert persisted["requests"] >= 0, (
        f"persisted count must be ≥ 0 after save; got {persisted['requests']}. "
        "Negative on-disk count perpetuates the quota-bypass attack."
    )
    # Specifically: the new total should be exactly the in-memory delta
    # (which was 1, the implicit increment for this save call), proving
    # the clamp zeroed the tampered base before the addition.
    assert persisted["requests"] == 1, (
        f"new_total must equal delta=1 (clamped base + delta), "
        f"got {persisted['requests']}"
    )
    assert result == 1


# ---------------------------------------------------------------------------
# Static-check: AST-style assertion that both sites carry the clamp
# ---------------------------------------------------------------------------


def test_vor_quota_load_site_carries_clamp_against_negative_drift() -> None:
    """Audit invariant: ``load_request_count`` must clamp ``int_count`` at 0.

    A future refactor that drops the ``max(0, int_count)`` fix would re-
    open the negative-count quota-bypass surface. This test pins the
    canonical post-fix shape so any such regression fails at PR-review.
    """
    repo_root = Path(__file__).resolve().parents[1]
    source = (repo_root / "src" / "providers" / "vor.py").read_text(
        encoding="utf-8"
    )

    # Locate the load_request_count function. Its int_count computation
    # block must include the `max(0, int_count)` clamp adjacent to the
    # try/except ValueError/TypeError guard.
    assert "int_count = max(0, int_count)" in source, (
        "src/providers/vor.py:load_request_count must clamp int_count at 0 "
        "via `int_count = max(0, int_count)` to defeat the negative-count "
        "quota-bypass attack from a poisoned data/vor_request_count.json. "
        "See tests/test_sentinel_vor_quota_negative_bypass.py for PoC."
    )

    assert "disk_count = max(0, disk_count)" in source, (
        "src/providers/vor.py:save_request_count must also clamp disk_count "
        "at 0 inside the file_lock block. Without it, the tampered file is "
        "re-read under lock and the negative offset is perpetuated to "
        "subsequent runs even after load_request_count is fixed."
    )
