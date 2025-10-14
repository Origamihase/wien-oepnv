#!/usr/bin/env python3
"""Run a single VOR fetch cycle and report the outcome.

This helper mirrors the production provider behaviour by invoking
``providers.vor.fetch_events`` once.  It records the request counter
before and after the run so that operators can see whether the API
consumed one of the daily request slots even if the fetch fails.
The script emits a JSON document to stdout and returns a non-zero
exit status when no data could be retrieved.
"""

from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path
from typing import Any, Dict

from requests.exceptions import RequestException

BASE_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = BASE_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

try:  # pragma: no cover - allow flat and src layouts
    from providers import vor
except ModuleNotFoundError:  # pragma: no cover
    from src.providers import vor  # type: ignore


def _mask_token(token: str | None) -> str | None:
    """Return a masked representation of an access token."""

    if not token:
        return None
    token = token.strip()
    if len(token) <= 4:
        return "*" * len(token)
    return f"{token[:2]}***{token[-2:]}"


def _serialize_count(entry: tuple[str | None, int]) -> Dict[str, Any]:
    date, count = entry
    return {"date": date, "count": count}


def run_test() -> Dict[str, Any]:
    """Execute the VOR provider once and collect diagnostic data."""

    access_id = vor.refresh_access_credentials()
    before = vor.load_request_count()

    result: Dict[str, Any] = {
        "access_id": {
            "configured": bool(access_id),
            "masked": _mask_token(access_id),
            "uses_default": access_id == vor.DEFAULT_ACCESS_ID,
        },
        "request_count": {"before": _serialize_count(before)},
    }

    events: list[Dict[str, Any]] = []
    success = False
    error: str | None = None

    try:
        events = vor.fetch_events()
        success = True
    except RequestException as exc:
        error = str(exc)
    except Exception as exc:  # pragma: no cover - defensive guard
        error = f"{exc.__class__.__name__}: {exc}"
        result["traceback"] = traceback.format_exc()

    after = vor.load_request_count()
    result["request_count"]["after"] = _serialize_count(after)

    before_date, before_count = before
    after_date, after_count = after
    if before_date and before_date == after_date:
        result["request_count"]["delta"] = after_count - before_count
    else:
        result["request_count"]["delta"] = None

    result["fetch"] = {
        "success": success and bool(events),
        "events_returned": len(events),
        "error": None if success and events else error,
    }

    return result


def main(argv: list[str]) -> int:
    report = run_test()
    json.dump(report, sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
    sys.stdout.write("\n")

    fetch_info = report.get("fetch", {})
    success = bool(fetch_info.get("success"))
    return 0 if success else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv))
