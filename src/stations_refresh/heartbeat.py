"""Heartbeat sidecar writer for the station refresh.

Moved verbatim from ``scripts/update_all_stations.py``.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from collections.abc import Mapping

from src.utils.files import atomic_write


def _write_heartbeat_file(path: Path, heartbeat: Mapping[str, Any]) -> None:
    """Atomically write the orchestrator heartbeat to *path*.

    Security (Trojan-Source / BiDi-Mark Drift Round 10): the file is
    operator-facing diagnostic state, committed to ``main`` by the
    ``update-cycle.yml`` cron pipeline (``data/stations_last_run.json``)
    and reviewed via ``cat`` / ``less`` / the GitHub web UI / IDE
    preview. ``ensure_ascii=True`` escapes every non-ASCII code point
    as a literal ``\\uXXXX`` sequence, so a future heartbeat field
    carrying station- / provider- / environment-controlled content
    cannot leak the canonical CVE-2021-42574 Trojan-Source / zero-width
    / Unicode-line-terminator / 8-bit C1 union as raw UTF-8 bytes.
    Mirrors the canonical fix shape pinned in PR #1434 for
    ``_write_quarantine_file`` so the closing checklist's invariant is
    uniform across the committed ``data/*.json`` sidecar writer family.
    Forensic intent is preserved (``json.loads`` recovers the original
    string from the literal escape sequence).

    Security (Coordinate finite/range drift, committed-writer
    defence-in-depth): ``allow_nan=False`` mirrors the canonical
    writer-side pin established in Round 1485 at
    :func:`src.places.merge.write_stations` and extended in Round
    1487 to the sibling stations / cache-events writers. The
    heartbeat payload is a ``dict[str, Any]`` so any future field
    (e.g. a fractional success-rate metric, average tick duration)
    inherits the missing pin and could land non-standard
    ``NaN`` / ``Infinity`` literals (invalid per RFC 8259) in
    the committed ``data/stations_last_run.json`` sidecar.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with atomic_write(path, mode="w", encoding="utf-8") as handle:
        json.dump(heartbeat, handle, ensure_ascii=True, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")
