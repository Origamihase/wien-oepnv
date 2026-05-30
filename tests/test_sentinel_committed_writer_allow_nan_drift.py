"""Sentinel PoC: ``allow_nan=False`` writer-defence drift across the
*non-coordinate* committed-to-main JSON writer family.

The 2026-05-14 coordinate finite/range round (PR #1485, commit
``31570fe``) pinned ``allow_nan=False`` on
:func:`src.places.merge.write_stations` and the 2026-05-14 companion
round (PR #1487, commit ``123ed25``) extended the pin to the five
sibling ``data/stations.json`` writers + ``src.utils.cache.write_cache``
+ the Baustellen parser.  Those rounds enumerated only the **coordinate
emitting** writer/parser landscape (``data/stations.json``,
``cache/<provider>/events.json``).

The drift this round closes: **eight additional writer sites all emit
committed-to-main JSON artefacts but only the canonical coordinate
writers carry the** ``allow_nan=False`` **pin.**  These sibling
writers were closed by PR #1434 / PR #1435 ("Trojan-Source / BiDi-Mark
Drift Round 11") against the Trojan-Source attack-byte union via
``ensure_ascii=True`` (or, for ``write_feed_health_json``, via the
matching ``_CONTROL_CHARS_RE.sub("")`` scrubs at the payload-build
boundary), but the writer-side defence-in-depth pin against
non-standard ``NaN`` / ``Infinity`` / ``-Infinity`` literals (invalid
per RFC 8259) was never added.

Sites enumerated
================

All eight writers below land into committed-to-main artefacts (data/,
docs/, or cache/ paths) and accept ``dict[str, Any]`` / float-bearing
payloads from caller code that touches operator-, provider-, or
environment-controlled values.

(A) Public ``docs/`` artefact — HIGHEST IMPACT:

    1. :func:`src.feed.reporting.write_feed_health_json`
       (``docs/feed-health.json``) — written by the feed builder on
       every cron tick, committed to ``main`` by ``build-feed.yml``,
       served on GitHub Pages, consumed by external SIEM dashboards.
       Carries ``providers[i].duration`` (``float | None`` from
       :class:`src.feed.reporting.ProviderReport.duration`) and
       ``durations`` (``dict[str, float]`` from
       :attr:`src.feed.reporting.RunReport.durations`) — **concrete
       float types** that accept ``float('nan')`` from any caller
       that programmatically sets them (``ProviderReport.record(
       duration=float('nan'))`` or
       ``RunReport.finish(durations={'k': float('nan')})``).

(B) Committed-to-main ``data/`` sidecars:

    2. :func:`src.utils.cache.write_status`
       (``cache/<provider>/last_run.json``) — heartbeat helper.  The
       status payload is a public ``dict[str, Any]`` so any future
       caller adding a float field (latency, response_size_ratio …)
       inherits the missing-pin.
    3. :meth:`src.places.quota.MonthlyQuota.save_atomic`
       (``data/places_quota.json``) — Google Places quota state.
       Current writer casts every numeric to ``int(...)`` but the
       defence-in-depth pin would surface a future ``float`` field
       (e.g. fractional cost accounting) as a loud ValueError.
    4. :func:`src.build_feed._save_state` (the inline
       ``json.dump(merged_state, ...)``) — ``data/first_seen.json``.
       The state dict's values are ISO date strings today but the
       ``Any``-typed value slot accepts any JSON literal that
       ``json.loads`` returns — including ``float('nan')`` from a
       compromised previous-run state file that re-roundtrips.
    5. :func:`src.providers.vor._write_request_count_file`
       (``data/vor_request_count.json``) — Mapping[str, Any] payload,
       same Any-typed surface.

(C) Committed-to-main heartbeat / orchestrator state in scripts/:

    6. :func:`scripts.update_all_stations._write_heartbeat_file`
       (``data/stations_last_run.json``) — orchestrator heartbeat.
    7. :func:`scripts.update_all_stations._write_quarantine_file`
       (``data/quarantine.json``) — validator quarantine sidecar
       that COPIES OPERATOR-FACING ENTRY CONTENT verbatim into
       the JSON output via ``"entry": dict(entry)``.  Quarantined
       stations are by definition flagged as carrying unsafe
       content; a poisoned coordinate from the same source that
       triggered the quarantine slips through the writer without
       the pin.
    8. :func:`scripts.sync_hafas_profile._write_profile`
       (``data/hafas_profile.json``) — HAFAS Mgate credentials
       sidecar.  Input is regex-extracted from upstream JavaScript
       so present-day NaN risk is low, but the writer is the
       defensive line if the regex broadens or a different
       extraction strategy is wired in.

Threat model
============

Three distinct attacker positions can plant ``NaN`` / ``Infinity`` /
``-Infinity`` literals into the cron pipeline:

  1. **Programmatic in-process injection**.  Any caller of
     :meth:`ProviderReport.record` / :meth:`RunReport.finish` may
     pass ``duration=float('nan')`` or ``durations={'k': float('nan')}``
     directly.  Today's call-graph uses :func:`perf_counter` deltas
     that are always finite, but a future provider plugin that wraps
     a third-party SDK (e.g. a Prometheus-style instrumentation
     library that surfaces ``NaN`` for missing observations) lands
     the bytes verbatim into the public ``docs/feed-health.json``.

  2. **Poisoned on-disk state file round-tripped**.  ``_save_state``
     reads ``data/first_seen.json`` via ``json.loads`` (Python's
     default lenient mode parses ``NaN`` / ``Infinity`` literals as
     :func:`float`-NaN / -Inf) and writes it back.  A planted
     non-standard literal survives the round-trip.

  3. **Compromised upstream** (HAFAS profile case — low risk under
     the current regex, but the pin is the defensive line).

Public sinks impacted
=====================

  * ``docs/feed-health.json`` — committed to ``main`` by
    ``build-feed.yml`` on every cron tick, served on GitHub Pages,
    consumed by external monitoring dashboards.  **A single NaN /
    Infinity literal breaks every conforming JSON parser
    (``JSON.parse`` in every modern browser, ``serde_json`` Rust
    strict mode, ``encoding/json`` Go) — the SIEM dashboards go
    blind for that build cycle.**

  * Six committed-to-main ``data/*.json`` sidecars consumed by the
    cron orchestrator and reviewed via ``cat`` / GitHub web UI / IDE
    preview — operator confusion + downstream-consumer breakage.

  * One ``cache/<provider>/last_run.json`` heartbeat (currently
    without active callers but pinned by sentinel coverage).

Severity: **MEDIUM** — public-artefact data-integrity attack with an
in-process programmatic path (no upstream control required). Same
shape class as the Round 1485 / Round 1487 drift but for the
non-coordinate writer landscape.

The fix
=======

Eight coordinated edits, all pinned by this test file:

  1. ``src/feed/reporting.py:write_feed_health_json`` —
     pass ``allow_nan=False`` to ``json.dump``.
  2. ``src/utils/cache.py:write_status`` — same.
  3. ``src/places/quota.py:MonthlyQuota.save_atomic`` — same.
  4. ``src/build_feed.py:_save_state`` — same.
  5. ``src/providers/vor.py:_write_request_count_file`` — same.
  6. ``scripts/update_all_stations.py:_write_heartbeat_file`` — same.
  7. ``scripts/update_all_stations.py:_write_quarantine_file`` — same.
  8. ``scripts/sync_hafas_profile.py:_write_profile`` — same.

Inventory invariant
===================

Every committed-to-main JSON writer that may carry ``Any``-typed
float values must pin ``allow_nan=False`` (writer-side defence-in-
depth).  The ``test_inventory_*`` cases below each load the function
source via :func:`inspect.getsource` and assert that the literal
``allow_nan=False`` is part of the call — any future edit that drops
the contract (or copies the function shape into a new sibling writer
without the pin) fails the test on the next pytest run.
"""

