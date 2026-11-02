"""Feed-Wächter: source drift and sources without a fresh answer.

Moved verbatim from ``scripts/feed_watch.py``.
"""

from __future__ import annotations

import re
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from src.feed_watch.model import (
    DISCARD_SHARE_JUMP,
    EMPTY_CACHE_MIN_REFERENCE,
    FETCH_SOURCES,
    LOGGER,
    MAX_SOURCE_BYTES,
    MIN_REFERENCE_SAMPLES,
    SAMPLE_WINDOW,
    SOURCE_DROP_FACTOR,
    SOURCE_DROP_MIN_REFERENCE,
    SOURCE_LABELS,
    SOURCE_STALE,
    VAO_VERSION_REPORT,
    Finding,
    SourceSample,
    _local,
    _parse_iso,
)
from src.utils.files import read_capped_json
from src.utils.vao_version import VersionState

# --- source drift ---------------------------------------------------------------


def _count_list(payload: object, *keys: str) -> int | None:
    node: object = payload
    for key in keys:
        if not isinstance(node, Mapping):
            return None
        node = node.get(key)
    return len(node) if isinstance(node, list) else None


def read_sources(cache_dir: Path, raw_dir: Path) -> SourceSample:
    """Counts of the provider caches and the raw answers of this run."""
    sample = SourceSample()
    if cache_dir.is_dir():
        for directory in sorted(cache_dir.iterdir()):
            provider = directory.name.split("_", 1)[0]
            if provider not in {"wl", "oebb", "baustellen"}:
                continue
            payload = read_capped_json(directory / "events.json", MAX_SOURCE_BYTES, label="cache", logger=LOGGER)
            if isinstance(payload, list):
                sample.cache[provider] = len(payload)
    raw: dict[str, tuple[str, tuple[str, ...]]] = {
        "wl-stoerungen": ("wl/trafficInfoList.json", ("data", "trafficInfos")),
        "wl-hinweise": ("wl/newsList.json", ("data", "pois")),
        "oebb": ("oebb/rss.json", ("items",)),
        "baustellen-linien": ("baustellen/BAUSTELLENLINOGD.json", ("features",)),
        "baustellen-punkte": ("baustellen/BAUSTELLENPKTOGD.json", ("features",)),
    }
    for name, (relative, keys) in raw.items():
        payload = read_capped_json(raw_dir / relative, MAX_SOURCE_BYTES, label="raw", logger=LOGGER)
        count = _count_list(payload, *keys)
        if count is not None:
            sample.raw[name] = count
    for provider in ("wl", "oebb", "baustellen"):
        payload = read_capped_json(
            raw_dir / provider / "verworfen.json", MAX_SOURCE_BYTES, label="raw", logger=LOGGER
        )
        count = _count_list(payload, "verworfen")
        if count is not None:
            sample.discarded[provider] = count
    return sample


_DISCARD_BASE: dict[str, tuple[str, ...]] = {
    "wl": ("wl-stoerungen", "wl-hinweise"),
    "oebb": ("oebb",),
    "baustellen": ("baustellen-linien", "baustellen-punkte"),
}


def _discard_share(sample: Mapping[str, Any], provider: str) -> float | None:
    raw = sample.get("raw") or {}
    discarded = sample.get("discarded") or {}
    if not isinstance(raw, Mapping) or not isinstance(discarded, Mapping) or provider not in discarded:
        return None
    names = _DISCARD_BASE[provider]
    if not all(name in raw for name in names):
        return None
    total = sum(int(raw[name]) for name in names)
    return int(discarded[provider]) / total if total else None


def check_sources(sample: SourceSample, history: Sequence[Mapping[str, Any]], now: datetime) -> list[Finding]:
    """Compare this run's counts with the samples of the last seven days."""
    reference = [
        entry
        for entry in history
        if isinstance(entry, Mapping)
        and (moment := _parse_iso(entry.get("t"))) is not None
        and now - SAMPLE_WINDOW <= moment < now
    ]
    if len(reference) < MIN_REFERENCE_SAMPLES:
        return []
    findings: list[Finding] = []

    def values(section: str, name: str) -> list[float]:
        out: list[float] = []
        for entry in reference:
            part = entry.get(section)
            if isinstance(part, Mapping) and isinstance(part.get(name), int | float):
                out.append(float(part[name]))
        return out

    for provider, count in sample.cache.items():
        past = values("cache", provider)
        if count == 0 and past and statistics.median(past) >= EMPTY_CACHE_MIN_REFERENCE:
            findings.append(
                Finding(
                    "quelle-leer",
                    provider,
                    f"Cache leer, üblich {statistics.median(past):.0f} Meldungen",
                )
            )
    for name, count in sample.raw.items():
        past = values("raw", name)
        if not past:
            continue
        usual = statistics.median(past)
        if usual >= SOURCE_DROP_MIN_REFERENCE and count < SOURCE_DROP_FACTOR * usual:
            findings.append(
                Finding("quelle-einbruch", name, f"{count} Meldungen, üblich {usual:.0f}")
            )
    current = {"raw": sample.raw, "discarded": sample.discarded}
    for provider in _DISCARD_BASE:
        share = _discard_share(current, provider)
        if share is None:
            continue
        past_shares = [s for entry in reference if (s := _discard_share(entry, provider)) is not None]
        if not past_shares:
            continue
        usual = statistics.median(past_shares)
        if abs(share - usual) > DISCARD_SHARE_JUMP:
            findings.append(
                Finding(
                    "verwurf-sprung",
                    provider,
                    f"{share:.0%} verworfen, üblich {usual:.0%}",
                )
            )
    return findings


