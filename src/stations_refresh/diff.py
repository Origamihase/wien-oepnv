"""Before/after diff and Markdown diff report for the station refresh.

Moved verbatim from ``scripts/update_all_stations.py``.
"""
from __future__ import annotations

from typing import Any, TypedDict
from collections.abc import Mapping, Sequence

from src.utils.liquid import liquid_safe
from src.utils.geo import haversine_m
from src.utils.text import (
    escape_markdown,
    normalise_markdown_text,
    safe_markdown_codespan,
)

_COORD_SHIFT_THRESHOLD_M = 100.0


def _station_key(entry: Mapping[str, Any]) -> str:
    """Stable identity key for diff matching: bst_id, else wl_diva, else name.

    WL-only entries carry no bst_id, and their names are not unique (several
    DIVAs share a display name, e.g. two "Wien Kirchengasse (WL)"). Keying
    them by name collapsed such twins into one dict slot, so a WL rename of
    one DIVA showed up as a large coordinate shift instead of a rename.
    """
    bst_id = entry.get("bst_id")
    if bst_id is not None and str(bst_id).strip():
        return f"bst:{str(bst_id).strip()}"
    wl_diva = entry.get("wl_diva")
    if wl_diva is not None and str(wl_diva).strip():
        return f"diva:{str(wl_diva).strip()}"
    name = str(entry.get("name") or "<unnamed>").strip()
    return f"name:{name}"


class _DiffResult(TypedDict):
    added: list[tuple[str, str]]
    removed: list[tuple[str, str]]
    renamed: list[tuple[str, str, str]]
    coord_shifted: list[tuple[str, str, int]]


def _coerce_coord(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    return None


def _compute_diff(
    before: Sequence[Mapping[str, Any]],
    after: Sequence[Mapping[str, Any]],
) -> _DiffResult:
    """Compute additions/removals/renames/coord-shifts between two snapshots."""
    by_key_before = {_station_key(s): s for s in before}
    by_key_after = {_station_key(s): s for s in after}

    added_keys = sorted(set(by_key_after) - set(by_key_before))
    removed_keys = sorted(set(by_key_before) - set(by_key_after))

    renamed: list[tuple[str, str, str]] = []
    coord_shifted: list[tuple[str, str, int]] = []

    for key in sorted(set(by_key_before) & set(by_key_after)):
        before_entry = by_key_before[key]
        after_entry = by_key_after[key]

        before_name = str(before_entry.get("name") or "")
        after_name = str(after_entry.get("name") or "")
        if before_name != after_name:
            renamed.append((key, before_name, after_name))

        before_lat = _coerce_coord(before_entry.get("latitude"))
        before_lon = _coerce_coord(before_entry.get("longitude"))
        after_lat = _coerce_coord(after_entry.get("latitude"))
        after_lon = _coerce_coord(after_entry.get("longitude"))
        if (
            before_lat is None
            or before_lon is None
            or after_lat is None
            or after_lon is None
        ):
            continue
        try:
            distance = haversine_m(before_lat, before_lon, after_lat, after_lon)
        except (TypeError, ValueError):
            continue
        if distance >= _COORD_SHIFT_THRESHOLD_M:
            coord_shifted.append((key, after_name or before_name, round(distance)))

    return _DiffResult(
        added=[(k, str(by_key_after[k].get("name") or "")) for k in added_keys],
        removed=[(k, str(by_key_before[k].get("name") or "")) for k in removed_keys],
        renamed=renamed,
        coord_shifted=coord_shifted,
    )


def _render_diff_markdown(
    diff: _DiffResult,
    before_count: int,
    after_count: int,
    timestamp: str,
) -> str:
    # Security (Trojan-Source / BiDi-Mark Drift Round 9): station names
    # in ``diff["*"]`` come from the unsanitised pre-/post-merge
    # snapshots. Any name carrying a CVE-2021-42574 BiDi control
    # (U+202A-U+202E / U+2066-U+2069), a zero-width primitive
    # (U+200B-U+200F), a Unicode line / paragraph separator
    # (U+2028-U+2029), the BOM (U+FEFF), or an 8-bit C1 terminal-escape
    # byte (\\x9b CSI / \\x9d OSC / \\x90 DCS) would otherwise reach the
    # ``docs/stations_diff.md`` body verbatim. That artefact is
    # committed by the cron pipeline (``update-cycle.yml``) and
    # rendered on GitHub Pages — GitHub's Markdown renderer honours
    # BiDi formatting characters, so a malicious station name turns the
    # public diff page into a Trojan-Source viewer attack. Route every
    # interpolated name through the canonical Markdown sanitiser pair
    # (``normalise_markdown_text`` strips the unsafe union;
    # ``escape_markdown`` then escapes the surviving Markdown
    # metacharacters) and route every code-span identifier through
    # ``safe_markdown_codespan`` so the ``name:<raw>`` key form is
    # also normalised. Mirrors the canonical pattern pinned by the
    # 2026-05-09 ``Markdown Injection Drift Round 3`` entry for
    # ``ValidationReport.to_markdown()``. GitHub Pages also runs Liquid
    # over the page: a ``{%`` in a name stops the Pages build, a
    # ``{{ … }}`` vanishes, so the finished page goes through
    # ``liquid_safe`` (src/utils/liquid.py, review of 2026-11-01).
    lines = [
        "# stations.json — Diff-Bericht",
        "",
        f"_Erzeugt am: {timestamp}_",
        "",
        f"**Stationen: {before_count} → {after_count} (Δ {after_count - before_count:+d})**",
        "",
    ]

    def section(title: str, items: list[Any], formatter: Any) -> None:
        lines.append(f"## {title} ({len(items)})")
        lines.append("")
        if items:
            for item in items:
                lines.append(formatter(item))
        else:
            lines.append("_Keine._")
        lines.append("")

    def _safe_key(raw: str) -> str:
        return safe_markdown_codespan(raw)

    def _safe_name(raw: str) -> str:
        return escape_markdown(normalise_markdown_text(raw))

    section(
        "Hinzugefügt",
        diff["added"],
        lambda it: f"- `{_safe_key(it[0])}` — {_safe_name(it[1])}",
    )
    section(
        "Entfernt",
        diff["removed"],
        lambda it: f"- `{_safe_key(it[0])}` — {_safe_name(it[1])}",
    )
    section(
        "Umbenannt",
        diff["renamed"],
        lambda it: f'- `{_safe_key(it[0])}`: "{_safe_name(it[1])}" → "{_safe_name(it[2])}"',
    )
    section(
        f"Koordinaten verschoben (≥ {int(_COORD_SHIFT_THRESHOLD_M)} m)",
        diff["coord_shifted"],
        lambda it: f"- `{_safe_key(it[0])}` — {_safe_name(it[1])} ({it[2]} m)",
    )

    return liquid_safe("\n".join(lines).rstrip() + "\n")
