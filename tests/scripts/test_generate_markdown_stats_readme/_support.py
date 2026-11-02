"""Hilfen und Fixtures für die Tests in diesem Paket.

Früher in tests/scripts/test_generate_markdown_stats_readme.py.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


from scripts import generate_markdown_stats as script  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[3]

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

VIENNA_TZ = ZoneInfo("Europe/Vienna")

NOW = datetime(2026, 5, 9, 12, 0, tzinfo=VIENNA_TZ)


def _make_stam(
    delay_minutes: float,
    *,
    timestamp: datetime,
    direction: str = "Wien Hbf -> Floridsdorf",
) -> script.StammstreckeRow:
    return script.StammstreckeRow(
        timestamp=timestamp,
        weekday="Mo",
        hour=timestamp.hour,
        direction=direction,
        delay_minutes=delay_minutes,
    )


def _readme_with_markers(extra: str = "") -> str:
    """Return a minimal README scaffold containing the Stammstrecke marker pair.

    The surrounding text is intentionally non-trivial so a flawed
    patcher that rewrites more than the marker contents shows up as a
    diff in the post-condition assertions.
    """
    return (
        "# Wien ÖPNV Feed\n"
        "\n"
        "Some hand-authored intro paragraph that must not be touched.\n"
        "\n"
        "<!-- STATS:STAMMSTRECKE:BEGIN -->\n"
        "_Platzhalter Stammstrecke._\n"
        "<!-- STATS:STAMMSTRECKE:END -->\n"
        "\n"
        f"Trailing user-authored content. {extra}\n"
    )


# ---- main() smoke tests ---------------------------------------------------


def _seed_csvs(stats_dir: Path, *, year: int = 2026) -> None:
    """Write the minimum two CSVs ``main()`` needs to render a non-empty
    dashboard.
    """
    stats_dir.mkdir(parents=True, exist_ok=True)
    (stats_dir / f"stammstrecke_{year}.csv").write_text(
        ",".join(("timestamp", "weekday", "hour", "direction", "delay_minutes"))
        + "\n"
        + "2026-05-09T08:00:00+02:00,Sa,8,Wien Hbf->Floridsdorf,12.0\n"
        + "2026-05-09T08:30:00+02:00,Sa,8,Wien Hbf->Floridsdorf,5.0\n",
        encoding="utf-8",
    )
    (stats_dir / f"stoerungen_{year}.csv").write_text(
        ",".join(("timestamp", "weekday", "hour", "provider", "location_name"))
        + "\n"
        + "2026-05-09T08:00:00+02:00,Sa,8,wl,Wien Hbf\n"
        + "2026-05-09T08:30:00+02:00,Sa,8,wl,Wien Hbf\n"
        + "2026-05-09T09:00:00+02:00,Sa,9,oebb,Floridsdorf\n",
        encoding="utf-8",
    )


def _seed_ausfall_csv(stats_dir: Path, *, year: int = 2026) -> None:
    """Write a minimal ``ausfaelle_<year>.csv`` to exercise the
    cancellation rendering path.
    """
    stats_dir.mkdir(parents=True, exist_ok=True)
    (stats_dir / f"ausfaelle_{year}.csv").write_text(
        ",".join(("timestamp", "weekday", "hour", "direction", "line"))
        + "\n"
        + "2026-05-09T08:00:00+02:00,Sa,8,Meidling,S1\n"
        + "2026-05-09T08:30:00+02:00,Sa,8,Praterstern,S2\n",
        encoding="utf-8",
    )


def _readme_with_ausfaelle_markers() -> str:
    """README scaffold with all four marker pairs the script emits.

    The Ausfälle markers are unconditionally emitted by ``main()`` so
    operators can distinguish "stable service" (0) from "data missing"
    (placeholder). A README missing the Ausfälle markers logs a
    warning but does not fail the dashboard regeneration.
    """
    return (
        "# Wien ÖPNV Feed\n"
        "\n"
        "Some hand-authored intro paragraph that must not be touched.\n"
        "\n"
        "<!-- STATS:STAMMSTRECKE_LIVE:BEGIN -->\n"
        "_Platzhalter Stammstrecke LIVE._\n"
        "<!-- STATS:STAMMSTRECKE_LIVE:END -->\n"
        "\n"
        "<!-- STATS:STAMMSTRECKE:BEGIN -->\n"
        "_Platzhalter Stammstrecke._\n"
        "<!-- STATS:STAMMSTRECKE:END -->\n"
        "\n"
        "<!-- STATS:AUSFAELLE_LIVE:BEGIN -->\n"
        "_Platzhalter Ausfälle LIVE._\n"
        "<!-- STATS:AUSFAELLE_LIVE:END -->\n"
        "\n"
        "<!-- STATS:AUSFAELLE:BEGIN -->\n"
        "_Platzhalter Ausfälle._\n"
        "<!-- STATS:AUSFAELLE:END -->\n"
        "\n"
        "Trailing user-authored content.\n"
    )


__all__ = [
    "NOW",
    "REPO_ROOT",
    "VIENNA_TZ",
    "_make_stam",
    "_readme_with_ausfaelle_markers",
    "_readme_with_markers",
    "_seed_ausfall_csv",
    "_seed_csvs",
]
