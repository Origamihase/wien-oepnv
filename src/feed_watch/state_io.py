"""Feed-Wächter: I/O of the caches and the state file, and the report.

Moved verbatim from ``scripts/feed_watch.py``.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from src.feed_watch.model import (
    LOGGER,
    MAX_SOURCE_BYTES,
    MAX_STATE_BYTES,
    RULES,
    Finding,
    _clean,
    _local,
)
from src.utils.files import atomic_write, read_capped_json
from src.utils.serialize import scrub_trojan_source_primitives

# --- I/O --------------------------------------------------------------------------


def load_oebb_cache(cache_dir: Path) -> dict[str, str]:
    """GUID → raw description of every ÖBB message in the cache."""
    out: dict[str, str] = {}
    if not cache_dir.is_dir():
        return out
    for directory in sorted(cache_dir.glob("oebb*")):
        payload = read_capped_json(directory / "events.json", MAX_SOURCE_BYTES, label="ÖBB cache", logger=LOGGER)
        for entry in payload if isinstance(payload, list) else []:
            if isinstance(entry, Mapping) and isinstance(entry.get("guid"), str):
                out[entry["guid"]] = str(entry.get("description") or "")
    return out


def load_state(path: Path) -> dict[str, Any]:
    payload = read_capped_json(path, MAX_STATE_BYTES, label="Feed-Wächter", logger=LOGGER)
    return dict(payload) if isinstance(payload, Mapping) else {}


def write_state(path: Path, state: Mapping[str, Any]) -> None:
    scrubbed = scrub_trojan_source_primitives(dict(state))
    with atomic_write(path, mode="w", encoding="utf-8", permissions=0o644) as handle:
        json.dump(scrubbed, handle, ensure_ascii=False, indent=1, allow_nan=False)
        handle.write("\n")


def _report(findings: Sequence[Finding], now: datetime) -> None:
    in_actions = os.getenv("GITHUB_ACTIONS") == "true"
    for finding in findings:
        rule = RULES[finding.rule_id]
        LOGGER.warning("Feed-Wächter %s: %s: %s", finding.rule_id, _clean(finding.subject), _clean(finding.text))
        if in_actions:
            message = f"{finding.subject}: {finding.text}".replace("\n", " ").replace("::", ":")
            print(f"::warning title=Feed-Wächter: {rule.title}::{_clean(message)}")
    if not findings:
        LOGGER.info("Feed-Wächter: alle %d Regeln halten", len(RULES))
    summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    lines = [f"## Feed-Wächter — {_local(now):%d.%m.%Y %H:%M} Wiener Zeit", ""]
    if findings:
        lines += ["| Regel | Rang | Eintrag | Befund |", "| --- | --- | --- | --- |"]
        for finding in findings:
            rule = RULES[finding.rule_id]
            cells = [rule.title, rule.rank, finding.subject, finding.text]
            lines.append("| " + " | ".join(_clean(c).replace("|", "/") for c in cells) + " |")
        lines += ["", "Hinweise, kein Fehler: Der Feed wurde wie gebaut veröffentlicht."]
    else:
        lines.append(f"Alle {len(RULES)} Regeln halten.")
    try:
        with open(summary_path, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
    except OSError as exc:
        # Die Zusammenfassung ist Zierde; Log und ::warning tragen die Funde schon.
        LOGGER.debug("Feed-Wächter: Step-Summary nicht schreibbar: %s", _clean(str(exc)))
