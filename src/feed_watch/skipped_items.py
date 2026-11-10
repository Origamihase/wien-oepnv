"""Feed-Wächter: items the feed build left out of this run.

When one item makes a step of the feed chain fail, the build leaves that item
out and builds the feed with the rest. It records each such item in its
health report (``docs/feed-health.json``, key ``skipped_items``). The watcher
runs in the same Update run, reads that list and reports one finding per
item, so the operator gets an issue and not only a log line.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from src.feed import config as feed_config
from src.feed.config import InvalidPathError, validate_path
from src.feed_watch.model import LOGGER, Finding, _clean
from src.utils.files import read_capped_json

RULE_ID = "meldung-uebersprungen"

# The health report of a build is a few kilobytes; 2 MB is the limit of the
# watcher's own state file.
MAX_HEALTH_BYTES = 2 * 1024 * 1024
# The issue lists ten findings and counts the rest; the state file keeps every
# finding, so the list is bounded here.
MAX_SKIPPED_ITEMS = 50
MAX_SOURCE_CHARS = 80
MAX_TITLE_CHARS = 200
MAX_ERROR_CHARS = 200

NO_SOURCE = "unbekannte Quelle"
NO_TITLE = "(ohne Titel)"
NO_ERROR = "Fehler nicht angegeben"


@dataclass(frozen=True)
class SkippedItem:
    """One entry of ``skipped_items`` in the build's health report."""

    source: str
    title: str
    error: str


def _one_line(value: object, limit: int, fallback: str) -> str:
    """*value* as one printable line of at most *limit* characters.

    A finding becomes one bullet of an issue and one row of the job summary,
    so line breaks and non-printing characters are taken out. A field that is
    missing or not a string gives *fallback*: the item was still left out.
    """
    if not isinstance(value, str):
        return fallback
    text = "".join(ch for ch in " ".join(value.split()) if ch.isprintable())
    text = " ".join(text.split())
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text or fallback


def parse_skipped_items(payload: object) -> list[SkippedItem]:
    """The ``skipped_items`` of a health report; empty for every other shape.

    A report without the key comes from a build before the key existed and
    means that nothing was left out.
    """
    if not isinstance(payload, Mapping):
        return []
    entries = payload.get("skipped_items")
    if not isinstance(entries, list):
        return []
    items: list[SkippedItem] = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        item = SkippedItem(
            source=_one_line(entry.get("source"), MAX_SOURCE_CHARS, NO_SOURCE),
            title=_one_line(entry.get("title"), MAX_TITLE_CHARS, NO_TITLE),
            error=_one_line(entry.get("error"), MAX_ERROR_CHARS, NO_ERROR),
        )
        if item not in items:
            items.append(item)
        if len(items) >= MAX_SKIPPED_ITEMS:
            break
    return items


def health_report_path(override: Path | None = None) -> Path | None:
    """Where the build of this run wrote its health report as JSON.

    Without *override* it is ``FEED_HEALTH_JSON_PATH`` of ``src.feed.config``
    (default ``docs/feed-health.json``), resolved the way the build resolves
    it before it writes the file. A configured path the build would refuse
    gives ``None``: the build wrote no report there.
    """
    if override is not None:
        return override
    try:
        return validate_path(Path(feed_config.FEED_HEALTH_JSON_PATH), "FEED_HEALTH_JSON_PATH")
    except InvalidPathError as exc:
        LOGGER.warning("Feed-Wächter: Pfad des Feed-Health-Berichts nicht nutzbar: %s", _clean(exc))
        return None


def load_skipped_items(path: Path | None) -> list[SkippedItem]:
    """The items the build of this run left out, from its health report.

    No path, or a missing, unreadable, oversized or malformed file, gives an
    empty list: the watcher then has nothing to report for this rule and goes
    on with the other rules.
    """
    if path is None:
        return []
    try:
        payload = read_capped_json(path, MAX_HEALTH_BYTES, label="Feed-Health", logger=LOGGER)
    except ValueError as exc:
        # E.g. a number with more digits than ``int`` converts: not a
        # ``JSONDecodeError``, so the reader lets it through.
        LOGGER.warning("Feed-Wächter: Feed-Health-Bericht nicht lesbar: %s", _clean(exc))
        return []
    return parse_skipped_items(payload)


def check_skipped_items(skipped: Sequence[SkippedItem] | None) -> list[Finding]:
    """Rule ``meldung-uebersprungen``: one finding per item the build left out."""
    return [
        Finding(
            RULE_ID,
            item.title,
            f"Quelle {item.source}, beim Bau übersprungen wegen {item.error}; "
            "fehlt im Feed, bis die Ursache behoben ist",
        )
        for item in skipped or ()
    ]
