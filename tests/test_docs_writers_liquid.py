"""Every script that writes a page under ``docs/`` makes Liquid pairs inert.

Bot commits reach GitHub Pages without a test run, so the text a script
takes from a source has to be safe when it is written. The review of
2026-11-01 reproduced a failed Pages build with a station name
``Wien Mitte {% Ersatzhaltestelle`` in ``docs/stations_diff.md`` and a
silently changed cell with a VAO line name ``S{{1}}`` in
``docs/statistik.md``; the KI-Wächter's old guard let ``{{{`` through.
``scripts/publish_to_main.sh`` catches what a writer misses (step 2b).
"""

from __future__ import annotations

from pathlib import Path

from scripts import generate_markdown_stats as stats
from scripts import update_all_stations as stations
from src.utils.liquid import liquid_findings
from src.utils.stations_validation import NamingIssue, SecurityIssue, ValidationReport

_HOSTILE = ("Wien Mitte {% Ersatzhaltestelle", "Schwedenplatz {{ Steg }}", "S{{{1}}}")


def test_statistik_page_is_written_inert(tmp_path: Path) -> None:
    target = tmp_path / "statistik.md"
    stats.write_dashboard("| Linie | Ausfälle |\n| --- | --- |\n" + "".join(f"| {n} | 3 |\n" for n in _HOSTILE), output_path=target)
    assert liquid_findings(target.read_text(encoding="utf-8")) == []


def test_stations_diff_page_is_inert() -> None:
    diff: stations._DiffResult = {
        "added": [(f"bst:{i}", name) for i, name in enumerate(_HOSTILE)],
        "removed": [],
        "renamed": [("bst:9", "Alt {% x", "Neu {{ y }}")],
        "coord_shifted": [],
    }
    rendered = stations._render_diff_markdown(diff, 1, 4, "2026-11-01T00:00:00+00:00")
    assert liquid_findings(rendered) == []
    assert "Ersatzhaltestelle" in rendered


def test_stations_validation_report_is_inert() -> None:
    report = ValidationReport(
        total_stations=1,
        duplicates=(),
        alias_issues=(),
        coordinate_issues=(),
        gtfs_issues=(),
        security_issues=(SecurityIssue(identifier="bst:1", name=_HOSTILE[0], reason="x {% y"),),
        cross_station_id_issues=(),
        provider_issues=(),
        naming_issues=(NamingIssue(identifier="bst:2", name=_HOSTILE[1], reason=_HOSTILE[2]),),
        gtfs_stop_count=0,
    )
    markdown = report.to_markdown()
    assert liquid_findings(markdown) == []
    assert "Ersatzhaltestelle" in markdown
