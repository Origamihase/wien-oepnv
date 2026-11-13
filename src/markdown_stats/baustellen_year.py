"""The Baustellen section of a finished year's page, read from the ledger.

Owner's decision (Michael, 2026-11-10 17:44 Europe/Vienna), point 4: "The
year's Baustellen (roadworks) are summarised by Gemini." The summary in
words is the second half of that (``src/markdown_stats/year_review.py``);
this module is the first half and the fallback: the count and the list of
the year's sites come straight from ``data/stats/baustellen_<Jahr>.csv``
and stand on the page whether or not a review exists.

The ledger holds one row per guid of the Stadt Wien data. That guid is a
hash of title, start and end (``src/providers/baustellen_events.py``), so
a site whose end was moved appears under a new guid; the 2026 ledger
holds 61 rows for 19 sites. :func:`load_sites` therefore merges the rows
of one title into one site and shows the period the city named last
(review of 2026-11-10: the city also moves an end earlier, postpones a
site past its old end, and the same address can be worked on twice);
:func:`_sites_of_title` says when two rows of one title are two sites.

Licence: the texts are the city's, CC BY 4.0, and appear edited
(``NOTICE.md``, "Generierte Feeds"): the section ends with the source, the
note on the editing and the licence link.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Final

from src.markdown_stats.baustellen_facts import (
    DISTRICT_NAMES, FIRST_LEDGER_YEAR, MEASURE_NOT_GIVEN, MEASURE_OTHER, MONTH_NAMES, _day, district_label,
    district_numbers, fact_counts, facts_block, months_touched, period_text, recorded_since,
)
from src.markdown_stats.baustellen_sites import (
    LEDGER_KIND, MAX_DESCRIPTIONS, BaustelleSite, _read_rows, forget_reported, load_sites,
)
from src.utils.text import escape_markdown_cell, normalise_markdown_text

# The reading of the ledger and the merge into sites stand in
# ``baustellen_sites.py``, district, period and counts in
# ``baustellen_facts.py``; this module holds the section and hands on every
# name another file imports or a test reads.
__all__ = [
    "DISTRICT_NAMES", "FIRST_LEDGER_YEAR", "LEDGER_KIND", "LICENCE_URL", "LIST_HEADING", "MAX_DESCRIPTIONS",
    "MAX_LISTED", "MEASURE_NOT_GIVEN", "MEASURE_OTHER", "MONTH_NAMES", "REVIEW_HEADING", "SECTION_HEADING",
    "SOURCE_ATTRIBUTION", "BaustelleSite", "_read_rows", "district_label", "district_numbers", "fact_counts",
    "facts_block", "forget_reported", "load_sites", "months_touched", "period_text", "recorded_since",
    "render_baustellen_section",
]

SECTION_HEADING: Final = "## Baustellen mit Folgen für den öffentlichen Verkehr"
REVIEW_HEADING: Final = "### Rückblick"
LIST_HEADING: Final = "### Baustellen im Jahr"
# Rows of the list. A docs page may hold 700 lines
# (tests/test_file_line_limit.py) and the year page carries five more
# sections; the 2026 ledger has 19 sites. The section says how many it
# left out.
MAX_LISTED: Final = 120
# The ledger writer caps title, district and measure at 200 characters
# (``_field`` in src/utils/stats_baustellen.py, through
# ``_CSV_TEXT_FIELD_MAX_LEN`` in src/utils/stats_sanitize.py); the same
# cap here cuts nothing the ledger holds and still bounds a hand-edited
# cell.
_CELL_MAX_LEN: Final = 200
# The guard accepts at most 1,500 characters (year_review.MAX_REVIEW_CHARS);
# the bound here only keeps a caller's unchecked text from filling the page.
_REVIEW_MAX_LEN: Final = 2000
LICENCE_URL: Final = "https://creativecommons.org/licenses/by/4.0/"
# NOTICE.md, table "Quelle": the attribution of the Stadt Wien data, verbatim.
SOURCE_ATTRIBUTION: Final = "Datenquelle: Stadt Wien – data.wien.gv.at"


# ---- the section ----------------------------------------------------------


def _cell(text: str) -> str:
    """Ledger text as a Markdown table cell; an empty one as a dash.

    A backtick becomes an apostrophe, as in ``safe_markdown_codespan``:
    kramdown pairs backticks before it reads the escapes, so two cells
    with one escaped backtick each ran together into one cell.
    """
    clean = normalise_markdown_text(text, max_len=_CELL_MAX_LEN).replace("`", "'")
    return escape_markdown_cell(clean) if clean else "–"


def _count_sentence(count: int, year: int) -> str:
    if count == 1:
        return f"Im Jahr {year} ist **eine** solche Baustelle erfasst."
    return f"Im Jahr {year} sind **{count}** solche Baustellen erfasst."


def _review_lines(review: str) -> list[str]:
    """The review in words with the line that says who wrote and who checked it.

    Escaped like a table cell: kramdown makes a table of a paragraph line
    with a bare "|". The guard rejects that character before a text is
    stored; this is the second line of defence for a caller's own text.
    """
    return [
        REVIEW_HEADING,
        "",
        escape_markdown_cell(normalise_markdown_text(review, max_len=_REVIEW_MAX_LEN)),
        "",
        (
            "_Dieser Rückblick wurde automatisch mit Gemini (Google) aus den Texten "
            "der Stadt Wien zusammengefasst und von einem festen Skript gegen diese "
            "Texte geprüft. Anzahl und Liste stammen direkt aus den Daten._"
        ),
        "",
    ]


def _list_lines(sites: Sequence[BaustelleSite]) -> list[str]:
    """The table of the sites, capped at :data:`MAX_LISTED` rows, the rest counted."""
    lines = [
        LIST_HEADING,
        "",
        "| Ort | Zeitraum | Bezirk | Maßnahme |",
        "| --- | --- | --- | --- |",
    ]
    for site in sites[:MAX_LISTED]:
        lines.append(
            f"| {_cell(site.title)} | {_cell(period_text(site))} "
            f"| {_cell(district_label(site.district))} | {_cell(site.measure)} |"
        )
    lines.append("")
    rest = len(sites) - MAX_LISTED
    if rest > 0:
        noun = "weitere Baustelle ist" if rest == 1 else "weitere Baustellen sind"
        lines += [
            f"_Die Liste zeigt die ersten {MAX_LISTED} nach Beginn; {rest} {noun} hier nicht aufgeführt._",
            "",
        ]
    return lines


def render_baustellen_section(
    sites: Sequence[BaustelleSite],
    *,
    year: int,
    review: str | None = None,
    pending: bool = False,
) -> list[str]:
    """The section "Baustellen mit Folgen für den öffentlichen Verkehr" as Markdown lines.

    Empty without *sites*: a year without ledger rows gets no section.
    Otherwise, in this order: what the section covers, how the sites are
    told apart and, for the year the ledger began in, since when they were
    recorded (:func:`recorded_since`), the count, the review in words when
    *review* is given (an accepted text of ``year_review``; labelled as
    written by Gemini and checked by script), or, with *pending* and no
    review, one line that a review follows, then the list of the sites and
    the attribution ``NOTICE.md`` asks for.

    The kinds of consequences are examples ("etwa"): the rule
    (``mentions_oepnv`` in ``src/providers/baustellen.py``) takes any
    sentence that names public transport as affected, and 3 of the 19
    sites of 2026 are listed for a suspended tram service alone. The
    sentences on names say how :func:`load_sites` counts: a changed period
    counts once, the same name again when it is entered after the period in
    force (the one shown) had ended, for a separate period
    (:func:`_sites_of_title`), and a site under two names twice; the city
    entered one site of 2026 under two titles, and nothing in the data
    says the two are one.

    Count and list never come from the model (owner's decision of
    2026-11-10, point 4): the deterministic part is always present and is
    the fallback.
    """
    if not sites:
        return []
    review_text = (review or "").strip()
    intro = (
        "Hier stehen die Baustellen aus den Daten der Stadt Wien, deren Text eine "
        "Folge für den öffentlichen Verkehr nennt, etwa eine verlegte oder aufgelassene "
        "Haltestelle, eine umgeleitete oder kurzgeführte Linie, einen eingestellten "
        "Straßenbahnbetrieb oder Ersatzbusse. Gezählt wird nach dem Namen, unter dem "
        "die Stadt Wien eine Baustelle führt. Ändert die Stadt den Zeitraum, bevor der "
        "alte zu Ende ist, gilt der neue; trägt sie denselben Namen erst nach dem Ende "
        "des geltenden Zeitraums für einen Zeitraum ein, der an diesen weder anschließt "
        "noch ihn überschneidet, zählt er noch einmal. Trägt sie eine Baustelle unter "
        "einem anderen Namen neu ein, zählt sie unter jedem Namen einmal."
    )
    since = recorded_since(sites, year)
    if since is not None:
        intro += f" Die Aufzeichnung für {year} beginnt am {_day(since)}."
    lines = [
        SECTION_HEADING,
        "",
        intro,
        "",
        _count_sentence(len(sites), year),
        "",
    ]
    if review_text:
        lines += _review_lines(review_text)
    elif pending:
        lines += ["_Ein kurzer Rückblick in Worten wird hier ergänzt._", ""]
    lines += _list_lines(sites)
    edited = (
        "gekürzt, vereinheitlicht und im Rückblick maschinell zusammengefasst"
        if review_text
        else "gekürzt und vereinheitlicht"
    )
    lines.append(
        f"> {SOURCE_ATTRIBUTION}. Die Texte der Stadt Wien sind hier bearbeitet "
        f"({edited}). Lizenz: [CC BY 4.0]({LICENCE_URL})."
    )
    return lines
