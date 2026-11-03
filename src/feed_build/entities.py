"""Entity patterns for the EN translation (brands, lines, streets, places).

Moved verbatim from ``src/build_feed.py`` (700-line rule, Etappe 8); ``src.build_feed``
re-exports every name that src/, scripts/ or the tests read from it.
"""

from __future__ import annotations

import re

from ..utils.name_letters import latin_names

__all__ = [
    "_BRAND_ENTITIES", "_LINE_ENTITY_RE", "_TRAM_LETTER_LINE_RE", "_STREET_SUFFIX_RE", "_SPACED_STREET_NAME_RE",
    "_PLACE_NAME_ENTITY_RE", "_PREPOSITION_NAME_RE", "_DIRECTION_NAME_RE", "_MAIN_STATION_RE", "_GATE_SUFFIX_RE",
    "_DATE_ENTITY_RE",
]


# ---------------- Entity preservation (proper-noun masking) ----------------
#
# The Helsinki opus-mt-de-en model translates German to English by
# statistical token mapping; it does not know which spans are proper
# nouns and will happily translate ``Stephansplatz`` to
# ``Stephen's Square`` or ``Wiener Linien`` to ``Vienna Lines``.
# To prevent this we mask known entities BEFORE handing the text to
# the model and unmask them AFTER inference. Three entity sources are
# composed (longest-first so e.g. ``Wien Hauptbahnhof`` matches before
# ``Hauptbahnhof``):
#
#   1. A static list of operator brands / network names that never
#      change shape across feed builds.
#   2. A compiled regex for ÖPNV line identifiers (U-Bahn, S-Bahn,
#      tram, bus) — these are short alphanumeric tokens
#      (``U1``..``U6``, ``S1``..``S99``, ``1``..``99[A-Z]?``) that
#      machine translation routinely loses.
#   3. A lazily-built regex covering every name + alias from the
#      project's station directory (``data/stations.json`` via
#      :func:`src.utils.stations._station_entries`). Loading is gated
#      behind ``@lru_cache`` so the CLI commands that never request
#      EN output pay zero cost.
#
# The placeholder format ``XENT<n>X`` is alphanumeric and starts with
# a letter so the SentencePiece tokenizer used by Marian/Helsinki
# models keeps each placeholder as a single token without inserting
# extra whitespace. Translation that drops a placeholder degrades
# gracefully — :func:`_unmask_entities` only restores the placeholders
# it can still find.
_BRAND_ENTITIES: tuple[str, ...] = (
    "Wiener Linien",
    "Wiener Lokalbahnen",
    "Badner Bahn",
    "WLB",
    "ÖBB",
    "VOR",
    "VAO",
    "WESTbahn",
    "Cat",
    "RegioJet",
    "S-Bahn",
    "S-Bahn-Stammstrecke",
    "Stammstrecke",
)

# Line identifiers must reach the EN feed byte-for-byte: they are names,
# not words. Anything this pattern misses goes to the NMT model unprotected.
#
# Live regression, 2026-09-12 — ``docs/feed.en.xml`` item 10:
#
#   DE  43A/44A/844/N43/44B: Gleisbauarbeiten (Phase 2)
#   EN  43A/44A844/N43/44B: track construction works (phase 2)
#
# The model swallowed the slash between the masked ``44A`` and the
# unprotected ``844``, inventing the line ``44A844``.
#
# The previous shape ``U[1-6]|S[0-9]+|[1-9][0-9]?[A-Z]?`` covered 58 of the
# 71 line tokens present in the live caches. The 13 it missed:
#
#   844          three-digit regional bus  (only two digits were allowed)
#   86AR         two-letter suffix         (only one letter was allowed)
#   U6E          U-Bahn Verstärker         (no suffix was allowed after U<n>)
#   N8 N20 N29 N43 N46 N49 N65 N66 N71     night buses (no N prefix at all)
#   D            tram D — see _TRAM_LETTER_LINE_RE below
#
# ``86AR``, ``N71`` and ``N31`` came through intact on the audited run. That
# was luck, not protection: an unmasked token survives only as long as the
# model happens to leave it alone.
#
# Every alternative below is a strict SUPERSET of the shape it replaces, so
# no token that used to be protected can lose its protection —
# ``test_the_pattern_is_a_strict_superset_of_the_old_one`` pins that.
# ``S[0-9]+`` deliberately keeps its unbounded digit run rather than being
# tightened to the S1–S80 range actually in service: narrowing is the one
# change here that could unprotect something.
_LINE_ENTITY_RE: re.Pattern[str] = re.compile(
    r"\b("
    r"U[1-6][A-Z]?"  # U1 … U6, plus Verstärker wie U6E
    r"|S[0-9]+[A-Z]?"  # S1 … S80
    r"|N?[1-9][0-9]{0,2}[A-Z]{0,2}"  # 18, 43A, 844, 86AR, N8, N43
    r")\b"
)

