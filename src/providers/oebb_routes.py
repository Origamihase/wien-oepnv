"""ÖBB route extraction: category words, prefix stripping and route endpoints.

Moved verbatim from ``src/providers/oebb.py`` (700-line limit); the facade
``src.providers.oebb`` re-exports the names other code uses.
"""

from __future__ import annotations

import html
import re
from itertools import pairwise

from ..utils.stations import station_info


NON_LOCATION_PREFIXES = {
    "bauarbeiten", "störung", "störungen", "ausfall", "ausfälle", "verspätung", "verspätungen", "sperre",
    "einschränkung", "verkehrsunfall", "feuerwehreinsatz", "rettungseinsatz",
    "polizeieinsatz", "notarzteinsatz", "weichenstörung", "signalstörung",
    "oberleitungsstörung", "stellwerksstörung", "fahrzeugschaden", "personenschaden",
    "wetter", "unwetter", "schnee", "hochwasser", "murenabgang",
    "lawinengefahr", "streik", "demonstration", "veranstaltung", "wartungsarbeiten",
    "update", "info", "information", "hinweis", "achtung", "verkehrsmeldung",
    "umleitung", "haltausfall", "schienenersatzverkehr", "sev", "ersatzverkehr",
    "streckenunterbrechung", "unterbrechung", "teilausfall", "zugausfall",
    "verkehrseinschränkung"
}

def _is_category(text: str) -> bool:
    t = text.lower()

    t = re.sub(r"^(?:db|öbb|oebb|nj|rj|rjx|ic|ice|rex|s)[-\s]+", "", t)

    parts = re.split(r"[\s↔<>/\-–]+", t)
    for part in parts:
        if part in NON_LOCATION_PREFIXES:
            return True

    for k in NON_LOCATION_PREFIXES:
        if t == k or t.startswith(k + " "):
            return True

    return False

# ---------------- Region / Filter Logic ----------------

def _strip_oebb_prefixes(text: str) -> str:
    """
    Entfernt iterativ typische ÖBB-Präfixe wie Liniencodes oder Störungsarten.

    Warum iterativ (while-Schleife) mit Regex anstatt naivem Split?
    ÖBB-Titel sind oft mehrfach mutiert, z.B. "REX 51: Störung: Wien". Ein naives
    Abschneiden am letzten oder ersten Doppelpunkt (`.split(':')`) würde echte
    Stationsnamen zerstören, die selbst Doppelpunkte enthalten (z.B. "Wien 10.: Favoriten").
    Daher iterieren wir und entfernen von vorne nur bekannte Präfixe, bis keines mehr matcht.
    """
    # Sucht nach Linien (z.B. REX 51, RJX 123) oder Wörtern gefolgt von Doppelpunkt
    base_pattern = (r"^(?:(?:REX|RJX|RJ|S|U|EC|ICE|IC|WB|NJ|CJX|D|R)\s*\d+|Störung|Verspätung|Zugausfall"
                    r"|DB-Bauarbeiten|Bauarbeiten|Info|Information|Einschränkung|Unterbrechung"
                    r"|Umleitung|Haltausfall|Schienenersatzverkehr|geänderte\s+Fahrzeiten"
                    r"|Verkehrsmeldung|Hinweis)\s*:\s*")
    while re.search(base_pattern, text, re.IGNORECASE):
        text = re.sub(base_pattern, "", text, flags=re.IGNORECASE)
    return text.strip()


# ---------------- Route extraction ("zwischen X und Y") ----------------

