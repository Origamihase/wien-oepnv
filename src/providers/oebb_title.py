"""ÖBB title cosmetics: arrows, Bahnhof suffixes, line/update prefixes.

Moved verbatim from ``src/providers/oebb.py`` (700-line limit); the facade
``src.providers.oebb`` re-exports the names other code uses.
"""

from __future__ import annotations

import html
import re

from ..utils.stations import canonical_name, display_name, is_in_vienna
from .oebb_routes import _is_category, _without_transfer_markers


# ---------------- Titel + Endpunkte ----------------
# remove generic suffixes like "Bahnhof" or "Hbf" when they appear as standalone
# tokens (optionally followed by "(U)", "(S)" or similar short indicators)
BAHNHOF_TRIM_RE = re.compile(
    r"\s*\b(?:Bahnhof|Bahnhst|Hbf|Bf)\b(?:\s*\(\s*[US]\d*\s*\))?",
    re.IGNORECASE,
)
# treat simple hyphen as separator only when surrounded by spaces
# Also swallow surrounding "decorations" like < > or &lt; &gt; if they wrap the arrow
# Also support double-escaped entities like &amp;lt; and &amp;gt; (seen in some feeds)
ARROW_ANY_RE    = re.compile(
    r"\s*(?:(?:<|&lt;|&amp;lt;|&#60;|&#x3C;)+\s*)?"
    r"(?:<=>|<->|<>|→|↔|=>|->|<-|=|–|—|\s-\s)"
    r"(?:\s*(?:>|&gt;|&amp;gt;|&#62;|&#x3E;)+)?\s*"
)
DESC_CLEANUP_RE = re.compile(
    r"(?:(?:<|&lt;|&amp;lt;|&#60;|&#x3C;)+\s*)"
    r"(?:<=>|<->|<>|→|↔|=>|->|<-)"
    r"(?:\s*(?:>|&gt;|&amp;gt;|&#62;|&#x3E;)+)|(?:<->|<=>)"
)

MULTI_ARROW_RE  = re.compile(r"(?:\s*↔\s*){2,}")
_MULTI_SLASH_RE = re.compile(r"\s*/{2,}\s*")
_MULTI_COMMA_RE = re.compile(r"\s*,{2,}\s*")

def _clean_endpoint(p: str) -> str:
    p = BAHNHOF_TRIM_RE.sub("", p)
    p = _MULTI_SLASH_RE.sub("/", p)
    p = _MULTI_COMMA_RE.sub(", ", p)
    p = re.sub(r"\s{2,}", " ", p)
    return p.strip(" ,/")

def _clean_description(text: str) -> str:
    if not text:
        return ""
    # Normalize arrows wrapped in angle brackets or specific ASCII arrows to ↔
    text = DESC_CLEANUP_RE.sub(" ↔ ", text)
    # Collapse spaces
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip()


# Recognises an ÖBB line marker at the very start of a title. The
# prefix is split off before the segment iteration runs and re-attached
# afterwards — without this, ``S40: Wien Franz-Josefs-Bahnhof ↔ Wien
# Heiligenstadt`` produced ``Wien Heiligenstadt ↔ S40: Wien
# Franz-Josefs-…`` because the Vienna-first reorder logic mistakenly
# classifies ``S40: Wien Franz-Josefs-Bahnhof`` (with the line prefix
# still glued on) as "not in Vienna" — the prefix breaks the
# station_info lookup — and swaps the endpoints.
#
# Several lines share the prefix when a message disrupts several
# (``R 40/REX 41/REX 4/S 40: …``, see :func:`_affected_lines`).
_LINE_CODE = r"(?:REX|RJX|RJ|EC|ICE|IC|WB|NJ|CJX|S-Bahn|S|U-Bahn|U|R|D)\s*\d+[A-Za-z]?"
_LEADING_LINE_PREFIX_RE = re.compile(
    rf"^\s*({_LINE_CODE}(?:\s*/\s*{_LINE_CODE})*)\s*:\s*",
    re.IGNORECASE,
)


# Verankert am LETZTEN ``: `` — nicht am ersten. ÖBB stellt dem Titel häufig
# ein Kategorie-Label voran, und dann liegt das redundante Paar hinter dem
# zweiten Doppelpunkt:
#
#   "Bauarbeiten: kein Halt in Lind-Rosegg Föderlach: Lind-Rosegg Föderlach"
#
# Mit einem First-Colon-Anker verglich der Check „Bauarbeiten" gegen den
# gesamten Rest und fand keine Redundanz. Der greedy Kopf ``(.+)`` verschiebt
# den Anker ans letzte ``: ``.
#
# Uhrzeiten bleiben unberührt, weil das Muster ein Leerzeichen NACH dem
# Doppelpunkt verlangt: "Sperre 17:30 Uhr" matcht nicht.
_REDUNDANT_SUFFIX_RE = re.compile(r"^(.+):\s+([^:]+)$")


