"""Does an upstream answer still have the shape the fetch was built for?

A source that renames or drops a field does not fail: the fetch keeps
running and fills the gap with a fallback. Measured on the real raw data of
2026-10-04 (``reports/quellenausfall-2026-10-04.md`` in the project files):

* WL without ``title`` → the fetch falls back to ``name``, an internal id,
  and the German feed showed ``15A: I20261004-0020`` on all ten places.
* WL without ``time`` → long-running notices count as new and took all ten
  places; they kept them after the next good answer, because the run
  stamped their ``first_seen``.
* ÖBB without ``description`` → the Vienna filter let through six
  construction notices from Hohenau to Laa an der Thaya.
* Baustellen without dates → nine of ten places went to construction sites.

So a field that **no** record of an answer carries means the answer is
broken, and the fetch treats it like an unreachable source. Fields that
only some records carry are normal (WL: ``relatedLines`` on 69 of 80
disruptions). A short list is not judged: three night-time disruptions
without a line are possible, eighty are not.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any

# A field is checked only when the answer holds at least this many records.
MIN_RECORDS = 3


def _present(value: Any) -> bool:
    return value not in (None, "", [], {})


def has(field: str) -> Callable[[Any], bool]:
    """Check: the record is a mapping with a non-empty *field*."""

    def check(record: Any) -> bool:
        return isinstance(record, Mapping) and _present(record.get(field))

    return check


def missing_fields(
    records: Iterable[Any],
    checks: Mapping[str, tuple[Callable[[Any], bool], int]],
) -> list[str]:
    """Names of the *checks* that no record passes.

    *checks* maps a name to ``(predicate, min_records)``: the check is
    skipped when the answer holds fewer than ``min_records`` records.
    """
    items = list(records)
    return [
        name
        for name, (predicate, min_records) in checks.items()
        if len(items) >= min_records and not any(predicate(item) for item in items)
    ]


__all__ = ["MIN_RECORDS", "has", "missing_fields"]