# --- source without a fresh answer -----------------------------------------------


def parse_fetch_exit(value: str | None) -> dict[str, int]:
    """``"wl=0 oebb=1 baustellen=0"`` → exit code per source; unknown parts are left out."""
    out: dict[str, int] = {}
    for part in re.split(r"[\s,]+", value or ""):
        name, _, code = part.partition("=")
        if name in FETCH_SOURCES and code.strip().isdigit():
            out[name] = int(code)
    return out


def _fmt_hours(delta: timedelta) -> str:
    minutes = int(delta.total_seconds() // 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes:02d} min" if hours else f"{minutes} min"


@dataclass(frozen=True)
class PollSample:
    """The VAO poll as ``cache/stammstrecke/poll_status.json`` records it."""

    last_attempt: datetime
    last_result: str
    last_error: str = ""
    last_success: datetime | None = None


def check_fresh_answers(
    fetch_exit: Mapping[str, int] | None,
    poll: PollSample | None,
    previous: object,
    now: datetime,
) -> tuple[list[Finding], dict[str, Any]]:
    """Every source the feed has taken from its last good answer for :data:`SOURCE_STALE`.

    The free sources: the Update run hands over the fetch exit codes; the state
    keeps the time of the last fresh answer per source. A source without an exit
    code in this run (step killed, local run) keeps its state and gives no
    finding. The VAO poll keeps its own last success in its status file.
    """
    old = previous if isinstance(previous, Mapping) else {}
    findings: list[Finding] = []
    state: dict[str, Any] = {}
    codes = fetch_exit or {}
    for source in FETCH_SOURCES:
        raw = old.get(source)
        entry = dict(raw) if isinstance(raw, Mapping) else {}
        code = codes.get(source)
        if code == 0:
            entry = {"last_ok": now.isoformat()}
        elif code is not None:
            entry["last_exit"] = code
            # Before the first fresh answer the watcher saw, count from the first failure.
            entry.setdefault("failing_since", now.isoformat())
            last_ok = _parse_iso(entry.get("last_ok")) or _parse_iso(entry.get("failing_since")) or now
            if now - last_ok >= SOURCE_STALE:
                good = (
                    f"; der Feed nutzt die letzte gute Antwort von {_local(last_ok):%d.%m. %H:%M}"
                    if "last_ok" in entry
                    else ""
                )
                findings.append(
                    Finding(
                        "quelle-ohne-antwort",
                        SOURCE_LABELS[source],
                        f"seit {_fmt_hours(now - last_ok)} keine brauchbare Antwort "
                        f"(Abruf endet mit Exit {code}){good}",
                    )
                )
        if entry:
            state[source] = entry
    if poll is not None and poll.last_success is not None and now - poll.last_success >= SOURCE_STALE:
        reason = f"{poll.last_result} {poll.last_error}".strip()
        findings.append(
            Finding(
                "quelle-ohne-antwort",
                SOURCE_LABELS["stammstrecke"],
                f"letzter erfolgreicher Abruf {_local(poll.last_success):%d.%m. %H:%M}, vor "
                f"{_fmt_hours(now - poll.last_success)}; letzter Versuch "
                f"{_local(poll.last_attempt):%H:%M}: {reason}",
            )
        )
    return findings, state


def check_vao_version(choice: VersionState | None, now: datetime) -> list[Finding]:
    """The Stammstrecke monitor changed its VAO version shortly before this run."""
    if choice is None or choice.previous is None or choice.version is None or choice.changed_at is None:
        return []
    if not timedelta(0) <= now - choice.changed_at <= VAO_VERSION_REPORT:
        return []
    return [
        Finding(
            "vao-version",
            SOURCE_LABELS["stammstrecke"],
            f"{choice.previous} → {choice.version} am {_local(choice.changed_at):%d.%m. %H:%M}; "
            f"die VAO listet {', '.join(choice.listed) or 'keine lesbare Liste'}",
        )
    ]


def load_poll_sample(path: Path) -> PollSample | None:
    payload = read_capped_json(path, MAX_SOURCE_BYTES, label="Stammstrecke-Abrufstatus", logger=LOGGER)
    if not isinstance(payload, Mapping):
        return None
    attempt = _parse_iso(payload.get("last_attempt"))
    result = payload.get("last_result")
    if attempt is None or not isinstance(result, str):
        return None
    error = payload.get("last_error")
    return PollSample(
        last_attempt=attempt,
        last_result=result,
        last_error=error if isinstance(error, str) else "",
        last_success=_parse_iso(payload.get("last_success")),
    )