# HTML-tolerant: a description usually contains entries like
# "zwischen <b>Flughafen Wien Bahnhof</b> und <b>Wien Mitte-Landstraße Bahnhof</b>".
# We strip HTML before matching so we only need a single plain-text pattern.
#
# The lookahead is the tricky part. Two real-world failure modes drove the
# current shape:
#
# 1. A bare "." in the boundary class truncated abbreviated station names:
#    "und St. Pölten ist …" was captured as endpoint "St". The fix accepts
#    a period as a boundary only when it ends a sentence (end-of-string,
#    closing tag, or followed by whitespace + a lowercase German word) —
#    not when it sits inside an abbreviation like "St.".
# 2. Several common follow-up words ("aufgrund", "wegen", em-dash, …) were
#    not in the boundary list, so the regex over-extended into them
#    ("Mödling aufgrund Sturm" became the second endpoint). The list below
#    covers the recurring ÖBB phrasings.
_ZWISCHEN_PLAIN_RE = re.compile(
    r"zwischen\s+(?P<a>.+?)\s+und\s+(?P<b>.+?)"
    r"(?="
    r"\s+(?:"
    # Time/date prepositions
    r"von|bis|am|im|in\s+der|jeweils|ab|seit|um|gegen|nach|vor|"
    # Causal/circumstantial
    r"aufgrund|wegen|durch|f[üu]r|infolge|trotz|während|waehrend|"
    # Verbs typical for the predicate that follows the route phrase
    r"nicht|der\s+Zug|halten|fahren|kommt|f[äa]hrt|fallen|k[öo]nnen|"
    r"werden|wird|kann|d[üu]rfen|sollen|soll|m[üu]ssen|"
    # State / past-participle endings of the sentence
    r"ist|sind|war|waren|gesperrt|geschlossen|blockiert|eingestellt|"
    r"unterbrochen|gestört|gestoert|ausgefallen|verspätet|verspaetet|"
    r"verz[öo]gert|aufgehoben|freigegeben|"
    # Connectors that introduce a side clause / next "zwischen X und Y"
    r"sowie|sondern|sowie\s+zwischen|und\s+zwischen|,\s*und|"
    # Quantifiers that introduce a noun phrase about affected trains
    # ("…Bruck/Leitha Bahnhof einige Nahverkehrszüge ausgefallen"). Without
    # these, the non-greedy ``b`` over-extended into the entire affected-
    # train clause and produced frankenstring endpoints.
    r"einige|keine|kein|alle|mehrere|wenige|s[äa]mtliche|"
    # Intermediate-via marker — ends the captured endpoint at the via stop
    # so "Mödling über Wiener Neudorf" yields b="Mödling". ``zu|zur`` ends it
    # at the dominant ÖBB closer "kommt es zwischen X und Y zu <…>" (bug b1).
    r"zu|zur|[üu]ber|via|"
    # ÖBB's disruption wording "sind zwischen X und Y Zugfahrten derzeit nur
    # eingeschränkt möglich". Not a boundary before: 11 endpoints of the
    # cache history ran on into it ("Wien Meidling Bahnhof (U) Zugfahrten"),
    # resolved to no station, and the strict route check then dropped any
    # such message whose title was not a route itself. The replacement-bus
    # closers ("… Bahnhst eingerichtet") stay unbounded on purpose: a bus
    # route is the alternative, not the disruption, and must not reach the
    # title.
    r"Zugfahrten|Zugverkehr|Z[üu]ge|derzeit"
    r")\b"
    r"|[,;!?]"  # Plain sentence punctuation (period excluded — see above)
    r"|[—–]"  # German em-/en-dash often introduces a side remark
    r"|<"  # HTML tag start (defensive: we strip HTML, but stay safe)
    # Period followed by a German sentence-starter word — typical ÖBB
    # closers like "Mödling. Auch Auswirkung …" or "Mödling. Wir bitten
    # …". Listed words cannot be the second part of a station name, so
    # "St. Pölten" stays intact while sentence boundaries are recognised.
    r"|\.\s+(?:Auch|Bitte|Wir|Es|Hier|Hinweis|Achtung|Wegen|Aufgrund|"
    r"Heute|Morgen|Reisende|Details|Diese|Dieser|Bei|Im|Aus|Mit|"
    r"Fahrgäste|Fahrgaeste|Beachten|ACHTUNG|HINWEIS)"
    r"|\.\s*$"  # Period only when it terminates the description
    r"|\s*$"
    r")",
    re.IGNORECASE | re.DOTALL,
)