# Trams D and O are line identifiers whose surface form is a single letter,
# so they cannot be matched on shape alone — a bare ``D`` is far more often
# prose ("Vitamin D", "Ausgang D") than a tram. They are masked only inside
# a line context: leading a title before the colon, adjacent to a slash in a
# line group, or spelled out after "Linie".
#
# Kept separate from _LINE_ENTITY_RE on purpose. That pattern is also used
# as a ``fullmatch`` predicate to keep line-shaped station names out of the
# station protection regex (see _station_entity_pattern), and a
# context-dependent alternative has no meaningful fullmatch semantics there.
_TRAM_LETTER_LINE_RE: re.Pattern[str] = re.compile(
    r"(?<=/)[DO]\b"  # 43A/D
    r"|\b[DO](?=/)"  # D/43A
    r"|^[DO](?=:)"  # "D: Gleisbauarbeiten"
    r"|(?<=Linie )[DO]\b"  # "Linie D"
)


# German compound-noun suffixes that mark a token as a street / public-
# space proper noun. The masker treats any word ending in one of these
# suffixes — and capitalised at the start — as an entity to preserve
# verbatim. Live regressions on the public feed (2026-05-22):
#
#   * ``Pasettistraße`` → "Pasetti Street"
#   * ``Landstraßer Hauptstraße`` → "Landstraßer main road"
#
# The model heuristically renders ``-straße`` as "Street" / "road" once
# the word stem is unknown; by masking the entire compound we keep the
# Austrian street name as a single recognisable label in the EN feed.
# The Latin-1 supplement umlauts (``ä``/``ö``/``ü``/``ß``) are part of
# ``\w`` under Python 3 ``re`` so the ``\b`` word boundary works
# naturally on tokens like ``Hellwagstraße``.
#
# Zwei Lücken kosteten das Schild am 2026-09-17 im Livefeed (Befund C.4,
# ``docs/archive/audits/audit-2026-09-17.md``):
#
#   * ``Hintere Zollamtsstr.`` → **"rear customs office"**. WL kürzt
#     ``Straße`` routinemäßig zu ``Str.`` ab; die Abkürzung stand nicht in
#     der Alternation, der Name wurde also GAR NICHT maskiert und
#     vollständig übersetzt. Aus einem Straßennamen wurde die Bezeichnung
#     eines Gebäudetyps — wer im EN-Feed danach sucht, findet die Straße
#     nicht, und wer es liest, sucht ein Zollamt.
#   * ``Vordere Zollamtsstraße`` → "Front Zollamtsstraße",
#     ``Kleine Marxerbrücke`` → "Small Marxerbrücke". Das Muster verlangte,
#     dass das Suffix am selben Wort klebt, also blieb ein vorangestelltes
#     Adjektiv außerhalb der Spanne und wurde übersetzt.
#
# Dass ``Hintere Zollamtsstraße`` (ausgeschrieben) heil blieb, war Zufall:
# Der Schutz kam aus dem Stationsverzeichnis (Pass 2), weil zufällig eine
# Haltestelle dieses Namens existiert — nicht von diesem Muster. Ein Test,
# der gegen den fertigen Feed statt gegen den Masker prüft, bestünde aus
# ebendiesem Zufallsgrund; ``tests/test_street_name_masking.py`` prüft
# deshalb direkt hier.
#
# Der Adjektiv-Kopf ist bewusst eine GESCHLOSSENE Liste. Freistehende Namen
# mit einem Wort auf ``-er`` davor (``Mariahilfer Straße``) fasst seit dem
# EN-Audit vom 2026-10-03 ein eigener Zweig, ``_STREET_ER_NAME`` unten, mit
# einer Ausschlussliste für die Determinative (``Dieser Platz``, ``Jeder
# Weg``). Die frühere Annahme, solche Namen kämen unverändert durchs Modell,
# hat der Vergleich aller veröffentlichten DE/EN-Paare widerlegt.
#: Suffixe, die ein Wort als Straßen-/Platznamen ausweisen.
_STREET_SUFFIX_CORE = (
    r"[Ss]traße|[Ss]trasse|[Gg]asse|[Pp]latz|[Bb]rücke|[Bb]rucke"
    r"|[Mm]arkt|[Ww]eg|[Rr]ing|[Aa]llee|[Ss]tieg|[Ss]teig"
    r"|[Pp]romenade|[Kk]ai|[Gg]raben|[Zz]eile"
    # Standing alone these three were already street words
    # (:data:`_STREET_WORD`), in a compound not: ``Neubaugürtel`` reached the
    # EN feed as "New belt" (Baustellen title, 2026-09-02).
    r"|[Gg]ürtel|[Ll]ände|[Dd]amm"
)
#: Deklinierte Adjektive, die in Wiener Straßennamen vor dem Namen stehen.
#: Nur solche, die als eigenes Wort ein echtes englisches Wort ergeben —
#: genau die, die das Modell übersetzt.
_STREET_ATTRIBUTE = (
    r"Vordere|Hintere|Obere|Untere|Große|Grosse|Kleine|Alte|Neue"
    r"|Linke|Rechte|Lange|Kurze|Innere|Äußere|Aeussere"
)
#: Zusammengesetzt (``Zollamtsstraße``) oder abgekürzt (``Zollamtsstr.``).
#: Beim abgekürzten Zweig sitzt ``\b`` VOR dem Punkt: Nach ``str.`` folgt
#: meist ein Leerzeichen, und zwischen zwei Nicht-Wort-Zeichen gibt es
#: keine Wortgrenze — ein nachgestelltes ``\b`` würde den Zweig nie greifen
#: lassen.
#:
#: Der Genitiv der männlichen und sächlichen Suffixe (``Algenwegs``,
#: ``Aumannplatzes``, ``Margaretengürtels``) ist ein eigener Zweig: Das
#: ``\b`` hinter dem Suffix ließ ``des Algenwegs`` ungeschützt, das Modell
#: schrieb "of the algae path" (89A, Jules-Audits 26. und 27.10.2026).
#: :func:`_street_names_nominative` stellt im englischen Text den Namen in
#: der Grundform wieder her, wie er am Straßenschild steht.
_STREET_GENITIVE_SUFFIX = (
    r"(?:[Ww]eg|[Pp]latz|[Mm]arkt|[Rr]ing|[Ss]tieg|[Ss]teig|[Dd]amm)(?:es|s)"
    r"|(?:[Kk]ai|[Gg]raben|[Gg]ürtel)s"
)
#: Gattungswörter mit einem Straßensuffix: ``Bahnsteig``, ``Gehsteig``,
#: ``Radweg``, ``Flohmarkt``, ``Bahnhofsvorplatz``. Im Genitiv gingen sie
#: schon immer durchs Modell und lasen richtig ("Sanierung des Bahnsteigs"
#: → "renovation of the platform", "eines Flohmarktes" → "a flea market");
#: der Genitiv-Zweig lässt sie dort, auch am Ende eines Kompositums
#: (``Ersatzgehsteiges``). Eine GESCHLOSSENE Liste wie
#: ``_STREET_ER_EXCLUDED``. Die Grundform bleibt, wie sie war (siehe
#: ``test_known_gap_bahnsteig_is_shielded_as_if_it_were_a_street``): WL
#: schreibt Haltestellen wie ``Cobenzl, Parkplatz``, die das
#: Stationsverzeichnis nicht kennt und nur dieser Schutz heil lässt.
#:
#: Seit der Zweitprüfung vom 27.10.2026 auch die Gattungswörter, die WL in
#: der Grundform schreibt (``Einstieg`` in 327 Texten seit Juli, ``Zustieg``,
#: ``Umstieg``, ``Bussteig``, ``Campingplatz``), die übrigen Wörter auf
#: ``-stieg`` mit einer Präposition davor, häufige Gattungswörter auf
#: ``-platz``, ``-weg``, ``-markt`` und ``-damm`` und die Adverbien auf
#: ``-wegs``: Im Genitiv las das Modell sie richtig ("closure of entry",
#: "edge of the playground"), geschützt stand das deutsche Wort im
#: englischen Text ("closure of Einstieg").
_STREET_COMMON_NOUNS = (
    r"[Bb]ahnsteig|[Gg]ehsteig|[Bb]ussteig|[Rr]adweg|[Gg]ehweg|[Ff]u(?:ß|ss)weg|[Uu]mweg"
    r"|(?:[Ee]in|[Aa]us|[Uu]m|[Zz]u|[Aa]uf|[Aa]b|[Üü]ber)stieg"
    r"|[Rr]ettungsweg|[Ff]luchtweg|[Ss]chulweg|[Ff]ahrweg|[Zz]ufahrtsweg|[Zz]ugangsweg"
    r"|[Ll]ieferweg|[Ss]eitenweg"
    r"|[Vv]orplatz|[Pp]arkplatz|[Ss]tellplatz|[Aa]bstellplatz|[Hh]alteplatz"
    r"|[Ss]pielplatz|[Ss]portplatz|[Aa]rbeitsplatz|[Cc]ampingplatz"
    r"|[Ff]lohmarkt|[Bb]aumarkt|[Ss]upermarkt|[Ww]eihnachtsmarkt"
    r"|[Aa]dventmarkt|[Cc]hristkindlmarkt|[Bb]ahndamm"
    r"|[Uu]nterweg|[Dd]urchweg|[Kk]einesweg|[Hh]albweg|[Gg]eradeweg"
)
_STREET_NAME_BODY = (
    r"[A-ZÄÖÜ][A-Za-zÄÖÜäöüß\-.]{1,30}(?:" + _STREET_SUFFIX_CORE + r")\b"
    r"|(?!(?:[A-ZÄÖÜ][a-zäöüß]*)?(?:" + _STREET_COMMON_NOUNS + r")(?:es|s)\b)"
    r"[A-ZÄÖÜ][A-Za-zÄÖÜäöüß\-.]{1,30}(?:" + _STREET_GENITIVE_SUFFIX + r")\b"
    r"|[A-ZÄÖÜ][A-Za-zÄÖÜäöüß\-]{1,30}[Ss]tr\b\.?"
)
#: Ein freistehender Straßenname mit einem Ortsadjektiv auf ``-er`` davor:
#: ``Mariahilfer Straße``, ``Südtiroler Platz``, ``Währinger Gürtel``,
#: ``Simmeringer Hauptstraße``. Die Annahme oben, solche Namen kämen heil
#: durchs Modell, hat der EN-Audit vom 2026-10-03 widerlegt: ``Südtiroler
#: Platz`` → "South Tyrolean square" (2026-09-29), ``Währinger Gürtel`` →
#: "moving belt" (2026-09-26), ``Schloßhofer Straße`` → "Schlosshofer
#: Straße" (2026-10-03); 41 solche Namen erreichten das Modell ungeschützt.
#: Die befürchteten Determinative sind eine geschlossene Wortklasse und hier
#: vollständig ausgeschlossen, ebenso die großgeschriebenen Adverbien und
#: Präpositionen auf ``-er``, die einen Satz eröffnen können.
_STREET_WORD = (
    r"(?:Straße|Strasse|Gasse|Platz|Gürtel|Ring|Allee|Weg|Brücke|Kai|Lände"
    r"|Zeile|Markt|Damm|Berg|Anger|Promenade|Graben|Steig|Schleife)\b|Str\."
    # Genitive after an "-er" name: "des Währinger Gürtels", "des
    # Floridsdorfer Marktes".
    r"|(?:Platz|Ring|Weg|Markt|Damm|Steig)(?:es|s)\b|(?:Gürtel|Kai|Graben)s\b"
)
_STREET_ER_EXCLUDED = (
    r"Der|Dieser|Jener|Jeder|Welcher|Mancher|Solcher|Aller|Keiner|Einer"
    r"|Meiner|Deiner|Seiner|Ihrer|Unser|Unserer|Euer|Eurer|Anderer|Weiterer"
    r"|Erster|Zweiter|Dritter|Letzter|Nächster|Über|Unter|Hinter|Wieder"
    r"|Weiter|Aber|Oder|Außer|Gegenüber|Hier|Seither|Bisher|Vorher|Nachher"
    r"|Daher|Leider|Später|Immer|Mehr|Sehr|Wer|Er|Ihr"
)
_STREET_ER_NAME = (
    r"(?!(?:" + _STREET_ER_EXCLUDED + r")\s)[A-ZÄÖÜ][a-zäöüß]+er\s+"
    r"(?:" + _STREET_NAME_BODY + r"|" + _STREET_WORD + r")"
)
_STREET_SUFFIX_RE: re.Pattern[str] = re.compile(latin_names(
    r"\b(?:" + _STREET_ATTRIBUTE + r")\s+(?:" + _STREET_NAME_BODY + r")"
    r"|\b" + _STREET_ER_NAME
    + r"|\b(?:" + _STREET_NAME_BODY + r")"
))