from __future__ import annotations

import importlib
import inspect
import json
import math
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


# Sentinel marker shared by every PoC site so a future grep for
# ``SENTINEL_COMMITTED_WRITER_ALLOW_NAN_DRIFT`` finds the full
# call-graph at once.
SENTINEL_COMMITTED_WRITER_ALLOW_NAN_DRIFT = (
    "committed writer allow_nan=False drift"
)


def _raise_on_nan_or_infinity(token: str) -> float:
    """Strict-JSON ``parse_constant`` hook: refuse any non-finite literal.

    Mirrors the reader-side defence pinned in
    ``tests/test_sentinel_coordinate_nan_inf_range_drift.py``.
    """
    raise ValueError(f"Non-finite JSON literal {token!r} in committed artefact")


def _import_script(name: str) -> Any:
    """Import a ``scripts/<name>.py`` module and return it."""
    module = importlib.import_module(name)
    return module


# ---------------------------------------------------------------------------
# Inventory pins (source-grep): every writer must carry ``allow_nan=False``.
# ---------------------------------------------------------------------------


def _assert_allow_nan_pin(func: Any, *, where: str) -> None:
    """Assert that ``func``'s source contains the ``allow_nan=False`` pin."""
    source = inspect.getsource(func)
    assert "allow_nan=False" in source, (
        f"{where}: missing ``allow_nan=False`` pin — non-standard NaN / "
        f"Infinity / -Infinity literals (invalid per RFC 8259) would land "
        f"in the committed artefact and break every strict JSON parser "
        f"downstream.\n\nMarker: {SENTINEL_COMMITTED_WRITER_ALLOW_NAN_DRIFT}"
    )


