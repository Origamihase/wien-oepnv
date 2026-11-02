"""Hilfen und Fixtures für die Tests in diesem Paket.

Früher in tests/test_utils_stats.py.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path
from zoneinfo import ZoneInfo


REPO_ROOT = Path(__file__).resolve().parents[2]

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

VIENNA_TZ = ZoneInfo("Europe/Vienna")


def _read_csv(path: Path) -> tuple[list[str], list[list[str]]]:
    """Return ``(header, data_rows)`` from a CSV path."""
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    if not rows:
        return [], []
    return rows[0], rows[1:]


# ---- CSV formula-injection defence (CWE-1236) -----------------------------
#
# OWASP CWE-1236 ("Improper Neutralization of Formula Elements in a CSV
# File"): a CSV cell that begins with ``=``, ``+``, ``-``, ``@``, TAB
# (``\t``), or CR (``\r``) is interpreted as a *formula* by Excel,
# LibreOffice Calc, and Google Sheets when the file is opened. The
# append-only stats writers in :mod:`src.utils.stats` accept three
# operator-/upstream-influenced text fields verbatim:
#
# * ``provider`` — comes from the feed item's ``source`` field. Today
#   the providers hardcode "ÖBB" / "Wiener Linien" / "VOR/VAO", but
#   ``wl_fetch.py`` re-emits ``ev["source"]`` and ``b["source"]`` from
#   on-disk cache entries (see :mod:`src.providers.wl_fetch` lines 736
#   and 858), so a poisoned ``cache/wl/*.json`` can land any string
#   into ``provider``.
# * ``location_name`` — extracted from upstream titles/descriptions via
#   :func:`extract_location_name`. Currently constrained by anchored
#   ``[A-ZÄÖÜ]`` regexes, but the writer is a public helper that
#   accepts any string.
# * ``direction`` — comes from the canonical station directory
#   (``data/stations.json``) via ``display_name`` in
#   :mod:`scripts.update_stammstrecke_status`; a poisoned directory
#   lands anything into the ``direction`` field.
#
# These tests exercise the writer directly with formula payloads to
# pin the boundary defence: the writer must never let a formula prefix
# survive into the CSV file, regardless of whether *any* current
# caller exercises the path. Defence-in-depth at the boundary closes
# the cartesian product of upstream / cache / directory poisoning
# vectors with a single sanitiser.


_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


# ---------------------------------------------------------------------------
# Regression: the WL ``| Haltestelle: …`` marker must survive a long
# description.
#
# ``src/providers/wl_fetch.py`` appends the marker to the *end* of the built
# description. Audit 2026-09-13 measured 69% of live WL descriptions above
# 1024 chars (max 4972), so the single 1024-char search window silently
# dropped the most authoritative signal the WL API provides (the curated
# ``relatedStops`` list) and filed those incidents under ``unbekannt`` in the
# dashboard's "Häufigste Störungsorte" table.
# ---------------------------------------------------------------------------

_LONG_WL_DESCRIPTION = (
    "<p>Wegen Gleisbauarbeiten kommt es zu Einschr&auml;nkungen. "
    "Ersatzverkehr eingerichtet. </p> " * 22
)


def _wl_item(description: str) -> dict[str, object]:
    return {
        "title": "43A: Gleisbauarbeiten",
        "description": description,
        "source": "Wiener Linien",
    }


__all__ = [
    "REPO_ROOT",
    "VIENNA_TZ",
    "_FORMULA_PREFIXES",
    "_LONG_WL_DESCRIPTION",
    "_read_csv",
    "_wl_item",
]
