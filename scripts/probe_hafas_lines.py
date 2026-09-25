#!/usr/bin/env python3
"""Probe ÖBB HAFAS for the line data a station line check needs.

Stage 2 of the line plausibility check (audit 2026-09-25, A.14) wants the
lines serving each ÖBB station. The development sandbox cannot reach
``fahrplan.oebb.at``, so this manual diagnostic runs in
``.github/workflows/probe-hafas-lines.yml`` (``workflow_dispatch`` only)
and prints a summary of the response *shape* — never the raw payload —
for the stage to be built against real answers.

Per station it sends two Mgate requests through
:func:`src.places.hafas_client.post_mgate`:

1. ``LocMatch``, the request the coordinate enrichment already makes:
   does the location carry product references (``pRefL``), so that a
   station's lines could come from this one cheap call?
2. ``StationBoard``, departures in a two-hour window on the next
   Tuesday: which products run there, how HAFAS names them (``S 80`` or
   ``S80``, ``REX 7``), their ``cls`` bits, and the response size.

Four requests with the two default stations. Writes nothing.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import date, timedelta
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.places.hafas_client import HafasProfileError, loc_match_request, post_mgate
from src.utils.logging import sanitize_log_arg

DEFAULT_STATIONS = ("Wien Hütteldorf", "Wien Meidling")
# A departure board is far larger than a LocMatch answer; the probe also
# reports the size so stage 2 can size its own cap.
BOARD_MAX_BYTES = 5 * 1024 * 1024
MAX_PRODUCT_LINES = 120
_PRODUCT_FIELDS = ("name", "nameS", "number", "cls")
_CONTEXT_FIELDS = ("line", "lineId", "catOut", "catOutS", "catOutL", "catCode", "admin")


def _clean(value: object) -> str:
    return str(sanitize_log_arg(str(value)))


def _service_result(payload: object) -> tuple[str, dict[str, Any]]:
    """Return ``(err, res)`` of the first ``svcResL`` entry."""
    if not isinstance(payload, dict):
        return ("<not an object>", {})
    services = payload.get("svcResL")
    if not isinstance(services, list) or not services or not isinstance(services[0], dict):
        return (_clean(payload.get("err", "<no svcResL>")), {})
    service = services[0]
    res = service.get("res")
    return (_clean(service.get("err", "?")), res if isinstance(res, dict) else {})


def _describe_product(product: object) -> str:
    if not isinstance(product, dict):
        return "<not an object>"
    parts = [f"{key}={_clean(product[key])}" for key in _PRODUCT_FIELDS if key in product]
    context = product.get("prodCtx")
    if isinstance(context, dict):
        parts += [
            f"ctx.{key}={_clean(context[key])}" for key in _CONTEXT_FIELDS if key in context
        ]
    return " ".join(parts) or "<empty product>"


def summarise_loc_match(payload: object) -> tuple[list[str], str | None]:
    """Summarise a ``LocMatch`` answer; also return the first location's ``lid``."""
    err, res = _service_result(payload)
    lines = [f"LocMatch err={err}"]
    match = res.get("match")
    locations = match.get("locL") if isinstance(match, dict) else None
    if not isinstance(locations, list) or not locations or not isinstance(locations[0], dict):
        lines.append("  no location")
        return lines, None
    location = locations[0]
    lines.append(
        f"  name={_clean(location.get('name'))} extId={_clean(location.get('extId'))}"
    )
    lines.append(f"  location keys: {', '.join(sorted(_clean(k) for k in location))}")
    common = res.get("common")
    products = common.get("prodL") if isinstance(common, dict) else None
    refs = location.get("pRefL")
    if isinstance(refs, list):
        lines.append(f"  pRefL: {len(refs)} product references")
        for ref in refs[:MAX_PRODUCT_LINES]:
            if isinstance(ref, int) and isinstance(products, list) and 0 <= ref < len(products):
                lines.append(f"    {_describe_product(products[ref])}")
    else:
        lines.append("  pRefL: absent")
    lid = location.get("lid")
    return lines, lid if isinstance(lid, str) and lid else None


def summarise_board(payload: object, size: int) -> list[str]:
    """Summarise a ``StationBoard`` answer: journeys and distinct products."""
    err, res = _service_result(payload)
    journeys = res.get("jnyL")
    common = res.get("common")
    products = common.get("prodL") if isinstance(common, dict) else None
    lines = [
        f"StationBoard err={err} bytes={size} "
        f"journeys={len(journeys) if isinstance(journeys, list) else 'absent'} "
        f"products={len(products) if isinstance(products, list) else 'absent'}"
    ]
    if isinstance(products, list):
        described = sorted({_describe_product(product) for product in products})
        lines += [f"  {text}" for text in described[:MAX_PRODUCT_LINES]]
        if len(described) > MAX_PRODUCT_LINES:
            lines.append(f"  … {len(described) - MAX_PRODUCT_LINES} more")
    return lines


def _next_tuesday(today: date) -> date:
    return today + timedelta(days=(1 - today.weekday()) % 7 or 7)


def _board_request(lid: str, day: date) -> dict[str, object]:
    return {
        "meth": "StationBoard",
        "req": {
            "type": "DEP",
            "date": day.strftime("%Y%m%d"),
            "time": "070000",
            "dur": 120,
            "stbLoc": {"type": "S", "lid": lid},
            "maxJny": 1000,
            "getPasslist": False,
        },
    }


def probe(station: str, day: date) -> list[str]:
    """Run both requests for *station* and return the printable summary."""
    lines = [f"=== {_clean(station)}"]
    loc_match = post_mgate([loc_match_request(station)])
    loc_lines, lid = summarise_loc_match(loc_match)
    lines += loc_lines
    if lid is None:
        return lines
    board = post_mgate([_board_request(lid, day)], max_bytes=BOARD_MAX_BYTES)
    # Re-serialised with ASCII escapes: within a few percent of the wire size.
    size = len(json.dumps(board, allow_nan=False))
    lines += summarise_board(board, size)
    return lines


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("stations", nargs="*", default=list(DEFAULT_STATIONS))
    args = parser.parse_args(argv)
    day = _next_tuesday(date.today())
    print(f"StationBoard window: {day.isoformat()} 07:00, 120 min")
    failures = 0
    for station in args.stations:
        try:
            print("\n".join(probe(station, day)))
        except (HafasProfileError, ValueError, OSError) as exc:
            # ``requests.RequestException`` is an ``OSError``.
            failures += 1
            print(f"=== {_clean(station)}: FAILED {_clean(type(exc).__name__)}: {_clean(exc)}")
    return 1 if failures == len(args.stations) else 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