# A street named after a person, written with spaces as WL's tickers do:
# "Julius Raab Platz", "Victor Braun Platz". Neither word carries a street
# suffix, so nothing shielded the name and the model translated its last word
# ("Julius Raab Square", "Victor Braun place 1"; EN check 2026-10-08). Two
# capitalised words in front of a street word; neither may be an article,
# preposition or one of WL's own nouns, so a sentence such as "Die Linie
# Platz …" cannot form a name, and the second is no "-er" place adjective
# ("Kreuzung Währinger Straße" is a noun and a street, which
# ``_STREET_ER_NAME`` shields on its own).
_SPACED_NAME_EXCLUDED = (
    _STREET_ER_EXCLUDED
    + r"|Die|Das|Den|Dem|Des|Ein|Eine|Am|An|Auf|Im|In|Bei|Beim|Vom|Von|Zum|Zur"
    r"|Nach|Vor|Über|Wegen|Ab|Bis|Ecke|Höhe|Richtung|Fahrtrichtung|Haltestelle"
    r"|Haltestellen|Station|Linie|Linien|Bahnhof|Umleitung|Betrieb|Busse|Züge"
    r"|Kreuzung|Einmündung"
)
_SPACED_STREET_NAME_RE: re.Pattern[str] = re.compile(latin_names(
    r"\b(?!(?:" + _SPACED_NAME_EXCLUDED + r")\s)[A-ZÄÖÜ][a-zäöüß]+"
    r" (?!(?:" + _SPACED_NAME_EXCLUDED + r")\s)(?![A-ZÄÖÜ][a-zäöüß]+er\s)[A-ZÄÖÜ][a-zäöüß]+"
    r" (?:Platz|Gasse|Straße|Allee|Ring|Weg|Brücke|Markt|Promenade|Kai|Zeile"
    r"|Steig|Lände|Damm|Gürtel)\b"
))