def _drop_redundant_suffix(t: str) -> str:
    """Collapse ``"Text: Station"`` to ``"Text"`` when the station already
    appears in the text (z.B. ``"Aufzug in X defekt: X"``).

    Wird in :func:`_clean_title_keep_places` ZWEIMAL aufgerufen: einmal auf
    dem Rohtitel und einmal auf dem fertig zusammengesetzten Titel. Der
    zweite Durchlauf ist der entscheidende — die Dopplung ist im Rohtitel
    oft noch gar nicht wörtlich:

        "Bauarbeiten - kein Halt in Lind-Rosegg Bahnhst Föderlach Bahnhof:
         Lind-Rosegg Föderlach"

    Beide Hälften meinen dieselben zwei Stationen, aber erst
    :func:`_clean_endpoint` entfernt die Bahnhofs-Zusätze und macht aus der
    linken Hälfte buchstäblich „Lind-Rosegg Föderlach". Ein Check, der nur
    vor der Normalisierung läuft, vergleicht die UNnormalisierten Formen,
    findet keine Übereinstimmung und lässt die Dopplung stehen — genau so
    stand sie live im Feed (``cache/oebb_c40d21/events.json``, Titel über
    60 Cache-Refreshes byte-identisch).
    """
    match = _REDUNDANT_SUFFIX_RE.search(t)
    if not match:
        return t
    text_part, suffix_part = match.group(1), match.group(2)
    # Check ob suffix im Text enthalten ist (case-sensitive)
    if suffix_part.strip() in text_part or text_part.strip() in suffix_part:
        return text_part if len(text_part) > len(suffix_part) else suffix_part
    return t


# ÖBB republishes a running disruption under a numbered update headline:
#
#     Update 4 (25.09.2026 09:59) Verkehrseinschränkung: St.Pölten
#
# Published verbatim on 2026-09-25 (``docs/feed.xml`` item 3): the prefix is
# ~30 characters a display reader cannot use, and it also blocks the
# category-prefix loop below — the first colon sits inside "09:59", which
# the loop deliberately never splits (clock-time guard), so the title was
# left untouched and "St.Pölten" kept its missing space. Without the prefix
# the same loop yields "St. Pölten Hauptbahnhof". Four such titles reached
# the feed since 2026-08-01, each for two to three cycles.
_UPDATE_PREFIX_RE = re.compile(
    r"^\s*Update\s+\d+\s*\(\s*\d{1,2}\.\d{1,2}\.\d{4}(?:,?\s+\d{1,2}:\d{2})?\s*\)\s*[:\-–]?\s*",
    re.IGNORECASE,
)

# An all-clear ("Aufhebung Verkehrseinschränkung: Wien Handelskai") names the
# end of a disruption. Its label contains a category word, so the loop below
# would drop it as a plain category prefix and leave only "Wien Handelskai" —
# an all-clear that reads like a running disruption. Three of the four
# update-prefixed titles since August were all-clears; the label stays.
#
# The label is split off like the line prefix, the place behind it goes
# through the normal cleanup, and the label is put back in front. Kept whole,
# the place would skip the cleanup: published 2026-09-25 11:01 as
# "Update 5 (25.09.2026 10:48) Aufhebung Verkehrseinschränkung: St.Pölten",
# the missing space would have survived the prefix fix.
_ALL_CLEAR_PREFIX_RE = re.compile(r"^\s*Aufhebung\b", re.IGNORECASE)
_ALL_CLEAR_LABEL_RE = re.compile(r"^\s*(Aufhebung\b[^:]*?)\s*:(?!\d)\s*", re.IGNORECASE)


def _split_all_clear_label(title: str) -> tuple[str, str]:
    """Split ``"Aufhebung …: Ort"`` into ``("Aufhebung …", "Ort")``.

    Returns ``("", title)`` when the title carries no all-clear label.
    """
    match = _ALL_CLEAR_LABEL_RE.match(title)
    if not match:
        return "", title
    return match.group(1).strip(), title[match.end():]


def _join_label(label: str, rest: str) -> str:
    """Put a split-off ``label`` back in front of ``rest`` as ``"label: rest"``."""
    if not label:
        return rest
    return f"{label}: {rest}" if rest else label


def _strip_update_prefix(title: str) -> str:
    """Remove ÖBB's ``Update N (TT.MM.JJJJ hh:mm)`` headline prefix."""
    return _UPDATE_PREFIX_RE.sub("", title or "", count=1)


