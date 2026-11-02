"""The two ``/trip`` query directions of the legacy Stammstrecke monitor.

Moved verbatim from ``scripts/update_stammstrecke_status.py``.
``DIRECTIONS`` is still built once at import time.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.stammstrecke.core import (
    FLORIDSDORF_VOR_ID,
    MEIDLING_CANONICAL_SEED,
    MEIDLING_VOR_ID,
)
from src.utils.stations import canonical_name, display_name

def _short_target_label(seed_name: str) -> str:
    """Return the compact user-facing label for *seed_name*.

    Looks up the canonical station name in the project's station
    directory (``data/stations.json`` via :mod:`src.utils.stations`),
    applies ``display_name`` for project-wide overrides (e.g.
    ``Wien Mitte-Landstraße`` → ``Wien Mitte``), and strips the
    leading ``Wien `` prefix. The Stammstrecke description text
    ("in Richtung Meidling") implicitly assumes Vienna, so omitting
    the prefix produces natural German — but the canonical lookup
    still drives the suffix portion, so a future rename in the
    directory propagates automatically.

    The fallback chain — try directory, accept any
    :class:`Exception`, finally strip ``Wien `` from the seed —
    keeps the script resilient against a missing/corrupt directory
    file (fresh clone before stations sync, restricted CI runner
    without ``data/`` mounted, etc.).
    """

    try:
        canonical = canonical_name(seed_name)
    except Exception:  # pragma: no cover - defensive: directory load failure
        canonical = None

    name = display_name(canonical) if canonical else seed_name.strip()
    if name.startswith("Wien "):
        return name[len("Wien ") :]
    return name


@dataclass(frozen=True)
class _Direction:
    """A single Stammstrecke query direction.

    Carries the per-direction parameters (origin/destination VOR IDs,
    user-facing target label for the description, identity prefix for
    the deduplication key and GUID) so the main loop can iterate over
    both directions without branching on direction-specific logic.

    ``target_label`` is populated at module import time via
    :func:`_short_target_label`, which routes through the project's
    canonical station directory rather than hardcoding the display
    name.
    """

    origin_id: str
    destination_id: str
    target_label: str
    identity_prefix: str


# Direction labels: aligned with the Hbf script and the feed renderer.
#
# The northbound label was renamed from "Floridsdorf" → "Praterstern"
# in 2026-05-15: the new name is the next Stammstrecke stop after Hbf
# heading north, which is symmetric with the southbound "Meidling"
# label and accommodates short-turn trains that terminate at
# Praterstern (or even Wien Mitte) without continuing all the way
# to Floridsdorf. The label is hardcoded here rather than derived
# from a station-directory seed because the canonical seed
# "Wien Floridsdorf" no longer reflects the bucket's semantics —
# the bucket holds every Stammstrecke-northbound train regardless
# of its actual terminus.
#
# This script is no longer wired into the cron path (the Hbf
# ``/departureBoard`` script replaced it 2026-05-15 in PR #1496),
# but the constants are kept aligned so that a manual invocation
# produces ledger entries the Hbf reader can still consume.
DIRECTIONS: tuple[_Direction, ...] = (
    _Direction(
        origin_id=FLORIDSDORF_VOR_ID,
        destination_id=MEIDLING_VOR_ID,
        target_label=_short_target_label(MEIDLING_CANONICAL_SEED),
        identity_prefix="stammstrecke_delay_meidling",
    ),
    _Direction(
        origin_id=MEIDLING_VOR_ID,
        destination_id=FLORIDSDORF_VOR_ID,
        target_label="Praterstern",
        identity_prefix="stammstrecke_delay_praterstern",
    ),
)
