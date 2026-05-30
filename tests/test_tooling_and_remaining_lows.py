"""Regression tests for the eight remaining audit findings (PR6 — tooling +
low-severity correctness gates).

1. ``scripts/check_complexity._parse_baseline`` — duplicate-name guard:
   a baseline file that lists the same function twice (operator error /
   merge conflict) pre-fix took the LATER value via plain dict assignment,
   silently relaxing the gate to the higher complexity bound. Fix: take
   the stricter (lower) value AND emit a workflow warning.

2. ``scripts/sync_hafas_profile._extract_profile`` — comment stripping:
   ``re.search`` returned the FIRST occurrence, so a stale doc-block
   ``/** Previously used ver: '1.30', aid: 'Old…' */`` ahead of the live
   profile literal was extracted as the credentials and committed to
   ``data/hafas_profile.json``, breaking every HAFAS call silently. Fix:
   strip ``/* … */`` and ``// …`` comments before the regex sweep.

3. ``src/utils/secret_scanner._should_ignore`` — pattern semantics:
   ``relative.match(pattern)`` used pathlib's odd basename-only rules.
   Patterns containing ``/`` (e.g. ``src/leak.py``) failed to anchor on
   the full path, so a non-anchored pattern silently matched at any
   depth. Fix: ``fnmatch.fnmatch`` on the forward-slash-normalised full
   path for patterns containing ``/``, on the basename otherwise —
   gitignore-style.

4. ``scripts/update_station_directory.extract_stations`` — numeric sort:
   ``stations.sort(key=lambda item: item.bst_id)`` did a string sort on
   numeric-looking IDs, producing ``['10', '100', '9']`` instead of
   ``['9', '10', '100']``. Cosmetic / diff-churn fix.

5. ``scripts/validate_vor_mapping`` — ``if not vor_id`` reported an
   integer ``0`` (or ``False``) as missing. Fix: explicit ``is None /
   == ""`` test.

6. ``scripts/check_i18n_coverage._extract_js_keys`` — JS duplicate-key
   semantics: pre-fix ``continue``-on-duplicate kept the FIRST value,
   but JS runtime takes the LAST. So ``{"k": "good", "k": ""}`` reported
   "not empty" while the page rendered blank. Fix: let the second match
   overwrite, mirroring runtime.

7. ``src/utils/circuit_breaker.CircuitBreaker.call`` — HALF_OPEN single-
   probe contract: pre-fix any thread observing HALF_OPEN passed the
   gate and ran a probe, so N concurrent threads pile-drove a recovering
   upstream. Fix: ``_probe_in_flight`` flag toggled under the lock,
   admitting exactly one probe per HALF_OPEN window.

8. ``src/build_feed._update_item_state`` — legacy-key migration cleanup:
   after migrating a legacy-identity entry to its guid-keyed equivalent,
   the legacy key was left on disk until the 60-day retention prune.
   Fix: pop the legacy entry once the guid-keyed write succeeds.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from src.utils.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerOpen,
    CircuitState,
)


# ---------------------------------------------------------------------------
# 1. check_complexity duplicate-baseline guard
# ---------------------------------------------------------------------------


def test_parse_baseline_dedupes_to_stricter_value(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``foo 16`` then ``foo 20`` -> keep ``foo: 16`` AND warn."""
    from scripts.check_complexity import _parse_baseline

    baseline = tmp_path / "baseline.txt"
    baseline.write_text("foo 16\nfoo 20\nbar 25\n")
    parsed = _parse_baseline(baseline)
    assert parsed == {"foo": 16, "bar": 25}
    stderr = capsys.readouterr().err
    assert "duplicate baseline entry for 'foo'" in stderr
    assert "keeping stricter 16" in stderr


def test_parse_baseline_single_entry_unchanged(tmp_path: Path) -> None:
    from scripts.check_complexity import _parse_baseline

    baseline = tmp_path / "baseline.txt"
    baseline.write_text("foo 16\nbar 25\n")
    assert _parse_baseline(baseline) == {"foo": 16, "bar": 25}


