#!/usr/bin/env python3
"""Probe ÖBB HAFAS for the line data a station line check needs.

Stage 2 of the line plausibility check (audit 2026-09-25, A.14) wants the
lines serving each ÖBB station. The development sandbox cannot reach
``fahrplan.oebb.at``, so this manual diagnostic runs in
``.github/workflows/probe-hafas-lines.yml`` (``workflow_dispatch`` only)
and prints a summary of the response *shape* — never the raw payload —
for the stage to be built against real answers.

Per station it sends Mgate requests through
:func:`src.places.hafas_client.post_mgate`:

1. ``LocMatch``, the request the coordinate enrichment already makes. The
   first run (2026-09-25) showed it carries only product *classes*
   (``pCls``, ``pRefL`` to nameless products), not lines.
2. ``StationBoard`` for the whole next Tuesday, rail classes only, in the
   form ``hafas-client`` sends for ÖBB (``stbLoc`` ``A=1@L=<extId>@``, a
   ``PROD`` filter, no ``getPasslist``): how HAFAS names the lines
   (``S 80`` or ``S80``, ``REX 7``), and the size of a full day. The first
   run's two-hour board with ``getPasslist`` and the full ``lid`` came
   back ``err=PARSE``.
3. Only if (2) fails: the first run's form without ``getPasslist``, to
   narrow the cause down.

At most six requests with the two default stations. Writes nothing.
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
# ÖBB product class bits, from public-transport/hafas-client p/oebb/products.js.
PRODUCT_CLASSES: dict[int, str] = {
    1: "ICE/RJ",
    2: "IC/EC",
    4: "IC/EC",
    8: "D/EN",
    16: "R/REX",
    32: "S-Bahn",
    64: "Bus",
    128: "Fähre",
    256: "U-Bahn",
    512: "Straßenbahn",
    2048: "Rufbus",
    4096: "D/EN",
}
# Every rail class (ICE/RJ, IC/EC, D/EN, R/REX, S-Bahn): 4159.
RAIL_CLASSES = 1 | 2 | 4 | 8 | 16 | 32 | 4096
_PRODUCT_FIELDS = ("name", "nameS", "number", "cls")
_CONTEXT_FIELDS = ("line", "lineId", "catOut", "catOutS", "catOutL", "catCode", "admin")


def _clean(value: object) -> str:
    return str(sanitize_log_arg(str(value)))


def _service_result(payload: object) -> tuple[str, dict[str, Any]]:
    """Return ``(err, res)`` of the first ``svcResL`` entry.

    ``err`` carries HAFAS's ``errTxt`` when there is one, e.g.
    ``PARSE (…)``.
    """
    if not isinstance(payload, dict):
        return ("<not an object>", {})
    services = payload.get("svcResL")
    if not isinstance(services, list) or not services or not isinstance(services[0], dict):
        return (_clean(payload.get("err", "<no svcResL>")), {})
    service = services[0]
    res = service.get("res")
    err = _clean(service.get("err", "?"))
    text = service.get("errTxt") or service.get("errTxtOut")
    if text:
        err = f"{err} ({_clean(text)})"
    return (err, res if isinstance(res, dict) else {})


def _class_names(bits: object) -> str:
    """``4159`` → ``ICE/RJ, IC/EC, D/EN, R/REX, S-Bahn``."""
    if not isinstance(bits, int) or isinstance(bits, bool):
        return "?"
    names = [name for bit, name in sorted(PRODUCT_CLASSES.items()) if bits & bit]
    return ", ".join(dict.fromkeys(names)) or "-"


def _describe_product(product: object) -> str:
    if not isinstance(product, dict):
        return "<not an object>"
    parts = [f"{key}={_clean(product[key])}" for key in _PRODUCT_FIELDS if key in product]
    if "cls" in product:
        parts.append(f"({_class_names(product['cls'])})")
    context = product.get("prodCtx")
    if isinstance(context, dict):
        parts += [
            f"ctx.{key}={_clean(context[key])}" for key in _CONTEXT_FIELDS if key in context
        ]
    return " ".join(parts) or "<empty product>"


def summarise_loc_match(payload: object) -> tuple[list[str], str | None, str | None]:
    """Summarise a ``LocMatch`` answer; also return the first location's
    ``lid`` and ``extId``.
    """
    err, res = _service_result(payload)
    lines = [f"LocMatch err={err}"]
    match = res.get("match")
    locations = match.get("locL") if isinstance(match, dict) else None
    if not isinstance(locations, list) or not locations or not isinstance(locations[0], dict):
        lines.append("  no location")
        return lines, None, None
    location = locations[0]
    lines.append(
        f"  name={_clean(location.get('name'))} extId={_clean(location.get('extId'))}"
    )
    if "pCls" in location:
        lines.append(
            f"  pCls={_clean(location['pCls'])} ({_class_names(location['pCls'])})"
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
    ext_id = location.get("extId")
    return (
        lines,
        lid if isinstance(lid, str) and lid else None,
        ext_id if isinstance(ext_id, str) and ext_id.strip() else None,
    )


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


def board_request(ext_id: str, day: date) -> dict[str, object]:
    """A whole day's rail departures, in the form hafas-client sends for ÖBB."""
    return {
        "meth": "StationBoard",
        "req": {
            "type": "DEP",
            "date": day.strftime("%Y%m%d"),
            "time": "000000",
            "stbLoc": {"type": "S", "lid": f"A=1@L={ext_id}@"},
            "jnyFltrL": [{"type": "PROD", "mode": "INC", "value": str(RAIL_CLASSES)}],
            "dur": 1439,
        },
    }


def _fallback_board_request(lid: str, day: date) -> dict[str, object]:
    """The first run's request (full ``lid``, two hours) minus ``getPasslist``."""
    return {
        "meth": "StationBoard",
        "req": {
            "type": "DEP",
            "date": day.strftime("%Y%m%d"),
            "time": "070000",
            "dur": 120,
            "stbLoc": {"type": "S", "lid": lid},
        },
    }


def _run_board(label: str, request: dict[str, object]) -> tuple[list[str], bool]:
    board = post_mgate([request], max_bytes=BOARD_MAX_BYTES)
    # Re-serialised with ASCII escapes: within a few percent of the wire size.
    size = len(json.dumps(board, allow_nan=False))
    summary = summarise_board(board, size)
    summary[0] = f"{label}: {summary[0]}"
    return summary, _service_result(board)[0] == "OK"


def probe(station: str, day: date) -> list[str]:
    """Run the requests for *station* and return the printable summary."""
    lines = [f"=== {_clean(station)}"]
    loc_match = post_mgate([loc_match_request(station)])
    loc_lines, lid, ext_id = summarise_loc_match(loc_match)
    lines += loc_lines
    if ext_id is None:
        return lines
    summary, ok = _run_board("day, rail", board_request(ext_id, day))
    lines += summary
    if not ok and lid is not None:
        lines += _run_board("fallback", _fallback_board_request(lid, day))[0]
    return lines


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("stations", nargs="*", default=list(DEFAULT_STATIONS))
    args = parser.parse_args(argv)
    day = _next_tuesday(date.today())
    print(f"StationBoard: {day.isoformat()}, whole day, rail classes {RAIL_CLASSES}")
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
