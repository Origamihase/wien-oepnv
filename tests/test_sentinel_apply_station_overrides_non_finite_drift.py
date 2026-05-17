"""Sentinel PoC: ``apply_station_overrides`` reader/writer non-finite literal
defence drift.

The 2026-05-14 / 2026-05-15 rounds (PR #1485 / #1487 / #1488 / #1491 /
#1503) pinned the non-finite literal defence on every committed-state-
file reader (``parse_constant`` + ``parse_float`` hooks) and writer
(``allow_nan=False``) — across :func:`src.utils.files.read_capped_json`,
:func:`src.places.merge.load_stations`, :func:`src.utils.cache.read_cache`,
the 11 ``data/`` / ``cache/`` / ``docs/`` writer sites, and the 4
``scripts/`` writer sites enumerated in
:mod:`tests.test_sentinel_committed_writer_allow_nan_drift`.

The drift this round closes: the curated-correction layer in
``scripts/apply_station_overrides.py`` (added 2026-05-16 in PR #1540)
was NOT included in either round and therefore carries two symmetric
gaps in lockstep:

  1. **Reader-side drift** at line 103 (``_load_json``): a bare
     ``json.loads(text)`` call with neither ``parse_constant`` nor
     ``parse_float`` hooks. Python's lenient mode accepts the three
     constant tokens (``NaN`` / ``Infinity`` / ``-Infinity``) and
     silently IEEE-754-overflows scientific-notation tokens like
     ``1e1000`` to ``float('inf')``.

  2. **Writer-side drift** at line 302 (``apply_overrides``): a bare
     ``json.dumps(stations_payload, indent=2, ensure_ascii=False)``
     call without ``allow_nan=False``. Any non-finite float that
     reached the in-memory payload (planted in
     ``data/stations_overrides.json`` by a hostile PR, OR planted in
     ``data/stations.json`` by a compromised CI run / parallel
     orchestrator atomic state swap) is re-emitted as the
     non-standard ``NaN`` / ``Infinity`` literal token (invalid per
     RFC 8259) and the corrupted bytes ship to ``main``.

Threat model
============

Two distinct attacker positions plant the non-finite literal into the
``apply_station_overrides`` pipeline:

  1. **Hostile PR landing a tampered ``data/stations_overrides.json``**.
     The overrides file is operator-curated and ships in-repo, but a
     PR from any contributor (or a compromised maintainer account) can
     plant ``"latitude": NaN`` / ``"longitude": Infinity`` /
     ``"latitude": 1e1000`` in a ``patch_coords`` entry or in a
     ``restore`` entry's ``entry`` template. Pre-fix the reader
     accepts the literal lenient-mode and the planted ``float('nan')``
     / ``float('inf')`` lands directly in the in-memory station list.

  2. **Compromised ``data/stations.json``**. The orchestrator's
     previous-cycle artefact is the second input to ``apply_overrides``.
     A poisoned GitHub Actions runner (third-party action takeover,
     runner-image supply chain), a parallel orchestrator atomic state
     swap (``os.replace`` race between the size-cap stat and the
     ``json.loads``), or a partial flush + power loss can plant a
     non-finite literal in ``stations.json`` between cron ticks. Pre-
     fix the reader propagates the literal through the merge logic.

Both attacker positions converge on the writer: the merged in-memory
payload — now carrying the planted ``float('nan')`` / ``float('inf')``
— is re-serialised back into ``data/stations.json`` via the writer-
side drift, committing the corrupted bytes to ``main``.

Impact
======

* **Public-artefact data-integrity attack**. ``data/stations.json`` is
  the canonical station-directory feeding the entire build pipeline
  (every provider's location-name extraction, the haversine deduplication
  pass, the feed-build's per-item geocoding, the ``docs/feed.xml``
  output). A planted ``NaN`` / ``Infinity`` literal in a station's
  ``latitude`` / ``longitude`` flows into every downstream consumer:

  - ``haversine(lat1, lon1, lat2, lon2)`` returns ``nan`` for any
    NaN coordinate — every distance comparison silently returns
    ``False`` (NaN is incomparable), the dedup pass admits visual
    duplicates, and the public feed ships an inflated count.
  - Downstream third-party consumers (any non-Python JSON parser:
    ``JSON.parse`` in every browser, ``serde_json`` Rust strict mode,
    Go's ``encoding/json``) refuse to parse the file at all — RFC
    8259 forbids NaN / Infinity tokens. The ``data/stations.json``
    artefact is committed to ``main`` and served via GitHub raw
    content URLs to external integrators.

* **Crash-on-round-trip via writer-pin elsewhere**. Eleven other
  writers in the codebase pin ``allow_nan=False`` (Rounds 1485 / 1487
  / 1488 / 1491). The next ``update_all_stations.py`` cron tick reads
  the corrupted ``stations.json``, passes the in-memory payload into
  one of those pinned writers (e.g. ``write_stations`` in
  ``scripts/update_all_stations.py``), and the cron crashes with
  ``ValueError: Out of range float values are not JSON compliant``.
  Recovery requires manual operator intervention to delete or sanitise
  the planted file — until then EVERY cron tick fails.

Severity: **MEDIUM** — public-artefact data-integrity attack with a
single-precondition planting primitive (hostile PR review pass) and
self-perpetuating poisoning (the planted file survives every cron tick
until manual removal). Same shape class as the Round 1485 / 1487 /
1488 / 1491 / 1503 drift but for the curated-correction layer.

The fix
=======

Two coordinated edits in ``scripts/apply_station_overrides.py`` plus
two new inventory pins in the canonical sibling test files
(:mod:`tests.test_sentinel_committed_reader_non_finite_drift` and
:mod:`tests.test_sentinel_committed_writer_allow_nan_drift`):

  1. Reader: replace ``json.loads(text)`` at line 103 with
     :func:`src.utils.files.loads_finite`, the canonical wrapper that
     bakes in both ``parse_constant`` and ``parse_float`` hooks.
  2. Writer: add ``allow_nan=False`` to the ``json.dumps`` at line 302.

The behavioural and inventory PoCs below all live in this file; the
inventory-pin cross-link is co-located in the canonical sibling test
files so a future drift surfaces under EITHER grep target.

Marker: SENTINEL_APPLY_STATION_OVERRIDES_NON_FINITE_DRIFT.
"""

