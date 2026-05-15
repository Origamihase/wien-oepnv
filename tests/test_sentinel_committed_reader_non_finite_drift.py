"""Sentinel PoC: reader-side non-finite (``NaN`` / ``Infinity`` /
``-Infinity`` / scientific-notation-overflow) literal defence drift
across the committed-state-file reader landscape.

The 2026-05-14 / 2026-05-15 rounds (PR #1485, PR #1487, PR #1488,
PR #1491) pinned ``allow_nan=False`` on every committed-to-main
**writer**:

    * :func:`src.places.merge.write_stations`
      (``data/stations.json`` — Round 1485, the canonical coordinate
      writer)
    * 5 sibling ``data/stations.json`` writers + the unified
      :func:`src.utils.cache.write_cache` (Round 1487 — companion
      coordinate writers)
    * :func:`src.feed.reporting.write_feed_health_json`
      (``docs/feed-health.json``), :func:`src.utils.cache.write_status`,
      :meth:`src.places.quota.MonthlyQuota.save_atomic`,
      :func:`src.build_feed._save_state`,
      :func:`src.providers.vor._write_request_count_file`,
      :func:`scripts.update_all_stations._write_heartbeat_file`,
      :func:`scripts.update_all_stations._write_quarantine_file`,
      :func:`scripts.sync_hafas_profile._write_profile` (Round 1488 —
      non-coordinate sibling closure)
    * :class:`src.feed.logging_safe.SafeJSONFormatter` (PR #1491 —
      the ``json.dumps`` log-sink sibling)

The drift this round closes: **the reader-side parse path was NEVER
hardened**.  Python's ``json.loads`` lenient mode parses three
non-standard literal tokens — ``NaN`` / ``Infinity`` / ``-Infinity`` —
as ``float('nan')`` / ``float('inf')`` / ``float('-inf')`` and
silently overflows scientific-notation tokens like ``1e1000`` to
``float('inf')`` (via the default ``parse_float=float`` hook).  Every
``json.loads(raw)`` callsite in a committed-state-file reader is
therefore the **symmetric companion** to the writer-side pin: a
planted on-disk literal propagates as ``float('nan')`` / ``float
('inf')`` through the in-memory data structure WITHOUT the writer's
defence-in-depth ever firing — because the bytes never travel
through the writer in the first place; the reader reads them
directly.

Threat model (three concrete attacker positions)
================================================

1. **Compromised CI runner.**  A poisoned GitHub Actions runner
   (third-party action takeover, runner-image supply chain) writes
   ``NaN`` / ``Infinity`` / ``1e1000`` literals into
   ``data/first_seen.json`` / ``data/stations.json`` /
   ``cache/<provider>/events.json`` BEFORE the next cron tick.  The
   subsequent ``_load_state`` / ``load_stations`` / ``read_cache``
   call parses the planted literal lenient-mode and ``float('nan')``
   / ``float('inf')`` flows into the build pipeline.

2. **Parallel orchestrator atomic state swap.**  A second concurrent
   ``update_all_stations.py`` (cron-doubled, manual re-trigger,
   matrix-job) performs an ``os.replace(tmp, target)`` of
   ``data/stations.json`` between the size-cap stat and the
   ``json.loads`` of the first orchestrator.  The two writers are
   not coordinated (the file lock is per-script).  Race outcome:
   the open() returns the swapped inode; if the swapped file
   contains ``NaN`` literals (from a buggy or compromised second
   orchestrator), the first orchestrator's reader is now poisoned.

3. **Hostile PR landing a tampered fixture.**  An external
   contributor opens a PR that modifies
   ``tests/fixtures/<thing>.json`` to include ``NaN`` / ``Infinity``
   literals.  CI runs the consuming test, the file is read,
   ``float('nan')`` propagates through the test harness, the test
   passes (silent comparison: ``nan != nan`` is True), and the
   ``allow_nan=False`` writer-pin only fires on the round-trip back
   — which the test may not exercise.  The fixture lands on
   ``main`` and now every CI run reads it.

Impact
======

* **Silent comparison bugs.**  ``nan != nan`` returns ``True``.  Every
  dedup / first-seen / retention-cutoff comparison that uses ``!=``
  silently misbehaves: a planted ``first_seen: NaN`` in
  ``data/first_seen.json`` makes EVERY ``fs_utc < retention_cutoff``
  return ``False`` (NaN is incomparable), and the state entry is
  never pruned — unbounded state growth.

* **Silent arithmetic poisoning.**  ``nan + 5`` returns ``nan``.
  ``inf - inf`` returns ``nan``.  Every latency / duration / age
  averaging operation that pulls a planted non-finite from disk
  pollutes the entire averaged result.

* **Crash-on-round-trip via writer-pin.**  Post Round 1485 / 1487 /
  1488 / 1491 the writers reject ``allow_nan=False``: a planted NaN
  read in, propagated, and written back hits ``ValueError`` from
  ``json.dump(..., allow_nan=False)`` and the cron pipeline crashes
  mid-write.  Recovery requires manual operator intervention to
  delete or sanitise the planted state file — until then EVERY cron
  tick fails.

* **Scientific-notation-overflow bypass of ``parse_constant``.**
  ``parse_constant`` is invoked ONLY for the three literal tokens
  ``NaN`` / ``Infinity`` / ``-Infinity``.  A planted ``"x": 1e1000``
  literal is a syntactically-valid JSON NUMBER token, so
  ``parse_constant`` is NOT called — instead the default
  ``parse_float=float`` hook IEEE-754-overflows the value to
  ``float('inf')`` silently.  The companion ``parse_float`` hook
  re-checks ``math.isfinite`` and rejects the overflow at the parse
  boundary, closing this bypass.

Fix shape (canonical helpers + per-callsite pin)
================================================

Two canonical helpers in :mod:`src.utils.files`:

    * :func:`_reject_non_finite_constant` — ``parse_constant`` hook
      that raises ``json.JSONDecodeError`` on every NaN / Infinity /
      -Infinity token.
    * :func:`_reject_non_finite_float` — ``parse_float`` hook that
      re-checks ``math.isfinite`` after the standard ``float()``
      parse and raises ``json.JSONDecodeError`` on overflow /
      underflow to non-finite.

Every committed-state-file reader pins both hooks on its
``json.loads(...)`` call:

    json.loads(
        raw,
        parse_constant=_reject_non_finite_constant,
        parse_float=_reject_non_finite_float,
    )

Sites migrated this round (8 production sites in src/):

    * :func:`src.utils.files.read_capped_json` (the canonical helper)
    * :func:`src.utils.stations._read_capped_json` (duplicate helper
      used for ``data/stations.json`` + ``vienna_polygons.json``)
    * :func:`src.build_feed._load_state` (inline reader for
      ``data/first_seen.json``)
    * :func:`src.utils.cache.read_cache` (``cache/<provider>/events.json``)
    * :func:`src.utils.cache.write_cache` (existing-cache check inside
      the writer for the data-degradation guard)
    * :func:`src.utils.cache.read_status`
      (``cache/<provider>/last_run.json``)
    * :meth:`src.places.quota.MonthlyQuota.load`
      (``data/places_quota.json``)
    * :func:`src.places.merge.load_stations` (``data/stations.json``)
    * :func:`src.places.tiling.load_tiles_from_env` (``PLACES_TILES``
      env value)
    * :func:`src.places.tiling.load_tiles_from_file` (tile config file)
    * :func:`src.utils.stations_validation._load_stations`
      (``data/stations.json`` validator)

Inventory tests (source-grep)
=============================

Every reader's source must contain ``parse_constant=`` AND
``parse_float=`` on its ``json.loads(...)`` call.  A future
refactor that drops either hook re-fails the gate at PR-review
time.

Behavioural PoC tests
=====================

For each canonical reader: read a planted ``NaN`` literal from
on-disk bytes, verify that the post-fix reader either (a) returns
``None`` (the size-cap helpers' fail-secure recovery shape) or
(b) raises the canonical ``ValueError`` / ``StationValidationError``
/ ``json.JSONDecodeError`` per its existing contract.
"""

