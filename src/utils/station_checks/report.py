"""Station validation: the Markdown report (``ValidationReport``).

Moved verbatim from ``src/utils/stations_validation.py`` (700-line rule, 2026-11-02).
"""

from __future__ import annotations

from dataclasses import dataclass

from src.utils.liquid import liquid_safe
from src.utils.station_checks.issues import (
    AliasCollisionIssue,
    AliasIssue,
    CoordinateIssue,
    CrossNameAliasIssue,
    CrossStationIDIssue,
    DuplicateGroup,
    GTFSIssue,
    IdentityFieldConflict,
    NameOwnershipIssue,
    NamingIssue,
    ProviderIssue,
    SecurityIssue,
)
from src.utils.text import escape_markdown, normalise_markdown_text


# Per-cell length cap applied at every ``stations.json``-derived
# Markdown sink in :meth:`ValidationReport.to_markdown`. Mirrors the
# canonical ``_DASHBOARD_FIELD_MAX_LEN`` constant in
# ``scripts/generate_markdown_stats.py``: the report renders bullet
# lines that already include identifier + name + reason from the same
# row; an additional render-side cap keeps the layout legible even if
# a future writer persists multi-KiB blobs to the source field.
_REPORT_FIELD_MAX_LEN = 400


def _safe_md(text: object) -> str:
    """Render ``text`` for inclusion as bullet body in the validation report.

    Composes :func:`normalise_markdown_text` (strips C0/C1 controls +
    Trojan-Source / line-terminator union + ZWSP family + BiDi marks,
    collapses whitespace, caps length) with :func:`escape_markdown`
    (HTML-escapes + backslash-escapes the CommonMark structural set
    ``[]()*_`@<>``). Mirrors the canonical defence pattern applied at
    every other Markdown-renderer boundary in the project — see the
    module-level threat model in :meth:`ValidationReport.to_markdown`.
    """
    return escape_markdown(
        normalise_markdown_text(str(text), max_len=_REPORT_FIELD_MAX_LEN)
    )


