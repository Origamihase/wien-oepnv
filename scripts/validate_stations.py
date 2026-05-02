#!/usr/bin/env python3
"""Validate station directory entries for VOR metadata.

Thin CLI wrapper around :func:`src.utils.stations_validation.validate_stations`.
Exit code semantics are preserved from the previous inline implementation:
only ``provider_issues`` and ``cross_station_id_issues`` trigger a non-zero
exit. Other issue categories (alias, coordinate, GTFS, security, duplicate)
are tolerated to match historical CLI behaviour and to keep the
``scripts/update_all_stations.py`` wrapper's strictness contract unchanged.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from src.utils.stations_validation import (  # noqa: E402
    StationValidationError,
    validate_stations,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate station directory entries for VOR metadata."
    )
    parser.add_argument(
        "--stations",
        type=Path,
        default=Path("data/stations.json"),
        help="Path to stations.json to validate (default: data/stations.json)",
    )
    args = parser.parse_args()

    try:
        report = validate_stations(args.stations)
    except StationValidationError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    has_error = False

    if report.provider_issues:
        for p_issue in report.provider_issues:
            print(p_issue.reason, file=sys.stderr)
        has_error = True

    if report.cross_station_id_issues:
        for c_issue in report.cross_station_id_issues:
            print(
                f"Cross-station alias conflict: Alias '{c_issue.alias}' in '{c_issue.name}' "
                f"({c_issue.identifier}) collides with {c_issue.colliding_field} of "
                f"'{c_issue.colliding_name}' ({c_issue.colliding_identifier})",
                file=sys.stderr,
            )
        has_error = True

    return 1 if has_error else 0


if __name__ == "__main__":
    sys.exit(main())