from __future__ import annotations

import inspect
import json
import math
from pathlib import Path
from typing import Any

import pytest


# Sentinel marker shared by every PoC site so a future grep for
# ``SENTINEL_COMMITTED_READER_NON_FINITE_DRIFT`` finds the full
# call-graph at once.
SENTINEL_COMMITTED_READER_NON_FINITE_DRIFT = (
    "committed reader non-finite literal drift"
)


# ---------------------------------------------------------------------------
# Canonical-helper behavioural tests (the foundation).
# ---------------------------------------------------------------------------


def test_canonical_reject_non_finite_constant_rejects_NaN_token() -> None:
    """``_reject_non_finite_constant`` MUST raise JSONDecodeError on NaN."""
    from src.utils.files import _reject_non_finite_constant

    with pytest.raises(json.JSONDecodeError):
        _reject_non_finite_constant("NaN")


def test_canonical_reject_non_finite_constant_rejects_Infinity_tokens() -> None:
    """Hook must reject +Infinity and -Infinity tokens identically."""
    from src.utils.files import _reject_non_finite_constant

    with pytest.raises(json.JSONDecodeError):
        _reject_non_finite_constant("Infinity")
    with pytest.raises(json.JSONDecodeError):
        _reject_non_finite_constant("-Infinity")