# Alternative route-phrasing — "von X nach Y", "ab X bis Y" — used in
# ÖBB descriptions when a relation is described directionally rather
# than as a connection. Captures both endpoints so the strict route
# check can reject Wien↔Distant variants (e.g. "ab Wien Hbf bis Graz
# Hbf").
_VON_NACH_PLAIN_RE = re.compile(
    r"\b(?:von|ab)\s+(?P<a>.+?)\s+(?:nach|bis)\s+(?P<b>.+?)"
    r"(?="
    r"\s+(?:gesperrt|geschlossen|unterbrochen|eingestellt|"
    r"betroffen|beeintr[äa]chtigt|gest[öo]rt|eingeschr[äa]nkt|"
    r"auf|au[ßs]er\s+betrieb|nicht|kein|von|bis|am|im|in\s+der|"
    r"f[üu]r|wegen|aufgrund|durch|infolge|"
    r"ist|sind|war|waren|wird|werden|kann|k[öo]nnen|"
    r"f[äa]hrt|fahren|kommt|fallen|halten|halten\s+nicht|[üu]ber|via)\b"
    r"|[,;!?]"
    r"|[—–]"
    r"|<"
    r"|\.\s+(?:Auch|Bitte|Wir|Es|Hier|Hinweis|Achtung|Wegen|Aufgrund|"
    r"Heute|Morgen|Reisende|Details|Diese|Dieser|Bei|Im|Aus|Mit|"
    r"Fahrgäste|Fahrgaeste|Beachten|ACHTUNG|HINWEIS)"
    r"|\.\s*$"
    r"|\s*$"
    r")",
    re.IGNORECASE | re.DOTALL,
)


# Alternative route-phrasing some descriptions use instead of "zwischen X und Y":
# "Strecke X — Y", "Verbindung X-Y", "Linie X bis Y". The hyphen / en-dash /
# em-dash separator is required to be surrounded by whitespace so we don't
# split compound station names like "Wien Mitte-Landstraße".
_STRECKE_PLAIN_RE = re.compile(
    r"(?:strecke|verbindung|linie|abschnitt)\s+(?P<a>.+?)\s+[-—–]\s+(?P<b>.+?)"
    r"(?="
    r"\s+(?:gesperrt|geschlossen|unterbrochen|eingestellt|"
    r"betroffen|beeintr[äa]chtigt|gest[öo]rt|eingeschr[äa]nkt|"
    r"auf|au[ßs]er\s+betrieb|nicht|kein|von|bis|am|im|in\s+der|"
    r"f[üu]r|wegen|aufgrund|durch|infolge|"
    r"ist|sind|war|waren|wird|werden|kann|k[öo]nnen|kommt|fahren|fallen|halten|[üu]ber|via)\b"
    r"|[,;!?]"
    r"|[—–]"
    r"|<"
    r"|\.\s*$"
    r"|\s*$"
    r")",
    re.IGNORECASE | re.DOTALL,
)

# Suffix stripped before looking up a station name. Anchored at end-of-string
# AND rejects a leading hyphen so compound proper nouns like
# ``Wien Franz-Josefs-Bahnhof`` keep the trailing ``-Bahnhof`` intact.
# Stripping it produced a dangling ``Wien Franz-Josefs-`` that leaked into
# dedup keys (and into visible titles whenever the alias resolution in
# :func:`station_info` failed to bridge the truncation).
_BAHNHOF_TRAILING_END_RE = re.compile(
    r"(?<![\-‐-―])\s+\b(?:Hauptbahnhof|Bahnhof|Bahnhst|Hbf|Bhf|Bf)\b\.?\s*$",
    re.IGNORECASE,
)
_PARENS_TRAILING_RE = re.compile(r"\s*\(\s*[A-Za-z]\d*\s*\)\s*$")


def _without_transfer_markers(name: str) -> str:
    """Drop ÖBB's trailing transfer markers: ``Wien Meidling Bahnhof (U)`` → ``Wien Meidling Bahnhof``.

    ``(U)``/``(S)`` say which lines stop there; no station alias carries
    them. Looked up with the marker, ``Wien Meidling Bahnhof (U)`` resolved
    to the Wiener-Linien stop ``Wien Bhf. Meidling (WL)`` through its alias
    ``Wien Bhf. Meidling U`` — Hütteldorf, Atzgersdorf and five more ÖBB
    stations in Vienna likewise.
    """
    while True:
        stripped = _PARENS_TRAILING_RE.sub("", name).strip()
        if stripped == name:
            return name
        name = stripped

