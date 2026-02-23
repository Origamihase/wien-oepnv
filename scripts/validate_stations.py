#!/usr/bin/env python3
"""Validate station directory entries for VOR metadata."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


def main() -> int:
    stations_path = Path("data/stations.json")

    try:
        data = json.loads(stations_path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            stations = data.get("stations", [])
        elif isinstance(data, list):
            stations = data
        else:
            stations = []
    except FileNotFoundError:
        print("data/stations.json not found", file=sys.stderr)
        return 1
    except json.JSONDecodeError as exc:
        print(f"Invalid JSON in data/stations.json: {exc}", file=sys.stderr)
        return 1

    vor_entries = []
    for station in stations:
        source = station.get("source")
        is_vor = False
        if isinstance(source, str):
            parts = [s.strip() for s in source.split(",")]
            if "vor" in parts:
                is_vor = True
        elif isinstance(source, list):
            if "vor" in source:
                is_vor = True

        if is_vor:
            vor_entries.append(station)

    if len(vor_entries) < 2:
        print("Need at least two VOR entries", file=sys.stderr)
        return 1

    # Historically, we expected all VOR identifiers to start with "900" and be six
    # digits long. In practice there are legitimate five digit identifiers such as
    # "93010" which caused the validation to reject otherwise valid data. Allow
    # either five, six, or seven digit numeric identifiers starting with "8" or "9"
    # (IDs starting with 81... appear in GTFS for specific platforms).
    pattern = re.compile(r"[89]\d{4,6}(?::\d+)?")
    for station in vor_entries:
        # If the station comes from GTFS (source="vor"), it might rely on vor_id
        # instead of bst_id/bst_code. We check vor_id if bst_code is missing.
        check_val = station.get("bst_code") or station.get("vor_id")

        if not isinstance(check_val, str) or not pattern.fullmatch(check_val):
            # Allow skipping this check if we have a valid vor_id but no bst_* fields,
            # which is common for pure GTFS stops.
            if station.get("vor_id") and not station.get("bst_code"):
                 if pattern.fullmatch(station.get("vor_id")):
                     continue

            print(f"Invalid identifier for VOR: {check_val} (bst_code/vor_id)", file=sys.stderr)
            return 1

    oebb_codes = {
        station.get("bst_code")
        for station in stations
        if station.get("source") == "oebb"
    }
    conflicts = [station for station in vor_entries if station.get("bst_code") in oebb_codes]
    if conflicts:
        print("VOR bst_code collides with OEBB", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
