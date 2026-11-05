"""ÖBB descriptions on several lines: every line in short.

ÖBB's title names every line the text calls disrupted
(``_affected_lines`` in ``src/providers/oebb_route_title.py``), but the
German summary keeps ÖBB's first sentence and the second only when both fit
the 180 characters (``_with_second_sentence``). A notice on several lines
says each line in its own sentence, so the TV showed one line of the title
and nothing about the others. "REX50/REX51/S50: Wien Westbahnhof ↔ St.
Pölten Hbf" (02.11. to 11.11.2026) read "… keine REX50-Züge fahren." while
REX51 and S50 trains between Wien Westbahnhof and Wien Hütteldorf did not
run either: the second sentence is a list of about 60 train numbers. Seen
by the Jules audits of 31.10. and 02.11.2026; in the ÖBB cache since
February 9 of 65 texts on several lines lost a line this way.

Operator decision 2026-11-05 ("Ja, so bauen"): like a WL disruption on
several lines (``_every_line_in_short``, since 2026-10-15) the summary then
shows every line in short, with its section and its days::

    Wegen Bauarbeiten fallen Züge aus: REX50 St. Pölten Hbf ↔ Wien
    Westbahnhof 03.–10.11.; REX51, S50 Wien Westbahnhof ↔ Wien Hütteldorf
    02.–11.11.

Lines of one section stand together. What does not fit the 180 characters
goes from the end and the text closes with "…" (R40/REX41/REX4/S40 has five
sections). The short form stands only where the shown text misses a line of
the title and every line of the title is read from the text; otherwise the
summary stays as it was.
"""

from __future__ import annotations

import re

from ..feed_types import FeedItem
from ..providers.oebb_route_title import _OFFERED_TRAINS_RE
from .oebb_lead import _OEBB_SECOND_SENTENCE_END_RE

__all__ = [
    "_OEBB_SHORT_LEAD_RE", "_oebb_every_line_in_short", "_oebb_lines_in_short", "_oebb_title_lines",
]

_OEBB_SOURCE = "ÖBB"
_LINE = r"(?:REX|CJX|S|R)\s?\d{1,3}"
# "keine REX50-Züge", "die REX51-Züge 1604, …", "Der REX 41-Zug 2157",
# "einige S4 und S6-Züge": the line or two lines before "-Zug"/"-Züge".
_LINE_TRAINS_RE: re.Pattern[str] = re.compile(
    rf"\b({_LINE})(?:\s*(?:,|und)\s*({_LINE}))?\s*-Z[üu]ge?\b"
)
# A sentence that says trains do not run ("… keine R 40-Züge fahren.",
# "… können die S50-Züge 20083 und 20080 nicht fahren."). A sentence that
# offers trains instead ("die REX6-Züge … zu nehmen") is no outage.
_NOT_RUNNING_RE: re.Pattern[str] = re.compile(r"\bnicht fahren\b|\bkeine\b[^.]*\bfahren\b")
_OFFERED_RE: re.Pattern[str] = _OFFERED_TRAINS_RE
_SECTION_RE: re.Pattern[str] = re.compile(
    r"\b(?i:zwischen)\s+(?P<a>.+?)\s+und\s+(?P<b>.+?)"
    r"(?=\s+(?:von|am|ab|bis|können|kann|keine|die|der|einige|nicht)\s|,|\.?$)"
)
_STATION_NOISE_RE: re.Pattern[str] = re.compile(r"\s*\((?:U|Wien)\)|\s+(?:Bahnhof|Bahnhst\.?|Bhf\.?)(?=\s|$)")
_DATE_RE: re.Pattern[str] = re.compile(r"\b(\d{2})\.\s?(\d{2})\.(\d{4})\b")
_CAUSE_RE: re.Pattern[str] = re.compile(r"^(Wegen\s.+?)\s+(?:können|kann)\b")
# The lead of the short form; the English feed renders it by template.
_OEBB_SHORT_LEAD_RE: re.Pattern[str] = re.compile(
    r"^(?:(?P<cause>Wegen\s.+?) fallen|Es fallen) Züge aus: (?P<rest>.+)$"
)


_Day = tuple[int, int, int]
_Span = tuple[_Day, _Day]


def _norm_line(token: str) -> str:
    return re.sub(r"\s+", "", token)


