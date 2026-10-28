#!/usr/bin/env python3
"""Record how the side steps of a workflow run ended, for the Health check.

The side steps run with ``continue-on-error`` so a failure never blocks the
feed publish: the statistics, the line check and the Feed-Wächter in
``update-cycle.yml``, the HAFAS station lines in ``update-stations.yml``.
Such a failure left only an orange annotation in a green run, and GitHub
mails nobody about it. Operator decision 2026-10-28 (Michael, on the
question whether a side step that cannot run should turn the Health check
red): "Ich tendiere zu ‚er soll rot werden', denn ein Fehler sollte
auffallen."

The last step of each such workflow calls this script with every side step's
``steps.<id>.outcome`` (``outcome``, not ``conclusion``: ``continue-on-error``
turns the conclusion of a failed step into ``success``). The file rides the
run's own commit; ``scripts/health_check.py`` (check "Nebenschritte") turns
red while the last run of a side step failed, and when the file itself is
older than the workflow's own limit (this script could not record).

    python scripts/record_side_steps.py --state data/side_steps/update-cycle.json \\
        "Statistik=failure" "Linienprüfung=success"

One file per workflow, so two workflows publishing at the same time never
touch the same file. ``skipped``, ``cancelled`` and an empty outcome leave the
step's previous record alone: the step did not run to an end.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from logging import INFO, getLogger
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.feed.config import InvalidPathError, validate_path
from src.feed.logging_safe import setup_script_logging
from src.utils.files import atomic_write, read_capped_json
from src.utils.logging import sanitize_log_arg
from src.utils.serialize import scrub_trojan_source_primitives

LOGGER = getLogger("record_side_steps")

RECORDED_OUTCOMES = ("success", "failure")
MAX_STATE_BYTES = 64 * 1024


def run_url() -> str:
    """Link to this workflow run, empty outside GitHub Actions."""
    server = os.getenv("GITHUB_SERVER_URL", "")
    repo = os.getenv("GITHUB_REPOSITORY", "")
    run_id = os.getenv("GITHUB_RUN_ID", "")
    if not (server and repo and run_id.isdigit()):
        return ""
    return f"{server}/{repo}/actions/runs/{run_id}"


def parse_outcomes(pairs: Sequence[str]) -> dict[str, str]:
    outcomes: dict[str, str] = {}
    for pair in pairs:
        label, sep, outcome = pair.partition("=")
        if not sep or not label.strip():
            raise ValueError(f"expected <label>=<outcome>, got {pair!r}")
        outcomes[label.strip()] = outcome.strip()
    return outcomes


def update(
    state: Mapping[str, Any], outcomes: Mapping[str, str], *, now: datetime, url: str
) -> dict[str, Any]:
    previous = state.get("steps")
    if not isinstance(previous, Mapping):
        previous = {}
    steps: dict[str, Any] = {
        label: dict(entry) for label, entry in previous.items() if isinstance(entry, Mapping)
    }
    stamp = now.astimezone(UTC).isoformat(timespec="seconds")
    for label, outcome in outcomes.items():
        if outcome not in RECORDED_OUTCOMES:
            continue
        entry = steps.setdefault(label, {})
        entry.update(outcome=outcome, at=stamp, run=url)
        if outcome == "success":
            entry["last_success"] = stamp
    return {"updated": stamp, "steps": steps}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("outcomes", nargs="+", help="<label>=<steps.<id>.outcome>")
    args = parser.parse_args(argv)
    setup_script_logging(INFO)
    try:
        path = validate_path(args.state, "--state")
        outcomes = parse_outcomes(args.outcomes)
    except (InvalidPathError, ValueError) as exc:
        LOGGER.error("Nebenschritte: %s", sanitize_log_arg(str(exc)))
        return 1
    loaded = read_capped_json(path, MAX_STATE_BYTES, label="Nebenschritte", logger=LOGGER)
    state = update(
        loaded if isinstance(loaded, Mapping) else {},
        outcomes,
        now=datetime.now(UTC),
        url=run_url(),
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with atomic_write(path, mode="w", encoding="utf-8", permissions=0o644) as handle:
        json.dump(scrub_trojan_source_primitives(state), handle, ensure_ascii=False,
                  indent=1, allow_nan=False, sort_keys=True)
        handle.write("\n")
    for label, outcome in outcomes.items():
        if outcome == "failure":
            LOGGER.warning("Nebenschritt gescheitert: %s", sanitize_log_arg(label))
    LOGGER.info(
        "Nebenschritte: %s",
        sanitize_log_arg(", ".join(f"{label} {outcome or 'ohne Ergebnis'}" for label, outcome in outcomes.items())),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