def test_inventory_write_feed_health_json_pins_allow_nan() -> None:
    from src.feed.reporting import write_feed_health_json

    _assert_allow_nan_pin(
        write_feed_health_json,
        where="src/feed/reporting.py:write_feed_health_json (docs/feed-health.json)",
    )


def test_inventory_write_status_pins_allow_nan() -> None:
    from src.utils.cache import write_status

    _assert_allow_nan_pin(
        write_status,
        where="src/utils/cache.py:write_status (cache/<provider>/last_run.json)",
    )


def test_inventory_monthly_quota_save_atomic_pins_allow_nan() -> None:
    from src.places.quota import MonthlyQuota

    _assert_allow_nan_pin(
        MonthlyQuota.save_atomic,
        where=(
            "src/places/quota.py:MonthlyQuota.save_atomic "
            "(data/places_quota.json)"
        ),
    )


def test_inventory_build_feed_save_state_pins_allow_nan() -> None:
    from src import build_feed

    # The ``json.dump(merged_state, ...)`` writer lives in the
    # ``_write_merged_state`` helper that ``_save_state`` calls under the lock
    # (extracted so the exclusive-lock site sits next to its failure handler).
    _assert_allow_nan_pin(
        build_feed._write_merged_state,
        where="src/build_feed.py:_write_merged_state (data/first_seen.json)",
    )


def test_inventory_vor_write_request_count_file_pins_allow_nan() -> None:
    from src.providers import vor

    _assert_allow_nan_pin(
        vor._write_request_count_file,
        where="src/providers/vor.py:_write_request_count_file (data/vor_request_count.json)",
    )


def test_inventory_update_all_stations_write_heartbeat_pins_allow_nan() -> None:
    module = _import_script("update_all_stations")

    _assert_allow_nan_pin(
        module._write_heartbeat_file,
        where=(
            "scripts/update_all_stations.py:_write_heartbeat_file "
            "(data/stations_last_run.json)"
        ),
    )


def test_inventory_update_all_stations_write_quarantine_pins_allow_nan() -> None:
    module = _import_script("update_all_stations")

    _assert_allow_nan_pin(
        module._write_quarantine_file,
        where=(
            "scripts/update_all_stations.py:_write_quarantine_file "
            "(data/quarantine.json)"
        ),
    )


def test_inventory_sync_hafas_profile_write_profile_pins_allow_nan() -> None:
    module = _import_script("sync_hafas_profile")

    _assert_allow_nan_pin(
        module._write_profile,
        where="scripts/sync_hafas_profile.py:_write_profile (data/hafas_profile.json)",
    )


