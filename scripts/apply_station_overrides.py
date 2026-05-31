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
from src.utils.files import atomic_write, loads_finite, read_capped_text  # noqa: E402
from src.utils.serialize import scrub_trojan_source_primitives  # noqa: E402
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
        # Security (reader-side non-finite literal defence, symmetric
        # to the writer-side ``allow_nan=False`` pin in
        # ``apply_overrides`` below). ``loads_finite`` bakes in both
        # ``parse_constant=_reject_non_finite_constant`` and
        # ``parse_float=_reject_non_finite_float`` so a planted
        # ``NaN`` / ``Infinity`` / ``-Infinity`` constant token OR a
        # planted ``1e1000`` scientific-notation overflow in either
        # ``data/stations_overrides.json`` (hostile PR landing a
        # tampered patch_coords entry) or ``data/stations.json``
        # (compromised CI runner / parallel orchestrator atomic state
        # swap / partial flush + power loss) surfaces as
        # ``json.JSONDecodeError`` at this boundary rather than
        # propagating ``float('nan')`` / ``float('inf')`` through
        # ``patch_coords`` and round-tripping back to
        # ``data/stations.json``. Mirrors the canonical sibling
        # readers (``read_capped_json`` / ``load_stations`` /
        # ``read_cache`` / ``_load_state`` / ``MonthlyQuota.load`` —
        # 2026-05-15 PR #1503). ``RecursionError`` is re-raised
        # unchanged by ``loads_finite`` so the depth-bomb defence
        # (JSON Depth-Bomb Drift Round 5) is preserved.
        return loads_finite(text)
    except (json.JSONDecodeError, RecursionError) as exc:
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


def _find_by_eva(stations: list[dict[str, Any]], eva: str) -> dict[str, Any] | None:
    """Return the first station entry with matching ``eva_nr``, or None.

    Lets ``patch_coords`` target the manual ÖBB-station entries
    (``type=manual_*``) that carry an ``eva_nr`` but no ``wl_diva``.
    ``eva_nr`` is a string in stations.json; ``str(...)`` keeps the match
    robust against a defensively int-typed value.
    """
    for entry in stations:
        value = entry.get("eva_nr")
        if value is not None and str(value).strip() == eva:
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
    # Force the inserted record's identity field to equal the idempotency
    # key. Otherwise, if a curated ``entry`` template ever carried a
    # different (or missing) ``wl_diva``, the next run's
    # ``_find_by_diva(stations, diva)`` would not match the inserted
    # record and restore would re-insert a duplicate on every cron tick.
    new_entry["wl_diva"] = diva
    name = new_entry.get("name", "<unknown>")
    insert_at = _alpha_insertion_index(stations, name if isinstance(name, str) else "")
    stations.insert(insert_at, new_entry)
    return f"restored {name!r} at index {insert_at}"


