#!/usr/bin/env python3
"""Hard-gate API calls on the persisted daily / monthly quota state.

Defense-in-depth wrapper around the existing in-script quota guards
(:func:`src.providers.vor.load_request_count` for VAO,
:class:`src.places.quota.MonthlyQuota` for Google Places). The
in-script guards are only consulted *during* the script run; this
preflight exits early — *before* any HTTP request fires — when the
persisted counter shows that a fresh run would breach the contractual
daily / monthly cap.

Why a separate preflight when the in-script guard already exists?
Two operational reasons:

1. **Explicit fail-fast in CI logs.** The in-script ``_charge_one_request``
   raises ``_QuotaExceeded`` after ~12 lines of session setup, which
   buries the budget-exhausted signal under unrelated DEBUG noise. The
   preflight prints a single, scrape-friendly line at the top of the
   workflow log so operators can see "quota was the reason" without
   reading the full trace.
2. **Safe-by-default workflow gating.** Subsequent API-using steps
   gate on ``if: steps.preflight.outputs.quota_ok == 'true'`` so the
   workflow continues (and successfully commits whatever non-API work
   already finished) even when the budget is exhausted. Without the
   preflight the only options are "fail the whole workflow" or "let
   the in-script guard silently swallow the request" — neither of
   which surfaces the quota state cleanly.

Exit codes:
    0    quota OK; planned reservation fits inside the daily / monthly cap.
    1    quota would be exceeded; caller MUST skip the API call.
    2    misuse (bad CLI args, unreadable state file, missing limit env).

The script intentionally has zero non-stdlib runtime dependencies so
it works in the early Actions step (``mkdir -p`` cache dir / setup
Python with cache) before ``install-deps`` has run. The two project
imports come from packages with no transitive third-party deps for
the preflight code paths exercised here.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Final, cast

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Project import: ``read_capped_json`` is the canonical on-disk JSON
# loader that combines the byte-size cap (open-then-fstat the open
# fd, defeating the stat/open TOCTOU) with the depth-bomb catch
# tuple. ``src.utils.files`` itself imports only stdlib modules
# (json, logging, os, pathlib, typing, contextlib, hashlib, secrets,
# zipfile, re), so this preserves the script's invariant of zero
# non-stdlib runtime dependencies — the helper is reachable BEFORE
# ``install-deps`` has run, exactly like the rest of this script.
from src.utils.files import read_capped_json  # noqa: E402

LOGGER = logging.getLogger("preflight_quota_check")

# Hard-coded contractual ceilings. Mirrors the *defaults* in the
# respective modules; the env-overridable ``MAX_*`` constants in
# ``src.providers.vor`` clamp these from above (``min(env, default)``)
# so this preflight cannot be loosened by an env override that an
# operator forgot to revert.
VOR_DAILY_LIMIT: Final[int] = 100
VOR_QUOTA_FILE: Final[Path] = REPO_ROOT / "data" / "vor_request_count.json"

# Google Places monthly + daily caps. Mirror the env defaults used by
# the OSM-first / Google-Places-fallback step in ``update-stations.yml``.
PLACES_QUOTA_FILE: Final[Path] = REPO_ROOT / "data" / "places_quota.json"
PLACES_DEFAULT_MONTHLY: Final[int] = 4000

# Security: defense-in-depth byte-size cap on the persisted quota
# state files. The state shape is tiny in production
# (``{"date": "YYYY-MM-DD", "requests": N}`` is a few dozen bytes;
# the Places state ``{"month": "YYYY-MM", "counts": {...}, "total":
# N, "daily_key": "...", "daily_total": N}`` is well under 1 KiB).
# 1 MiB is ~5000x the largest legitimate state shape and matches
# ``src/places/quota.py:MAX_QUOTA_FILE_BYTES``. The pre-fix shape
# (``json.load(handle)`` with no cap) was a JSON-size-bomb sibling
# missed by the 2026-05-08 *Round 3* sweep — the script was added
# AFTER Round 3 closed sixteen siblings in ``scripts/`` and the
# auto-discoverable inventory test
# (``test_no_direct_json_load_in_scripts``) is the closing-checklist
# anchor that catches any future re-introduction.
MAX_PREFLIGHT_QUOTA_FILE_BYTES: Final[int] = 1 * 1024 * 1024


def _read_json_file(path: Path) -> dict[str, object]:
    """Return the parsed JSON object at *path* or an empty dict.

    Defensive: a missing file is treated as "fresh state" (count = 0).
    A malformed / oversized file is treated as the same — the daily
    reset logic in the consumer modules will rewrite it on the next
    save.

    Security: a planted-huge state file at the documented path
    (compromised CI runner / corrupted previous run / partial flush
    + power loss) is rejected at the ``MAX_PREFLIGHT_QUOTA_FILE_BYTES``
    cap so a ``MemoryError`` (``BaseException``, NOT in the
    ``read_capped_json`` catch tuple) cannot crash the pre-flight
    gate. Mirrors the canonical defence pinned by the eight prior
    rounds of the JSON size-bomb family.
    """
    data = read_capped_json(
        path,
        MAX_PREFLIGHT_QUOTA_FILE_BYTES,
        label="preflight quota state",
        logger=LOGGER,
    )
    if not isinstance(data, dict):
        return {}
    return cast(dict[str, object], data)


def _today_vienna_iso() -> str:
    """Return the current calendar date in Europe/Vienna as ``YYYY-MM-DD``.

    Mirrors :func:`src.providers.vor.load_request_count` so the date
    comparison agrees byte-for-byte. Uses a stdlib-only zoneinfo
    import (the Actions image ships with the IANA tzdata).
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo("Europe/Vienna")).strftime("%Y-%m-%d")