def test_inventory_apply_station_overrides_writer_pins_allow_nan() -> None:
    """Sibling-drift closure: the curated-correction layer's writer
    (``scripts/apply_station_overrides.py:apply_overrides`` — the
    ``json.dumps(stations_payload, ...)`` call) was added in
    2026-05-16 PR #1540 — AFTER the Round 1485 / 1487 / 1488 / 1491
    sweep — and inherited neither the ``allow_nan=False`` pin nor
    the sibling-protection contract until the
    SENTINEL_APPLY_STATION_OVERRIDES_NON_FINITE_DRIFT round (see
    ``tests/test_sentinel_apply_station_overrides_non_finite_drift.py``).

    Without this pin, any ``float('nan')`` / ``float('inf')`` that
    bypassed the reader-side defence (e.g. via a regression to
    ``json.loads(text)``) re-serialises as the non-standard
    ``NaN`` / ``Infinity`` literal token (invalid per RFC 8259) and
    the corrupted ``data/stations.json`` bytes ship to ``main``,
    breaking every strict downstream parser and crashing the next
    cron tick's ``allow_nan=False`` writers mid-write.
    """
    module = _import_script("apply_station_overrides")

    _assert_allow_nan_pin(
        module.apply_overrides,
        where=(
            "scripts/apply_station_overrides.py:apply_overrides "
            "(data/stations.json persistence)"
        ),
    )


# ---------------------------------------------------------------------------
# Behavioural PoC: write_feed_health_json with NaN inputs must fail loudly.
# ---------------------------------------------------------------------------


def _make_run_report_with_nan_duration() -> tuple[Any, Any]:
    """Build a RunReport carrying a NaN provider-duration + NaN aggregate.

    This is the concrete attacker / buggy-upstream surface: any caller
    of :meth:`ProviderReport.finish` or :meth:`RunReport.finish` can
    pass ``float('nan')`` (or ``float('inf')``) and the value lands in
    the public ``docs/feed-health.json`` artefact verbatim — invalid
    per RFC 8259, rejected by every conforming downstream parser.
    """
    from src.feed.reporting import (
        FeedHealthMetrics,
        ProviderReport,
        RunReport,
    )

    report = RunReport(statuses=[("wl", True)])
    entry = ProviderReport(name="wl", enabled=True, fetch_type="rss")
    entry.start()
    # SENTINEL_COMMITTED_WRITER_ALLOW_NAN_DRIFT: programmatic injection
    # path — any caller (test harness, future provider plugin wrapping
    # a third-party instrumentation SDK, manual diagnostic) can pass
    # NaN here and the value lands in the public artefact.
    entry.finish("ok", items=12, duration=float("nan"))
    report.providers["wl"] = entry
    report.finish(
        build_successful=True,
        raw_items=12,
        final_items=12,
        durations={"collect": float("inf"), "rss": 0.42},
        feed_path=Path("docs/feed.xml"),
    )
    metrics = FeedHealthMetrics(
        raw_items=12,
        filtered_items=12,
        deduped_items=12,
        new_items=4,
        duplicate_count=0,
        duplicates=(),
    )
    return report, metrics


def test_poc_write_feed_health_json_rejects_nan_duration(tmp_path: Path) -> None:
    """The fixed writer must raise ``ValueError`` on NaN duration."""
    from src.feed.reporting import write_feed_health_json

    report, metrics = _make_run_report_with_nan_duration()
    output_path = tmp_path / "feed-health.json"

    # POST-FIX: write_feed_health_json wraps ``json.dump(..., allow_nan=False)``
    # so a NaN float anywhere in the payload (here: providers[0].duration)
    # surfaces as a loud ValueError BEFORE the corrupt bytes are written
    # to the public artefact.
    with pytest.raises(ValueError):
        write_feed_health_json(report, metrics, output_path=output_path)