def _op_patch_coords(stations: list[dict[str, Any]], override: dict[str, Any]) -> str:
    # Target by wl_diva (WL stops) or, as a fallback, eva_nr (manual ÖBB
    # stations that carry no wl_diva). The apply loop guarantees at least
    # one identifier is present.
    diva = override.get("wl_diva")
    if isinstance(diva, str) and diva.strip():
        target = _find_by_diva(stations, diva.strip())
        key_desc = f"wl_diva={diva.strip()}"
    else:
        eva_key = str(override.get("eva_nr") or "").strip()
        target = _find_by_eva(stations, eva_key)
        key_desc = f"eva_nr={eva_key}"
    if target is None:
        log.warning(
            "patch_coords: %s not present in stations.json — skipping "
            "(the upstream may have removed the station; consider "
            "retiring this override)",
            key_desc,
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
        return f"skip (no change, {key_desc})"
    return f"patched {key_desc}, fields={changed_fields}"


def _op_remove(stations: list[dict[str, Any]], override: dict[str, Any]) -> str:
    diva = override.get("wl_diva")
    if isinstance(diva, str) and diva.strip():
        target = diva.strip()
        for i, entry in enumerate(stations):
            value = entry.get("wl_diva")
            if isinstance(value, str) and value.strip() == target:
                removed_name = entry.get("name", "?")
                stations.pop(i)
                return f"removed wl_diva={target} ({removed_name!r}) at index {i}"
        log.warning(
            "remove: wl_diva=%s not present in stations.json — skipping "
            "(the upstream may have removed it already; consider retiring "
            "this override)",
            target,
        )
        return "skip (target missing)"

    # bst_code targeting: for oebb_geonetz Betriebsstellen that carry no
    # wl_diva and that the build re-creates each run (e.g. the Handelskai
    # "Nw  H2" record, which duplicates the canonical "Wien Handelskai" by
    # eva_nr). Remove EVERY match so a regenerated copy cannot survive into
    # the validator gate.
    code = str(override.get("bst_code") or "").strip()
    removed_names = [
        str(entry.get("name", "?"))
        for entry in stations
        if isinstance(entry.get("bst_code"), str) and entry["bst_code"].strip() == code
    ]
    if not removed_names:
        log.warning(
            "remove: bst_code=%s not present in stations.json — skipping "
            "(the upstream may have removed it already; consider retiring "
            "this override)",
            code,
        )
        return "skip (target missing)"
    stations[:] = [
        entry
        for entry in stations
        if not (isinstance(entry.get("bst_code"), str) and entry["bst_code"].strip() == code)
    ]
    return f"removed bst_code={code} ({removed_names!r})"


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
        eva = raw_override.get("eva_nr")
        if op not in _ALLOWED_OPS:
            log.error(
                "Override #%d has unknown op %r (allowed: %s)",
                index, op, sorted(_ALLOWED_OPS),
            )
            return 1
        has_diva = isinstance(diva, str) and bool(diva.strip())
        bst_code = raw_override.get("bst_code")
        has_bst_code = isinstance(bst_code, str) and bool(bst_code.strip())
        # ``patch_coords`` may key on ``eva_nr`` (manual ÖBB stations have no
        # ``wl_diva``); ``remove`` may key on ``bst_code`` (oebb_geonetz
        # Betriebsstellen have no ``wl_diva``); ``restore`` still requires
        # ``wl_diva``.
        if op == "patch_coords":
            has_eva = eva is not None and bool(str(eva).strip())
            if not (has_diva or has_eva):
                log.error(
                    "Override #%d (op=patch_coords) needs a wl_diva or eva_nr",
                    index,
                )
                return 1
        elif op == "remove":
            if not (has_diva or has_bst_code):
                log.error(
                    "Override #%d (op=remove) needs a wl_diva or bst_code",
                    index,
                )
                return 1
        elif not has_diva:
            log.error(
                "Override #%d (op=%s) missing or invalid wl_diva", index, op
            )
            return 1
        if has_diva:
            ident = str(diva).strip()
        elif has_bst_code:
            ident = f"bst_code={str(bst_code).strip()}"
        else:
            ident = f"eva_nr={str(eva).strip()}"
        handler = _HANDLERS[op]
        try:
            result = handler(stations, raw_override)
        except OverrideError as exc:
            log.error("Override #%d (op=%s, %s): %s", index, op, ident, exc)
            return 1
        log.info("Override #%d (op=%s, %s): %s", index, op, ident, result)
        if not result.startswith("skip"):
            applied += 1

    # Persist
    # Security (Trojan-Source / BiDi-Mark Drift, ingestion-boundary
    # defence): strip the canonical CVE-2021-42574 attack-byte union
    # (BiDi formatting controls, BiDi isolates, zero-width primitives +
    # LRM/RLM/ALM, Unicode line / paragraph separators, the BOM / ZWNBSP,
    # and the 8-bit C1 terminal-escape primitives) from every reachable
    # string in the stations payload BEFORE ``json.dumps``. Two attack
    # vectors are closed here: (a) a previously-poisoned
    # ``data/stations.json`` carrying historic BiDi marks (planted via
    # a bypass of the canonical writer, surviving from a corrupted
    # previous cron run, or written by an early-deployment build
    # pre-dating the Round 12-14 closing rounds) would otherwise be
    # re-emitted verbatim via this re-write; (b) the ``_op_restore``
    # handler inserts the override's ``entry`` template verbatim via
    # ``dict(entry_template)``, so a hostile PR landing a tampered
    # ``data/stations_overrides.json`` carrying U+202E in an
    # ``entry`` ``name`` field would otherwise plant the byte directly
    # into the committed ``data/stations.json``. ``ensure_ascii=False``
    # is preserved at the writer below so legitimate German station
    # names (umlauts ä/ö/ü/Ä/Ö/Ü + sharp s ß) stay compact in the
    # commit diff. Mirrors the canonical writer-side pin established
    # in Round 13 at ``src/places/merge.py:write_stations`` and extended
    # in Round 14 to the eight named script-level writers
    # (``tests/test_sentinel_script_station_writers_trojan_source.py``);
    # the closing-rule walker is
    # ``tests/test_sentinel_trojan_source_audit_walker.py``.
    scrubbed_payload = scrub_trojan_source_primitives(stations_payload)
    serialisable = (
        scrubbed_payload
        if isinstance(scrubbed_payload, dict | list)
        else stations_payload
    )
    # Security (writer-side non-finite literal defence, symmetric to the
    # ``loads_finite`` reader pin above). ``allow_nan=False`` surfaces any
    # ``float('nan')`` / ``float('inf')`` that bypassed the reader-side
    # defence (e.g. via a regression to ``json.loads(text)``) as
    # ``ValueError`` BEFORE the corrupted bytes ship to
    # ``data/stations.json`` (committed to ``main`` and consumed by every
    # downstream RFC-8259-strict parser: ``JSON.parse`` in browsers,
    # ``serde_json`` Rust strict mode, Go's ``encoding/json``). Mirrors
    # the canonical writer-side pins in ``src/places/merge.py:write_stations``
    # and ``scripts/update_all_stations.py:_write_stations`` (2026-05-14
    # PR #1485 / #1487 / #1488 / #1491).
    text = json.dumps(serialisable, indent=2, ensure_ascii=False, allow_nan=False)
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
