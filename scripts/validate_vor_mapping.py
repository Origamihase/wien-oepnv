#!/usr/bin/env python3
"""Validate VOR station mapping IDs."""

import json
import re
import sys
from pathlib import Path

def main() -> int:
    mapping_path = Path("data/vor-haltestellen.mapping.json")
    if not mapping_path.exists():
        print(f"File not found: {mapping_path}", file=sys.stderr)
        return 1

    try:
        data = json.loads(mapping_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"JSON decode error: {e}", file=sys.stderr)
        return 1

    if not isinstance(data, list):
        print("Root element must be a list", file=sys.stderr)
        return 1

    # HAFAS ID pattern: typically 7 to 9 digits (e.g. 430471000, 8100002)
    # The requirement says "usually 7-9 digits, often starting with 81 or 43".
    # We will flag anything that is NOT 7-9 digits.
    pattern = re.compile(r"^\d{7,9}$")

    errors = 0
    for i, entry in enumerate(data):
        vor_id = entry.get("vor_id")
        name = entry.get("station_name") or "Unknown"

        if not vor_id:
            print(f"Entry {i} ({name}) missing vor_id", file=sys.stderr)
            errors += 1
            continue

        if not isinstance(vor_id, str):
            # Try to cast int to str
            vor_id = str(vor_id)

        if not pattern.match(vor_id):
            print(f"Invalid VOR ID format for '{name}': {vor_id} (expected 7-9 digits)", file=sys.stderr)
            errors += 1

    if errors:
        print(f"Found {errors} errors.", file=sys.stderr)
        return 1

    print("Validation successful. All IDs match format.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