def test_poc_write_feed_health_json_rejects_inf_in_durations(tmp_path: Path) -> None:
    """The fixed writer must raise ``ValueError`` on +Inf in durations dict."""
    from src.feed.reporting import (
        FeedHealthMetrics,
        ProviderReport,
        RunReport,
        write_feed_health_json,
    )

    report = RunReport(statuses=[("oebb", True)])
    entry = ProviderReport(name="oebb", enabled=True, fetch_type="rss")
    entry.start()
    entry.finish("ok", items=3, duration=0.1)
    report.providers["oebb"] = entry
    report.finish(
        build_successful=True,
        raw_items=3,
        final_items=3,
        # SENTINEL_COMMITTED_WRITER_ALLOW_NAN_DRIFT: +Inf in the
        # aggregate durations dict — every value in this dict is
        # ``float`` per RunReport.durations: dict[str, float] and
        # ``RunReport.finish(durations=...)`` performs no validation.
        durations={"collect": float("inf"), "rss": 0.2},
        feed_path=Path("docs/feed.xml"),
    )
    metrics = FeedHealthMetrics(
        raw_items=3,
        filtered_items=3,
        deduped_items=3,
        new_items=3,
        duplicate_count=0,
        duplicates=(),
    )
    output_path = tmp_path / "feed-health.json"

    with pytest.raises(ValueError):
        write_feed_health_json(report, metrics, output_path=output_path)


def test_poc_write_feed_health_json_finite_payload_round_trips(
    tmp_path: Path,
) -> None:
    """The fix must NOT regress legitimate finite-float payloads."""
    from src.feed.reporting import (
        FeedHealthMetrics,
        ProviderReport,
        RunReport,
        write_feed_health_json,
    )

    report = RunReport(statuses=[("wl", True), ("oebb", True)])
    for name in ("wl", "oebb"):
        entry = ProviderReport(name=name, enabled=True, fetch_type="rss")
        entry.start()
        entry.finish("ok", items=10, duration=0.5)
        report.providers[name] = entry
    report.finish(
        build_successful=True,
        raw_items=20,
        final_items=20,
        durations={"collect": 0.8, "rss": 0.4, "total": 1.2},
        feed_path=Path("docs/feed.xml"),
    )
    metrics = FeedHealthMetrics(
        raw_items=20,
        filtered_items=20,
        deduped_items=20,
        new_items=8,
        duplicate_count=0,
        duplicates=(),
    )
    output_path = tmp_path / "feed-health.json"

    # Must not raise on finite floats.
    write_feed_health_json(report, metrics, output_path=output_path)

    # And the on-disk artefact must parse cleanly under the strict
    # parse_constant hook — RFC 8259 conforming parsers (JSON.parse,
    # serde_json, encoding/json) reject NaN/Infinity literals.
    parsed = json.loads(
        output_path.read_text(encoding="utf-8"),
        parse_constant=_raise_on_nan_or_infinity,
    )
    assert parsed["metrics"]["raw_items"] == 20
    assert math.isfinite(parsed["durations"]["total"])


# ---------------------------------------------------------------------------
# Behavioural PoC: every fixed writer must surface NaN as ValueError.
# ---------------------------------------------------------------------------


def test_poc_write_status_rejects_nan_in_status_payload(tmp_path: Path) -> None:
    """The fixed cache-status writer must raise on NaN inside the status dict.

    ``write_status`` accepts ``dict[str, Any]`` — any future caller adding
    a float field (latency_seconds, response_size_ratio, ...) lands NaN
    in the committed ``cache/<provider>/last_run.json`` heartbeat verbatim
    pre-fix.
    """
    from src.utils import cache as cache_module

    # Direct the writer at a temp dir so the test doesn't touch the
    # repo's real cache.
    monkey_root = tmp_path / "cache_root"
    monkey_root.mkdir()

    # ``_status_file`` derives ``cache/<sanitized>/last_run.json`` from
    # a module-level _CACHE_DIR — override via monkeypatching the
    # module-level constant.
    original_cache_dir = cache_module._CACHE_DIR
    try:
        cache_module._CACHE_DIR = monkey_root
        with pytest.raises(ValueError):
            cache_module.write_status(
                "sentinel-test-provider",
                {
                    "status": "ok",
                    # SENTINEL_COMMITTED_WRITER_ALLOW_NAN_DRIFT:
                    "latency_seconds": float("nan"),
                },
            )
    finally:
        cache_module._CACHE_DIR = original_cache_dir