def _oebb_title_lines(title: str) -> list[str]:
    """``["REX50", "REX51", "S50"]`` for "REX50/REX51/S50: Wien Westbahnhof ↔ …"."""
    prefix = re.match(rf"^({_LINE}(?:\s*/\s*{_LINE})*):\s", title)
    return [_norm_line(p) for p in prefix[1].split("/")] if prefix else []


def _names_line(text: str, line: str) -> bool:
    """Whether *text* names *line* ("REX50", "REX 50", "REX50-Züge")."""
    letters, number = re.match(r"(\D+)(\d+)", line).groups()  # type: ignore[union-attr]
    return re.search(rf"\b{letters}\s?{number}(?!\d)", text) is not None


def _station(name: str) -> str:
    return re.sub(r"\s+", " ", _STATION_NOISE_RE.sub("", name)).strip(" ,")


def _days(sentence: str) -> _Span | None:
    found: list[_Day] = sorted((int(y), int(m), int(d)) for d, m, y in _DATE_RE.findall(sentence))
    return (found[0], found[-1]) if found else None


def _days_text(days: _Span | None) -> str:
    """"03.–10.11.", "31.10.–03.11.", "01.11." (no year: the time line has it)."""
    if days is None:
        return ""
    (_y1, m1, d1), (_y2, m2, d2) = days
    if (m1, d1) == (m2, d2):
        return f" {d1:02d}.{m1:02d}."
    if m1 == m2:
        return f" {d1:02d}.–{d2:02d}.{m2:02d}."
    return f" {d1:02d}.{m1:02d}.–{d2:02d}.{m2:02d}."


def _oebb_lines_in_short(summary: str, title_lines: list[str]) -> str | None:
    """*summary* with every line of *title_lines* in short, else ``None``.

    ``None`` when a sentence about a line has no "zwischen … und …" or a
    title line is in no such sentence: then a part would be left out
    without a "…".
    """
    sections: list[tuple[str, list[str], list[_Span]]] = []
    for sentence in _OEBB_SECOND_SENTENCE_END_RE.split(summary):
        if not _NOT_RUNNING_RE.search(sentence) or _OFFERED_RE.search(sentence):
            continue
        lines = [
            _norm_line(token)
            for match in _LINE_TRAINS_RE.finditer(sentence)
            for token in match.groups()
            if token
        ]
        if not lines:
            continue
        section = _SECTION_RE.search(sentence)
        if section is None:
            return None
        where = f"{_station(section['a'])} ↔ {_station(section['b'])}"
        days = _days(sentence)
        for known_where, known_lines, known_days in sections:
            if known_where == where:
                known_lines.extend(line for line in lines if line not in known_lines)
                if days is not None:
                    known_days.append(days)
                break
        else:
            sections.append((where, list(dict.fromkeys(lines)), [days] if days else []))
    named = {line for _w, group, _d in sections for line in group}
    if len(sections) < 2 or not set(title_lines) <= named:
        return None
    cause = _CAUSE_RE.match(summary)
    head = f"{cause[1]} fallen Züge aus: " if cause else "Es fallen Züge aus: "
    parts = []
    for where, group, spans in sections:
        span: _Span | None = (min(d[0] for d in spans), max(d[1] for d in spans)) if spans else None
        parts.append(f"{', '.join(group)} {where}{_days_text(span)}")
    text = f"{head}{'; '.join(parts)}".removesuffix(".") + "."
    while len(text) > 180 and len(parts) > 1:
        parts.pop()
        text = f"{head}{'; '.join(parts)} …"
    return text if len(text) <= 180 else None


def _oebb_every_line_in_short(item: FeedItem, summary: str, shown: str, title: str) -> str | None:
    """The short form of an ÖBB notice's lines when *shown* misses one, else ``None``.

    *summary* is the whole text, *shown* what the TV would show without the
    short form (sentences and the 180 cut applied).
    """
    if item.get("source") != _OEBB_SOURCE:
        return None
    title_lines = _oebb_title_lines(title)
    if len(title_lines) < 2 or all(_names_line(shown, line) for line in title_lines):
        return None
    short = _oebb_lines_in_short(summary, title_lines)
    if short is None:
        return None
    # It must say more than the text it replaces.
    if sum(_names_line(short, line) for line in title_lines) <= sum(
        _names_line(shown, line) for line in title_lines
    ):
        return None
    return short
