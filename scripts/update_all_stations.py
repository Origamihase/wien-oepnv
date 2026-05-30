#!/usr/bin/env python3
"""Convenience wrapper to refresh all station datasets.

Pipeline:
  1. Copy the live ``data/stations.json`` into a temp directory so each
     sub-script merges into the previous result without touching the repo.
  2. Run every script in :data:`_SCRIPT_ORDER` against the temp file.
  3. Validate the merged result. ``provider_issues``,
     ``cross_station_id_issues``, ``naming_issues`` and ``security_issues``
     trigger the *auto-quarantine* path: instead of aborting the run, the
     offending entries are partitioned out of the merged file, persisted
     to ``data/quarantine.json`` for operator review, and the pipeline
     proceeds with the remaining valid stations. This soft-fail
     behaviour lets the feed survive partial upstream data corruption.
  4. Compute the before/after diff (added/removed/renamed/coord-shifted)
     and write ``data/stations_last_run.json`` (heartbeat) plus
     ``docs/stations_diff.md`` (human-readable diff report).
  5. Atomically copy the merged file back into ``data/stations.json``.
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import shutil
# Bandit B404: subprocess is required to invoke internal cache-refresh
# scripts. Inputs are static lists, never user-supplied.
import subprocess  # nosec B404
import sys
import tempfile
import time
from datetime import datetime, UTC
from pathlib import Path
from typing import Any, TypedDict
from collections.abc import Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.feed.logging_safe import setup_script_logging  # noqa: E402
from src.utils.files import atomic_write, read_capped_json  # noqa: E402  (import after path setup)
from src.utils.serialize import scrub_trojan_source_primitives  # noqa: E402
from src.utils.stations_validation import (  # noqa: E402
    StationValidationError,
    ValidationReport,
    _format_identifier,
    validate_stations,
)
from src.utils.text import (  # noqa: E402
    escape_markdown,
    normalise_markdown_text,
    safe_markdown_codespan,
)

# Security cap against wide-but-flat JSON size-bomb attacks. Mirrors the
# canonical ``MAX_*_FILE_BYTES`` contract from ``src/utils/cache.py`` /
# ``src/utils/stations.py``: depth-bomb catch alone misses ``MemoryError``
# (a ``BaseException`` subclass) so a planted-huge file would propagate
# past the loader and crash the orchestrator after the merge has already
# written. 50 MiB is ~285x the production stations.json.
MAX_JSON_FILE_BYTES = 50 * 1024 * 1024

_SCRIPT_ORDER = (
    "update_station_directory.py",
    "update_wl_stations.py",
    "enrich_station_aliases.py",
    # Curated correction layer (PR #1540): patches/restores/removes
    # entries that suffer from documented Wiener Linien OGD upstream
    # defects (wrong coordinates, missing haltepunkte for live DIVAs,
    # geographic-duplicate haltepunkte for distinct DIVAs). Runs after
    # enrich_station_aliases so the override's alias set survives the
    # enrichment pass; runs before the validator gate so the curated
    # state is what the gate measures. Idempotent — skipping is safe.
    # See ``data/stations_overrides.json`` for the live override list
    # and the ``expires_when`` predicates that document when each
    # override can retire.
    "apply_station_overrides.py",
)

_SCRIPT_OUTPUT_FLAG = {
    "update_station_directory.py": "--output",
    "update_wl_stations.py": "--stations",
    "enrich_station_aliases.py": "--stations",
    "apply_station_overrides.py": "--stations",
}

_DEFAULT_HEARTBEAT_PATH = REPO_ROOT / "data" / "stations_last_run.json"
_DEFAULT_DIFF_REPORT_PATH = REPO_ROOT / "docs" / "stations_diff.md"
_DEFAULT_POLYGON_PATH = REPO_ROOT / "data" / "LANDESGRENZEOGD.json"
_DEFAULT_QUARANTINE_PATH = REPO_ROOT / "data" / "quarantine.json"
_COORD_SHIFT_THRESHOLD_M = 100.0

# Sentinel identifier the validator emits for directory-wide provider
# issues (e.g. "Need at least two VOR entries") that cannot be tied to
# a single station. The auto-quarantine logic skips this marker — no
# entry's ``_format_identifier`` ever matches it, and removing
# arbitrary stations would not repair the global condition.
_GLOBAL_ISSUE_SENTINEL = "<global>"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run all station update scripts in sequence.",
    )
    parser.add_argument(
        "--python",
        default=sys.executable,
        help="Python interpreter used to invoke the update scripts (default: current interpreter).",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose logging output for the wrapper and update scripts.",
    )
    # The three output paths used to be hardcoded module-level
    # constants. Exposing them as CLI args lets the regression test
    # suite point the wrapper at a ``tmp_path`` so an end-to-end run
    # does not mutate the production working tree — the root-cause
    # fix for the pollution that PR #1607 mitigated via an autouse
    # fixture. Production cron runs (every workflow invokes the
    # script with no arguments) get the same byte-identical
    # behaviour because the defaults preserve the historical paths.
    parser.add_argument(
        "--target",
        type=Path,
        default=REPO_ROOT / "data" / "stations.json",
        help=(
            "Path to write the final merged stations directory "
            "(default: data/stations.json under the repository root)."
        ),
    )
    parser.add_argument(
        "--heartbeat",
        type=Path,
        default=REPO_ROOT / "data" / "stations_last_run.json",
        help=(
            "Path to write the run heartbeat "
            "(default: data/stations_last_run.json under the repository root)."
        ),
    )
    parser.add_argument(
        "--diff-report",
        type=Path,
        default=REPO_ROOT / "docs" / "stations_diff.md",
        help=(
            "Path to write the human-readable diff report "
            "(default: docs/stations_diff.md under the repository root)."
        ),
    )
    parser.add_argument(
        "--quarantine",
        type=Path,
        default=REPO_ROOT / "data" / "quarantine.json",
        help=(
            "Path to write the auto-quarantine sidecar on validation "
            "failure (default: data/quarantine.json under the repository root)."
        ),
    )
    return parser.parse_args(argv)


def configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    # Sentinel: route through SafeFormatter so any raw exception text
    # logged via %s in this script is sanitised at the formatter layer.
    setup_script_logging(level)


def run_script(python: str, script_path: Path, verbose: bool, output_flag: str, output_path: Path) -> None:
    cmd = [python, str(script_path), output_flag, str(output_path)]
    if verbose:
        cmd.append("--verbose")
    logging.info("Running %s", script_path.name)
    # Enforce a 10-minute timeout for each update script to prevent indefinite hangs
    subprocess.run(cmd, check=True, shell=False, timeout=600)  # nosec B603


def _load_stations(path: Path) -> list[Mapping[str, Any]]:
    # Security: ``read_capped_json`` enforces both the depth-bomb catch
    # tuple and the byte-size cap (see MAX_JSON_FILE_BYTES). The
    # orchestrator uses this loader for diff detection — without the
    # cap a wide-but-flat planted file would propagate ``MemoryError``
    # past ``main()`` and crash the run after the merged file is
    # already written, masking the real cause.
    data = read_capped_json(
        path, MAX_JSON_FILE_BYTES, label="Stations",
    )
    if data is None:
        return []
    if isinstance(data, dict):
        entries = data.get("stations", [])
    elif isinstance(data, list):
        entries = data
    else:
        return []
    return [e for e in entries if isinstance(e, Mapping)]


def _station_key(entry: Mapping[str, Any]) -> str:
    """Stable identity key for diff matching: bst_id if present, else name."""
    bst_id = entry.get("bst_id")
    if bst_id is not None and str(bst_id).strip():
        return f"bst:{str(bst_id).strip()}"
    name = str(entry.get("name") or "<unnamed>").strip()
    return f"name:{name}"


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(a))


class _DiffResult(TypedDict):
    added: list[tuple[str, str]]
    removed: list[tuple[str, str]]
    renamed: list[tuple[str, str, str]]
    coord_shifted: list[tuple[str, str, int]]


def _coerce_coord(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    return None


def _compute_diff(
    before: Sequence[Mapping[str, Any]],
    after: Sequence[Mapping[str, Any]],
) -> _DiffResult:
    """Compute additions/removals/renames/coord-shifts between two snapshots."""
    by_key_before = {_station_key(s): s for s in before}
    by_key_after = {_station_key(s): s for s in after}

    added_keys = sorted(set(by_key_after) - set(by_key_before))
    removed_keys = sorted(set(by_key_before) - set(by_key_after))

    renamed: list[tuple[str, str, str]] = []
    coord_shifted: list[tuple[str, str, int]] = []

    for key in sorted(set(by_key_before) & set(by_key_after)):
        before_entry = by_key_before[key]
        after_entry = by_key_after[key]

        before_name = str(before_entry.get("name") or "")
        after_name = str(after_entry.get("name") or "")
        if before_name != after_name:
            renamed.append((key, before_name, after_name))

        before_lat = _coerce_coord(before_entry.get("latitude"))
        before_lon = _coerce_coord(before_entry.get("longitude"))
        after_lat = _coerce_coord(after_entry.get("latitude"))
        after_lon = _coerce_coord(after_entry.get("longitude"))
        if (
            before_lat is None
            or before_lon is None
            or after_lat is None
            or after_lon is None
        ):
            continue
        try:
            distance = _haversine_m(before_lat, before_lon, after_lat, after_lon)
        except (TypeError, ValueError):
            continue
        if distance >= _COORD_SHIFT_THRESHOLD_M:
            coord_shifted.append((key, after_name or before_name, round(distance)))

    return _DiffResult(
        added=[(k, str(by_key_after[k].get("name") or "")) for k in added_keys],
        removed=[(k, str(by_key_before[k].get("name") or "")) for k in removed_keys],
        renamed=renamed,
        coord_shifted=coord_shifted,
    )


def _render_diff_markdown(
    diff: _DiffResult,
    before_count: int,
    after_count: int,
    timestamp: str,
) -> str:
    # Security (Trojan-Source / BiDi-Mark Drift Round 9): station names
    # in ``diff["*"]`` come from the unsanitised pre-/post-merge
    # snapshots. Any name carrying a CVE-2021-42574 BiDi control
    # (U+202A-U+202E / U+2066-U+2069), a zero-width primitive
    # (U+200B-U+200F), a Unicode line / paragraph separator
    # (U+2028-U+2029), the BOM (U+FEFF), or an 8-bit C1 terminal-escape
    # byte (\\x9b CSI / \\x9d OSC / \\x90 DCS) would otherwise reach the
    # ``docs/stations_diff.md`` body verbatim. That artefact is
    # committed by the cron pipeline (``update-cycle.yml``) and
    # rendered on GitHub Pages — GitHub's Markdown renderer honours
    # BiDi formatting characters, so a malicious station name turns the
    # public diff page into a Trojan-Source viewer attack. Route every
    # interpolated name through the canonical Markdown sanitiser pair
    # (``normalise_markdown_text`` strips the unsafe union;
    # ``escape_markdown`` then escapes the surviving Markdown
    # metacharacters) and route every code-span identifier through
    # ``safe_markdown_codespan`` so the ``name:<raw>`` key form is
    # also normalised. Mirrors the canonical pattern pinned by the
    # 2026-05-09 ``Markdown Injection Drift Round 3`` entry for
    # ``ValidationReport.to_markdown()``.
    lines = [
        "# stations.json — Diff-Bericht",
        "",
        f"_Erzeugt am: {timestamp}_",
        "",
        f"**Stationen: {before_count} → {after_count} (Δ {after_count - before_count:+d})**",
        "",
    ]

    def section(title: str, items: list[Any], formatter: Any) -> None:
        lines.append(f"## {title} ({len(items)})")
        lines.append("")
        if items:
            for item in items:
                lines.append(formatter(item))
        else:
            lines.append("_Keine._")
        lines.append("")

    def _safe_key(raw: str) -> str:
        return safe_markdown_codespan(raw)

    def _safe_name(raw: str) -> str:
        return escape_markdown(normalise_markdown_text(raw))

    section(
        "Hinzugefügt",
        diff["added"],
        lambda it: f"- `{_safe_key(it[0])}` — {_safe_name(it[1])}",
    )
    section(
        "Entfernt",
        diff["removed"],
        lambda it: f"- `{_safe_key(it[0])}` — {_safe_name(it[1])}",
    )
    section(
        "Umbenannt",
        diff["renamed"],
        lambda it: f'- `{_safe_key(it[0])}`: "{_safe_name(it[1])}" → "{_safe_name(it[2])}"',
    )
    section(
        f"Koordinaten verschoben (≥ {int(_COORD_SHIFT_THRESHOLD_M)} m)",
        diff["coord_shifted"],
        lambda it: f"- `{_safe_key(it[0])}` — {_safe_name(it[1])} ({it[2]} m)",
    )

    return "\n".join(lines).rstrip() + "\n"


def _count_polygon_vertices(path: Path) -> int | None:
    # Security: ``read_capped_json`` enforces both the depth-bomb catch
    # tuple and the byte-size cap (see MAX_JSON_FILE_BYTES). The
    # orchestrator calls this at heartbeat-build time *after* the
    # merged stations.json has already been atomically written, so an
    # unhandled ``MemoryError`` (from the wide-but-flat axis the
    # depth-bomb catch does NOT cover) would crash ``_build_heartbeat``
    # → ``main()`` and leave partial state with no heartbeat record.
    data = read_capped_json(
        path, MAX_JSON_FILE_BYTES, label="Polygon",
    )
    features = data.get("features") if isinstance(data, dict) else None
    if not isinstance(features, list):
        return None
    total = 0
    for feature in features:
        if not isinstance(feature, dict):
            continue
        geometry = feature.get("geometry")
        if not isinstance(geometry, dict):
            continue
        coords = geometry.get("coordinates")
        if geometry.get("type") == "Polygon" and isinstance(coords, list):
            for ring in coords:
                if isinstance(ring, list):
                    total += len(ring)
        elif geometry.get("type") == "MultiPolygon" and isinstance(coords, list):
            for polygon in coords:
                if not isinstance(polygon, list):
                    continue
                for ring in polygon:
                    if isinstance(ring, list):
                        total += len(ring)
    return total or None


def _build_heartbeat(
    report: ValidationReport,
    diff: _DiffResult,
    sub_scripts: Sequence[Mapping[str, Any]],
    before_count: int,
    after_count: int,
    polygon_vertices: int | None,
    timestamp: str,
) -> dict[str, Any]:
    return {
        "timestamp": timestamp,
        "sub_scripts": list(sub_scripts),
        "stations": {
            "before": before_count,
            "after": after_count,
            "delta": after_count - before_count,
        },
        "validation": {
            "duplicates": len(report.duplicates),
            "alias_issues": len(report.alias_issues),
            "coordinate_issues": len(report.coordinate_issues),
            "gtfs_issues": len(report.gtfs_issues),
            "security_issues": len(report.security_issues),
            "cross_station_id_issues": len(report.cross_station_id_issues),
            "provider_issues": len(report.provider_issues),
            "naming_issues": len(report.naming_issues),
        },
        "diff": {
            "added": len(diff["added"]),
            "removed": len(diff["removed"]),
            "renamed": len(diff["renamed"]),
            "coord_shifted": len(diff["coord_shifted"]),
        },
        "polygon_vertices": polygon_vertices,
    }


def _write_heartbeat_file(path: Path, heartbeat: Mapping[str, Any]) -> None:
    """Atomically write the orchestrator heartbeat to *path*.

    Security (Trojan-Source / BiDi-Mark Drift Round 10): the file is
    operator-facing diagnostic state, committed to ``main`` by the
    ``update-cycle.yml`` cron pipeline (``data/stations_last_run.json``)
    and reviewed via ``cat`` / ``less`` / the GitHub web UI / IDE
    preview. ``ensure_ascii=True`` escapes every non-ASCII code point
    as a literal ``\\uXXXX`` sequence, so a future heartbeat field
    carrying station- / provider- / environment-controlled content
    cannot leak the canonical CVE-2021-42574 Trojan-Source / zero-width
    / Unicode-line-terminator / 8-bit C1 union as raw UTF-8 bytes.
    Mirrors the canonical fix shape pinned in PR #1434 for
    ``_write_quarantine_file`` so the closing checklist's invariant is
    uniform across the committed ``data/*.json`` sidecar writer family.
    Forensic intent is preserved (``json.loads`` recovers the original
    string from the literal escape sequence).

    Security (Coordinate finite/range drift, committed-writer
    defence-in-depth): ``allow_nan=False`` mirrors the canonical
    writer-side pin established in Round 1485 at
    :func:`src.places.merge.write_stations` and extended in Round
    1487 to the sibling stations / cache-events writers. The
    heartbeat payload is a ``dict[str, Any]`` so any future field
    (e.g. a fractional success-rate metric, average tick duration)
    inherits the missing pin and could land non-standard
    ``NaN`` / ``Infinity`` literals (invalid per RFC 8259) in
    the committed ``data/stations_last_run.json`` sidecar.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with atomic_write(path, mode="w", encoding="utf-8") as handle:
        json.dump(heartbeat, handle, ensure_ascii=True, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")


def _collect_blocking_issues(report: ValidationReport) -> list[tuple[str, str]]:
    """Return (category, message) tuples for issues that trigger auto-quarantine."""
    blocking: list[tuple[str, str]] = []
    for provider in report.provider_issues:
        blocking.append(("provider", provider.reason))
    for cross in report.cross_station_id_issues:
        blocking.append((
            "cross-station",
            f"alias '{cross.alias}' in '{cross.name}' ({cross.identifier}) "
            f"collides with {cross.colliding_field} of "
            f"'{cross.colliding_name}' ({cross.colliding_identifier})",
        ))
    for naming in report.naming_issues:
        blocking.append((
            "naming",
            f"{naming.name} ({naming.identifier}): {naming.reason}",
        ))
    for security in report.security_issues:
        blocking.append((
            "security",
            f"{security.name} ({security.identifier}): {security.reason}",
        ))
    return blocking


def _collect_quarantine_identifiers(report: ValidationReport) -> set[str]:
    """Return the identifiers of stations that should be auto-quarantined.

    The string format mirrors :func:`stations_validation._format_identifier`
    so a downstream caller can match each entry deterministically. The
    ``<global>`` sentinel emitted by ``_find_provider_issues`` for
    directory-wide conditions is filtered out — no individual station
    matches it and removing one would not repair the underlying issue.
    """
    identifiers: set[str] = set()
    for provider in report.provider_issues:
        identifier = provider.identifier
        if identifier and identifier != _GLOBAL_ISSUE_SENTINEL:
            identifiers.add(identifier)
    for cross in report.cross_station_id_issues:
        if cross.identifier:
            identifiers.add(cross.identifier)
    for naming in report.naming_issues:
        if naming.identifier:
            identifiers.add(naming.identifier)
    for security in report.security_issues:
        if security.identifier:
            identifiers.add(security.identifier)
    return identifiers


def _collect_quarantine_reasons(
    report: ValidationReport,
) -> dict[str, list[dict[str, str]]]:
    """Map quarantineable identifier → list of ``{category, reason}`` dicts.

    The returned mapping powers the per-station ``issues`` array in
    ``data/quarantine.json`` so operators can review *why* each entry
    was removed without correlating against the run log.
    """
    reasons: dict[str, list[dict[str, str]]] = {}
    for provider in report.provider_issues:
        identifier = provider.identifier
        if not identifier or identifier == _GLOBAL_ISSUE_SENTINEL:
            continue
        reasons.setdefault(identifier, []).append({
            "category": "provider",
            "reason": provider.reason,
        })
    for cross in report.cross_station_id_issues:
        if not cross.identifier:
            continue
        reasons.setdefault(cross.identifier, []).append({
            "category": "cross-station",
            "reason": (
                f"alias '{cross.alias}' collides with {cross.colliding_field} "
                f"of '{cross.colliding_name}' ({cross.colliding_identifier})"
            ),
        })
    for naming in report.naming_issues:
        if not naming.identifier:
            continue
        reasons.setdefault(naming.identifier, []).append({
            "category": "naming",
            "reason": naming.reason,
        })
    for security in report.security_issues:
        if not security.identifier:
            continue
        reasons.setdefault(security.identifier, []).append({
            "category": "security",
            "reason": security.reason,
        })
    return reasons


def _partition_stations(
    stations: Sequence[Mapping[str, Any]],
    quarantine_identifiers: set[str],
) -> tuple[list[Mapping[str, Any]], list[Mapping[str, Any]]]:
    """Split *stations* into ``(valid, quarantined)`` by identifier match.

    Each station's identifier is recomputed with
    :func:`stations_validation._format_identifier` so the match honours
    the exact format the validator emitted into the report.
    """
    valid: list[Mapping[str, Any]] = []
    quarantined: list[Mapping[str, Any]] = []
    for entry in stations:
        if _format_identifier(entry) in quarantine_identifiers:
            quarantined.append(entry)
        else:
            valid.append(entry)
    return valid, quarantined


def _dedupe_exact_duplicates(
    stations: Sequence[Mapping[str, Any]],
) -> tuple[list[Mapping[str, Any]], int]:
    """Drop byte-identical duplicate entries, preserving first-seen order.

    A station directory must never carry two identical records. When it
    does — e.g. the same ``oebb_geonetz`` entry preserved twice through the
    ``update_station_directory`` existing-file → ``manual_stations``
    round-trip, whose ``final_stations.extend(manual_stations)`` assembly
    has no dedup pass — the intentional "a station's own ``bst_code`` is
    also one of its aliases" rule (``enrich_station_aliases``) turns the
    pair into a *cross-station* ID collision: copy A's alias shadows copy
    B's ``bst_code`` and vice versa. ``_find_cross_station_id_conflicts``
    self-excludes by object identity, so a single copy is fine, but two
    copies both fire and the auto-quarantine then removes *every* entry
    matching that identifier — silently dropping a valid station from the
    directory (observed for Wien Siebenhirten / Wien Handelskai). Collapsing
    exact duplicates to one copy restores the self-exclusion invariant.

    Only byte-identical entries are merged. Two records that share an
    identity field but differ elsewhere are a genuine conflict and are left
    untouched for ``_find_identity_field_conflicts`` to surface.
    """
    seen: set[str] = set()
    deduped: list[Mapping[str, Any]] = []
    removed = 0
    for entry in stations:
        try:
            fingerprint = json.dumps(
                dict(entry), sort_keys=True, ensure_ascii=True, allow_nan=False, default=str
            )
        except ValueError:
            # A non-finite value (NaN/Inf) in a coordinate field makes the
            # ``allow_nan=False`` fingerprint raise. Don't abort the whole
            # orchestrator (which would leave heartbeat/diff unwritten): treat
            # the entry as unique so exact-dedup degrades gracefully.
            logging.warning("Skipping exact-dedup fingerprint for entry with non-finite value")
            deduped.append(entry)
            continue
        if fingerprint in seen:
            removed += 1
            continue
        seen.add(fingerprint)
        deduped.append(entry)
    return deduped, removed


def _write_stations_payload(
    path: Path, stations: Sequence[Mapping[str, Any]]
) -> None:
    """Atomically rewrite *path* with the canonical wrapped JSON shape.

    Mirrors the ``{"stations": [...]}`` envelope written by
    ``scripts/update_station_directory.py:write_json`` so a downstream
    consumer reading the merged temp file (and the final copy-back to
    ``data/stations.json``) sees the same format as a clean run.

    Security (Trojan-Source / BiDi-Mark Drift Round 14, ingestion-boundary
    defence): strip the canonical CVE-2021-42574 attack-byte union BEFORE
    ``json.dump`` so a poisoned upstream provider (OEBB Excel / OSM
    Overpass / Wien OGD CSV) cannot leak raw BiDi marks into the
    orchestrator's temp file. The temp file is copy-back'd to
    ``data/stations.json`` and the weekly ``update-stations.yml`` cron
    commits it to ``main`` with ``add_options: "-A"``. Mirrors
    ``src/places/merge.py:write_stations`` (Round 13).

    Security (Coordinate finite/range drift, companion-writer
    defence-in-depth): ``allow_nan=False`` mirrors the canonical
    writer-side pin established in Round 1485 at
    ``src/places/merge.py:write_stations``. The orchestrator's temp
    file is the LAST writer before the final copy-back to
    ``data/stations.json`` — any poisoned ``NaN`` / ``Infinity``
    coordinate that survived the per-script parser checks lands here
    verbatim. Without this floor the temp file (and the
    copy-back-destination committed to ``main``) silently carries
    non-standard JSON literals (invalid per RFC 8259).
    """
    raw_payload = {"stations": [dict(entry) for entry in stations]}
    payload = scrub_trojan_source_primitives(raw_payload)
    with atomic_write(path, mode="w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")


def _write_quarantine_file(
    path: Path,
    quarantined: Sequence[Mapping[str, Any]],
    reasons_by_identifier: Mapping[str, Sequence[Mapping[str, str]]],
    timestamp: str,
) -> None:
    """Atomically write the auto-quarantine sidecar at *path*.

    The file is operator-facing diagnostic state — keep the schema
    forwards-compatible by emitting a wrapped object with an explicit
    ``count``, the original ``entry`` payload, and the validator's
    ``issues`` so a future drift in either side is auditable.

    Security (Trojan-Source / BiDi-Mark Drift Round 9): the canonical
    quarantine path is the destination for entries that the validator
    already flagged as carrying ``_UNSAFE_CHARS_RE`` bytes — every
    CVE-2021-42574 BiDi formatting control (U+202A-U+202E /
    U+2066-U+2069), every zero-width primitive (U+200B-U+200F), every
    Unicode line / paragraph separator (U+2028-U+2029), the BOM
    (U+FEFF), and the 8-bit C1 terminal-escape primitives (\\x9b CSI /
    \\x9d OSC / \\x90 DCS) is *by definition* present in the entries
    written here. ``ensure_ascii=True`` forces ``json.dump`` to emit
    every non-ASCII code point as a literal ``\\uXXXX`` escape, so the
    raw UTF-8 bytes of the BiDi controls (e.g. ``\\xe2\\x80\\xae`` for
    U+202E) never reach the on-disk file. Operators viewing
    ``data/quarantine.json`` via ``cat`` / ``less`` / editor preview /
    the GitHub web UI see the escaped sentinel ``\\u202e`` rather than
    the byte sequence that triggers terminal / Markdown rendering
    attacks. Forensic intent is preserved: ``json.loads`` decodes the
    escapes back to the original bytes, so the file remains a complete
    record of what tried to slip through.
    """
    entries: list[dict[str, Any]] = []
    for entry in quarantined:
        identifier = _format_identifier(entry)
        name_obj = entry.get("name")
        name = name_obj.strip() if isinstance(name_obj, str) else ""
        entries.append({
            "identifier": identifier,
            "name": name or "<unknown>",
            "issues": [dict(reason) for reason in reasons_by_identifier.get(identifier, ())],
            "entry": dict(entry),
        })
    payload: dict[str, Any] = {
        "timestamp": timestamp,
        "count": len(entries),
        "stations": entries,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    # Security (Coordinate finite/range drift, committed-writer
    # defence-in-depth): ``allow_nan=False`` mirrors the canonical
    # writer-side pin established in Round 1485 at
    # :func:`src.places.merge.write_stations` and extended in Round
    # 1487 to the sibling stations / cache-events writers. The
    # quarantined entries carry ``"entry": dict(entry)`` — a verbatim
    # copy of the operator-facing station entry that was flagged
    # *because* it carries unsafe content. A poisoned ``latitude`` /
    # ``longitude`` (the same coordinate threat model that motivated
    # Round 1485 / 1487) flows through this writer without the pin
    # and lands non-standard ``NaN`` / ``Infinity`` literals
    # (invalid per RFC 8259) in the committed
    # ``data/quarantine.json`` sidecar.
    with atomic_write(path, mode="w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=True, indent=2, allow_nan=False)
        handle.write("\n")


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    configure_logging(args.verbose)

    script_dir = Path(__file__).resolve().parent
    target_stations_json: Path = args.target.resolve()
    heartbeat_path: Path = args.heartbeat
    diff_report_path: Path = args.diff_report
    quarantine_path: Path = args.quarantine

    sub_script_results: list[dict[str, Any]] = []

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_stations_path = Path(tmp_dir) / "stations.json"
        before_snapshot: list[Mapping[str, Any]] = []

        if target_stations_json.exists():
            shutil.copy2(target_stations_json, tmp_stations_path)
            before_snapshot = _load_stations(tmp_stations_path)

        for script_name in _SCRIPT_ORDER:
            script_path = script_dir / script_name
            if not script_path.exists():
                logging.error("Script not found: %s", script_path)
                return 1

            output_flag = _SCRIPT_OUTPUT_FLAG.get(script_name)
            if not output_flag:
                logging.error("No output flag mapping found for %s", script_name)
                return 1

            start = time.monotonic()
            try:
                run_script(args.python, script_path, args.verbose, output_flag, tmp_stations_path)
            except subprocess.CalledProcessError as exc:  # pragma: no cover - thin wrapper
                duration = time.monotonic() - start
                sub_script_results.append({
                    "name": script_name,
                    "exit_code": exc.returncode or 1,
                    "duration_s": round(duration, 2),
                })
                logging.error(
                    "Script %s failed with exit code %s", script_path.name, exc.returncode
                )
                return exc.returncode or 1
            duration = time.monotonic() - start
            sub_script_results.append({
                "name": script_name,
                "exit_code": 0,
                "duration_s": round(duration, 2),
            })

        # Collapse byte-identical duplicate entries before validation.
        # ``update_station_directory.py`` assembles its output as
        # ``fresh + manual_stations`` with no dedup pass, so a station
        # preserved through the existing-file → manual round-trip can be
        # written twice. A duplicated record self-collides on its own
        # ``bst_code``-as-alias and would otherwise auto-quarantine the
        # whole station every run (see ``_dedupe_exact_duplicates``).
        merged_before_validation = _load_stations(tmp_stations_path)
        deduped_stations, removed_duplicates = _dedupe_exact_duplicates(
            merged_before_validation
        )
        if removed_duplicates:
            logging.warning(
                "Removed %d byte-identical duplicate station entr%s before validation",
                removed_duplicates,
                "y" if removed_duplicates == 1 else "ies",
            )
            _write_stations_payload(tmp_stations_path, deduped_stations)

        # Run validation
        logging.info("Validating merged %s", tmp_stations_path)
        try:
            report = validate_stations(tmp_stations_path)
        except StationValidationError as exc:
            logging.error("Validation could not be completed: %s", exc)
            logging.error(
                "Validation failed on the new stations data. Working tree left unmodified."
            )
            return 1

        # Auto-quarantine path (replaces the previous hard-fail return).
        # Blocking issues no longer halt the pipeline. Instead, the
        # offending entries are partitioned out of the merged file,
        # persisted to data/quarantine.json for operator review, and
        # the remainder of the run proceeds with the valid set so a
        # partial upstream corruption does not stop the feed update.
        blocking = _collect_blocking_issues(report)
        timestamp = datetime.now(UTC).isoformat(timespec="seconds")
        if blocking:
            for category, message in blocking:
                logging.warning("validation issue (%s): %s", category, message)

            quarantine_identifiers = _collect_quarantine_identifiers(report)
            merged_stations = _load_stations(tmp_stations_path)
            valid_stations, quarantined_stations = _partition_stations(
                merged_stations, quarantine_identifiers
            )

            if quarantined_stations:
                _write_stations_payload(tmp_stations_path, valid_stations)
                _write_quarantine_file(
                    quarantine_path,
                    quarantined_stations,
                    _collect_quarantine_reasons(report),
                    timestamp,
                )
                quarantined_names = [
                    str(entry.get("name") or "<unknown>")
                    for entry in quarantined_stations
                ]
                logging.warning(
                    "Auto-quarantined %d station(s) with blocking validation issues; "
                    "details written to %s. Affected: %s",
                    len(quarantined_stations),
                    quarantine_path,
                    ", ".join(quarantined_names),
                )
            else:
                # Either every blocking issue was the ``<global>`` sentinel
                # or the validator's identifiers did not match any merged
                # entry. Without a target to remove, auto-quarantine cannot
                # repair the directory — log the gap and proceed with the
                # full set so the pipeline survives.
                logging.warning(
                    "Auto-quarantine could not isolate the failing stations "
                    "(no entry matched the validator's identifiers); proceeding "
                    "with the unmodified merged set."
                )

        # Compute the diff between the pre-update snapshot and the merged file
        # before atomic copy-back so the heartbeat reflects what is about to land.
        after_snapshot = _load_stations(tmp_stations_path)
        diff = _compute_diff(before_snapshot, after_snapshot)
        polygon_vertices = _count_polygon_vertices(_DEFAULT_POLYGON_PATH)
        heartbeat = _build_heartbeat(
            report=report,
            diff=diff,
            sub_scripts=sub_script_results,
            before_count=len(before_snapshot),
            after_count=len(after_snapshot),
            polygon_vertices=polygon_vertices,
            timestamp=timestamp,
        )

        # Atomic copy-back: atomic_write writes a temp file inside
        # target_stations_json.parent (same filesystem as the target —
        # sidesteps the cross-FS issue with tempfile.TemporaryDirectory
        # which lives on /tmp), fsyncs, then os.replace's into position.
        # No partial-file window on data/stations.json.
        with open(tmp_stations_path, "rb") as src, atomic_write(
            target_stations_json, mode="wb"
        ) as dst:
            shutil.copyfileobj(src, dst)
        logging.info("stations.json successfully updated and validated.")

        # Persist the heartbeat and diff report next to the data they describe.
        # Both files are atomic-written so a partial run never produces
        # half-written observability artefacts.
        _write_heartbeat_file(heartbeat_path, heartbeat)
        diff_report_path.parent.mkdir(parents=True, exist_ok=True)
        with atomic_write(diff_report_path, mode="w", encoding="utf-8") as handle:
            handle.write(_render_diff_markdown(
                diff,
                before_count=len(before_snapshot),
                after_count=len(after_snapshot),
                timestamp=timestamp,
            ))
        logging.info(
            "Wrote heartbeat (%s) and diff report (%s)",
            heartbeat_path.name,
            diff_report_path.name,
        )

    logging.info("All station update scripts completed successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
