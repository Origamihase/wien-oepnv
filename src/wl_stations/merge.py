"""VOR mapping loader and the stations.json merge of ``scripts/update_wl_stations.py``.

Moved verbatim from the script; ``MAX_JSON_FILE_BYTES`` lives here with
both of its users, ``load_vor_mapping`` and ``merge_into_stations``.
A test that patches a constant or function used here must patch it
on this module, not on the script.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from collections.abc import Callable, Iterable, Mapping

from src.wl_stations.base import (
    _path_fingerprint,
    atomic_write,
    log,
    read_capped_json,
    scrub_trojan_source_primitives,
)
from src.wl_stations.ogd_csv import _normalize_key


# Security cap against wide-but-flat JSON size-bomb attacks. Mirrors the
# canonical ``MAX_*_FILE_BYTES`` contract from ``src/utils/cache.py`` /
# ``src/utils/stations.py``: depth-bomb catch alone misses ``MemoryError``
# (a ``BaseException`` subclass) so a planted-huge stations.json
# (~1 GiB of ``[0,0,…]``) buffered via ``json.load`` propagates past
# the loader and crashes the WL merge — running under
# ``subprocess.run(check=True)`` aborts the whole cron pipeline. 50 MiB
# is ~285x the production stations.json so legitimate state is never
# rejected.
MAX_JSON_FILE_BYTES = 50 * 1024 * 1024


def load_vor_mapping(path: Path) -> dict[str, Mapping[str, object]]:
    if not path.exists():
        log.info(
            "No VOR mapping found at [path-sha256=%s]",
            _path_fingerprint(path),
        )
        return {}
    # Security: ``read_capped_json`` enforces both the depth-bomb catch
    # tuple and the byte-size cap (see MAX_JSON_FILE_BYTES). Same
    # cron-pipeline blast radius as the sibling loader in
    # ``scripts/enrich_station_aliases.py``.
    raw = read_capped_json(
        path, MAX_JSON_FILE_BYTES, label="VOR mapping", logger=log,
    )
    if raw is None:
        log.warning(
            "Could not parse VOR mapping [path-sha256=%s] (missing/invalid/oversized)",
            _path_fingerprint(path),
        )
        return {}
    mapping: dict[str, Mapping[str, object]] = {}
    if not isinstance(raw, list):
        return mapping
    for entry in raw:
        if not isinstance(entry, Mapping):
            continue
        candidates = set()
        for key in ("station_name", "resolved_name"):
            text = str(entry.get(key) or "").strip()
            if text:
                candidates.add(_normalize_key(text))
        vor_id = str(entry.get("vor_id") or "").strip()
        if vor_id:
            candidates.add(_normalize_key(vor_id))
        for candidate in candidates:
            if candidate:
                mapping[candidate] = entry
    return mapping


def _normalize_sources(value: object | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        candidates = value.split(",")
    elif isinstance(value, Iterable):  # pragma: no cover - defensive guard
        candidates = list(value)
    else:
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        text = str(item).strip()
        if not text:
            continue
        if text in seen:
            continue
        seen.add(text)
        normalized.append(text)
    return normalized


def _merge_sources(*values: object | None) -> str:
    """Merge multiple source-token lists into a single comma-separated string.

    Output is alphabetically sorted so that two callers that emit the same
    set of providers in different orders produce identical strings —
    e.g. "google_places,oebb" instead of "oebb,google_places". The sort
    matches the convention already used by ``src/places/merge.py:182``.
    """
    merged: set[str] = set()
    for value in values:
        for item in _normalize_sources(value):
            merged.add(item)
    return ",".join(sorted(merged))


def _ensure_sorted_aliases(entry: dict[str, object]) -> None:
    aliases = entry.get("aliases")
    if not isinstance(aliases, list):
        return
    unique: set[str] = set()
    cleaned: list[str] = []
    for alias in aliases:
        if not isinstance(alias, str):
            continue
        text = alias.strip()
        if not text or text in unique:
            continue
        unique.add(text)
        cleaned.append(text)
    cleaned.sort()
    entry["aliases"] = cleaned


_WL_DIVA_ALIAS_RE = re.compile(r"60\d{6}")


def _is_stale_wl_diva_alias(alias: object, current_wl_diva: str) -> bool:
    """Return True for aliases that look like a Wiener Linien DIVA value
    but do not match the current ``wl_diva`` of the entry.

    Wiener Linien renumbered DIVAs at least once between the
    ``data.wien.gv.at`` proxy era and the current ``wienerlinien.at``
    OGD-Echtzeit schema (e.g. ``60201076`` was Karlsplatz pre-PR #1442
    and is Ratzenhofergasse today). Because ``merge_into_stations``
    preserves existing aliases across runs, stale DIVA copies pinned by
    an earlier cron tick survive and trivially collide with the current
    ``wl_diva`` of a *different* entry, tripping
    ``_find_cross_station_id_conflicts`` and the auto-quarantine path.

    Heuristic: any alias matching the WL DIVA shape (``60`` + 6 digits,
    yielding 8 digits total) that is *not* the entry's current
    ``wl_diva`` value is treated as stale and dropped at merge time.
    Real per-stop RBL identifiers remain available via the structured
    ``wl_stops[].stop_id`` field.
    """
    if not isinstance(alias, str):
        return False
    stripped = alias.strip()
    if not _WL_DIVA_ALIAS_RE.fullmatch(stripped):
        return False
    return stripped != current_wl_diva


def _merge_wl_payload(target: dict[str, object], payload: Mapping[str, object]) -> None:
    if payload.get("wl_diva"):
        target["wl_diva"] = payload["wl_diva"]

    wl_stops = payload.get("wl_stops")
    if isinstance(wl_stops, list):
        target["wl_stops"] = wl_stops

    # Like the WL-only entries, which are rebuilt from scratch every run,
    # a merged entry carries exactly this run's lines — none if WL's line
    # data was unavailable.
    wl_lines = payload.get("wl_lines")
    if isinstance(wl_lines, list) and wl_lines:
        target["wl_lines"] = wl_lines
    else:
        target.pop("wl_lines", None)

    target["source"] = _merge_sources(target.get("source"), payload.get("source"), "wl")

    from typing import cast
    current_wl_diva = str(target.get("wl_diva") or "").strip()
    existing_aliases: list[str] = []
    raw_target_aliases = target.get("aliases")
    if isinstance(raw_target_aliases, list):
        # Strip stale WL-DIVA aliases left over from prior runs against
        # an older Wiener Linien numbering scheme — see
        # ``_is_stale_wl_diva_alias`` for the renumbering rationale.
        existing_aliases = [
            cast(str, a)
            for a in raw_target_aliases
            if not _is_stale_wl_diva_alias(a, current_wl_diva)
        ]

    incoming_aliases: list[str] = []
    raw_payload_aliases = payload.get("aliases")
    if isinstance(raw_payload_aliases, list):
        incoming_aliases = cast(list[str], list(raw_payload_aliases))
    target["aliases"] = existing_aliases + incoming_aliases
    _ensure_sorted_aliases(target)

    if target.get("latitude") in (None, "") and payload.get("latitude") is not None:
        target["latitude"] = payload["latitude"]
    if target.get("longitude") in (None, "") and payload.get("longitude") is not None:
        target["longitude"] = payload["longitude"]


def _lookup_candidates(index: Mapping[str, dict[str, object]], key: object | None) -> dict[str, object] | None:
    if key is None:
        return None
    text = str(key).strip()
    if not text:
        return None
    return index.get(text)


def merge_into_stations(
    stations_path: Path,
    wl_entries: list[dict[str, Any]],
    *,
    reconcile: Callable[[list[dict[str, object]]], None] | None = None,
) -> None:
    # Security: ``read_capped_json`` enforces both the depth-bomb catch
    # tuple and the byte-size cap (see MAX_JSON_FILE_BYTES). The
    # depth-bomb-only catch missed ``MemoryError`` (a ``BaseException``
    # subclass) — a planted-huge stations.json would propagate past
    # the loader and crash the WL merge. On miss we start fresh and
    # let the merge restore the canonical schema for the next run.
    raw_data: object
    if stations_path.exists():
        loaded = read_capped_json(
            stations_path, MAX_JSON_FILE_BYTES, label="Stations", logger=log,
        )
        if loaded is None:
            log.warning(
                "stations.json could not be parsed (missing/invalid/oversized)"
                " – starting WL merge from empty state",
            )
            raw_data = []
        else:
            raw_data = loaded
    else:
        raw_data = []

    existing: list[dict[str, object]] = []

    if isinstance(raw_data, list):
        existing = raw_data
    elif isinstance(raw_data, dict) and isinstance(raw_data.get("stations"), list):
        existing = raw_data["stations"]
    else:
        raise ValueError("stations.json must contain a JSON array or a dict with a 'stations' array")

    # Drop non-dict elements before the merge loop below calls ``entry.get(...)``.
    # read_capped_json validates only the TOP-LEVEL type, so a structurally
    # valid but malformed file (``[1, 2, "x"]`` / ``{"stations": ["foo"]}`` — a
    # corrupted previous run or a parallel atomic state swap, the documented
    # threat model) would otherwise raise an uncaught AttributeError and abort
    # the cron pipeline (the orchestrator runs this via ``subprocess(check=True)``).
    # Mirrors update_all_stations._load_stations and the sibling NaN/size/depth
    # defences in this file, which degrade gracefully instead of crashing.
    existing = [entry for entry in existing if isinstance(entry, dict)]

    filtered: list[dict[str, object]] = []
    vor_index: dict[str, dict[str, object]] = {}
    bst_index: dict[str, dict[str, object]] = {}
    name_index: dict[str, dict[str, object]] = {}
    # Added 2026-05-16 (PR #1539): index by ``wl_diva`` so a WL payload
    # whose name doesn't normalise to the existing ``google_places`` /
    # ``oebb`` entry's name (the Innenstadt-U-Bahn pattern — payload
    # ``Wien Herrengasse (WL)`` vs existing ``Herrengasse``) is still
    # merged into the existing record instead of duplicated. Pre-fix,
    # ten Innenstadt-U-Bahn DIVAs (Herrengasse, Schwedenplatz,
    # Volkstheater, …) were silently duplicated each cron tick because
    # ``_normalize_key`` rendered the two names as distinct keys.
    wl_diva_index: dict[str, dict[str, object]] = {}

    for entry in existing:
        source = entry.get("source")
        if isinstance(source, str) and source.strip() == "wl":
            continue
        filtered.append(entry)

        vor_id = entry.get("vor_id")
        if vor_id is not None:
            key = str(vor_id).strip()
            if key and key not in vor_index:
                vor_index[key] = entry

        bst_id = entry.get("bst_id")
        if bst_id is not None:
            key = str(bst_id).strip()
            if key and key not in bst_index:
                bst_index[key] = entry

        name = entry.get("name")
        if isinstance(name, str):
            key = _normalize_key(name)
            if key and key not in name_index:
                name_index[key] = entry

        wl_diva = entry.get("wl_diva")
        if wl_diva is not None:
            key = str(wl_diva).strip()
            if key and key not in wl_diva_index:
                wl_diva_index[key] = entry

    log.info("Keeping %d existing non-WL stations", len(filtered))

    # Data-loss floor: with no WL entries to re-add, writing ``filtered``
    # back would permanently drop every existing ``source == "wl"`` station.
    # An empty set only happens when the OGD CSV load failed (oversized →
    # ``read_capped_text`` returned ``None``, download/format error), so
    # refuse and keep the committed file. A genuinely empty or unreadable
    # existing file has no WL rows to lose (``len(filtered) == len(existing)``),
    # so the depth-bomb / fresh-start path still writes through.
    if not wl_entries and len(filtered) < len(existing):
        log.error(
            "No WL entries to merge but %d existing WL station(s) would be "
            "deleted — refusing to overwrite stations [path-sha256=%s].",
            len(existing) - len(filtered),
            _path_fingerprint(stations_path),
        )
        return

    unmatched: list[dict[str, object]] = []
    for payload in wl_entries:
        merged_into: dict[str, object] | None = None

        # Match precedence: ``wl_diva`` first — strongest WL-domain
        # identifier and the failure mode the new index was added for.
        # Then ``vor_id`` / ``bst_id`` / ``name`` as before so cross-
        # provider stubs (VOR-only / ÖBB-only) continue to consolidate
        # the same way.
        wl_diva = payload.get("wl_diva")
        merged_into = _lookup_candidates(wl_diva_index, wl_diva)

        if merged_into is None:
            vor_id = payload.get("vor_id")
            merged_into = _lookup_candidates(vor_index, vor_id)

        if merged_into is None:
            bst_id = payload.get("bst_id")
            merged_into = _lookup_candidates(bst_index, bst_id)

        if merged_into is None:
            name = payload.get("name")
            if isinstance(name, str):
                merged_into = _lookup_candidates(name_index, name)

        if merged_into is not None:
            _merge_wl_payload(merged_into, payload)
            continue

        entry = dict(payload)
        entry["source"] = _merge_sources(payload.get("source"), "wl") or "wl"
        _ensure_sorted_aliases(entry)
        unmatched.append(entry)

    filtered.extend(unmatched)

    # Coordinate consensus (WL → HAFAS): runs on the fully
    # merged list so overlap entries already carry both ``wl_diva`` and the
    # ÖBB-side HAFAS identity. Optional so direct callers / unit tests keep
    # the historical no-reconcile behaviour; ``main()`` injects the real,
    # network-backed pass behind the ``WIEN_OEPNV_AT_RECONCILE`` gate.
    if reconcile is not None:
        reconcile(filtered)

    # Security (Trojan-Source / BiDi-Mark Drift Round 14, ingestion-boundary
    # defence): strip the canonical CVE-2021-42574 attack-byte union from
    # the merged stations BEFORE ``json.dump``. A planted Wien OGD CSV
    # (or hijacked ``data.wien.gv.at`` response) could plant U+202E in a
    # WL station ``name`` / ``aliases[]`` field — the file is committed
    # to ``main`` by the weekly cron via the orchestrator. Mirrors
    # ``src/places/merge.py:write_stations`` (Round 13). ``ensure_ascii=
    # False`` is preserved so legitimate German station names stay
    # compact in the commit diff.
    #
    # Security (Coordinate finite/range drift, companion-writer
    # defence-in-depth): ``allow_nan=False`` mirrors the canonical
    # writer-side pin established in Round 1485 at
    # ``src/places/merge.py:write_stations``. The local
    # ``_coerce_float`` parser (line 267 pre-fix) accepts the
    # literal strings ``"nan"`` / ``"inf"`` / ``"infinity"`` via
    # ``float(text)`` from a compromised Wien OGD CSV cell — the
    # per-stop ``latitude`` / ``longitude`` fields flow into
    # ``data/stations.json`` verbatim and Python's default
    # ``json.dump`` emits non-standard JSON literals (invalid per
    # RFC 8259). The pin surfaces such a bypass as a loud
    # ``ValueError`` rather than silently corrupting the committed
    # artefact.
    scrubbed = scrub_trojan_source_primitives(filtered)
    serialisable = scrubbed if isinstance(scrubbed, list) else filtered
    with atomic_write(stations_path, mode="w", encoding="utf-8", permissions=0o644) as handle:
        json.dump(
            {"stations": serialisable},
            handle,
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )
        handle.write("\n")
    log.info("Wrote %d total stations", len(filtered))
