#!/usr/bin/env python3
"""Convenience wrapper to refresh all station datasets."""
from __future__ import annotations

import argparse
import logging
import shutil
import subprocess  # nosec B404 - utility script to run internal scripts
import sys
import tempfile
from pathlib import Path
from typing import Sequence

_SCRIPT_ORDER = (
    "update_station_directory.py",
    "update_vor_stations.py",
    "update_wl_stations.py",
    "enrich_station_aliases.py",
)


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


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    configure_logging(args.verbose)

    script_dir = Path(__file__).resolve().parent
    target_stations_json = Path("data/stations.json").resolve()

    _SCRIPT_OUTPUT_FLAG = {
        "update_station_directory.py": "--output",
        "update_vor_stations.py": "--stations",
        "update_wl_stations.py": "--stations",
        "enrich_station_aliases.py": "--stations",
    }

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_stations_path = Path(tmp_dir) / "stations.json"

        if target_stations_json.exists():
            shutil.copy2(target_stations_json, tmp_stations_path)

        for script_name in _SCRIPT_ORDER:
            script_path = script_dir / script_name
            if not script_path.exists():
                logging.error("Script not found: %s", script_path)
                return 1

            output_flag = _SCRIPT_OUTPUT_FLAG.get(script_name)
            if not output_flag:
                logging.error("No output flag mapping found for %s", script_name)
                return 1

            try:
                run_script(args.python, script_path, args.verbose, output_flag, tmp_stations_path)
            except subprocess.CalledProcessError as exc:  # pragma: no cover - thin wrapper
                logging.error(
                    "Script %s failed with exit code %s", script_path.name, exc.returncode
                )
                return exc.returncode or 1

        # Run validation
        validate_script = script_dir / "validate_stations.py"
        validate_cmd = [args.python, str(validate_script), "--stations", str(tmp_stations_path)]
        logging.info("Validating merged %s", tmp_stations_path)
        try:
            subprocess.run(validate_cmd, check=True, shell=False, timeout=300)  # nosec B603
        except subprocess.CalledProcessError as exc:
            logging.error(
                "Validation failed on the new stations data. Working tree left unmodified."
            )
            return exc.returncode or 1

        # Copy back to target on success
        shutil.copy(tmp_stations_path, target_stations_json)
        logging.info("stations.json successfully updated and validated.")

    logging.info("All station update scripts completed successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
