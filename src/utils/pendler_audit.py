"""Pendler-Candidates audit and Markdown report generator.

The Wien-ÖPNV station registry curates a name-based commuter whitelist
in ``data/pendler_candidates.json``. The monthly station-directory
update job matches each candidate against the ÖBB Excel-Verzeichnis;
successful matches end up in ``data/stations.json`` with
``pendler=true`` and a real ``bst_id``.

This module produces a coverage report that cross-references the two
files and answers two questions for editors:

1. **Which candidates have been adopted?** — i.e. a station with
   ``pendler=true`` and a valid ``bst_id`` exists for the candidate's
   name (or one of its alternative names).
2. **Which candidates are orphans?** — never matched, possibly because
   the name has drifted from ÖBB's spelling, the line was renamed, or
   the candidate was simply premature.

The report flags **stale orphans** — orphans whose ``added`` field is
older than a configurable horizon (default 365 days). These deserve
editor attention: either the spelling needs adjusting or the candidate
should be retired.

Architectural notes
-------------------

* **No external network calls.** The audit operates purely on local
  JSON files. The Saboteur ``CircuitBreaker`` primitive is therefore
  not applicable here.
* **Sentinel-style ceiling on the staleness horizon.** ``cap_stale_days``
  uses ``min()`` to clamp ``max_stale_days`` to :data:`MAX_STALE_DAYS_CAP`.
  The cap defends against pathological inputs (env-leaked configuration,
  user typo) that would otherwise embed effectively-unbounded date
  arithmetic into the audit pipeline.
* **Strict typing throughout** — every public surface is typed under
  ``mypy --strict``.

See Also:
    - ``data/pendler_candidates.json`` — the curated input.
    - ``docs/schema/pendler_candidates.schema.json`` — its JSON schema.
    - ``scripts/audit_pendler_candidates.py`` — thin CLI wrapper.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

log = logging.getLogger(__name__)

#: Hard ceiling for the ``--max-stale-days`` parameter. Editors typically
#: care about candidates older than a year or two; anything beyond that
#: range carries no extra signal and risks integer-overflow shapes
#: downstream. Mirrors the Sentinel ``min()``-cap pattern enforced
#: across the project (e.g. ``MAX_WL_FETCH_TIMEOUT``).
MAX_STALE_DAYS_CAP: int = 3650  # ~10 years, deliberately generous.

_VALID_PRIORITIES: frozenset[int] = frozenset({1, 2, 3})

_BAHNHOF_TOKEN_RE = re.compile(
    r"\b(?:bahnhof|bahnhst|bhf|hbf|bf|bahnsteig|bahnsteige|gleis)\b"
)
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_MULTI_SPACE_RE = re.compile(r"\s{2,}")


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Candidate:
    """A single entry from ``pendler_candidates.json`` after coercion.

    Attributes:
        name: The canonical name as used by editors.
        alternative_names: Tuple of alternative spellings (may be empty).
        priority: 1, 2 or 3, or ``None`` if missing/invalid.
        added: ISO date when the entry was added, or ``None``.
        line: Free-text annotation (S-Bahn line, etc.) or ``None``.
    """

    name: str
    alternative_names: tuple[str, ...] = ()
    priority: int | None = None
    added: date | None = None
    line: str | None = None


@dataclass(frozen=True)
class AuditEntry:
    """Per-candidate audit result.

    Attributes:
        name: Mirror of the candidate's canonical name.
        priority: Mirror of the candidate's priority.
        added: Mirror of the candidate's ``added`` date.
        adopted: ``True`` iff a matching pendler station exists.
        matched_station: The station name that satisfied the match,
            or ``None`` for orphans.
        stale: ``True`` iff the candidate is an orphan AND its
            ``added`` date is older than the configured horizon.
        age_days: Days between ``added`` and the audit reference date.
            ``None`` if the candidate has no ``added`` field.
    """

    name: str
    priority: int | None
    added: date | None
    adopted: bool
    matched_station: str | None
    stale: bool
    age_days: int | None


@dataclass(frozen=True)
class PriorityCoverage:
    """Adoption statistics per priority bucket."""

    priority: int | None
    adopted: int
    total: int

    @property
    def adoption_rate(self) -> float:
        """Return the adoption ratio (0.0 if the bucket is empty).

        Returns:
            Float in ``[0.0, 1.0]``.
        """
        if self.total == 0:
            return 0.0
        return self.adopted / self.total


@dataclass(frozen=True)
class AuditReport:
    """Aggregate audit result.

    Attributes:
        total: Number of candidates considered.
        adopted: How many candidates matched a pendler station.
        orphans: How many candidates did not match.
        stale_orphans: Subset of ``orphans`` whose ``added`` date is
            older than the configured horizon.
        max_stale_days: Effective horizon (after :func:`cap_stale_days`).
        entries: Per-candidate audit entries, in input order.
        priority_coverage: Coverage broken down by priority bucket
            (1, 2, 3 and ``None`` for unprioritised entries).
    """

    total: int
    adopted: int
    orphans: int
    stale_orphans: int
    max_stale_days: int
    entries: tuple[AuditEntry, ...]
    priority_coverage: tuple[PriorityCoverage, ...] = field(default_factory=tuple)

    @property
    def has_orphans(self) -> bool:
        """Return ``True`` iff at least one candidate is unadopted."""
        return self.orphans > 0

    @property
    def has_stale_orphans(self) -> bool:
        """Return ``True`` iff at least one orphan exceeds the horizon."""
        return self.stale_orphans > 0

    def iter_orphans(self) -> Iterable[AuditEntry]:
        """Yield only the orphan entries, preserving input order."""
        return tuple(entry for entry in self.entries if not entry.adopted)


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------


def _read_json(path: Path) -> object | None:
    """Read JSON from ``path``; return ``None`` for any I/O or parse error."""
    try:
        text = path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError) as exc:
        log.info("pendler_audit: could not read %s: %s", path, exc)
        return None
    try:
        parsed: object = json.loads(text)
    except json.JSONDecodeError as exc:
        log.warning("pendler_audit: invalid JSON in %s: %s", path, exc)
        return None
    return parsed


def _coerce_alternatives(value: object) -> tuple[str, ...]:
    """Filter ``value`` down to non-empty stripped strings."""
    if not isinstance(value, list):
        return ()
    out: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            out.append(item.strip())
    return tuple(out)


def _coerce_priority(value: object) -> int | None:
    """Coerce ``value`` to one of {1, 2, 3} or ``None``."""
    if isinstance(value, int) and not isinstance(value, bool) and value in _VALID_PRIORITIES:
        return value
    return None


def _coerce_added(value: object) -> date | None:
    """Parse ISO ``YYYY-MM-DD`` strings; return ``None`` otherwise."""
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _coerce_candidate(entry: object) -> Candidate | None:
    """Convert a raw ``pendler_candidates.json`` entry to :class:`Candidate`.

    Returns ``None`` if the entry is structurally invalid (not a dict,
    missing/empty name, etc.).
    """
    if not isinstance(entry, dict):
        return None
    name_raw = entry.get("name")
    if not isinstance(name_raw, str):
        return None
    name = name_raw.strip()
    if not name:
        return None

    line_raw = entry.get("line")
    line = line_raw.strip() if isinstance(line_raw, str) and line_raw.strip() else None

    return Candidate(
        name=name,
        alternative_names=_coerce_alternatives(entry.get("alternative_names")),
        priority=_coerce_priority(entry.get("priority")),
        added=_coerce_added(entry.get("added")),
        line=line,
    )


def load_candidates(path: Path) -> tuple[Candidate, ...]:
    """Load and coerce the pendler-candidates JSON file.

    Args:
        path: Path to the candidates JSON file.

    Returns:
        Tuple of :class:`Candidate` objects in input order. Empty tuple
        when the file is missing, malformed, or has the wrong shape.
    """
    payload = _read_json(path)
    if not isinstance(payload, dict):
        return ()
    raw = payload.get("candidates")
    if not isinstance(raw, list):
        return ()
    coerced: list[Candidate] = []
    for entry in raw:
        candidate = _coerce_candidate(entry)
        if candidate is not None:
            coerced.append(candidate)
    return tuple(coerced)


def _normalize_name_key(name: str) -> str:
    """Reduce a station name to a normalised match key.

    Mirrors the subset of :func:`_normalize_location_keys` from
    ``scripts/update_station_directory.py`` that's relevant for
    matching pendler candidates: ASCII-fold, remove "Bahnhof"-style
    tokens, replace separators with whitespace, casefold.

    Args:
        name: Raw station name (may contain umlauts, dashes, parens).

    Returns:
        Normalised key suitable for ``dict``-lookup matching.
    """
    folded = "".join(
        ch for ch in unicodedata.normalize("NFKD", name) if not unicodedata.combining(ch)
    )
    folded = folded.replace("ß", "ss").casefold()
    folded = _BAHNHOF_TOKEN_RE.sub(" ", folded)
    folded = folded.replace("-", " ").replace("/", " ")
    folded = _NON_ALNUM_RE.sub(" ", folded)
    return _MULTI_SPACE_RE.sub(" ", folded).strip()


def load_pendler_station_keys(path: Path) -> dict[str, str]:
    """Build a ``{normalised_key: display_name}`` index of pendler stations.

    Only stations satisfying ``pendler=true`` AND having a non-empty
    ``bst_id`` are included — those are the entries the updater would
    actually adopt. Each station contributes its name plus all aliases
    as keys, mapped to its canonical display name for reporting.

    Args:
        path: Path to ``stations.json``.

    Returns:
        Mapping from normalised name keys to canonical station names.
        Empty dict when the file is missing/malformed.
    """
    payload = _read_json(path)
    if not isinstance(payload, dict):
        return {}
    raw = payload.get("stations")
    if not isinstance(raw, list):
        return {}

    index: dict[str, str] = {}
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        if entry.get("pendler") is not True:
            continue
        bst_id = entry.get("bst_id")
        if not isinstance(bst_id, str) or not bst_id.strip():
            continue
        name = entry.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        canonical = name.strip()
        _index_station_name(index, canonical, canonical)
        for alias in _string_aliases(entry.get("aliases")):
            _index_station_name(index, alias, canonical)
    return index


def _string_aliases(value: object) -> tuple[str, ...]:
    """Extract non-empty string aliases from a station entry."""
    if not isinstance(value, list):
        return ()
    return tuple(item.strip() for item in value if isinstance(item, str) and item.strip())


def _index_station_name(
    index: dict[str, str], variant: str, canonical: str
) -> None:
    """Add a normalised key for ``variant`` mapping to ``canonical``."""
    key = _normalize_name_key(variant)
    if key and key not in index:
        index[key] = canonical


# ---------------------------------------------------------------------------
# Sentinel cap
# ---------------------------------------------------------------------------


def cap_stale_days(value: int) -> int:
    """Clamp ``value`` to the safe ``[0, MAX_STALE_DAYS_CAP]`` range.

    Args:
        value: Caller-supplied days threshold.

    Returns:
        ``max(0, min(value, MAX_STALE_DAYS_CAP))``. Sentinel pattern.
    """
    if value < 0:
        return 0
    return min(value, MAX_STALE_DAYS_CAP)


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


def _candidate_match(
    candidate: Candidate, station_index: Mapping[str, str]
) -> str | None:
    """Return the matched station's canonical name, or ``None`` for orphans."""
    for variant in (candidate.name, *candidate.alternative_names):
        key = _normalize_name_key(variant)
        if not key:
            continue
        match = station_index.get(key)
        if match is not None:
            return match
    return None