# Vienna's places whose names are ordinary German nouns. WL writes them bare,
# and the model translated them: "am Ring" → "on the ring", "Ring, Volkstheater
# U" → "ring, Volkstheater U", "Richtung Oper" → "in the direction of opera",
# "im Bereich Innere Stadt" → "in the area of Inner City", "Kurpark,
# Nordosteingang" → "spa park, northeast entrance", "Umleitung über Gürtel"
# → "diversion over belt" (EN check 2026-10-08). A name stays a name in the
# EN feed, as on the stop sign. Case-sensitive: "Ring" and "Oper" as WL
# writes them, never a lower-case word.
_PLACE_NAME_ENTITIES: tuple[str, ...] = (
    "Kurpark, Nordosteingang",
    "Kurpark Nordosteingang",
    "Innere Stadt",
    "Kurpark",
    "Gürtel",
    "Oper",
    "Ring",
)
_PLACE_NAME_ENTITY_RE: re.Pattern[str] = re.compile(
    r"(?<![\w-])(?:" + "|".join(re.escape(name) for name in _PLACE_NAME_ENTITIES) + r")(?![\w-])"
)

# A street named with a preposition: "An den Eisteichen", "Am Kaisermühlendamm",
# "Auf der Schmelz". No suffix marks it, and the directory knows only the ones
# that are stops ("Am Schöpfwerk"); "Falschparker im Bereich An den Eisteichen /
# Zanaschkagasse" read "in the area of At the ice ponds" (Jules audits 28. and
# 29.10.2026, 58 WL texts since July). Inside a sentence German writes a
# preposition in lower case, so a capitalised one there opens a name. At the
# start of a sentence, a field or a list item ("Am Samstag …", "Zeitraum: Am
# 1. November") it is an ordinary preposition and stays with the model.
_PREPOSITION_NAME_RE: re.Pattern[str] = re.compile(latin_names(
    r"(?P<lead>[^\s.:!?;] |\()"
    r"(?P<name>(?:Am|An der|An den|An dem|Auf der|Auf dem|Auf den|In der|In den"
    r"|Im|Zum|Zur|Unter den|Hinter der|Bei der|Beim|Vor dem|Neben der)"
    r" (?!(?:Montag|Dienstag|Mittwoch|Donnerstag|Freitag|Samstag|Sonntag|Wochenende)\b)"
    r"(?:[A-ZÄÖÜ][a-zäöüß]+(?:en|er|e) )?[A-ZÄÖÜ][a-zäöüß]+(?:-[A-ZÄÖÜ][a-zäöüß]+)*)(?![\w-])"
    # A street with its own suffix is the name already: "wird In der
    # Volksgartenstraße …" (Stadt Wien) is a capitalised slip, not "In der" +
    # name. "Am Kaisermühlendamm", "Am Rollerdamm" keep the preposition.
    r"(?<!straße)(?<!strasse)(?<!gasse)"
))


