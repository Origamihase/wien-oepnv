"""Record what each source delivered and what its fetch dropped.

Raw upstream responses cannot be fetched again afterwards, so questions
like "did Wiener Linien send this notice at all?" or "which stage dropped
it?" could not be answered from the provider caches alone (they hold only
what survived the fetch). With ``RAW_CAPTURE=1`` in the environment (set by
``update-cycle.yml``) every fetch keeps two kinds of file under
``data/raw/<source>/``:

* a **snapshot** of each upstream response (``write_snapshot``), and
* ``verworfen.json``, the items the fetch dropped and why
  (``reset_drops`` / ``note_drop`` / ``write_drops``).

Every file is overwritten on each run; the git history is the archive.
That is the cheapest layout measured for this repository (2026-10-03):
git stores only the delta between two versions of the same path, while
compressed files or one new file per run cost several times more in the
pack or the working tree. Three rules keep the deltas small:

1. Stable text: sorted keys, one value per line, lists in a stable order.
2. No values that change on every call (server time, row numbers); the
   per-source normalisers drop them before the write.
3. Unchanged content is not rewritten, so a quiet tick adds no commit line.

Capture is off by default, so tests, local builds and the health check
never write here. Nothing in this module may fail a fetch: every error is
logged and swallowed.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from .files import atomic_write
from .serialize import scrub_trojan_source_primitives

log = logging.getLogger(__name__)

RAW_CAPTURE_ENV = "RAW_CAPTURE"
RAW_ROOT = Path(__file__).resolve().parents[2] / "data" / "raw"

# A snapshot above this size is skipped (with a warning) rather than
# committed: a broken upstream must not bloat the repository.
MAX_SNAPSHOT_BYTES = 4 * 1024 * 1024
MAX_DROP_TITLE = 200

_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")
_drops: dict[str, set[tuple[str, str]]] = {}


def capture_enabled() -> bool:
    """True when ``RAW_CAPTURE`` asks for raw-data recording."""
    return os.getenv(RAW_CAPTURE_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def _target(source: str, name: str) -> Path:
    if not (_NAME_RE.match(source) and _NAME_RE.match(name)) or ".." in source or ".." in name:
        raise ValueError(f"invalid raw-capture name: {source!r}/{name!r}")
    return RAW_ROOT / source / f"{name}.json"


def render(payload: Any) -> str:
    """The stable text form of *payload*: scrubbed, sorted, one value per line."""
    scrubbed = scrub_trojan_source_primitives(payload)
    return json.dumps(scrubbed, ensure_ascii=False, sort_keys=True, indent=1, allow_nan=False) + "\n"


def _write_if_changed(path: Path, text: str) -> bool:
    encoded = text.encode("utf-8")
    if len(encoded) > MAX_SNAPSHOT_BYTES:
        log.warning(
            "Rohdaten %s/%s nicht geschrieben: %d Bytes über dem Limit von %d.",
            path.parent.name,
            path.name,
            len(encoded),
            MAX_SNAPSHOT_BYTES,
        )
        return False
    try:
        if path.read_bytes() == encoded:
            return False
    except OSError:
        pass
    with atomic_write(path, mode="w", encoding="utf-8", permissions=0o644) as handle:
        handle.write(text)
    return True


def write_snapshot(source: str, name: str, payload: Any) -> None:
    """Keep *payload* (already normalised) as ``data/raw/<source>/<name>.json``."""
    if not capture_enabled():
        return
    try:
        _write_if_changed(_target(source, name), render(payload))
    except (OSError, ValueError, TypeError, RecursionError) as exc:
        log.warning("Rohdaten %s/%s nicht geschrieben (%s).", source, name, type(exc).__name__)


def reset_drops(source: str) -> None:
    """Start a fresh drop list for *source* (call at the start of a fetch)."""
    _drops[source] = set()


def note_drop(source: str, reason: str, title: object) -> None:
    """Remember that the fetch of *source* dropped the item *title* for *reason*."""
    text = " ".join(str(title or "").split())[:MAX_DROP_TITLE]
    _drops.setdefault(source, set()).add((reason, text))


def collected_drops(source: str) -> list[dict[str, str]]:
    """The drops noted for *source* since the last reset, in a stable order."""
    return [{"grund": reason, "titel": title} for reason, title in sorted(_drops.get(source, ()))]


def write_drops(source: str) -> None:
    """Keep the drops of this fetch as ``data/raw/<source>/verworfen.json``."""
    write_snapshot(source, "verworfen", {"verworfen": collected_drops(source)})


def sorted_records(records: list[Any], *keys: str) -> list[Any]:
    """*records* sorted by the first of *keys* each carries, then by content.

    Upstream lists arrive in no guaranteed order; a stable order keeps a
    reshuffle from rewriting the whole file.
    """

    def sort_key(record: Any) -> tuple[str, str]:
        ident = ""
        if isinstance(record, dict):
            for key in keys:
                value = record.get(key)
                if value not in (None, ""):
                    ident = str(value)
                    break
        try:
            body = json.dumps(record, ensure_ascii=False, sort_keys=True, default=str)
        except (TypeError, ValueError):
            body = repr(record)
        return ident, body

    return sorted(records, key=sort_key)


__all__ = [
    "MAX_SNAPSHOT_BYTES",
    "RAW_CAPTURE_ENV",
    "RAW_ROOT",
    "capture_enabled",
    "collected_drops",
    "note_drop",
    "render",
    "reset_drops",
    "sorted_records",
    "write_drops",
    "write_snapshot",
]
