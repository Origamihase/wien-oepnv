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

    vor_entries = [station for station in stations if station.get("source") == "vor"]
    if len(vor_entries) < 2:
        print("Need at least two VOR entries", file=sys.stderr)
        return 1

    pattern = re.compile(r"900\d{3}")
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