def test_canonical_reject_non_finite_float_accepts_finite_floats() -> None:
    """``_reject_non_finite_float`` MUST pass through finite floats unchanged."""
    from src.utils.files import _reject_non_finite_float

    assert _reject_non_finite_float("3.14") == pytest.approx(3.14)
    assert _reject_non_finite_float("0.0") == 0.0
    assert _reject_non_finite_float("-273.15") == pytest.approx(-273.15)
    # Underflow to 0.0 is finite and ACCEPTED — loss of precision is a
    # separate threat model (RFC 8259 conformance is the bound here).
    assert _reject_non_finite_float("1e-1000") == 0.0


def test_canonical_reject_non_finite_float_rejects_scientific_overflow() -> None:
    """Hook must reject the scientific-notation-overflow bypass.

    A planted ``1e1000`` JSON number is syntactically valid and is NOT
    one of the three constant tokens — so ``parse_constant`` is NOT
    invoked.  Instead the default ``parse_float=float`` hook
    IEEE-754-overflows to ``float('inf')`` silently.  The
    ``parse_float`` hook re-checks ``math.isfinite`` and rejects.
    """
    from src.utils.files import _reject_non_finite_float

    with pytest.raises(json.JSONDecodeError):
        _reject_non_finite_float("1e1000")
    with pytest.raises(json.JSONDecodeError):
        _reject_non_finite_float("-1e1000")


def test_canonical_strict_loads_round_trips_finite_payload() -> None:
    """The fix MUST NOT regress legitimate finite payloads.

    A standard ``json.loads`` with both hooks attached must parse a
    real-world stations entry (lat/lon, integer counts, string fields)
    unchanged.
    """
    from src.utils.files import (
        _reject_non_finite_constant,
        _reject_non_finite_float,
    )

    payload = json.dumps(
        {
            "name": "Wien Hauptbahnhof",
            "latitude": 48.18568,
            "longitude": 16.37534,
            "elevation_m": 187.0,
            "platform_count": 12,
        },
        ensure_ascii=True,
    )
    parsed = json.loads(
        payload,
        parse_constant=_reject_non_finite_constant,
        parse_float=_reject_non_finite_float,
    )
    assert isinstance(parsed, dict)
    assert parsed["name"] == "Wien Hauptbahnhof"
    assert math.isfinite(parsed["latitude"])
    assert math.isfinite(parsed["longitude"])


# ---------------------------------------------------------------------------
# Inventory pins (source-grep): every reader must carry both hooks.
# ---------------------------------------------------------------------------


def _assert_parse_constant_pin(func: Any, *, where: str) -> None:
    """Assert that ``func``'s source pins BOTH parse_constant + parse_float."""
    source = inspect.getsource(func)
    assert "parse_constant=_reject_non_finite_constant" in source, (
        f"{where}: missing ``parse_constant=_reject_non_finite_constant`` "
        f"pin on the ``json.loads`` call.  A planted NaN / Infinity literal "
        f"in the on-disk payload propagates as ``float('nan')`` / "
        f"``float('inf')`` into Python computation.\n\nMarker: "
        f"{SENTINEL_COMMITTED_READER_NON_FINITE_DRIFT}"
    )
    assert "parse_float=_reject_non_finite_float" in source, (
        f"{where}: missing ``parse_float=_reject_non_finite_float`` "
        f"pin on the ``json.loads`` call.  A planted ``1e1000`` scientific-"
        f"notation overflow bypasses ``parse_constant`` and lands "
        f"``float('inf')`` in the parsed structure.\n\nMarker: "
        f"{SENTINEL_COMMITTED_READER_NON_FINITE_DRIFT}"
    )


def test_inventory_read_capped_json_pins_hooks() -> None:
    from src.utils.files import read_capped_json

    _assert_parse_constant_pin(
        read_capped_json,
        where="src/utils/files.py:read_capped_json (the canonical reader)",
    )


