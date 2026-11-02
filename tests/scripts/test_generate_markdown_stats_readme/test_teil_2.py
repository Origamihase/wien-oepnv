"""Teil 2 der Tests aus tests/scripts/test_generate_markdown_stats_readme.py.

Beschreibung in __init__.py.
"""

from __future__ import annotations

from pathlib import Path


from scripts import generate_markdown_stats as script  # noqa: E402

from ._support import (
    _readme_with_markers,
)


def test_main_loads_previous_year_when_cutoff_crosses_january(
    tmp_path: Path,
) -> None:
    """30-day window in early January must include December rows.

    Otherwise the README Stammstrecke snapshot silently empties out
    every January, which would be a confusing UX bug for users opening
    the repo on New Year's Day.
    """
    stats_dir = tmp_path / "stats"
    stats_dir.mkdir(parents=True, exist_ok=True)
    (stats_dir / "stammstrecke_2025.csv").write_text(
        "timestamp,weekday,hour,direction,delay_minutes\n"
        "2025-12-20T08:00:00+01:00,Sa,8,Wien Hbf->Floridsdorf,7.0\n",
        encoding="utf-8",
    )
    (stats_dir / "stammstrecke_2026.csv").write_text(
        "timestamp,weekday,hour,direction,delay_minutes\n"
        "2026-01-02T08:00:00+01:00,Fr,8,Wien Hbf->Floridsdorf,3.0\n",
        encoding="utf-8",
    )
    readme = tmp_path / "README.md"
    readme.write_text(_readme_with_markers(), encoding="utf-8")
    output = tmp_path / "statistik.md"

    rc = script.main(
        [
            "--year",
            "2026",
            "--stats-dir",
            str(stats_dir),
            "--output",
            str(output),
            "--readme-path",
            str(readme),
            "--readme-window-days",
            "30",
            "--now-iso",
            "2026-01-05T12:00:00+01:00",
        ]
    )
    assert rc == 0
    new_text = readme.read_text(encoding="utf-8")
    # Both years' rows survive the 30-day filter.
    assert "| Beobachtungen (gesamt) | 2 |" in new_text