# ``im``/``am`` sit in the boundary alternations above as time prepositions
# ("… und Felixdorf Bahnhof am 10.02.2026"), so a place name carrying one —
# "Baumgarten im Bgld-Schattendorf", "Brunn am Gebirge", "Neusiedl am See" —
# was cut to its first word when it stood second. Published 2026-09-22:
# "zwischen Ebenfurth Bahnhof und Baumgarten im Bgld-Schattendorf Bahnhof"
# became the endpoint "Baumgarten", which resolves to the Wiener-Linien stop
# "Wien Baumgarten (WL)"; the Burgenland route passed as Wien ↔ Pendler and
# took a slot in the German feed as "REX 6: Wien Baumgarten (WL) ↔ Ebenfurth".
#
# ÖBB writes the station suffix after the full name, so a single capitalised
# word followed directly by it marks the qualifier as part of the name. A
# date, a weekday or "im Bereich Wien Hbf" never has that shape.
_PLACE_QUALIFIER_TAIL_RE = re.compile(
    r"\s+(?:im|am)\s+[A-ZÄÖÜ][\w.\-]*(?=\s+(?:Hauptbahnhof|Bahnhof|Bahnhst|Hbf|Bhf|Bf)\b)"
)


def _with_place_qualifier(plain: str, match: re.Match[str], group: str) -> str:
    """Return *group* of *match*, re-attaching a place qualifier the boundary cut off."""
    tail = _PLACE_QUALIFIER_TAIL_RE.match(plain, match.end(group))
    return match.group(group) + (tail.group(0) if tail else "")


