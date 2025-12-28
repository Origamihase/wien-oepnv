#!/usr/bin/env python3
"""Convenience wrapper to refresh all station datasets."""
from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from pathlib import Path
from typing import Sequence

_SCRIPT_ORDER = (
    "update_station_directory.py",
    "update_vor_stations.py",
    "update_wl_stations.py",
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


def run_script(python: str, script_path: Path, verbose: bool) -> None:
    cmd = [python, str(script_path)]
    if verbose:
        cmd.append("--verbose")
    logging.info("Running %s", script_path.name)
    # Enforce a 10-minute timeout for each update script to prevent indefinite hangs
    subprocess.run(cmd, check=True, timeout=600)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    configure_logging(args.verbose)

    script_dir = Path(__file__).resolve().parent
    for script_name in _SCRIPT_ORDER:
        script_path = script_dir / script_name
        if not script_path.exists():
            logging.error("Script not found: %s", script_path)
            return 1
        try:
            run_script(args.python, script_path, args.verbose)
        except subprocess.CalledProcessError as exc:  # pragma: no cover - thin wrapper
            logging.error(
                "Script %s failed with exit code %s", script_path.name, exc.returncode
            )
            return exc.returncode or 1

    logging.info("All station update scripts completed successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
