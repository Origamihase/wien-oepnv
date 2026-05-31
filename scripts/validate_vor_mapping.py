#!/usr/bin/env python3
"""Validate VOR station mapping IDs."""

import re
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from src.utils.files import read_capped_json  # noqa: E402

# Security cap against wide-but-flat JSON size-bomb attacks. Mirrors the
# canonical ``MAX_*_FILE_BYTES`` contract: the depth-bomb catch alone
# misses ``MemoryError`` (a ``BaseException`` subclass) so a
# planted-huge mapping file (~1 GiB of ``[0,0,…]``) buffered via
# ``path.read_text()`` propagates past the loader and crashes the
# validator with an unhandled traceback instead of the documented
# exit-1 contract.
MAX_JSON_FILE_BYTES = 50 * 1024 * 1024


def main() -> int:
    mapping_path = Path("data/vor-haltestellen.mapping.json")
    if not mapping_path.exists():
        print(f"File not found: {mapping_path}", file=sys.stderr)
        return 1

    data = read_capped_json(
        mapping_path, MAX_JSON_FILE_BYTES, label="VOR mapping",
    )
    if data is None:
        print(
            f"JSON decode error or file too large: {mapping_path}",
            file=sys.stderr,
        )
        return 1

    if not isinstance(data, list):
        print("Root element must be a list", file=sys.stderr)
        return 1

    # HAFAS ID pattern: typically 7 to 9 digits (e.g. 430471000, 8100002)
    # The requirement says "usually 7-9 digits, often starting with 81 or 43".
    # We will flag anything that is NOT 7-9 digits. ``[0-9]`` (not ``\d``)
    # restricts to ASCII digits — the Unicode-aware ``\d`` would accept
    # Arabic-Indic/Devanagari/full-width digits — and the match is applied
    # via ``fullmatch`` so a trailing newline (which bare ``$`` tolerates)
    # is rejected too.
    pattern = re.compile(r"[0-9]{7,9}")

    errors = 0
    for i, entry in enumerate(data):
        # Zero Trust: a JSON-decoded list does not guarantee object elements.
        # A tampered or hand-edited mapping could contain scalars / nulls /
        # nested lists that would crash the validator with AttributeError on
        # ``.get()`` and bypass the documented "Found N errors" exit path.
        # Mirror the per-entry guard already applied to every other reader of
        # ``vor-haltestellen.mapping.json`` (``src/providers/vor.py``,
        # ``scripts/enrich_station_aliases.py``,
        # ``scripts/update_station_directory.py``,
        # ``scripts/update_wl_stations.py``).
        if not isinstance(entry, dict):
            print(
                f"Entry {i} is not a JSON object (got {type(entry).__name__})",
                file=sys.stderr,
            )
            errors += 1
            continue

        vor_id = entry.get("vor_id")
        name = entry.get("station_name") or "Unknown"

        # ``vor_id is None`` (or empty string) is "missing"; integer ``0``
        # / ``False`` (etc.) are STRUCTURALLY PRESENT — pre-fix
        # ``if not vor_id`` reported them as missing and short-circuited
        # the more informative downstream invalid-id message.
        if vor_id is None or vor_id == "":
            print(f"Entry {i} ({name}) missing vor_id", file=sys.stderr)
            errors += 1
            continue

        if not isinstance(vor_id, str):
            # Try to cast int to str
            vor_id = str(vor_id)

        if not pattern.fullmatch(vor_id):
            print(f"Invalid VOR ID format for '{name}': {vor_id} (expected 7-9 digits)", file=sys.stderr)
            errors += 1

    if errors:
        print(f"Found {errors} errors.", file=sys.stderr)
        return 1

    print("Validation successful. All IDs match format.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
