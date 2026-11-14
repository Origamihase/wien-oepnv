"""The yearly Baustellen review: prompt, guard and stored text (``src/markdown_stats/year_review.py``).

Owner's decision of 2026-11-10 (Michael), point 4: Gemini summarises the
year's Baustellen, his exception to "an AI only watches and reports, never
rewrites". What makes the exception safe to publish is the guard, so every
rule of it is pinned here with a text it accepts and one it rejects, and
every way :func:`ensure_review` can end is pinned as an outcome, never an
exception.

The tests are split by topic: ``test_year_review_guard.py`` (the rules a to
g), ``test_year_review_cleaning.py`` (``clean_review``),
``test_year_review_prompt.py`` and ``test_year_review_stored.py``
(:func:`ensure_review` and the file). This module holds what they and
``test_year_review_more.py`` / ``test_year_review_rules.py`` share: the four
sites, the accepted text and the helpers that check a text or ask for a
review.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path

from src.markdown_stats import year_review as yr
from src.markdown_stats.baustellen_year import BaustelleSite
from src.markdown_stats.review_client import ReviewAnswer
from src.utils.stats import VIENNA_TZ

__all__ = ["GOOD", "NOW", "REPO", "SITES", "YEAR", "_answer", "_check", "_ensure", "_never", "_site", "_with"]

YEAR = 2026
REPO = Path(__file__).resolve().parents[1]
NOW = datetime(2027, 1, 1, 0, 1, 30, tzinfo=VIENNA_TZ)


def _site(title: str, starts: date | None, ends: date | None, district: str, measure: str, *descriptions: str) -> BaustelleSite:
    return BaustelleSite(title, starts, ends, district, measure, descriptions, datetime(2026, 5, 24, 0, 52, tzinfo=VIENNA_TZ))


SITES = [
    _site("Vordere Zollamtsstraße von Marxergasse bis Radetzkybrücke", date(2026, 2, 1), None, "3", "Gleisbau",
          "Aufgrund der Sanierung der Tunneldecke der U4-Trasse wird die Fahrtrichtung zum Heumarkt umgeleitet."),
    _site("Eßlinger Hauptstraße 96", date(2026, 5, 25), date(2026, 9, 6), "22", "Rohrlegung",
          "Der Busverkehr der Linie 26A wird über den Kreisverkehr bei der Gartenheimstraße umgeleitet. "
          "Die Arbeiten erfolgen in der Zeit von 20:00 Uhr bis 05:00 Uhr."),
    _site("Burggasse 67", date(2026, 7, 28), date(2026, 10, 30), "7", "Straßenbau",
          "Die Haltestelle des betroffenen öffentlichen Verkehrsmittels wird von Burggasse ONr.67 nach "
          "Burggasse ONr. 69 verlegt."),
    _site("Rennweg von 33A bis 37", date(2026, 9, 30), date(2026, 11, 15), "3", "Gleisbau",
          "Der linke Fahrstreifen wird gesperrt. Die Linie 71 wird über die Wiedner Hauptstraße umgeleitet."),
]

GOOD = (
    "Im Jahr 2026 sind 4 Baustellen mit Folgen für den öffentlichen Verkehr erfasst. "
    "Zwei davon lagen im 3. Bezirk (Landstraße), je eine in Neubau und in der Donaustadt. "
    "In der Burggasse wurde eine Haltestelle verlegt, in der Eßlinger Hauptstraße wurde der Busverkehr "
    "der Linie 26A umgeleitet. "
    "Am Rennweg wurde ab 30.09.2026 an den Gleisen gearbeitet, die Linie 71 fuhr über die Wiedner Hauptstraße. "
    "Die Arbeiten an der U4-Trasse begannen im Februar."
)


def _check(text: str, sites: list[BaustelleSite] | None = None) -> list[str]:
    return yr.check_review(text, SITES if sites is None else sites, year=YEAR)


def _with(sentence: str) -> str:
    """The accepted text with one more sentence."""
    return f"{GOOD} {sentence}"


def _answer(text: str = GOOD, model: str = "gemini-3.8-flash") -> Callable[[str], ReviewAnswer | None]:
    return lambda prompt: ReviewAnswer(text=text, model=model)


def _never(prompt: str) -> ReviewAnswer | None:
    raise AssertionError("Gemini must not be asked")


def _ensure(tmp_path: Path, ask: Callable[[str], ReviewAnswer | None], sites: list[BaustelleSite] | None = None,
            key: str | None = "k") -> yr.ReviewOutcome:
    return yr.ensure_review(YEAR, SITES if sites is None else sites, now=NOW, stats_dir=tmp_path, key=key, ask=ask)