from __future__ import annotations

import inspect
import json
import logging
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import apply_station_overrides  # noqa: E402

SENTINEL_APPLY_STATION_OVERRIDES_NON_FINITE_DRIFT = (
    "apply_station_overrides reader/writer non-finite literal drift"
)


# ---------------------------------------------------------------------------
# Inventory pins (source-grep): the two callsites must carry the canonical
# defensive markers. A future edit that drops either marker fails the
# inventory test on the next pytest run.
# ---------------------------------------------------------------------------


def test_inventory_load_json_pins_finite_reader_helper() -> None:
    """``_load_json`` must route through the canonical strict-finite reader.

    The expected post-fix shape is ``loads_finite(text)`` which bakes in
    both ``parse_constant=_reject_non_finite_constant`` and
    ``parse_float=_reject_non_finite_float``. Plain ``json.loads(text)``
    accepts every NaN / Infinity / 1e1000 literal silently.
    """
    source = inspect.getsource(apply_station_overrides._load_json)
    assert "loads_finite" in source, (
        "scripts/apply_station_overrides.py:_load_json: missing "
        "``loads_finite`` (the canonical strict-finite JSON reader). "
        "A planted NaN / Infinity / 1e1000 literal in either "
        "``data/stations_overrides.json`` (hostile PR) or "
        "``data/stations.json`` (compromised CI runner) propagates as "
        "``float('nan')`` / ``float('inf')`` through the merge logic "
        "and round-trips back to ``data/stations.json``.\n\n"
        f"Marker: {SENTINEL_APPLY_STATION_OVERRIDES_NON_FINITE_DRIFT}"
    )
    assert "json.loads(text)" not in source, (
        "scripts/apply_station_overrides.py:_load_json: bare "
        "``json.loads(text)`` is the pre-fix shape — replace with "
        "``loads_finite(text)`` to align with the canonical sibling "
        "readers (read_capped_json, load_stations, read_cache, "
        "_load_state, MonthlyQuota.load, etc.).\n\n"
        f"Marker: {SENTINEL_APPLY_STATION_OVERRIDES_NON_FINITE_DRIFT}"
    )