def test_inventory_stations_read_capped_json_pins_hooks() -> None:
    from src.utils.stations import _read_capped_json

    _assert_parse_constant_pin(
        _read_capped_json,
        where=(
            "src/utils/stations.py:_read_capped_json "
            "(duplicate helper for stations + Vienna polygons)"
        ),
    )


def test_inventory_build_feed_load_state_pins_hooks() -> None:
    from src import build_feed

    _assert_parse_constant_pin(
        build_feed._load_state,
        where="src/build_feed.py:_load_state (data/first_seen.json)",
    )


def test_inventory_cache_read_cache_pins_hooks() -> None:
    from src.utils import cache

    _assert_parse_constant_pin(
        cache.read_cache,
        where="src/utils/cache.py:read_cache (cache/<provider>/events.json)",
    )


def test_inventory_cache_write_cache_existing_check_pins_hooks() -> None:
    """The existing-cache-data-degradation check inside ``write_cache``
    is a JSON reader that must carry the pin too."""
    from src.utils import cache

    _assert_parse_constant_pin(
        cache.write_cache,
        where=(
            "src/utils/cache.py:write_cache "
            "(existing-cache data-degradation read path)"
        ),
    )


def test_inventory_cache_read_status_pins_hooks() -> None:
    from src.utils import cache

    _assert_parse_constant_pin(
        cache.read_status,
        where="src/utils/cache.py:read_status (cache/<provider>/last_run.json)",
    )


def test_inventory_monthly_quota_load_pins_hooks() -> None:
    from src.places.quota import MonthlyQuota

    _assert_parse_constant_pin(
        MonthlyQuota.load,
        where="src/places/quota.py:MonthlyQuota.load (data/places_quota.json)",
    )


def test_inventory_places_load_stations_pins_hooks() -> None:
    from src.places.merge import load_stations

    _assert_parse_constant_pin(
        load_stations,
        where="src/places/merge.py:load_stations (data/stations.json)",
    )


def test_inventory_tiling_load_tiles_from_env_pins_hooks() -> None:
    from src.places.tiling import load_tiles_from_env

    _assert_parse_constant_pin(
        load_tiles_from_env,
        where="src/places/tiling.py:load_tiles_from_env (PLACES_TILES env)",
    )


def test_inventory_tiling_load_tiles_from_file_pins_hooks() -> None:
    from src.places.tiling import load_tiles_from_file

    _assert_parse_constant_pin(
        load_tiles_from_file,
        where="src/places/tiling.py:load_tiles_from_file (tile config file)",
    )


def test_inventory_stations_validation_load_pins_hooks() -> None:
    from src.utils import stations_validation

    _assert_parse_constant_pin(
        stations_validation._load_stations,
        where=(
            "src/utils/stations_validation.py:_load_stations "
            "(data/stations.json validator)"
        ),
    )


# ---------------------------------------------------------------------------
# Behavioural PoC: every fixed reader must reject planted non-finite literals.
# ---------------------------------------------------------------------------


def test_poc_read_capped_json_rejects_planted_NaN_literal(tmp_path: Path) -> None:
    """The canonical reader must treat a NaN-poisoned file as missing/invalid.

    PRE-FIX: ``json.loads(raw)`` parses ``{"x": NaN}`` lenient-mode and
    returns ``{"x": float('nan')}``.  The caller sees a "valid" parse
    and propagates the non-finite float downstream.

    POST-FIX: ``parse_constant=_reject_non_finite_constant`` raises
    ``json.JSONDecodeError``, the surrounding handler returns ``None``,
    and the caller falls through to its empty-state recovery path.

    Marker: SENTINEL_COMMITTED_READER_NON_FINITE_DRIFT.
    """
    from src.utils.files import read_capped_json

    poisoned = tmp_path / "state.json"
    poisoned.write_bytes(b'{"first_seen": "2026-05-15T00:00:00+00:00", "value": NaN}')

    result = read_capped_json(poisoned, label="state")
    # Post-fix: planted NaN is treated as a corrupt file → None.
    assert result is None, (
        "read_capped_json failed to reject planted NaN literal — the "
        "writer-side ``allow_nan=False`` defence has no symmetric "
        "reader-side counterpart."
    )


def test_poc_read_capped_json_rejects_planted_Infinity_literal(tmp_path: Path) -> None:
    """Mirror PoC for ``Infinity`` literal."""
    from src.utils.files import read_capped_json

    poisoned = tmp_path / "state.json"
    poisoned.write_bytes(b'{"duration_s": Infinity}')

    assert read_capped_json(poisoned, label="state") is None


