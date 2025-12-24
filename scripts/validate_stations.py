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
        stations = json.loads(stations_path.read_text(encoding="utf-8"))
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
    # either five or six digit numeric identifiers starting with "9".
    pattern = re.compile(r"9\d{4,5}")
    for station in vor_entries:
        for key in ("bst_id", "bst_code"):
            value = station.get(key)
            if not isinstance(value, str) or not pattern.fullmatch(value):
                print(f"Invalid {key} for VOR: {value}", file=sys.stderr)
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