def _candidate_age_and_stale(
    candidate: Candidate, *, reference_date: date, horizon: int, adopted: bool
) -> tuple[int | None, bool]:
    """Compute age in days and the stale flag for a single candidate."""
    if candidate.added is None:
        return None, False
    age_days = (reference_date - candidate.added).days
    stale = (not adopted) and age_days > horizon
    return age_days, stale


def _build_priority_coverage(
    entries: Iterable[AuditEntry],
) -> tuple[PriorityCoverage, ...]:
    """Group entries by priority and tally adoption stats."""
    buckets: dict[int | None, list[AuditEntry]] = {}
    for entry in entries:
        buckets.setdefault(entry.priority, []).append(entry)

    def _sort_key(prio: int | None) -> tuple[int, int]:
        # Real priorities first (1, 2, 3 ascending), then ``None``.
        return (1, 0) if prio is None else (0, prio)

    coverage: list[PriorityCoverage] = []
    for priority in sorted(buckets.keys(), key=_sort_key):
        bucket = buckets[priority]
        adopted = sum(1 for e in bucket if e.adopted)
        coverage.append(
            PriorityCoverage(priority=priority, adopted=adopted, total=len(bucket))
        )
    return tuple(coverage)


def audit_pendler_candidates(
    candidates: Iterable[Candidate],
    station_index: Mapping[str, str],
    *,
    reference_date: date,
    max_stale_days: int,
) -> AuditReport:
    """Cross-reference candidates against the pendler-station index.

    Args:
        candidates: Iterable of curated candidates.
        station_index: Mapping returned by :func:`load_pendler_station_keys`.
        reference_date: The "today" used to compute age and staleness.
        max_stale_days: Days threshold for the stale-orphan flag.
            Capped to :data:`MAX_STALE_DAYS_CAP` via :func:`cap_stale_days`
            (Sentinel ``min()``-cap pattern).

    Returns:
        :class:`AuditReport` describing adoption, orphans, stale orphans,
        per-priority coverage, and per-candidate detail.
    """
    horizon = cap_stale_days(max_stale_days)
    entries: list[AuditEntry] = []
    adopted_count = 0
    orphans_count = 0
    stale_count = 0

    for candidate in candidates:
        match = _candidate_match(candidate, station_index)
        adopted = match is not None
        age_days, stale = _candidate_age_and_stale(
            candidate,
            reference_date=reference_date,
            horizon=horizon,
            adopted=adopted,
        )
        entries.append(
            AuditEntry(
                name=candidate.name,
                priority=candidate.priority,
                added=candidate.added,
                adopted=adopted,
                matched_station=match,
                stale=stale,
                age_days=age_days,
            )
        )
        if adopted:
            adopted_count += 1
        else:
            orphans_count += 1
            if stale:
                stale_count += 1

    return AuditReport(
        total=len(entries),
        adopted=adopted_count,
        orphans=orphans_count,
        stale_orphans=stale_count,
        max_stale_days=horizon,
        entries=tuple(entries),
        priority_coverage=_build_priority_coverage(entries),
    )


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------