def test_poc_read_capped_json_rejects_scientific_overflow(tmp_path: Path) -> None:
    """A planted ``1e1000`` overflow bypasses ``parse_constant`` but is
    caught by the ``parse_float`` hook."""
    from src.utils.files import read_capped_json

    poisoned = tmp_path / "state.json"
    poisoned.write_bytes(b'{"latency_s": 1e1000}')

    assert read_capped_json(poisoned, label="state") is None


def test_poc_read_capped_json_finite_payload_round_trips(tmp_path: Path) -> None:
    """The fix must NOT regress legitimate finite-float payloads."""
    from src.utils.files import read_capped_json

    legitimate = tmp_path / "state.json"
    legitimate.write_text(
        json.dumps(
            {
                "latitude": 48.18568,
                "longitude": 16.37534,
                "first_seen": "2026-05-15T00:00:00+00:00",
            },
            ensure_ascii=True,
        ),
        encoding="utf-8",
    )

    result = read_capped_json(legitimate, label="state")
    assert isinstance(result, dict)
    assert result["latitude"] == pytest.approx(48.18568)
    assert result["longitude"] == pytest.approx(16.37534)


def test_poc_load_stations_rejects_planted_NaN_literal(tmp_path: Path) -> None:
    """``load_stations`` must raise on a planted NaN literal.

    PRE-FIX: the in-memory list contains an entry with
    ``latitude: float('nan')``.  Downstream merge / haversine math
    propagates NaN through every coordinate comparison.

    POST-FIX: ``parse_constant`` raises ``json.JSONDecodeError`` and the
    surrounding handler re-raises as ``ValueError`` per the existing
    contract.

    Marker: SENTINEL_COMMITTED_READER_NON_FINITE_DRIFT.
    """
    from src.places.merge import load_stations

    poisoned = tmp_path / "stations.json"
    poisoned.write_bytes(
        b'[{"name": "Test", "latitude": NaN, "longitude": 16.37}]'
    )

    with pytest.raises(ValueError):
        load_stations(poisoned)


def test_poc_load_stations_rejects_scientific_overflow(tmp_path: Path) -> None:
    """``load_stations`` must raise on ``1e1000`` overflow."""
    from src.places.merge import load_stations

    poisoned = tmp_path / "stations.json"
    poisoned.write_bytes(
        b'[{"name": "Test", "latitude": 48.18, "longitude": 1e1000}]'
    )

    with pytest.raises(ValueError):
        load_stations(poisoned)


def test_poc_load_stations_finite_payload_round_trips(tmp_path: Path) -> None:
    """``load_stations`` must NOT regress on a legitimate finite payload."""
    from src.places.merge import load_stations

    legitimate = tmp_path / "stations.json"
    legitimate.write_text(
        json.dumps(
            [
                {
                    "name": "Wien Hauptbahnhof",
                    "latitude": 48.18568,
                    "longitude": 16.37534,
                }
            ],
            ensure_ascii=True,
        ),
        encoding="utf-8",
    )

    stations = load_stations(legitimate)
    assert len(stations) == 1
    assert stations[0]["name"] == "Wien Hauptbahnhof"
    assert math.isfinite(float(stations[0]["latitude"]))
    assert math.isfinite(float(stations[0]["longitude"]))


def test_poc_monthly_quota_load_rejects_planted_NaN_literal(tmp_path: Path) -> None:
    """``MonthlyQuota.load`` must raise on planted NaN.

    The current writer casts every numeric to ``int(...)`` so a NaN
    cannot legitimately reach the file via the writer path.  But the
    threat model is a PLANTED file — bypassing the writer entirely.
    """
    from src.places.quota import MonthlyQuota

    poisoned = tmp_path / "places_quota.json"
    poisoned.write_bytes(
        b'{"month": "2026-05", "counts": {}, "total": 0, '
        b'"daily_key": "2026-05-15", "daily_total": NaN}'
    )

    with pytest.raises(ValueError):
        MonthlyQuota.load(poisoned)


def test_poc_load_tiles_from_env_rejects_planted_NaN_literal() -> None:
    """``load_tiles_from_env`` must raise on planted NaN coordinates."""
    from src.places.tiling import load_tiles_from_env

    with pytest.raises(ValueError):
        load_tiles_from_env('[{"latitude": NaN, "longitude": 16.37}]')