# The place a WL line runs towards is a stop's name, never a word to
# translate. The directory and the street rules above know most of them; a
# bare one-word name they miss went through the model, typos included
# ("Richtung Stammersdrof" → "towards Stammersdrif"). The word right after
# "Richtung" is shielded unless it is a compass direction or a generic noun
# the model should translate ("Richtung Süden", "Richtung Zentrum").
_DIRECTION_NAME_RE: re.Pattern[str] = re.compile(latin_names(
    r"(?<=\bRichtung )(?!(?:Süden|Norden|Osten|Westen|Zentrum|Stadtzentrum|Stadtmitte"
    r"|Innenstadt|Stadtgrenze|Stadt|Wien|Haltestellen?|Linien?|Stationen?|Bahnhof"
    r"|Endstelle)\b)[A-ZÄÖÜ][a-zäöüß][A-Za-zÄÖÜäöüß-]*(?![\w:-])"
))

# A main station as ÖBB writes it: ``Wien Hbf``, ``St.Pölten Hbf``,
# ``Wr.Neustadt Hbf``. The directory knows the long forms only, so the model
# saw the short ones and turned 96 ``Wien Hbf`` into "Vienna Hbf" while the
# same item kept "Wien Meidling" (EN audit 2026-10-03).
_MAIN_STATION_RE: re.Pattern[str] = re.compile(latin_names(
    r"(?<![\w.])(?:[A-ZÄÖÜ][a-zäöüß]{1,4}\.\s?)?[A-ZÄÖÜ][A-Za-zÄÖÜäöüß-]+ Hbf\b"
))