def _md_escape(value: str) -> str:
    """Escape pipe characters so generated Markdown tables stay intact."""
    return value.replace("|", "\\|")


def _format_added(added: date | None) -> str:
    return added.isoformat() if added is not None else "—"


def _format_priority(priority: int | None) -> str:
    return str(priority) if priority is not None else "—"


def _format_age(age_days: int | None) -> str:
    return f"{age_days}d" if age_days is not None else "—"


def _render_summary(report: AuditReport, *, reference_date: date) -> list[str]:
    """Render the top-level summary block."""
    coverage_pct = (report.adopted / report.total * 100.0) if report.total else 0.0
    return [
        "# Pendler Candidates Audit",
        "",
        f"Reference date: {reference_date.isoformat()}",
        f"Stale-days horizon: {report.max_stale_days}",
        "",
        f"- Total candidates: {report.total}",
        f"- Adopted: {report.adopted} ({coverage_pct:.1f}%)",
        f"- Orphans: {report.orphans}",
        f"- Stale orphans: {report.stale_orphans}",
        "",
    ]


def _render_priority_table(coverage: Iterable[PriorityCoverage]) -> list[str]:
    """Render the per-priority coverage table."""
    lines: list[str] = ["## Coverage by priority", "", "| Priority | Adopted | Total | Rate |", "| --- | --- | --- | --- |"]
    for entry in coverage:
        rate_pct = entry.adoption_rate * 100.0
        lines.append(
            f"| {_format_priority(entry.priority)} | {entry.adopted} | {entry.total} | {rate_pct:.1f}% |"
        )
    lines.append("")
    return lines