def test_poc_load_tiles_from_env_rejects_scientific_overflow() -> None:
    """``load_tiles_from_env`` must raise on ``1e1000`` overflow."""
    from src.places.tiling import load_tiles_from_env

    with pytest.raises(ValueError):
        load_tiles_from_env('[{"latitude": 1e1000, "longitude": 16.37}]')


def test_poc_load_tiles_from_file_rejects_planted_NaN_literal(
    tmp_path: Path,
) -> None:
    """``load_tiles_from_file`` must raise on planted NaN coordinates."""
    from src.places.tiling import load_tiles_from_file

    poisoned = tmp_path / "tiles.json"
    poisoned.write_bytes(b'[{"latitude": NaN, "longitude": 16.37}]')

    with pytest.raises(ValueError):
        load_tiles_from_file(poisoned)


def test_poc_stations_validation_rejects_planted_NaN_literal(tmp_path: Path) -> None:
    """``_load_stations`` validator must raise on planted NaN."""
    from src.utils.stations_validation import (
        StationValidationError,
        _load_stations,
    )

    poisoned = tmp_path / "stations.json"
    poisoned.write_bytes(
        b'[{"name": "Test", "latitude": NaN, "longitude": 16.37}]'
    )

    with pytest.raises(StationValidationError):
        _load_stations(poisoned)


def test_poc_cache_read_cache_returns_empty_on_planted_NaN(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``read_cache`` must treat a NaN-poisoned cache as invalid (empty).

    The caller treats an unparseable cache as a fresh start — the
    planted literal flows through the ``except json.JSONDecodeError``
    branch and out as an empty list.
    """
    from src.utils import cache as cache_module

    monkeypatch.setattr(cache_module, "_CACHE_DIR", tmp_path)
    # ``_cache_file`` builds the canonical path via ``safe_path_join``;
    # write the poisoned bytes there directly.
    cache_path = cache_module._cache_file("test-provider-poisoned")
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_bytes(b'[{"title": "Test", "duration_s": NaN}]')

    # read_cache returns [] when JSON is invalid; planted NaN now triggers
    # that path.
    result = cache_module.read_cache("test-provider-poisoned")
    assert result == [], (
        "read_cache failed to reject planted NaN literal — propagated "
        "float('nan') into the feed-build dedup pipeline."
    )


def test_poc_cache_read_status_returns_none_on_planted_Infinity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``read_status`` must treat an Infinity-poisoned status file as missing."""
    from src.utils import cache as cache_module

    monkeypatch.setattr(cache_module, "_CACHE_DIR", tmp_path)
    status_path = cache_module._status_file("test-provider-poisoned-status")
    status_path.parent.mkdir(parents=True, exist_ok=True)
    status_path.write_bytes(b'{"status": "ok", "latency_s": Infinity}')

    # read_status returns None when JSON is invalid; planted Infinity now
    # triggers that path.
    assert cache_module.read_status("test-provider-poisoned-status") is None


# ---------------------------------------------------------------------------
# Symmetry check: writer-pin + reader-pin together close the round-trip.
# ---------------------------------------------------------------------------


def test_symmetry_writer_pin_plus_reader_pin_close_round_trip(
    tmp_path: Path,
) -> None:
    """End-to-end: a writer cannot produce a NaN AND a reader cannot
    consume one — the round-trip is closed at BOTH ends.

    This is the canonical-defence proof: the writer-side
    ``allow_nan=False`` pin (Round 1485+) raises ``ValueError`` on
    NaN-bearing write, AND the reader-side ``parse_constant`` +
    ``parse_float`` hooks (this round) raise ``json.JSONDecodeError``
    on NaN-bearing read.  No path through either boundary admits the
    non-finite literal.
    """
    from src.utils.files import (
        _reject_non_finite_constant,
        _reject_non_finite_float,
    )

    # Write-side: ``allow_nan=False`` raises on NaN.
    with pytest.raises(ValueError):
        json.dumps({"x": float("nan")}, allow_nan=False)

    # Read-side: ``parse_constant`` raises on NaN literal.
    with pytest.raises(json.JSONDecodeError):
        json.loads(
            '{"x": NaN}',
            parse_constant=_reject_non_finite_constant,
            parse_float=_reject_non_finite_float,
        )

    # Read-side: ``parse_float`` raises on scientific-notation overflow.
    with pytest.raises(json.JSONDecodeError):
        json.loads(
            '{"x": 1e1000}',
            parse_constant=_reject_non_finite_constant,
            parse_float=_reject_non_finite_float,
        )