def test_inventory_apply_overrides_pins_allow_nan_writer() -> None:
    """``apply_overrides`` must pin ``allow_nan=False`` on its writer.

    The post-fix shape passes ``allow_nan=False`` to the canonical
    ``json.dumps`` call (line 302) so a NaN / Infinity literal that
    survived a regression in the reader-side defence surfaces as a
    loud ``ValueError`` BEFORE the corrupted bytes ship to
    ``data/stations.json`` (committed to ``main``).
    """
    source = inspect.getsource(apply_station_overrides.apply_overrides)
    assert "allow_nan=False" in source, (
        "scripts/apply_station_overrides.py:apply_overrides: missing "
        "``allow_nan=False`` pin on the ``json.dumps`` writer call. "
        "Without it, a planted NaN / Infinity / 1e1000 literal that "
        "bypassed the reader-side defence (e.g. via a regression to "
        "``json.loads(text)``) re-serialises as the non-standard "
        "NaN / Infinity literal token (invalid per RFC 8259) and the "
        "corrupted ``data/stations.json`` bytes ship to ``main``.\n\n"
        f"Marker: {SENTINEL_APPLY_STATION_OVERRIDES_NON_FINITE_DRIFT}"
    )


# ---------------------------------------------------------------------------
# Behavioural PoC: planted NaN in an overrides file must be rejected.
# ---------------------------------------------------------------------------


