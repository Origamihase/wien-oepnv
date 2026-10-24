"""One-off probe (removed before merge): same /departureBoard query against
several VAO API versions, run through the real Hbf parser, compared.

Writes only parsed fields (no URL, no access ID) to the output JSON.
"""

from __future__ import annotations

import json
import os
import sys
from contextlib import ExitStack
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import update_stammstrecke_hbf as hbf  # noqa: E402
from src.providers import vor  # noqa: E402

VERSIONS = sys.argv[2:] or ["v1.11.0", "v1.12.0", "v1.13.0"]
OUT = Path(sys.argv[1])


def _dep_record(dep: Any) -> dict[str, Any]:
    return {
        "line": hbf._departure_line_name(dep),
        "date": dep.get("date"),
        "time": dep.get("time"),
        "rtDate": dep.get("rtDate"),
        "rtTime": dep.get("rtTime"),
        "track": hbf._extract_track_string(dep),
        "cancelled": hbf._departure_is_cancelled(dep),
        "direction": dep.get("direction"),
        "delay": hbf._departure_delay_minutes(dep),
        "keys": sorted(dep.keys()),
    }


def main() -> int:
    when = hbf._now_vienna()
    result: dict[str, Any] = {"when": when.isoformat(), "versions": {}}
    for version in VERSIONS:
        os.environ["VOR_VERSION"] = version
        os.environ.pop("VOR_BASE_URL", None)
        base = vor.refresh_base_configuration()
        entry: dict[str, Any] = {"base_ok": base.endswith(f"/{version}/")}
        try:
            hbf._charge_one_request(when)
            with ExitStack() as stack:
                session = hbf._build_session(stack)
                deps = hbf._query_departure_board(session, when=when)
            by_dir, diag = hbf._collect_hbf_observations(deps)
            entry["departures"] = [_dep_record(d) for d in deps]
            entry["observations"] = {
                k: [
                    {"name": o.name, "scheduled": o.scheduled.isoformat(), "delay": o.delay_minutes}
                    for o in v
                ]
                for k, v in by_dir.items()
            }
            entry["diagnostics"] = {
                "dropped_no_track": diag.dropped_no_track,
                "dropped_non_stammstrecke_track": diag.dropped_non_stammstrecke_track,
                "cancelled_observed": diag.cancelled_observed,
                "on_stammstrecke": diag.on_stammstrecke,
            }
        except Exception as exc:  # report, never print secrets
            entry["error"] = f"{type(exc).__name__}: {exc}"[:300]
        result["versions"][version] = entry
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str))
    for version, entry in result["versions"].items():
        print(version, "error" in entry and entry["error"] or len(entry["departures"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