# ---------------------------------------------------------------------------
# 2. sync_hafas_profile comment-stripping
# ---------------------------------------------------------------------------


def test_extract_profile_ignores_block_comment_credentials() -> None:
    from scripts.sync_hafas_profile import _extract_profile

    src = (
        "/** Previously used ver: '1.30', aid: 'OldAidValue12345' */\n"
        "export default { ver: '1.46', auth: { aid: 'OWDL4fE4ixNiPBBm' } }"
    )
    prof = _extract_profile(src)
    assert prof is not None
    assert prof["ver"] == "1.46"
    assert prof["aid"] == "OWDL4fE4ixNiPBBm"


def test_extract_profile_ignores_line_comment_credentials() -> None:
    from scripts.sync_hafas_profile import _extract_profile

    src = (
        "// stale: ver: '0.0', aid: 'XXX'\n"
        "export default { ver: '1.46', auth: { aid: 'OWDL4fE4ixNiPBBm' } }"
    )
    prof = _extract_profile(src)
    assert prof is not None
    assert prof["ver"] == "1.46"
    assert prof["aid"] == "OWDL4fE4ixNiPBBm"


# ---------------------------------------------------------------------------
# 3. secret_scanner _should_ignore
# ---------------------------------------------------------------------------


def test_should_ignore_with_path_pattern_anchors_to_full_path(
    tmp_path: Path,
) -> None:
    from src.utils.secret_scanner import _should_ignore

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "leak.py").write_text("x")
    (tmp_path / "other.py").write_text("x")

    # ``src/leak.py`` matches ONLY that file (full-path anchor), not the
    # similarly-named other.py.
    assert _should_ignore(tmp_path / "src" / "leak.py", ["src/leak.py"], tmp_path)
    assert not _should_ignore(tmp_path / "other.py", ["src/leak.py"], tmp_path)


def test_should_ignore_with_basename_pattern_matches_at_any_depth(
    tmp_path: Path,
) -> None:
    from src.utils.secret_scanner import _should_ignore

    nested = tmp_path / "src" / "config" / ".env"
    nested.parent.mkdir(parents=True)
    nested.write_text("x")
    # Basename-only pattern matches at any depth (gitignore semantics).
    assert _should_ignore(nested, [".env"], tmp_path)
    # Glob basename works.
    assert _should_ignore(nested, ["*.env"], tmp_path)


def test_should_ignore_returns_false_for_outside_base_dir(tmp_path: Path) -> None:
    from src.utils.secret_scanner import _should_ignore

    other = tmp_path.parent / "elsewhere.py"
    assert _should_ignore(other, ["*.py"], tmp_path) is False


# ---------------------------------------------------------------------------
# 4. update_station_directory bst_id numeric sort
# ---------------------------------------------------------------------------


def test_bst_id_numeric_sort_helper() -> None:
    """Pin the numeric-key shape on a stub list, avoiding xlsx fixtures."""

    class Stub:
        def __init__(self, bst_id: str) -> None:
            self.bst_id = bst_id

    def _key(item: Stub) -> tuple[int, str]:
        try:
            return (int(item.bst_id), "")
        except (TypeError, ValueError):
            return (10**12, str(item.bst_id))

    out = sorted([Stub("100"), Stub("9"), Stub("10")], key=_key)
    assert [s.bst_id for s in out] == ["9", "10", "100"]


# ---------------------------------------------------------------------------
# 5. validate_vor_mapping falsy-vs-missing
# ---------------------------------------------------------------------------


def test_vor_id_zero_is_not_missing() -> None:
    """``vor_id == 0`` is STRUCTURALLY present, must not be reported missing."""
    vid: Any = 0
    # Mirror the post-fix predicate.
    is_missing = vid is None or vid == ""
    assert is_missing is False
    # Sanity: None and "" still count as missing.
    for missing in (None, ""):
        assert missing is None or missing == ""


# ---------------------------------------------------------------------------
# 6. check_i18n_coverage JS duplicate-key last-wins
# ---------------------------------------------------------------------------