def _normalize_endpoint_name(name: str) -> str:
    """Strip HTML, trailing parenthetical markers and Bahnhof-suffixes.

    The result is suitable as input to :func:`station_info` for canonical
    classification.
    """
    if not name:
        return ""
    # Order matters: ``html.unescape`` first decodes entity-escaped angle
    # brackets (``&lt;b&gt;Wien Mitte&lt;/b&gt;``) into their actual
    # ``<`` / ``>`` chars, so the subsequent tag strip catches them. The
    # reverse order leaves entity-encoded tags intact and they survive
    # into the captured endpoint name. Same fix applied at the other
    # two sibling sites in this file.
    cleaned = html.unescape(name)
    cleaned = re.sub(r"<[^>]+>", " ", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    cleaned = _without_transfer_markers(cleaned)
    # Strip a single trailing Bahnhof/Hbf/Bf suffix (only at the end, so we
    # don't mangle names like "Wiener Neustadt Hauptbahnhof" → "Wiener Neustadt"
    # — which is actually what we want for lookup).
    cleaned = _BAHNHOF_TRAILING_END_RE.sub("", cleaned).strip()
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()

    # If the endpoint absorbed a sentence boundary ("Mödling. Auch ..."),
    # truncate to the part before the period when *that* part resolves
    # against the directory. Walk the candidate splits from RIGHTMOST to
    # LEFTMOST so the longest resolving prefix wins — important for
    # abbreviated saint names that contain a period themselves: pre-fix
    # ``partition(". ")`` always cut at the FIRST period, so
    # ``"St. Pölten. Bitte Reisende"`` left head=``"St"`` (unresolvable),
    # no truncation happened, and the garbled multi-sentence string
    # passed through as the endpoint. Walking from the right tries
    # ``"St. Pölten"`` first → resolves → truncation succeeds.
    # The single-period ``"St. Pölten"`` case still works (the only
    # split candidate is ``"St"`` which doesn't resolve → no change).
    # A name that resolves as a whole has no sentence boundary to cut:
    # ``"Wien Bhf. Meidling"`` would otherwise lose ``". Meidling"``, and
    # ``"Wien Bhf"`` resolves to Wien Hauptbahnhof. That turned the ÖBB
    # title ``Wien Hbf (U) ↔ Wien Meidling Bahnhof (U)`` into
    # ``Wien Hauptbahnhof ↔ Wien Hauptbahnhof`` (feed, 2026-09-29).
    if ". " in cleaned and station_info(cleaned) is None:
        parts = cleaned.split(". ")
        for split_at in range(len(parts) - 1, 0, -1):
            candidate = ". ".join(parts[:split_at]).strip()
            if candidate and station_info(candidate) is not None:
                cleaned = candidate
                break

    return cleaned


# German weekday / month names (and a few temporal words). The temporal
# phrasings ``von <Tag> bis <Tag>`` / ``zwischen <Tag> und <Tag>`` are matched
# by _VON_NACH_PLAIN_RE / _ZWISCHEN_PLAIN_RE, whose endpoints are validated only
# by _looks_like_station_name — which otherwise accepts a weekday/month word as
# a "station". The resulting bogus route (e.g. ("Montag", "Freitag …")), being
# the only route extracted, makes _is_relevant return False and silently drop an
# otherwise Wien-relevant single-station message. These tokens are never station
# endpoints, so reject them up-front.
_CALENDAR_WORDS = frozenset({
    "montag", "dienstag", "mittwoch", "donnerstag", "freitag", "samstag",
    "sonntag", "feiertag", "feiertage", "feiertagen", "werktag", "werktags",
    "jänner", "januar", "februar", "feber", "märz", "april", "mai", "juni",
    "juli", "august", "september", "oktober", "november", "dezember",
})


def _looks_like_station_name(text: str) -> bool:
    """Reject pure dates/numbers and date/time fragments.

    Real station names start with a letter (typically capitalised) and
    contain at least three consecutive alphabetic characters. Dates,
    times and number-prefixed phrases like ``03.10.2026 (23:15 Uhr)``
    are rejected up-front so the route extractor never treats them as
    endpoints.
    """
    if not text:
        return False
    text = text.strip()
    if not text:
        return False
    # Reject if starts with a digit — almost always a date/time fragment
    # ("03.10.2026 (23:15 Uhr) einige …").
    if text[0].isdigit():
        return False
    if not re.search(r"[A-Za-zÄÖÜäöüß]", text):
        return False
    # Reject if no run of three or more alphabetic characters survives
    # — guards against "Hbf (U)" residue after stripping.
    if not re.search(r"[A-Za-zÄÖÜäöüß]{3,}", text):
        return False
    # Defence in depth: reject if the entire string is dates / numbers
    # interleaved with punctuation.
    if re.fullmatch(r"[\d.\-/\s]+", text):
        return False
    # Reject endpoints whose first word is a German weekday / month name: the
    # temporal "von X bis Y" / "zwischen X und Y" phrasings capture these as
    # fake route endpoints (e.g. "Montag", "Freitag verkehren Busse").
    if text.casefold().split()[0] in _CALENDAR_WORDS:
        return False
    return True


def _extract_zwischen_routes(description: str) -> list[tuple[str, str]]:
    """Find all route mentions in *description*.

    Recognises both the dominant ``zwischen X und Y`` phrasing and the
    alternative ``Strecke|Verbindung|Linie|Abschnitt X — Y`` form. Returns a
    list of normalised ``(name_a, name_b)`` tuples, deduplicated regardless
    of A/B order (so ``A ↔ B`` and ``B ↔ A`` count once).
    """
    if not description:
        return []

    # Strip HTML tags and unescape entities; we want plain text for matching.
    # Order matters: unescape first so entity-encoded tags (``&lt;b&gt;``)
    # become real ``<b>`` and the subsequent tag strip catches them.
    plain = html.unescape(description)
    plain = re.sub(r"<[^>]+>", " ", plain)
    plain = re.sub(r"\s+", " ", plain).strip()

    routes: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    for regex in (_ZWISCHEN_PLAIN_RE, _STRECKE_PLAIN_RE, _VON_NACH_PLAIN_RE):
        for match in regex.finditer(plain):
            a_norm = _normalize_endpoint_name(match.group("a"))
            b_norm = _normalize_endpoint_name(_with_place_qualifier(plain, match, "b"))
            if not _looks_like_station_name(a_norm) or not _looks_like_station_name(b_norm):
                continue
            # Deduplicate regardless of A/B order
            sorted_pair = sorted([a_norm.casefold(), b_norm.casefold()])
            key: tuple[str, str] = (sorted_pair[0], sorted_pair[1])
            if key in seen:
                continue
            seen.add(key)
            routes.append((a_norm, b_norm))

    return routes


def _extract_routes(title: str, description: str) -> list[tuple[str, str]]:
    """Collect route endpoint pairs from title (split on ↔) and description.

    Pure category words like "Bauarbeiten ↔ Umleitung" are filtered out so
    they don't drag a real station-mention message into the strict-route path
    incorrectly. Likewise candidates whose endpoints are obviously
    non-station references (``Bahnsteig 1`` / ``Bahnsteig 5``, ``Gleis 3``,
    ``Aufzug``-etc.) are discarded so the single-station fall-through can
    pick up the real station mention.

    Real distant routes between two unknown stations (e.g. ``Mürzzuschlag
    ↔ Payerbach-Reichenau``) are *kept* — they look like real station
    names, and the strict route classifier in :func:`_route_is_wien_relevant`
    is then responsible for rejecting them.
    """
    candidates: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    # 1. Parse title — split on ↔
    if title and "↔" in title:
        # Multi-route titles like "A ↔ B / C ↔ D" must be split on " / "
        # before pairing, otherwise the inner endpoints fuse into
        # frankenstrings ("B / C" and "C / D") and the strict route check
        # never sees the real (A,B) and (C,D) pairs. We restrict the split
        # to whitespace-bounded slashes so compound names like "Linz/Donau"
        # or "Bruck/Leitha" stay intact.
        title_segments = [
            seg.strip() for seg in re.split(r"\s+/\s+", title) if seg.strip()
        ]
        for segment in title_segments:
            if "↔" not in segment:
                continue
            parts = [p.strip() for p in segment.split("↔")]
            for left, right in pairwise(parts):
                a_raw = _strip_oebb_prefixes(left)
                b_raw = _strip_oebb_prefixes(right)
                if _is_category(a_raw) or _is_category(b_raw):
                    continue
                a_norm = _normalize_endpoint_name(a_raw)
                b_norm = _normalize_endpoint_name(b_raw)
                if not _looks_like_station_name(a_norm) or not _looks_like_station_name(b_norm):
                    continue
                sorted_pair = sorted([a_norm.casefold(), b_norm.casefold()])
                key: tuple[str, str] = (sorted_pair[0], sorted_pair[1])
                if key in seen:
                    continue
                seen.add(key)
                candidates.append((a_norm, b_norm))

    # 2. Parse description — "zwischen X und Y" patterns
    for raw_a, raw_b in _extract_zwischen_routes(description):
        sorted_pair = sorted([raw_a.casefold(), raw_b.casefold()])
        desc_key: tuple[str, str] = (sorted_pair[0], sorted_pair[1])
        if desc_key in seen:
            continue
        seen.add(desc_key)
        candidates.append((raw_a, raw_b))

    # 3. Drop candidates that describe a station-internal element rather
    #    than a transit connection. This is what allows "Aufzug zwischen
    #    Bahnsteig 1 und Bahnsteig 5 in Wien Mitte defekt" to fall through
    #    to the single-station path and pick up the Wien-Mitte mention.
    routes: list[tuple[str, str]] = []
    for a, b in candidates:
        if _looks_like_facility_endpoint(a) or _looks_like_facility_endpoint(b):
            continue
        routes.append((a, b))

    return routes


# Endpoint candidates whose first word matches one of these never describe
# a real transit connection — they're typical inside-station references
# ("Bahnsteig 1 und Bahnsteig 5", "Gleis 3 und Gleis 7"). Routes built from
# such candidates are dropped so the message can fall through to the
# single-station path and pick up the real station mention.
_NON_STATION_FIRST_WORDS = frozenset({
    "bahnsteig",
    "gleis",
    "steig",
    "wagen",
    "waggon",
    "abteil",
    "ausgang",
    "eingang",
    "tor",
    "tür",
    "tuer",
    "sektor",
    "zone",
    "halle",
    "platz",
    "lift",
    "aufzug",
    "rolltreppe",
    "fahrtreppe",
})


def _looks_like_facility_endpoint(name: str) -> bool:
    """Return True if *name* describes a non-station element (platform,
    track, exit, …) rather than a transit station."""
    if not name:
        return False
    first = name.strip().split(maxsplit=1)[0].casefold().rstrip(".:,;")
    return first in _NON_STATION_FIRST_WORDS
