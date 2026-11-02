"""Auto-quarantine and exact-duplicate dedup for the station refresh.

Moved verbatim from ``scripts/update_all_stations.py``.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any
from collections.abc import Mapping, Sequence

from src.utils.files import atomic_write
from src.utils.stations_validation import (
    ValidationReport,
    _format_identifier,
)

# Sentinel identifier the validator emits for directory-wide provider
# issues (e.g. "Need at least two VOR entries") that cannot be tied to
# a single station. The auto-quarantine logic skips this marker — no
# entry's ``_format_identifier`` ever matches it, and removing
# arbitrary stations would not repair the global condition.
_GLOBAL_ISSUE_SENTINEL = "<global>"


def _collect_blocking_issues(report: ValidationReport) -> list[tuple[str, str]]:
    """Return (category, message) tuples for issues that trigger auto-quarantine."""
    blocking: list[tuple[str, str]] = []
    for provider in report.provider_issues:
        blocking.append(("provider", provider.reason))
    for cross in report.cross_station_id_issues:
        blocking.append((
            "cross-station",
            f"alias '{cross.alias}' in '{cross.name}' ({cross.identifier}) "
            f"collides with {cross.colliding_field} of "
            f"'{cross.colliding_name}' ({cross.colliding_identifier})",
        ))
    for naming in report.naming_issues:
        blocking.append((
            "naming",
            f"{naming.name} ({naming.identifier}): {naming.reason}",
        ))
    for security in report.security_issues:
        blocking.append((
            "security",
            f"{security.name} ({security.identifier}): {security.reason}",
        ))
    return blocking


def _collect_quarantine_identifiers(report: ValidationReport) -> set[str]:
    """Return the identifiers of stations that should be auto-quarantined.

    The string format mirrors :func:`stations_validation._format_identifier`
    so a downstream caller can match each entry deterministically. The
    ``<global>`` sentinel emitted by ``_find_provider_issues`` for
    directory-wide conditions is filtered out — no individual station
    matches it and removing one would not repair the underlying issue.
    """
    identifiers: set[str] = set()
    for provider in report.provider_issues:
        identifier = provider.identifier
        if identifier and identifier != _GLOBAL_ISSUE_SENTINEL:
            identifiers.add(identifier)
    for cross in report.cross_station_id_issues:
        if cross.identifier:
            identifiers.add(cross.identifier)
    for naming in report.naming_issues:
        if naming.identifier:
            identifiers.add(naming.identifier)
    for security in report.security_issues:
        if security.identifier:
            identifiers.add(security.identifier)
    return identifiers


def _collect_quarantine_reasons(
    report: ValidationReport,
) -> dict[str, list[dict[str, str]]]:
    """Map quarantineable identifier → list of ``{category, reason}`` dicts.

    The returned mapping powers the per-station ``issues`` array in
    ``data/quarantine.json`` so operators can review *why* each entry
    was removed without correlating against the run log.
    """
    reasons: dict[str, list[dict[str, str]]] = {}
    for provider in report.provider_issues:
        identifier = provider.identifier
        if not identifier or identifier == _GLOBAL_ISSUE_SENTINEL:
            continue
        reasons.setdefault(identifier, []).append({
            "category": "provider",
            "reason": provider.reason,
        })
    for cross in report.cross_station_id_issues:
        if not cross.identifier:
            continue
        reasons.setdefault(cross.identifier, []).append({
            "category": "cross-station",
            "reason": (
                f"alias '{cross.alias}' collides with {cross.colliding_field} "
                f"of '{cross.colliding_name}' ({cross.colliding_identifier})"
            ),
        })
    for naming in report.naming_issues:
        if not naming.identifier:
            continue
        reasons.setdefault(naming.identifier, []).append({
            "category": "naming",
            "reason": naming.reason,
        })
    for security in report.security_issues:
        if not security.identifier:
            continue
        reasons.setdefault(security.identifier, []).append({
            "category": "security",
            "reason": security.reason,
        })
    return reasons


def _partition_stations(
    stations: Sequence[Mapping[str, Any]],
    quarantine_identifiers: set[str],
) -> tuple[list[Mapping[str, Any]], list[Mapping[str, Any]]]:
    """Split *stations* into ``(valid, quarantined)`` by identifier match.

    Each station's identifier is recomputed with
    :func:`stations_validation._format_identifier` so the match honours
    the exact format the validator emitted into the report.
    """
    valid: list[Mapping[str, Any]] = []
    quarantined: list[Mapping[str, Any]] = []
    for entry in stations:
        if _format_identifier(entry) in quarantine_identifiers:
            quarantined.append(entry)
        else:
            valid.append(entry)
    return valid, quarantined


def _dedupe_exact_duplicates(
    stations: Sequence[Mapping[str, Any]],
) -> tuple[list[Mapping[str, Any]], int]:
    """Drop byte-identical duplicate entries, preserving first-seen order.

    A station directory must never carry two identical records. When it
    does — e.g. the same ``oebb_geonetz`` entry preserved twice through the
    ``update_station_directory`` existing-file → ``manual_stations``
    round-trip, whose ``final_stations.extend(manual_stations)`` assembly
    has no dedup pass — the intentional "a station's own ``bst_code`` is
    also one of its aliases" rule (``enrich_station_aliases``) turns the
    pair into a *cross-station* ID collision: copy A's alias shadows copy
    B's ``bst_code`` and vice versa. ``_find_cross_station_id_conflicts``
    self-excludes by object identity, so a single copy is fine, but two
    copies both fire and the auto-quarantine then removes *every* entry
    matching that identifier — silently dropping a valid station from the
    directory (observed for Wien Siebenhirten / Wien Handelskai). Collapsing
    exact duplicates to one copy restores the self-exclusion invariant.

    Only byte-identical entries are merged. Two records that share an
    identity field but differ elsewhere are a genuine conflict and are left
    untouched for ``_find_identity_field_conflicts`` to surface.
    """
    seen: set[str] = set()
    deduped: list[Mapping[str, Any]] = []
    removed = 0
    for entry in stations:
        try:
            fingerprint = json.dumps(
                dict(entry), sort_keys=True, ensure_ascii=True, allow_nan=False, default=str
            )
        except ValueError:
            # A non-finite value (NaN/Inf) in a coordinate field makes the
            # ``allow_nan=False`` fingerprint raise. Don't abort the whole
            # orchestrator (which would leave heartbeat/diff unwritten): treat
            # the entry as unique so exact-dedup degrades gracefully.
            logging.warning("Skipping exact-dedup fingerprint for entry with non-finite value")
            deduped.append(entry)
            continue
        if fingerprint in seen:
            removed += 1
            continue
        seen.add(fingerprint)
        deduped.append(entry)
    return deduped, removed


def _write_quarantine_file(
    path: Path,
    quarantined: Sequence[Mapping[str, Any]],
    reasons_by_identifier: Mapping[str, Sequence[Mapping[str, str]]],
    timestamp: str,
) -> None:
    """Atomically write the auto-quarantine sidecar at *path*.

    The file is operator-facing diagnostic state — keep the schema
    forwards-compatible by emitting a wrapped object with an explicit
    ``count``, the original ``entry`` payload, and the validator's
    ``issues`` so a future drift in either side is auditable.

    Security (Trojan-Source / BiDi-Mark Drift Round 9): the canonical
    quarantine path is the destination for entries that the validator
    already flagged as carrying ``_UNSAFE_CHARS_RE`` bytes — every
    CVE-2021-42574 BiDi formatting control (U+202A-U+202E /
    U+2066-U+2069), every zero-width primitive (U+200B-U+200F), every
    Unicode line / paragraph separator (U+2028-U+2029), the BOM
    (U+FEFF), and the 8-bit C1 terminal-escape primitives (\\x9b CSI /
    \\x9d OSC / \\x90 DCS) is *by definition* present in the entries
    written here. ``ensure_ascii=True`` forces ``json.dump`` to emit
    every non-ASCII code point as a literal ``\\uXXXX`` escape, so the
    raw UTF-8 bytes of the BiDi controls (e.g. ``\\xe2\\x80\\xae`` for
    U+202E) never reach the on-disk file. Operators viewing
    ``data/quarantine.json`` via ``cat`` / ``less`` / editor preview /
    the GitHub web UI see the escaped sentinel ``\\u202e`` rather than
    the byte sequence that triggers terminal / Markdown rendering
    attacks. Forensic intent is preserved: ``json.loads`` decodes the
    escapes back to the original bytes, so the file remains a complete
    record of what tried to slip through.
    """
    entries: list[dict[str, Any]] = []
    for entry in quarantined:
        identifier = _format_identifier(entry)
        name_obj = entry.get("name")
        name = name_obj.strip() if isinstance(name_obj, str) else ""
        entries.append({
            "identifier": identifier,
            "name": name or "<unknown>",
            "issues": [dict(reason) for reason in reasons_by_identifier.get(identifier, ())],
            "entry": dict(entry),
        })
    payload: dict[str, Any] = {
        "timestamp": timestamp,
        "count": len(entries),
        "stations": entries,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    # Security (Coordinate finite/range drift, committed-writer
    # defence-in-depth): ``allow_nan=False`` mirrors the canonical
    # writer-side pin established in Round 1485 at
    # :func:`src.places.merge.write_stations` and extended in Round
    # 1487 to the sibling stations / cache-events writers. The
    # quarantined entries carry ``"entry": dict(entry)`` — a verbatim
    # copy of the operator-facing station entry that was flagged
    # *because* it carries unsafe content. A poisoned ``latitude`` /
    # ``longitude`` (the same coordinate threat model that motivated
    # Round 1485 / 1487) flows through this writer without the pin
    # and lands non-standard ``NaN`` / ``Infinity`` literals
    # (invalid per RFC 8259) in the committed
    # ``data/quarantine.json`` sidecar.
    with atomic_write(path, mode="w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=True, indent=2, allow_nan=False)
        handle.write("\n")