def _check_vor(margin: int) -> int:
    """Return the count that *would* be reached after charging *margin*.

    Reads the persisted ``data/vor_request_count.json`` (or 0 on a
    missing / invalid / cross-day file) and returns ``count + margin``.
    The caller compares this against :data:`VOR_DAILY_LIMIT`.
    """
    state = _read_json_file(VOR_QUOTA_FILE)
    today = _today_vienna_iso()
    stored_date = state.get("date")
    if stored_date != today:
        # Cross-day boundary or missing file → counter resets to 0.
        return margin
    raw_count = state.get("requests")
    count = _coerce_int(raw_count)
    # Defensive clamp identical to ``src.providers.vor.load_request_count``:
    # a poisoned file recording a negative count would otherwise let
    # operators believe the budget was wider than reality.
    if count < 0:
        count = 0
    return count + margin


def _check_places(margin: int, limit: int) -> int:
    """Return the monthly count that *would* be reached after charging *margin*.

    Mirrors :class:`src.places.quota.MonthlyQuota.can_consume` for the
    *total* counter only (the per-kind sub-caps are checked inside
    the Python client; this preflight is the early-exit gate for the
    expensive workflow steps that run *before* the client is invoked).
    """
    state = _read_json_file(PLACES_QUOTA_FILE)
    # Cross-month boundary → counter resets to 0.
    from datetime import datetime
    from zoneinfo import ZoneInfo

    current_month = datetime.now(ZoneInfo("UTC")).strftime("%Y-%m")
    stored_month = state.get("month")
    if stored_month != current_month:
        return margin
    raw_total = state.get("total", 0)
    total = _coerce_int(raw_total)
    if total < 0:
        total = 0
    _ = limit  # limit is supplied for future per-kind checks
    return total + margin


def _coerce_int(raw: object) -> int:
    """Best-effort ``int(raw)`` — returns ``0`` on any failure."""
    try:
        if isinstance(raw, bool):
            # ``int(True) == 1`` would silently inflate a count when
            # the JSON file accidentally stores ``true`` instead of a
            # number — explicit refusal is the safer default.
            return 0
        if isinstance(raw, (int, float)):
            return int(raw)
        if isinstance(raw, str):
            return int(raw.strip())
    except (ValueError, TypeError):
        return 0
    return 0


def _emit_outputs(quota_ok: bool, projected: int, limit: int, kind: str) -> None:
    """Write the ``quota_ok`` / ``projected`` / ``limit`` GitHub-Actions outputs.

    Falls back to stdout when ``$GITHUB_OUTPUT`` is unset (local dev).
    """
    payload = {
        "quota_ok": "true" if quota_ok else "false",
        "projected": str(projected),
        "limit": str(limit),
        "kind": kind,
    }
    target = os.environ.get("GITHUB_OUTPUT")
    lines = [f"{key}={value}" for key, value in payload.items()]
    if target:
        try:
            with open(target, "a", encoding="utf-8") as handle:
                for line in lines:
                    handle.write(line + "\n")
        except OSError as exc:
            LOGGER.warning("preflight: konnte $GITHUB_OUTPUT nicht schreiben: %s", exc)
    for line in lines:
        print(line)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", maxsplit=1)[0])
    parser.add_argument(
        "--check",
        choices=("vor", "places"),
        required=True,
        help="Which API surface to gate.",
    )
    parser.add_argument(
        "--margin",
        type=int,
        default=2,
        help=(
            "Number of requests this run is about to make. The check "
            "fails when ``persisted_count + margin > limit``. Default: 2 "
            "(matches the 2 VOR /trip calls per Stammstrecke run)."
        ),
    )
    parser.add_argument(
        "--limit-override",
        type=int,
        default=None,
        help=(
            "Optional override for the contractual limit. By default the "
            "module-level ``VOR_DAILY_LIMIT`` / ``PLACES_DEFAULT_MONTHLY`` "
            "values are used. Lower-only: an override above the default "
            "is clamped to the default (defense-in-depth)."
        ),
    )
    args = parser.parse_args(argv)

    if args.margin < 0:
        LOGGER.error("preflight: --margin muss >= 0 sein, war %d.", args.margin)
        return 2

    logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    LOGGER.setLevel(logging.INFO)

    if args.check == "vor":
        default_limit = VOR_DAILY_LIMIT
        # Lower-only override: an env-derived override above the contract
        # limit is clamped to the contract limit.
        limit = (
            min(args.limit_override, default_limit)
            if args.limit_override is not None
            else default_limit
        )
        projected = _check_vor(args.margin)
        kind = "VOR daily"
    else:
        default_limit = PLACES_DEFAULT_MONTHLY
        limit = (
            min(args.limit_override, default_limit)
            if args.limit_override is not None
            else default_limit
        )
        projected = _check_places(args.margin, limit)
        kind = "Places monthly"

    quota_ok = projected <= limit
    if quota_ok:
        LOGGER.info(
            "preflight: %s quota OK — projected=%d, limit=%d, margin=%d.",
            kind,
            projected,
            limit,
            args.margin,
        )
    else:
        LOGGER.error(
            "preflight: %s quota EXHAUSTED — projected=%d, limit=%d, margin=%d. "
            "Subsequent API calls MUST be skipped.",
            kind,
            projected,
            limit,
            args.margin,
        )
    _emit_outputs(quota_ok, projected, limit, args.check)
    return 0 if quota_ok else 1


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