def _clean_title_keep_places(t: str) -> str:
    t = (t or "").strip()
    t = html.unescape(t)
    t = _strip_update_prefix(t)

    # Preserve a leading line marker (S40:, REX 7:, …) through the
    # cleanup. The endpoint-reordering logic below would otherwise
    # treat the prefix as part of the first endpoint and silently
    # swap A↔B when only one side resolves to a Vienna station.
    line_prefix = ""
    line_match = _LEADING_LINE_PREFIX_RE.match(t)
    if line_match:
        line_prefix = line_match.group(1).strip()
        t = t[line_match.end():]

    all_clear_label, t = _split_all_clear_label(t)

    t = _drop_redundant_suffix(t)

    # Allgemeiner Fall: „X und Y“ → „X ↔ Y“ für Stationen
    t = re.sub(r"\b([^,;|]+?)\s+und\s+([^,;|]+?)\b", r"\1 ↔ \2", t)
    # Pfeile/Bindestriche und Trennzeichen normalisieren
    raw_parts = [p for p in ARROW_ANY_RE.split(t) if p.strip()]
    canonical_parts: list[str] = []
    for part in raw_parts:
        segment = part.strip()
        if not segment:
            continue

        # NEU: Präfix iterativ vom jeweiligen Segment abtrennen
        while True:
            # ``:(?!\d)`` — ein Doppelpunkt direkt vor einer Ziffer gehört zu
            # einer UHRZEIT, nicht zu einem ``Label: Rest``-Präfix. Ohne diese
            # Bedingung zerschnitt die Schleife „Sperre 17:30 Uhr Wien Hbf" am
            # Doppelpunkt der Uhrzeit: Als Präfix blieb „Sperre 17", und das
            # akzeptiert :func:`_is_category`, weil es nur die WÖRTER prüft
            # („sperre" steht in ``NON_LOCATION_PREFIXES``). Übrig blieb
            # „30 Uhr Wien Hbf" — die Stunde war weg und der Feed zeigte
            # „30 Uhr Wien". Echte Label-Präfixe sind nicht betroffen, auch
            # nicht solche, die auf einer Ziffer enden („Linie 5: kein Halt").
            match = re.match(r"^\s*([^:]+):(?!\d)\s*", segment)
            if not match:
                break

            prefix = match.group(1).strip()
            if _is_category(prefix) and not _ALL_CLEAR_PREFIX_RE.match(prefix):
                segment = segment[match.end():]
            else:
                break

        if not segment:
            continue

        canon = canonical_name(_without_transfer_markers(segment))
        if not canon:
            cleaned = _clean_endpoint(segment)
            canon = canonical_name(cleaned)

            # If full string lookup fails, try splitting composite endpoints (e.g. "Wien/ Flughafen Wien")
            # We require a space after the slash to avoid splitting names like "Linz/Donau" or "2/3".
            if not canon and re.search(r"/\s", segment):
                sub_segments = re.split(r"/\s+", segment)
                sub_segments = [s.strip() for s in sub_segments if s.strip()]

                if len(sub_segments) > 1:
                    processed_subs = []
                    for s in sub_segments:
                        # Resolve each part individually
                        c = canonical_name(s)
                        if not c:
                            cl = _clean_endpoint(s)
                            c = canonical_name(cl) or cl
                        if c:
                            c = display_name(c)
                        processed_subs.append(c)
                    canon = " / ".join(processed_subs)

            if not canon:
                canon = cleaned

        if canon:
            canon = display_name(canon)
        canonical_parts.append(canon)
    parts = canonical_parts
    if len(parts) >= 2:
        # Check if first part is a category keyword -> use colon
        if _is_category(parts[0]):
            t = f"{parts[0]}: {parts[1]}"
            if len(parts) > 2:
                rest = " ".join(parts[2:]).strip()
                if rest:
                    t += f" {rest}"
        else:
            # Check ordering: if part[1] is Vienna and part[0] is not, swap
            if len(parts) == 2 and is_in_vienna(parts[1]) and not is_in_vienna(parts[0]):
                parts[0], parts[1] = parts[1], parts[0]

            # Multi-part titles arise from chains like ``A ↔ B / C ↔ D``
            # where ``ARROW_ANY_RE`` split off three parts (``A``, ``B / C``,
            # ``D``). Joining the tail with a plain space silently drops
            # the inner ``↔`` separators so the latent intermediate read
            # ``A ↔ B / C  D`` — only ``_format_route_title`` later
            # rebuilt a clean title. Joining everything with `` ↔ ``
            # keeps the inter-route arrows so the cleaner can be relied
            # on as a stand-alone pre-processor too.
            t = " ↔ ".join(parts)
    elif parts:
        t = parts[0]
    # Zweiter Redundanz-Durchlauf: s. Docstring von _drop_redundant_suffix —
    # die Dopplung wird oft erst durch die Normalisierung oben wörtlich.
    t = _drop_redundant_suffix(t)
    t = MULTI_ARROW_RE.sub(" ↔ ", t)
    t = re.sub(r"\s{2,}", " ", t)
    t = re.sub(r"&lt;|&gt;|&#60;|&#x3C;|&#62;|&#x3E;|[<>«»‹›]+", "", t)
    t = t.strip()
    t = _join_label(all_clear_label, t)
    # Re-attach the leading line prefix that was split off above so
    # downstream consumers (``_extract_line_prefix``) still recognise it.
    if line_prefix and t:
        t = f"{line_prefix}: {t}"
    elif line_prefix:
        t = line_prefix
    return t
