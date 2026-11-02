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
import logging
import shutil
# Bandit B404: subprocess is required to invoke internal cache-refresh
# scripts. Inputs are static lists, never user-supplied.
import subprocess  # nosec B404
import sys
import tempfile
import time
from datetime import datetime, UTC
from pathlib import Path
from typing import Any
from collections.abc import Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.feed.logging_safe import setup_script_logging  # noqa: E402
from src.places.merge import write_stations_payload  # noqa: E402
from src.utils.files import atomic_write, read_capped_json  # noqa: E402  (import after path setup)
from src.utils.stations_validation import (  # noqa: E402
    StationValidationError,
    ValidationReport,
    validate_stations,
)
from src.stations_refresh.diff import (  # noqa: E402
    _DiffResult as _DiffResult,
    _compute_diff as _compute_diff,
    _render_diff_markdown as _render_diff_markdown,
)
from src.stations_refresh.heartbeat import (  # noqa: E402
    _write_heartbeat_file as _write_heartbeat_file,
)
from src.stations_refresh.quarantine import (  # noqa: E402
    _collect_blocking_issues as _collect_blocking_issues,
    _collect_quarantine_identifiers as _collect_quarantine_identifiers,
    _collect_quarantine_reasons as _collect_quarantine_reasons,
    _dedupe_exact_duplicates as _dedupe_exact_duplicates,
    _partition_stations as _partition_stations,
    _write_quarantine_file as _write_quarantine_file,
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
        default=_DEFAULT_HEARTBEAT_PATH,
        help=(
            "Path to write the run heartbeat "
            "(default: data/stations_last_run.json under the repository root)."
        ),
    )
    parser.add_argument(
        "--diff-report",
        type=Path,
        default=_DEFAULT_DIFF_REPORT_PATH,
        help=(
            "Path to write the human-readable diff report "
            "(default: docs/stations_diff.md under the repository root)."
        ),
    )
    parser.add_argument(
        "--quarantine",
        type=Path,
        default=_DEFAULT_QUARANTINE_PATH,
        help=(
            "Path to write the auto-quarantine sidecar on validation "
            "failure (default: data/quarantine.json under the repository root)."
        ),
    )
    parser.add_argument(
        "--polygon",
        type=Path,
        default=_DEFAULT_POLYGON_PATH,
        help=(
            "Path to the Vienna boundary polygon whose vertex count is "
            "recorded in the heartbeat (default: data/LANDESGRENZEOGD.json "
            "under the repository root). Exposed so a relocated run reads a "
            "relocated polygon instead of the production data dir."
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
        # Mirror *every* category the report carries. The sidecar is what an
        # operator reads after a cron run, so a category missing here reads as
        # "clean" no matter what the run actually found. Audit 2026-09-13:
        # ``alias_collision_issues`` was the only non-zero category and was
        # one of the three omitted ones.
        "validation": {
            "duplicates": len(report.duplicates),
            "alias_issues": len(report.alias_issues),
            "coordinate_issues": len(report.coordinate_issues),
            "gtfs_issues": len(report.gtfs_issues),
            "security_issues": len(report.security_issues),
            "cross_station_id_issues": len(report.cross_station_id_issues),
            "identity_field_conflicts": len(report.identity_field_conflicts),
            "provider_issues": len(report.provider_issues),
            "naming_issues": len(report.naming_issues),
            "cross_name_alias_issues": len(report.cross_name_alias_issues),
            "alias_collision_issues": len(report.alias_collision_issues),
            "name_ownership_issues": len(report.name_ownership_issues),
        },
        "diff": {
            "added": len(diff["added"]),
            "removed": len(diff["removed"]),
            "renamed": len(diff["renamed"]),
            "coord_shifted": len(diff["coord_shifted"]),
        },
        "polygon_vertices": polygon_vertices,
    }




def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    configure_logging(args.verbose)

    script_dir = Path(__file__).resolve().parent
    target_stations_json: Path = args.target.resolve()
    heartbeat_path: Path = args.heartbeat
    diff_report_path: Path = args.diff_report
    quarantine_path: Path = args.quarantine
    polygon_path: Path = args.polygon

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
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:  # pragma: no cover - thin wrapper
                duration = time.monotonic() - start
                # TimeoutExpired is a sibling of CalledProcessError (NOT a
                # subclass) and carries no ``returncode``: a sub-script that
                # actually exceeds the 600 s budget — the exact hang the
                # timeout guards — would otherwise escape this handler and
                # crash the orchestrator with a traceback, bypassing the
                # structured failure record. Record it like any other failure.
                exit_code = getattr(exc, "returncode", None) or 1
                sub_script_results.append({
                    "name": script_name,
                    "exit_code": exit_code,
                    "duration_s": round(duration, 2),
                })
                logging.error(
                    "Script %s failed with exit code %s", script_path.name, exit_code
                )
                return exit_code
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
            write_stations_payload(tmp_stations_path, deduped_stations)

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
                write_stations_payload(tmp_stations_path, valid_stations)
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
        polygon_vertices = _count_polygon_vertices(polygon_path)
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