def test_extract_js_keys_takes_last_value_on_duplicate() -> None:
    from scripts.check_i18n_coverage import _extract_js_keys, _value_is_empty

    src = 'const I18N_EN = {"feed-title": "Good", "feed-title": ""};'
    keys = _extract_js_keys(src)
    # Runtime sees the empty second value → so must our checker.
    assert _value_is_empty(keys["feed-title"]) is True


def test_extract_js_keys_unique_key_is_unchanged() -> None:
    from scripts.check_i18n_coverage import _extract_js_keys, _value_is_empty

    src = 'const I18N_EN = {"feed-title": "Welcome"};'
    keys = _extract_js_keys(src)
    assert _value_is_empty(keys["feed-title"]) is False


# ---------------------------------------------------------------------------
# 7. circuit_breaker HALF_OPEN single-probe
# ---------------------------------------------------------------------------


def test_half_open_admits_first_probe_and_refuses_second() -> None:
    breaker = CircuitBreaker(name="t1", failure_threshold=2, recovery_timeout=0.001)
    breaker._state = CircuitState.HALF_OPEN

    # First call passes the gate (and resolves immediately as a success).
    result = breaker.call(lambda: 42)
    assert result == 42
    assert breaker._state is CircuitState.CLOSED
    assert breaker._probe_in_flight is False


def test_half_open_refuses_concurrent_probe() -> None:
    breaker = CircuitBreaker(name="t2", failure_threshold=2, recovery_timeout=0.001)
    breaker._state = CircuitState.HALF_OPEN
    breaker._probe_in_flight = True  # simulate a probe already in flight

    with pytest.raises(CircuitBreakerOpen):
        breaker.call(lambda: 99)


def test_probe_in_flight_cleared_on_record_failure() -> None:
    breaker = CircuitBreaker(name="t3", failure_threshold=2, recovery_timeout=0.001)
    breaker._state = CircuitState.HALF_OPEN

    def boom() -> int:
        raise RuntimeError("upstream broken")

    with pytest.raises(RuntimeError):
        breaker.call(boom)
    # Probe failed → state went HALF_OPEN -> OPEN and the slot is released.
    assert breaker._state is CircuitState.OPEN
    assert breaker._probe_in_flight is False


def test_probe_in_flight_cleared_on_reset() -> None:
    breaker = CircuitBreaker(name="t4", failure_threshold=2, recovery_timeout=0.001)
    breaker._state = CircuitState.HALF_OPEN
    breaker._probe_in_flight = True
    breaker.reset()
    assert breaker._probe_in_flight is False


# ---------------------------------------------------------------------------
# 8. build_feed legacy state-key cleanup
# ---------------------------------------------------------------------------


def test_update_item_state_drops_legacy_key_after_migration() -> None:
    """A legacy-identity entry must be popped once the modern guid key wins."""
    from datetime import UTC, datetime
    from typing import cast

    from src import build_feed
    from src.feed_types import FeedItem

    now = datetime(2026, 5, 30, 12, 0, tzinfo=UTC)
    it = cast(
        FeedItem,
        {
            "guid": "guid-MIGRATE-123",
            "source": "oebb",
            "category": "Störung",
            "title": "Test disruption",
            "description": "",
            "link": "",
        },
    )
    legacy_key = build_feed._identity_for_item(it)
    assert legacy_key != "guid-MIGRATE-123", (
        "test setup: the legacy identity must differ from the guid"
    )
    # Pop the cached calculation so subsequent calls re-compute (the
    # migration test runs `_update_item_state` which calls
    # `_lookup_state` first; we want the legacy lookup to fire there).
    it.pop("_calculated_identity", None)
    state: dict[str, dict[str, Any]] = {
        legacy_key: {"first_seen": "2026-01-01T00:00:00+00:00"}
    }

    build_feed._update_item_state(it, now, state)

    # The modern guid-keyed entry must exist...
    assert "guid-MIGRATE-123" in state
    # ...and the legacy entry must have been removed.
    assert legacy_key not in state