# WL numbers the gates of the Zentralfriedhof and addresses the stops
# by them ("Zentralfriedhof, 4. Tor" — the name printed on the stop
# sign). ``Tor`` is not a street suffix, so :data:`_STREET_SUFFIX_RE`
# never shielded it and Marian rendered the SAME stop inconsistently
# across items of one feed: "Zentralfriedhof, 3. Gate" in one title,
# "Zentralfriedhof, 3. Tor" in another
# (``docs/archive/audits/audit-2026-09-05.md`` §6). Masking the whole
# ``<Ziffer>. Tor`` group keeps the official stop designation verbatim,
# which is also what a traveller reads on site.
_GATE_SUFFIX_RE: re.Pattern[str] = re.compile(r"\b\d{1,2}\.\s?[Tt]or\b")

# A calendar date is one token to a reader and three to the line masker.
# ``_LINE_ENTITY_RE`` treats the day of ``27.09.2026`` as a line-shaped bare
# number and masks it alone, so the model sees ``XENT…X3X.09.2026`` — a
# placeholder glued to a bare period, the shape that already cost the ``N6``
# item its house number (see ``_RESIDUAL_PLACEHOLDER_RE``). Marian drops the
# period in that position: of the 8 dated titles translated since
# mid-August 2026, 3 shipped without it — ``U2: Folding ramps out of service
# on 2709.2026`` and ``27A/28A/29A: Event on 2709.2026`` (both 2026-09-24
# 19:31, the day before the 16:15 build had kept the same date intact) and
# ``Update 1 (1609.2026 07:22) …`` (ÖBB, 2026-09-16).
#
# Masked as ONE verbatim entity ahead of the line pass, the date is restored
# exactly, and a date the model drops fails the translation through
# ``_entities_dropped_by_translation`` instead of shipping mutilated. Day and
# month with one or two digits, the year with four, as WL and ÖBB write them.
_DATE_ENTITY_RE: re.Pattern[str] = re.compile(r"\b\d{1,2}\.\d{1,2}\.\d{4}\b")
