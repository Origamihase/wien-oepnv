#!/usr/bin/env python3
"""Convenience wrapper to refresh all station datasets.

Pipeline:
  1. Copy the live ``data/stations.json`` into a temp directory so each
     sub-script merges into the previous result without touching the repo.
  2. Run every script in :data:`_SCRIPT_ORDER` against the temp file.
  3. Validate the merged result. ``provider_issues``,
     ``cross_station_id_issues``, ``naming_issues`` and ``security_issues``
     are hard gates — the working tree is left untouched on any of them.
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
import subprocess  # nosec B404 - utility script to run internal scripts
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

from src.utils.files import atomic_write  # noqa: E402  (import after path setup)
from src.utils.stations_validation import (  # noqa: E402
    StationValidationError,
    ValidationReport,
    validate_stations,
)

_SCRIPT_ORDER = (
    "update_station_directory.py",
    "update_vor_stations.py",
    "update_wl_stations.py",
    "enrich_station_aliases.py",
)

_SCRIPT_OUTPUT_FLAG = {
    "update_station_directory.py": "--output",
    "update_vor_stations.py": "--stations",
    "update_wl_stations.py": "--stations",
    "enrich_station_aliases.py": "--stations",
}

_DEFAULT_HEARTBEAT_PATH = REPO_ROOT / "data" / "stations_last_run.json"
_DEFAULT_DIFF_REPORT_PATH = REPO_ROOT / "docs" / "stations_diff.md"
_DEFAULT_POLYGON_PATH = REPO_ROOT / "data" / "LANDESGRENZEOGD.json"
_COORD_SHIFT_THRESHOLD_M = 100.0


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
    return parser.parse_args(argv)


def configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(message)s")


def run_script(python: str, script_path: Path, verbose: bool, output_flag: str, output_path: Path) -> None:
    cmd = [python, str(script_path), output_flag, str(output_path)]
    if verbose:
        cmd.append("--verbose")
    logging.info("Running %s", script_path.name)
    # Enforce a 10-minute timeout for each update script to prevent indefinite hangs
    subprocess.run(cmd, check=True, shell=False, timeout=600)  # nosec B603


def _load_stations(path: Path) -> list[Mapping[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
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
    lines = [
        "# stations.json Diff Report",
        "",
        f"_Generated: {timestamp}_",
        "",
        f"**Stations: {before_count} → {after_count} (Δ {after_count - before_count:+d})**",
        "",
    ]

    def section(title: str, items: list[Any], formatter: Any) -> None:
        lines.append(f"## {title} ({len(items)})")
        lines.append("")
        if items:
            for item in items:
                lines.append(formatter(item))
        else:
            lines.append("_None._")
        lines.append("")

    section("Added", diff["added"], lambda it: f"- `{it[0]}` — {it[1]}")
    section("Removed", diff["removed"], lambda it: f"- `{it[0]}` — {it[1]}")
    section(
        "Renamed",
        diff["renamed"],
        lambda it: f'- `{it[0]}`: "{it[1]}" → "{it[2]}"',
    )
    section(
        f"Coordinates shifted (≥ {int(_COORD_SHIFT_THRESHOLD_M)} m)",
        diff["coord_shifted"],
        lambda it: f"- `{it[0]}` — {it[1]} ({it[2]} m)",
    )

    return "\n".join(lines).rstrip() + "\n"


def _count_polygon_vertices(path: Path) -> int | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
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


def _collect_blocking_issues(report: ValidationReport) -> list[tuple[str, str]]:
    """Return (category, message) tuples for issues that must block the commit."""
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


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    configure_logging(args.verbose)

    script_dir = Path(__file__).resolve().parent
    target_stations_json = Path("data/stations.json").resolve()

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

        blocking = _collect_blocking_issues(report)
        if blocking:
            for category, message in blocking:
                logging.error("%s issue: %s", category, message)
            logging.error(
                "Validation failed on the new stations data. Working tree left unmodified."
            )
            return 1

        # Compute the diff between the pre-update snapshot and the merged file
        # before atomic copy-back so the heartbeat reflects what is about to land.
        after_snapshot = _load_stations(tmp_stations_path)
        diff = _compute_diff(before_snapshot, after_snapshot)
        timestamp = datetime.now(UTC).isoformat(timespec="seconds")
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
        _DEFAULT_HEARTBEAT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with atomic_write(_DEFAULT_HEARTBEAT_PATH, mode="w", encoding="utf-8") as handle:
            json.dump(heartbeat, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        _DEFAULT_DIFF_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with atomic_write(_DEFAULT_DIFF_REPORT_PATH, mode="w", encoding="utf-8") as handle:
            handle.write(_render_diff_markdown(
                diff,
                before_count=len(before_snapshot),
                after_count=len(after_snapshot),
                timestamp=timestamp,
            ))
        logging.info(
            "Wrote heartbeat (%s) and diff report (%s)",
            _DEFAULT_HEARTBEAT_PATH.name,
            _DEFAULT_DIFF_REPORT_PATH.name,
        )

    logging.info("All station update scripts completed successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
