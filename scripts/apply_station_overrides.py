#!/usr/bin/env python3
"""Apply manual station-directory overrides on top of the WL merge output.

Reads ``data/stations_overrides.json``, walks the declared operations in
order, and patches ``data/stations.json`` accordingly. Designed to run
between ``scripts/update_wl_stations.py`` and the validator gate in
``scripts/update_all_stations.py`` so that:

* upstream-source defects in Wiener Linien OGD (wrong coordinates for
  some DIVAs, missing haltepunkte for retired-but-listed stations,
  geographic-duplicate haltepunkte for distinct DIVAs) can be patched
  without forking the build logic;
* every correction is auditable — each override carries a ``reason``
  field and an ``expires_when`` predicate that documents what needs to
  change in the upstream feed for the override to become obsolete;
* the correction surface is narrow — three operation types only
  (``restore`` / ``patch_coords`` / ``remove``) so a malicious or
  careless override cannot reshape the directory beyond its declared
  blast radius.

The script is idempotent: re-running it against an already-patched
``stations.json`` is a no-op (each operation checks the current state
before mutating). Unknown DIVAs in ``patch_coords`` / ``remove`` are
logged as a WARNING and skipped, so an override naturally expires the
day the upstream feed gets fixed without breaking the pipeline.

Exit codes:
    0  — overrides applied (or all already in place)
    1  — overrides file missing or unparseable
    2  — stations file missing or unparseable
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, cast

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.feed.logging_safe import setup_script_logging  # noqa: E402
from src.utils.files import atomic_write, read_capped_text  # noqa: E402
from src.utils.stations import MAX_STATIONS_FILE_BYTES  # noqa: E402

# Cap matches the overrides file's expected upper bound; 1 MiB is generous
# for a curated correction list (the four 2026-05-16 entries together are
# ~12 KiB).
MAX_OVERRIDES_FILE_BYTES = 1 * 1024 * 1024

log = logging.getLogger("apply_station_overrides")

_ALLOWED_OPS = frozenset({"restore", "patch_coords", "remove"})


class OverrideError(RuntimeError):
    """Raised when the overrides file violates the documented schema."""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Apply curated overrides to data/stations.json",
    )
    parser.add_argument(
        "--stations",
        type=Path,
        default=REPO_ROOT / "data" / "stations.json",
        help="Path to stations.json to patch (default: data/stations.json)",
    )
    parser.add_argument(
        "--overrides",
        type=Path,
        default=REPO_ROOT / "data" / "stations_overrides.json",
        help="Path to stations_overrides.json (default: data/stations_overrides.json)",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable INFO-level logging",
    )
    return parser.parse_args(argv)


def configure_logging(verbose: bool) -> None:
    # ``setup_script_logging`` installs the canonical ``SafeFormatter``
    # on the root handler so any string interpolated into a log record
    # — including the operator-controlled ``reason`` / ``expires_when``
    # fields in ``data/stations_overrides.json`` — gets the CVE-2021-
    # 42574 / log-injection / 8-bit-C1 / Tag-block / ANSI-ESC defence
    # the rest of the pipeline relies on. Replaces a plain
    # ``logging.basicConfig`` call; see
    # ``tests/test_sentinel_preflight_basicconfig_drift.py::test_no_
    # basicconfig_in_scripts`` for the pinned invariant.
    setup_script_logging(logging.INFO if verbose else logging.WARNING)


def _load_json(path: Path, max_bytes: int, label: str) -> Any:
    text = read_capped_text(path, max_bytes, encoding="utf-8", label=label, logger=log)
    if text is None:
        raise OverrideError(f"{label} not loadable: {path}")
    try:
        return json.loads(text)
    except (json.JSONDecodeError, RecursionError) as exc:
        # Security: ``RecursionError`` covers JSON depth-bomb attacks
        # — a deeply nested but otherwise valid JSON body served by a
        # compromised upstream / committed by a hostile fork would
        # otherwise propagate past this handler and crash the
        # orchestrator. Mirrors the canonical defence the rest of the
        # ``scripts/`` tree applies (see ``.jules/sentinel.md`` —
        # JSON Depth-Bomb Drift Round 5; pinned by
        # ``tests/test_sentinel_json_audit_walker.py::test_every_
        # json_parser_site_catches_recursion_error``).
        raise OverrideError(f"{label} parse error in {path}: {exc}") from exc


def _stations_list(payload: Any) -> list[dict[str, Any]]:
    """Return the mutable station list from either the wrapped or bare form."""
    if isinstance(payload, list):
        return cast(list[dict[str, Any]], payload)
    if isinstance(payload, dict) and isinstance(payload.get("stations"), list):
        return cast(list[dict[str, Any]], payload["stations"])
    raise OverrideError("stations payload must be a list or a {'stations': [...]} object")


def _find_by_diva(stations: list[dict[str, Any]], diva: str) -> dict[str, Any] | None:
    """Return the first station entry with matching wl_diva, or None."""
    for entry in stations:
        value = entry.get("wl_diva")
        if isinstance(value, str) and value.strip() == diva:
            return entry
    return None


def _alpha_insertion_index(stations: list[dict[str, Any]], target_name: str) -> int:
    """Pick a sorted insertion index by case-insensitive ``name``.

    Mirrors the alphabetical layout the WL pipeline emits so a restored
    entry doesn't visually disrupt the diff. Stations without a string
    ``name`` are skipped during comparison — they sort to the end and
    don't affect placement of normal entries.
    """
    key = target_name.casefold()
    for idx, entry in enumerate(stations):
        name = entry.get("name")
        if isinstance(name, str) and key < name.casefold():
            return idx
    return len(stations)


def _op_restore(stations: list[dict[str, Any]], override: dict[str, Any]) -> str:
    diva = override["wl_diva"]
    entry_template = override.get("entry")
    if not isinstance(entry_template, dict):
        raise OverrideError(
            f"restore: 'entry' must be an object for wl_diva={diva}"
        )
    existing = _find_by_diva(stations, diva)
    if existing is not None:
        return "skip (already present)"
    new_entry = dict(entry_template)
    name = new_entry.get("name", "<unknown>")
    insert_at = _alpha_insertion_index(stations, name if isinstance(name, str) else "")
    stations.insert(insert_at, new_entry)
    return f"restored {name!r} at index {insert_at}"


def _op_patch_coords(stations: list[dict[str, Any]], override: dict[str, Any]) -> str:
    diva = override["wl_diva"]
    target = _find_by_diva(stations, diva)
    if target is None:
        log.warning(
            "patch_coords: wl_diva=%s not present in stations.json — "
            "skipping (the upstream may have removed the station; "
            "consider retiring this override)",
            diva,
        )
        return "skip (target missing)"

    changed_fields: list[str] = []
    for field in ("latitude", "longitude", "in_vienna", "pendler"):
        if field in override and target.get(field) != override[field]:
            target[field] = override[field]
            changed_fields.append(field)

    stops_patch = override.get("wl_stops_patch") or []
    if stops_patch:
        wl_stops = target.get("wl_stops")
        if isinstance(wl_stops, list):
            by_stop_id = {
                str(s.get("stop_id", "")).strip(): s
                for s in wl_stops
                if isinstance(s, dict)
            }
            for patch in stops_patch:
                if not isinstance(patch, dict):
                    continue
                sid = str(patch.get("stop_id", "")).strip()
                target_stop = by_stop_id.get(sid)
                if target_stop is None:
                    continue
                for sub in ("latitude", "longitude"):
                    if sub in patch and target_stop.get(sub) != patch[sub]:
                        target_stop[sub] = patch[sub]
                        changed_fields.append(f"wl_stops[{sid}].{sub}")

    if not changed_fields:
        return f"skip (no change, wl_diva={diva})"
    return f"patched wl_diva={diva}, fields={changed_fields}"


def _op_remove(stations: list[dict[str, Any]], override: dict[str, Any]) -> str:
    diva = override["wl_diva"]
    for i, entry in enumerate(stations):
        value = entry.get("wl_diva")
        if isinstance(value, str) and value.strip() == diva:
            removed_name = entry.get("name", "?")
            stations.pop(i)
            return f"removed wl_diva={diva} ({removed_name!r}) at index {i}"
    log.warning(
        "remove: wl_diva=%s not present in stations.json — skipping "
        "(the upstream may have removed it already; consider retiring "
        "this override)",
        diva,
    )
    return "skip (target missing)"


_HANDLERS = {
    "restore": _op_restore,
    "patch_coords": _op_patch_coords,
    "remove": _op_remove,
}


def apply_overrides(
    stations_path: Path,
    overrides_path: Path,
) -> int:
    if not overrides_path.exists():
        log.error("Overrides file not found: %s", overrides_path)
        return 1
    if not stations_path.exists():
        log.error("Stations file not found: %s", stations_path)
        return 2

    try:
        overrides_payload = _load_json(
            overrides_path, MAX_OVERRIDES_FILE_BYTES, "Overrides"
        )
        stations_payload = _load_json(
            stations_path, MAX_STATIONS_FILE_BYTES, "Stations"
        )
    except OverrideError as exc:
        log.error("%s", exc)
        return 1

    if not isinstance(overrides_payload, dict) or not isinstance(
        overrides_payload.get("overrides"), list
    ):
        log.error(
            "Overrides file must be an object with an 'overrides' list: %s",
            overrides_path,
        )
        return 1

    try:
        stations = _stations_list(stations_payload)
    except OverrideError as exc:
        log.error("%s", exc)
        return 2

    applied = 0
    for index, raw_override in enumerate(overrides_payload["overrides"]):
        if not isinstance(raw_override, dict):
            log.error("Override #%d is not an object: %r", index, raw_override)
            return 1
        op = raw_override.get("op")
        diva = raw_override.get("wl_diva")
        if op not in _ALLOWED_OPS:
            log.error(
                "Override #%d has unknown op %r (allowed: %s)",
                index, op, sorted(_ALLOWED_OPS),
            )
            return 1
        if not isinstance(diva, str) or not diva.strip():
            log.error(
                "Override #%d (op=%s) missing or invalid wl_diva", index, op
            )
            return 1
        handler = _HANDLERS[op]
        try:
            result = handler(stations, raw_override)
        except OverrideError as exc:
            log.error("Override #%d (op=%s, wl_diva=%s): %s", index, op, diva, exc)
            return 1
        log.info("Override #%d (op=%s, wl_diva=%s): %s", index, op, diva, result)
        if not result.startswith("skip"):
            applied += 1

    # Persist
    text = json.dumps(stations_payload, indent=2, ensure_ascii=False)
    with atomic_write(stations_path, mode="w", encoding="utf-8", permissions=0o644) as handle:
        handle.write(text)
        handle.write("\n")
    log.info(
        "Applied %d/%d overrides → %s (%d stations)",
        applied, len(overrides_payload["overrides"]), stations_path, len(stations),
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    configure_logging(args.verbose)
    return apply_overrides(args.stations, args.overrides)


if __name__ == "__main__":
    sys.exit(main())