@dataclass(frozen=True)
class ValidationReport:
    """Summary returned by :func:`validate_stations`."""

    total_stations: int
    duplicates: tuple[DuplicateGroup, ...]
    alias_issues: tuple[AliasIssue, ...]
    coordinate_issues: tuple[CoordinateIssue, ...]
    gtfs_issues: tuple[GTFSIssue, ...]
    security_issues: tuple[SecurityIssue, ...]
    cross_station_id_issues: tuple[CrossStationIDIssue, ...]
    provider_issues: tuple[ProviderIssue, ...]
    naming_issues: tuple[NamingIssue, ...]
    gtfs_stop_count: int
    # Defaulted last so existing call sites that built ``ValidationReport``
    # positionally before the 2026-05-16 PR #1539 addition keep working
    # — adopted by the test fixtures + ``scripts/update_all_stations.py``
    # auto-quarantine path.
    identity_field_conflicts: tuple[IdentityFieldConflict, ...] = ()
    # Defaulted (later addition) so the existing keyword/``**defaults``
    # constructors in the test suite keep working without the new key —
    # same rationale as ``identity_field_conflicts`` above.
    cross_name_alias_issues: tuple[CrossNameAliasIssue, ...] = ()
    alias_collision_issues: tuple[AliasCollisionIssue, ...] = ()
    name_ownership_issues: tuple[NameOwnershipIssue, ...] = ()

    @property
    def has_issues(self) -> bool:
        return bool(
            self.duplicates
            or self.alias_issues
            or self.coordinate_issues
            or self.gtfs_issues
            or self.security_issues
            or self.cross_station_id_issues
            or self.provider_issues
            or self.naming_issues
            or self.identity_field_conflicts
            or self.cross_name_alias_issues
            or self.alias_collision_issues
            or self.name_ownership_issues
        )

    def to_markdown(self) -> str:
        # Security (Markdown injection at the public-artefact renderer
        # boundary): every text field interpolated below carries
        # operator-controlled data sourced from ``stations.json`` — which
        # is in turn populated by cron-driven scripts that fan out to
        # external API surfaces (VOR/OEBB/Wiener Linien/Google Places/
        # OSM Overpass). A compromised upstream / DNS-hijack / MITM (or
        # any future fetch path that does not pin the host) injects
        # arbitrary ``name`` / ``bst_code`` / ``vor_id`` / ``alias``
        # bytes that flow VERBATIM into ``docs/stations_validation_
        # report.md`` — auto-committed by the ``update-stations.yml``
        # cron workflow and the ``manual-full-refresh.yml`` workflow,
        # then rendered on github.com (and every operator's IDE /
        # Markdown viewer / GitHub Pages mirror). The canonical
        # ``escape_markdown`` + ``normalise_markdown_text`` defence pair
        # mirrors the renderer-boundary pattern applied in
        # ``scripts/generate_markdown_stats.py`` and
        # ``src/feed/reporting.py`` (2026-05-09 Markdown Injection Drift
        # rounds). ``_safe_md`` (module-level helper) strips C0/C1 controls
        # + Trojan-Source / line-terminator union + ZWSP family + BiDi
        # marks (via ``normalise_markdown_text``), then backslash-
        # escapes the CommonMark structural set ``[]()*_\`@<>`` and
        # HTML-escapes ``&<>"'`` (via ``escape_markdown``). Layered
        # defence: the upstream ``_collect_blocking_issues`` gate aborts
        # the ``update_all_stations.py`` commit when ``_UNSAFE_CHARS_RE``
        # fires on ``<>`` / ASCII C0 / BiDi, but that gate is ONLY
        # active in the orchestrator script — the standalone CLI
        # invocation (``python -m src.cli stations validate``), the
        # ``manual-full-refresh.yml`` workflow's regenerate-step, and
        # any future direct ``to_markdown()`` caller bypass it
        # entirely. Sanitising at THIS boundary closes every code path
        # in one cut.
        lines = ["# Stationsverzeichnis-Validierungsbericht", ""]
        lines.append(f"*Analysierte Stationen (gesamt)*: {self.total_stations}")
        lines.append(f"*Geladene GTFS-Stops*: {self.gtfs_stop_count}")
        lines.append(f"*Geografische Duplikate*: {len(self.duplicates)}")
        lines.append(f"*Alias-Probleme*: {len(self.alias_issues)}")
        lines.append(f"*Koordinaten-Anomalien*: {len(self.coordinate_issues)}")
        lines.append(f"*GTFS-Abweichungen*: {len(self.gtfs_issues)}")
        lines.append(f"*Sicherheitswarnungen*: {len(self.security_issues)}")
        lines.append(f"*Provider-Probleme*: {len(self.provider_issues)}")
        lines.append(f"*Stationsübergreifende-ID-Kollisionen*: {len(self.cross_station_id_issues)}")
        lines.append(f"*Identity-Field-Konflikte*: {len(self.identity_field_conflicts)}")
        lines.append(f"*Namens-Probleme*: {len(self.naming_issues)}")
        lines.append(f"*Namens-Alias-Kollisionen*: {len(self.cross_name_alias_issues)}")
        lines.append(f"*Alias-Schl\u00fcssel-Kollisionen*: {len(self.alias_collision_issues)}")
        lines.append(
            f"*Namens-Zuordnungskonflikte*: {len(self.name_ownership_issues)}"
        )
        lines.append("")

        if self.security_issues:
            lines.append("## Sicherheitswarnungen (potenzielle XSS/Injection)")
            for sec in self.security_issues:
                lines.append(
                    f"- {_safe_md(sec.identifier)} ({_safe_md(sec.name)}): {_safe_md(sec.reason)}"
                )
            lines.append("")

        if self.provider_issues:
            lines.append("## Provider-Probleme (VOR/OEBB)")
            for prov in self.provider_issues:
                lines.append(
                    f"- {_safe_md(prov.identifier)} ({_safe_md(prov.name)}): {_safe_md(prov.reason)}"
                )
            lines.append("")

        if self.cross_station_id_issues:
            lines.append("## Stationsübergreifende-ID-Kollisionen")
            for cross in self.cross_station_id_issues:
                lines.append(
                    f"- {_safe_md(cross.identifier)} ({_safe_md(cross.name)}): "
                    f"Alias '{_safe_md(cross.alias)}' kollidiert mit "
                    f"{_safe_md(cross.colliding_field)} von "
                    f"{_safe_md(cross.colliding_identifier)} ({_safe_md(cross.colliding_name)})"
                )
            lines.append("")

        lines.extend(self._render_identity_field_conflicts())

        if self.duplicates:
            lines.append("## Geografische Duplikate")
            for group in self.duplicates:
                joined_ids = ", ".join(_safe_md(ident) for ident in group.identifiers)
                lines.append(
                    f"- ({group.latitude:.5f}, {group.longitude:.5f}) → {joined_ids}"
                )
            lines.append("")

        if self.alias_issues:
            lines.append("## Alias-Probleme")
            for ali in self.alias_issues:
                lines.append(
                    f"- {_safe_md(ali.identifier)} ({_safe_md(ali.name)}): {_safe_md(ali.reason)}"
                )
            lines.append("")

        if self.coordinate_issues:
            lines.append("## Koordinaten-Anomalien")
            for coord in self.coordinate_issues:
                lines.append(
                    f"- {_safe_md(coord.identifier)} ({_safe_md(coord.name)}): {_safe_md(coord.reason)}"
                )
            lines.append("")

        if self.gtfs_issues:
            lines.append("## GTFS-Abweichungen")
            for gtfs in self.gtfs_issues:
                lines.append(
                    f"- {_safe_md(gtfs.identifier)} ({_safe_md(gtfs.name)}) → "
                    f"fehlende stop_id {_safe_md(gtfs.vor_id)}"
                )
            lines.append("")

        if self.naming_issues:
            lines.append("## Namens-Probleme")
            for naming in self.naming_issues:
                lines.append(
                    f"- {_safe_md(naming.identifier)} ({_safe_md(naming.name)}): {_safe_md(naming.reason)}"
                )
            lines.append("")

        lines.extend(self._render_cross_name_alias_issues())
        lines.extend(self._render_alias_collision_issues())
        lines.extend(self._render_name_ownership_issues())

        if not self.has_issues:
            lines.append("Keine Probleme festgestellt.")

        # GitHub Pages runs Liquid over docs/stations_validation_report.md: a
        # ``{%`` in a station name would stop the Pages build, a ``{{ … }}``
        # would vanish (src/utils/liquid.py, review of 2026-11-01).
        return liquid_safe("\n".join(lines).rstrip() + "\n")

    def _render_identity_field_conflicts(self) -> list[str]:
        """Render the ``## Identity-Field-Konflikte`` section.

        Split out of :meth:`to_markdown` so the parent method stays
        below the C901 complexity baseline (15) — adding the section
        inline pushed it from 18 to 20, above the project floor.
        """
        if not self.identity_field_conflicts:
            return []
        lines = ["## Identity-Field-Konflikte"]
        for conflict in self.identity_field_conflicts:
            joined_ids = ", ".join(_safe_md(ident) for ident in conflict.identifiers)
            joined_names = ", ".join(_safe_md(name) for name in conflict.names)
            lines.append(
                f"- {_safe_md(conflict.field)}={_safe_md(conflict.value)} "
                f"gemeinsam genutzt von [{joined_ids}] ({joined_names})"
            )
        lines.append("")
        return lines

    def _render_alias_collision_issues(self) -> list[str]:
        """Render the ``## Alias-Schlüssel-Kollisionen`` section.

        Split out like its two siblings so :meth:`to_markdown` keeps its
        C901 baseline instead of gaining a branch per section.
        """
        if not self.alias_collision_issues:
            return []
        lines = ["## Alias-Schlüssel-Kollisionen"]
        for issue in self.alias_collision_issues:
            joined_ids = ", ".join(_safe_md(ident) for ident in issue.identifiers)
            joined_names = ", ".join(_safe_md(name) for name in issue.names)
            joined_keys = ", ".join(_safe_md(key) for key in issue.alias_keys)
            lines.append(
                f"- {joined_keys}: {_safe_md(issue.reason)} "
                f"— [{joined_ids}] ({joined_names})"
            )
        lines.append("")
        return lines

    def _render_name_ownership_issues(self) -> list[str]:
        """Render the ``## Namens-Zuordnungskonflikte`` section.

        Split out like its three siblings so :meth:`to_markdown` keeps its
        C901 baseline of 18 instead of gaining a branch per section.
        """
        if not self.name_ownership_issues:
            return []
        lines = ["## Namens-Zuordnungskonflikte"]
        for issue in self.name_ownership_issues:
            joined_owners = ", ".join(
                _safe_md(ident) for ident in issue.owner_identifiers
            )
            joined_stops = ", ".join(
                _safe_md(stop) for stop in issue.winner_stop_names
            ) or "—"
            lines.append(
                f"- {_safe_md(issue.name)}: Nachschlag landet auf "
                f"{_safe_md(issue.winner_identifier)} (Haltestellen: "
                f"{joined_stops}), den Namen tr\u00e4gt aber "
                f"[{joined_owners}]"
            )
        lines.append("")
        return lines

    def _render_cross_name_alias_issues(self) -> list[str]:
        """Render the ``## Namens-Alias-Kollisionen`` section.

        Split out of :meth:`to_markdown` (like
        :meth:`_render_identity_field_conflicts`) so the parent method
        stays at its C901 baseline of 18 rather than gaining a branch.
        """
        if not self.cross_name_alias_issues:
            return []
        lines = ["## Namens-Alias-Kollisionen"]
        for issue in self.cross_name_alias_issues:
            lines.append(
                f"- {_safe_md(issue.identifier)} ({_safe_md(issue.name)}): "
                f"{issue.label_kind} '{_safe_md(issue.label)}' entspricht dem "
                f"Namen der entfernten Station {_safe_md(issue.colliding_name)} "
                f"(~{issue.distance_m} m)"
            )
        lines.append("")
        return lines
