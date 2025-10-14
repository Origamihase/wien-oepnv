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

import argparse
import contextlib
import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

from requests.exceptions import RequestException

BASE_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = BASE_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

try:  # pragma: no cover - allow flat and src layouts
    from utils.env import load_env_file
    from providers import vor
except ModuleNotFoundError:  # pragma: no cover
    from src.utils.env import load_env_file  # type: ignore
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


@contextlib.contextmanager
def _temporary_env(var: str, value: Optional[str]) -> Iterator[None]:
    """Temporarily set or unset an environment variable."""

    original = os.environ.get(var)
    try:
        if value is None:
            os.environ.pop(var, None)
        else:
            os.environ[var] = value
        yield
    finally:
        if original is None:
            os.environ.pop(var, None)
        else:
            os.environ[var] = original


def run_test(
    *,
    access_id_override: Optional[str] = None,
    base_url_override: Optional[str] = None,
) -> Dict[str, Any]:
    """Execute the VOR provider once and collect diagnostic data."""

    original_access_id = vor.VOR_ACCESS_ID
    original_raw = vor._VOR_ACCESS_TOKEN_RAW
    original_header = vor._VOR_AUTHORIZATION_HEADER
    original_base_url = vor.VOR_BASE_URL
    original_version = vor.VOR_VERSION
    report: Dict[str, Any] = {}

    with contextlib.ExitStack() as stack:
        if base_url_override is not None:
            stack.enter_context(_temporary_env("VOR_BASE_URL", base_url_override))

        if access_id_override is not None:
            stack.enter_context(_temporary_env("VOR_ACCESS_ID", access_id_override))

        vor.refresh_base_configuration()
        access_id = vor.refresh_access_credentials()
        before = vor.load_request_count()

        has_token = bool(access_id)

        report = {
            "access_id": {
                "configured": has_token,
                "masked": _mask_token(access_id),
                "override": access_id_override is not None,
            },
            "base_url": {
                "value": vor.VOR_BASE_URL,
                "version": vor.VOR_VERSION,
                "override": base_url_override is not None,
            },
            "request_count": {"before": _serialize_count(before)},
        }

        events: list[Dict[str, Any]] = []
        success = False
        error: str | None = None

        if not has_token:
            error = (
                "VOR_ACCESS_ID muss gesetzt sein – Abbruch, um nicht den Fallback-Zugang "
                "ohne Berechtigung zu verwenden."
            )
        else:
            try:
                events = vor.fetch_events()
                success = True
            except RequestException as exc:
                error = str(exc)
            except Exception as exc:  # pragma: no cover - defensive guard
                error = f"{exc.__class__.__name__}: {exc}"
                report["traceback"] = traceback.format_exc()

        after = vor.load_request_count()
        report["request_count"]["after"] = _serialize_count(after)

        before_date, before_count = before
        after_date, after_count = after
        if before_date and before_date == after_date:
            report["request_count"]["delta"] = after_count - before_count
        else:
            report["request_count"]["delta"] = None

        report["fetch"] = {
            "success": success and bool(events),
            "events_returned": len(events),
            "error": None if success and events else error,
            "skipped": not has_token,
        }

    vor.VOR_ACCESS_ID = original_access_id
    vor._VOR_ACCESS_TOKEN_RAW = original_raw
    vor._VOR_AUTHORIZATION_HEADER = original_header
    vor.VOR_BASE_URL = original_base_url
    vor.VOR_VERSION = original_version
    return report


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Testet den manuellen VOR-API-Abruf.")
    parser.add_argument(
        "--access-id",
        help="Überschreibt das Zugriffstoken (statt aus der Umgebung VOR_ACCESS_ID zu lesen).",
    )
    parser.add_argument(
        "--env-file",
        action="append",
        help=(
            "Lädt zusätzliche .env-Dateien, bevor die Provider-Konfiguration neu eingelesen wird. "
            "Relative Pfade beziehen sich auf den Projektstamm."
        ),
    )
    parser.add_argument(
        "--base-url",
        help="Überschreibt die Basis-URL der VOR-API (Standard: Wert aus VOR_BASE_URL/VOR_BASE).",
    )
    args = parser.parse_args(argv[1:])

    for env_entry in args.env_file or []:
        env_path = Path(env_entry)
        if not env_path.is_absolute():
            env_path = BASE_DIR / env_path
        load_env_file(env_path)

    report = run_test(
        access_id_override=args.access_id,
        base_url_override=args.base_url,
    )
    json.dump(report, sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
    sys.stdout.write("\n")

    fetch_info = report.get("fetch", {})
    skipped = bool(fetch_info.get("skipped"))
    success = bool(fetch_info.get("success"))
    if skipped:
        return 2
    return 0 if success else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv))