def _render_orphans_table(entries: Iterable[AuditEntry]) -> list[str]:
    """Render the orphan-candidates table (or a 'no orphans' notice)."""
    orphans = [e for e in entries if not e.adopted]
    if not orphans:
        return ["## Orphans", "", "No outstanding orphan candidates — all entries adopted.", ""]
    lines = [
        "## Orphans",
        "",
        "| Name | Priority | Added | Age | Status |",
        "| --- | --- | --- | --- | --- |",
    ]
    for entry in orphans:
        status = "stale" if entry.stale else "fresh"
        lines.append(
            f"| {_md_escape(entry.name)} | {_format_priority(entry.priority)} | "
            f"{_format_added(entry.added)} | {_format_age(entry.age_days)} | {status} |"
        )
    lines.append("")
    return lines


def _render_adopted_table(entries: Iterable[AuditEntry]) -> list[str]:
    """Render the adopted-candidates table."""
    adopted = [e for e in entries if e.adopted]
    if not adopted:
        return []
    lines = [
        "## Adopted",
        "",
        "| Name | Priority | Matched station |",
        "| --- | --- | --- |",
    ]
    for entry in adopted:
        matched = entry.matched_station or ""
        lines.append(
            f"| {_md_escape(entry.name)} | {_format_priority(entry.priority)} | {_md_escape(matched)} |"
        )
    lines.append("")
    return lines


def render_markdown(report: AuditReport, *, reference_date: date) -> str:
    """Render an :class:`AuditReport` as a Markdown document.

    Args:
        report: The audit result to serialise.
        reference_date: The "today" the audit was computed against —
            shown in the document header.

    Returns:
        Markdown string with summary, priority coverage, orphan listing
        and adopted listing.
    """
    if report.total == 0:
        return "\n".join(
            [
                "# Pendler Candidates Audit",
                "",
                f"Reference date: {reference_date.isoformat()}",
                "",
                "No candidates configured.",
                "",
            ]
        )

    parts: list[str] = []
    parts.extend(_render_summary(report, reference_date=reference_date))
    parts.extend(_render_priority_table(report.priority_coverage))
    parts.extend(_render_orphans_table(report.entries))
    parts.extend(_render_adopted_table(report.entries))
    return "\n".join(parts)


__all__ = [
    "MAX_STALE_DAYS_CAP",
    "AuditEntry",
    "AuditReport",
    "Candidate",
    "PriorityCoverage",
    "audit_pendler_candidates",
    "cap_stale_days",
    "load_candidates",
    "load_pendler_station_keys",
    "render_markdown",
]