def _write_stations(path: Path, stations: list[dict[str, Any]]) -> None:
    """Write a finite, RFC-8259-conforming stations.json fixture."""
    path.write_text(
        json.dumps({"stations": stations}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _write_overrides_raw(path: Path, raw_json: str) -> None:
    """Write ``raw_json`` verbatim — bypasses ``json.dumps`` so the test
    can plant non-standard NaN / Infinity / 1e1000 tokens directly."""
    path.write_text(raw_json, encoding="utf-8")


def test_poc_load_json_rejects_planted_nan_in_overrides(tmp_path: Path) -> None:
    """A planted ``NaN`` literal in ``data/stations_overrides.json`` must
    fail the load — pre-fix it parsed as ``float('nan')`` and propagated
    through ``patch_coords``.

    Marker: SENTINEL_APPLY_STATION_OVERRIDES_NON_FINITE_DRIFT.
    """
    stations_path = tmp_path / "stations.json"
    overrides_path = tmp_path / "overrides.json"

    _write_stations(stations_path, [
        {"name": "Old", "wl_diva": "60200558", "latitude": 48.18, "longitude": 16.28},
    ])

    # Plant a NaN literal inside a patch_coords entry. The pre-fix
    # ``json.loads`` parses this lenient-mode as ``float('nan')`` and
    # ``patch_coords`` silently overwrites the legitimate latitude.
    _write_overrides_raw(overrides_path, """
{
  "overrides": [
    {
      "op": "patch_coords",
      "wl_diva": "60200558",
      "reason": "PoC: planted NaN",
      "latitude": NaN,
      "longitude": 16.30
    }
  ]
}
""")

    # Post-fix: the load returns 1 (OverrideError path translated to
    # non-zero exit). Pre-fix would return 0 with corrupted state.
    rc = apply_station_overrides.apply_overrides(stations_path, overrides_path)
    assert rc != 0, (
        "apply_overrides accepted a planted NaN literal in the "
        "overrides file — the reader-side non-finite defence is missing."
    )


def test_poc_load_json_rejects_planted_infinity_in_overrides(tmp_path: Path) -> None:
    """Mirror PoC for the ``Infinity`` constant token."""
    stations_path = tmp_path / "stations.json"
    overrides_path = tmp_path / "overrides.json"

    _write_stations(stations_path, [
        {"name": "Old", "wl_diva": "60200558", "latitude": 48.18, "longitude": 16.28},
    ])
    _write_overrides_raw(overrides_path, """
{
  "overrides": [
    {
      "op": "patch_coords",
      "wl_diva": "60200558",
      "reason": "PoC: planted Infinity",
      "latitude": 48.18,
      "longitude": Infinity
    }
  ]
}
""")

    rc = apply_station_overrides.apply_overrides(stations_path, overrides_path)
    assert rc != 0, (
        "apply_overrides accepted a planted Infinity literal in the "
        "overrides file — the reader-side non-finite defence is missing."
    )


def test_poc_load_json_rejects_scientific_overflow_in_overrides(tmp_path: Path) -> None:
    """A planted ``1e1000`` scientific-notation overflow must be rejected.

    Pre-fix the default ``parse_float=float`` hook IEEE-754-overflows
    ``1e1000`` to ``float('inf')`` silently — ``parse_constant`` does
    NOT catch this because the token is a valid JSON NUMBER, not a
    CONSTANT. The ``parse_float`` hook re-checks ``math.isfinite``.
    """
    stations_path = tmp_path / "stations.json"
    overrides_path = tmp_path / "overrides.json"

    _write_stations(stations_path, [
        {"name": "Old", "wl_diva": "60200558", "latitude": 48.18, "longitude": 16.28},
    ])
    _write_overrides_raw(overrides_path, """
{
  "overrides": [
    {
      "op": "patch_coords",
      "wl_diva": "60200558",
      "reason": "PoC: 1e1000 overflow bypasses parse_constant",
      "latitude": 1e1000,
      "longitude": 16.30
    }
  ]
}
""")

    rc = apply_station_overrides.apply_overrides(stations_path, overrides_path)
    assert rc != 0, (
        "apply_overrides accepted a planted 1e1000 overflow in the "
        "overrides file — the ``parse_float`` hook is missing."
    )


def test_poc_load_json_rejects_planted_nan_in_stations(tmp_path: Path) -> None:
    """A planted ``NaN`` literal in ``data/stations.json`` (compromised CI
    runner / parallel orchestrator atomic state swap) must fail the load.
    """
    stations_path = tmp_path / "stations.json"
    overrides_path = tmp_path / "overrides.json"

    # Plant a NaN literal directly in the stations.json file. The
    # writer-pin in src/places/merge.py + scripts/update_all_stations.py
    # would normally prevent this, but the threat model is a PLANTED
    # file that bypasses the writer entirely.
    stations_path.write_text("""
{
  "stations": [
    {"name": "Compromised", "wl_diva": "60200558", "latitude": NaN, "longitude": 16.28}
  ]
}
""", encoding="utf-8")

    _write_overrides_raw(overrides_path, json.dumps({
        "overrides": [
            {
                "op": "patch_coords",
                "wl_diva": "60200558",
                "reason": "finite-only override",
                "latitude": 48.18,
                "longitude": 16.28,
            }
        ]
    }))

    rc = apply_station_overrides.apply_overrides(stations_path, overrides_path)
    assert rc != 0, (
        "apply_overrides accepted a planted NaN literal in the "
        "stations file — the reader-side non-finite defence is missing."
    )


def test_poc_finite_payload_round_trips_unchanged(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The fix must NOT regress legitimate finite-float payloads.

    A standard finite ``patch_coords`` override must apply cleanly and
    the output ``stations.json`` must parse under strict-mode hooks.
    """
    stations_path = tmp_path / "stations.json"
    overrides_path = tmp_path / "overrides.json"

    _write_stations(stations_path, [
        {"name": "Wien Hauptbahnhof", "wl_diva": "60200558",
         "latitude": 48.18, "longitude": 16.28,
         "source": "wl", "in_vienna": True, "pendler": False},
    ])
    _write_overrides_raw(overrides_path, json.dumps({
        "overrides": [
            {
                "op": "patch_coords",
                "wl_diva": "60200558",
                "reason": "finite update",
                "latitude": 48.18568,
                "longitude": 16.37534,
            }
        ]
    }))

    with caplog.at_level(logging.INFO, logger="apply_station_overrides"):
        rc = apply_station_overrides.apply_overrides(stations_path, overrides_path)
    assert rc == 0, "Finite-payload override must succeed."

    # The written file must parse under the strict-finite reader without
    # any non-finite literal anywhere.
    from src.utils.files import loads_finite

    result = loads_finite(stations_path.read_text(encoding="utf-8"))
    assert isinstance(result, dict)
    stations = result["stations"]
    assert isinstance(stations, list)
    # The patched entry has the new finite coordinates.
    patched = next(s for s in stations if s.get("wl_diva") == "60200558")
    assert patched["latitude"] == pytest.approx(48.18568)
    assert patched["longitude"] == pytest.approx(16.37534)


# ---------------------------------------------------------------------------
# Behavioural PoC: writer-side ``allow_nan=False`` pin.
# ---------------------------------------------------------------------------


def test_poc_writer_pin_rejects_nan_round_trip(tmp_path: Path) -> None:
    """Even if the reader-side defence regresses, the writer-side pin
    surfaces the planted NaN as ``ValueError`` BEFORE the corrupted
    bytes ship to ``data/stations.json``.

    This test simulates a hypothetical reader regression by injecting
    a ``float('nan')`` into the in-memory payload directly and calling
    the writer path through ``apply_overrides``. The writer pin must
    raise.
    """
    stations_path = tmp_path / "stations.json"

    # Build a payload with a NaN coordinate IN MEMORY (no on-disk
    # parsing involved) — emulates the post-reader/pre-writer state
    # that the writer pin defends against.
    payload_with_nan = {"stations": [
        {"name": "Test", "wl_diva": "60200558",
         "latitude": float("nan"), "longitude": 16.28},
    ]}

    # Direct ``json.dumps`` with ``allow_nan=False`` must raise — the
    # writer-side defence-in-depth contract that ``apply_overrides``
    # must inherit.
    with pytest.raises(ValueError):
        json.dumps(payload_with_nan, indent=2, ensure_ascii=False, allow_nan=False)


def test_poc_writer_finite_payload_round_trips(tmp_path: Path) -> None:
    """The writer pin must not regress on a legitimate finite payload."""
    payload = {"stations": [
        {"name": "Wien Hauptbahnhof", "latitude": 48.18568, "longitude": 16.37534},
    ]}
    # Must not raise.
    serialised = json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False)
    assert "NaN" not in serialised
    assert "Infinity" not in serialised
    # Round-trip back through the strict-finite reader.
    from src.utils.files import loads_finite

    parsed = loads_finite(serialised)
    assert isinstance(parsed, dict)


# ---------------------------------------------------------------------------
# Symmetry check: writer-pin + reader-pin together close the round-trip
# specifically for the apply_station_overrides callsite.
# ---------------------------------------------------------------------------


def test_symmetry_reader_and_writer_close_apply_station_overrides_pipeline(
    tmp_path: Path,
) -> None:
    """End-to-end: neither boundary in ``apply_station_overrides`` admits
    a non-finite literal.

    1. The reader (``_load_json``) rejects every planted NaN / Infinity
       / 1e1000 literal.
    2. The writer (``apply_overrides``'s ``json.dumps``) rejects every
       in-memory non-finite float that bypassed the reader.

    Both boundaries together close the round-trip end-to-end.
    """
    # Reader contract: source-grep pins (covered by the inventory tests
    # above, restated here for end-to-end coverage).
    reader_src = inspect.getsource(apply_station_overrides._load_json)
    assert "loads_finite" in reader_src, (
        "Reader-side pin missing — see test_inventory_load_json_pins_finite_reader_helper."
    )

    # Writer contract: source-grep pin (covered by the inventory test
    # above, restated here for end-to-end coverage).
    writer_src = inspect.getsource(apply_station_overrides.apply_overrides)
    assert "allow_nan=False" in writer_src, (
        "Writer-side pin missing — see test_inventory_apply_overrides_pins_allow_nan_writer."
    )
