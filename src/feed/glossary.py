"""ÖPNV domain glossary for the English feed: pure data, no logic.

Moved out of ``src/build_feed.py`` on 2026-10-15 (Michael's decision after
Jules' modularisation audit of 2026-10-14). Only these data tables moved;
every function that reads them (``_resolve_glossary``,
``_apply_domain_glossary``, the masking and the translation) stays in
``build_feed``, which imports the tables under the same names. That keeps
the 264 ``patch.object(bf, …)`` mocks of the test suite working, which a
move of the logic would break silently.

Changing an entry changes the EN feed: bump ``_TRANSLATION_CACHE_EPOCH`` in
``src/build_feed.py`` in the same PR (see ``docs/architecture.md`` §8).
"""

from __future__ import annotations

# The tables keep their private names from ``build_feed``, which imports them
# (code scanning counts an unlisted ``_name`` as unused).
__all__ = [
    "_FIELD_LABEL_EN",
    "_GLOSSARY_BASE",
    "_GLOSSARY_BY_CATEGORY",
    "_GLOSSARY_BY_SOURCE",
]

# ÖPNV domain glossary. The Helsinki opus-mt-de-en model has only
# seen everyday German prose during training; it routinely
# mistranslates Austrian transit jargon because the closest token in
# its vocabulary lives in a different domain. Live regressions
# observed on the public feed (2026-05-22):
#
#   * ``Betriebsstörung``        → "Harmful vehicle"   (sic)
#   * ``Fahrtbehinderung``       → "Disability"
#   * ``Aufgelassen``            → "Open"               (opposite meaning!)
#   * ``Hauptfahrbahn``          → "main runway"
#   * ``Schadhaftem Fahrzeug``   → "Harmful vehicle"
#
# Each entry below is a German source token mapped to its canonical
# English equivalent in the transit domain. The mapping is consumed
# by :func:`_apply_domain_glossary` BEFORE the model sees the text:
# the German token is replaced by an ``XGLO…`` placeholder whose
# mapping entry carries the *English* term. After the model returns
# its (now correct, because the problematic word is gone) translation,
# :func:`_unmask_entities` substitutes the English term back in.
#
# Architecture: three-layer composition driven by the per-item
# metadata (``FeedItem["source"]``, ``FeedItem["category"]``) that
# every provider already attaches to a disruption. At translation
# time the active glossary is the merger of:
#
#   * :data:`_GLOSSARY_BASE` — universally applicable transit jargon
#   * :data:`_GLOSSARY_BY_SOURCE` ``[source]`` — operator-specific
#     vocabulary (Wiener Linien / ÖBB / Stadt Wien Baustellen / …)
#   * :data:`_GLOSSARY_BY_CATEGORY` ``[category]`` — disruption-type
#     specific vocabulary (architectural extension point — empty
#     today because the project has a tight source↔category coupling)
#
# Layering applies later overlays last, so a source / category entry
# can override a base entry when needed. The merger and the compiled
# alternation regex are cached per ``(source, category)`` combination
# via :func:`_resolve_glossary` and :func:`_domain_glossary_pattern`
# — the build pays the merge/compile cost once per unique combination,
# not once per item. Longer compound terms appear first in the
# regex alternation so e.g. ``Schadhaftem Fahrzeug`` beats the
# single-word ``Schadhaftem`` and the model receives one placeholder
# per concept rather than two.
# WL's structured field labels, German → English, in ONE place.
#
# Two consumers derive from this table and must never drift apart:
# :data:`_GLOSSARY_BASE` (which translates the label) and
# :data:`_LABEL_RECORD_RE` (which finds the record so it can be kept away
# from the NMT model). A label present in one but not the other either
# translates inside a block the model still mangles, or is torn out of a
# block whose label then stays German.
#
# Order is irrelevant here — the glossary pattern sorts longest-first, which
# is what makes ``Haltestellen:`` beat ``Haltestelle:``.
_FIELD_LABEL_EN: dict[str, str] = {
    "Haltestellen": "Stops",
    "Haltestelle": "Stop",
    "Zeitraum": "Period",
    "Dauer": "Duration",
    "Grund": "Reason",
    "Nach": "To",
    "Von": "From",
}


_GLOSSARY_BASE: dict[str, str] = {
    # --- Disruption-type nouns --------------------------------------
    "Betriebsstörung": "service disruption",
    "Betriebsstörungen": "service disruptions",
    "Fahrtbehinderung": "service obstruction",
    "Fahrtbehinderungen": "service obstructions",
    "Streckenstörung": "line disruption",
    "Streckensperre": "line closure",
    "Gleissperre": "track closure",
    "Signalstörung": "signal disruption",
    "Weichenstörung": "switch fault",
    "Stellwerksstörung": "interlocking failure",
    # ÖBB spells it without the linking s; the model said "interlocking
    # fault", "interlocking disturbance" (EN audit 2026-10-03).
    "Stellwerkstörung": "interlocking failure",
    "Oberleitungsstörung": "overhead-line fault",
    # Spelled out from the WL ticker stub "Oberleitungsgebr" by
    # ``_TICKER_ABBREVIATIONS``; same fault, Austrian wording.
    "Oberleitungsgebrechen": "overhead-line fault",
    "Stromausfall": "power outage",
    "Verspätung": "delay",
    "Verspätungen": "delays",
    "Fahrtausfall": "service cancellation",
    "Fahrtausfälle": "service cancellations",
    "Zugausfall": "train cancellation",
    "Zugausfälle": "train cancellations",
    # --- Disruption resolution / progress markers -------------------
    # Marian's defaults read awkwardly here ("behoben" → "fixed",
    # "Behebung" → "elimination"). The transit-domain natural English
    # mirrors how UK / Irish operators phrase the same updates.
    "behoben": "resolved",
    "Behebung": "resolution",
    "Ursache": "cause",
    "wird untersucht": "is being investigated",
    "wird geprüft": "is being checked",
    # --- Cancelled stops --------------------------------------------
    # Operator-agnostic (WL: "Halt am Karlsplatz entfällt"; ÖBB: "Halt
    # in St. Pölten entfällt"). Sits in base because the surface form
    # and the EN rendering are identical across operators — there is
    # no operator-specific override to write.
    "Halt entfällt": "stop omitted",
    "Halt entfallen": "stop omitted",
    "Halte entfallen": "stops omitted",
    # --- Structured field labels ------------------------------------
    # WL publishes stop relocations as a label block rather than prose::
    #
    #     Haltestelle: Kraftwerk Simmering
    #     Von: 1. Haidequerstraße 2
    #     Nach: 1. Haidequerstraße 510
    #     Dauer: Ab …
    #
    # Marian has only seen everyday prose, where ``Nach`` is overwhelmingly
    # temporal, so it renders the label as "After:" — published 2026-09-18::
    #
    #     DE: Von: Anzengruberstraße gegenüber 77a  Nach: Hüttergasse 6A-6B
    #     EN: From: Anzengruberstraße opposite 77 a  After: Hüttergasse 6A-6B
    #
    # Paired with a correct "From:" that reads as nonsense: a relocation with
    # an origin and no destination. ``Dauer:`` alone came back as "expected
    # duration:", borrowing the "expected" from the neighbouring
    # ``voraussichtliche Dauer`` entry and asserting a qualifier the source
    # never made.
    #
    # The trailing colon is load-bearing, and it is the whole reason these
    # can live in a global glossary at all. ``Nach`` and ``Von`` are ordinary
    # prepositions; keyed bare they would rewrite "Umleitung nach
    # Hofmühlgasse" and "Von Montag bis Freitag". Keyed WITH the colon they
    # match only where the word is a field label — the glossary pattern
    # ``re.escape``s each key and closes with ``(?!\w)``, which a colon
    # satisfies. Verified against those three phrases in
    # ``test_structured_field_labels.py``.
    #
    # Scope is what reaches a subscriber: these six labels are the ones
    # attested across 154 distinct published items. The Stadt-Wien
    # roadworks source also emits ``Beginn:`` / ``Maßnahme:`` / ``Bezirk:``,
    # but those sit past the description truncation and appear in no
    # published item, so their English is unverified and they are left out.
    # The term these blocks open with, and the model renders it a different
    # way every time: across the published history one item said "Stop
    # change", one "Station relocation", the live 12A/14A item "Stop
    # transfer", and two were not translated at all. One WL term, four
    # English faces — exactly what this glossary exists to stop.
    "Haltestellenverlegungen": "stop relocations",
    "Haltestellenverlegung": "stop relocation",
    # The sister compound for a stop withdrawn for good. Without an entry
    # the model took it apart: "Haltestellenauflassung der Linie N31" reached
    # the EN feed as "Stop stop on line N31" (2026-09-24 16:30, after the
    # epoch-16 re-translation; "Station departure of line N31" before).
    # 4 of 563 WL items in the cache history carry it, 1 the plural.
    "Haltestellenauflassungen": "stop closures",
    "Haltestellenauflassung": "stop closure",
    **{f"{de}:": f"{en}:" for de, en in _FIELD_LABEL_EN.items()},
    # The qualified label WL's incident template opens its record with (see
    # ``_split_label_record``), one capitalised label like the others.
    "voraussichtliche Dauer:": "Expected duration:",
    # Spelled-out sibling of the ``ggü.`` entry above. WL uses both forms in
    # relocation addresses ("Anzengruberstraße gegenüber 77a"); only the
    # abbreviation was covered, so the model had to guess at the long form —
    # and once the record stops going through the model, guessing is not an
    # option any more.
    "gegenüber": "opposite",
    # --- Construction / works ---------------------------------------
    "Gleisbauarbeiten": "track construction works",
    "Gleisarbeiten": "track works",
    "Gleisschaden": "track damage",
    "Bauarbeiten": "construction works",
    "Wartungsarbeiten": "maintenance works",
    "Reparaturarbeiten": "repair works",
    "Kranarbeiten": "crane works",
    "Rohrleitungsarbeiten": "pipeline works",
    "Straßenbauarbeiten": "roadworks",
    # --- Time / expectancy adverbs ----------------------------------
    # Marian translates "voraussichtlich" as "presumably" which reads
    # awkwardly in a disruption / construction timeline. The transit
    # use case is always "expected to last / end at …".
    "voraussichtlich": "expected",
    "voraussichtliche Dauer": "expected duration",
    "voraussichtliches Ende": "expected end",
    # As one phrase, or "bis" and "expected" meet: the model rendered
    # "bis voraussichtlich 11:00 Uhr" as "to expected 11:00" (ÖBB, audit
    # 2026-09-25, A.3), and the label record as "until expected end of
    # November 2026". "approx." is the record's own word for "etwa".
    "bis voraussichtlich": "until approx.",
    "bis auf Weiteres": "until further notice",
    # --- Replacement services ---------------------------------------
    "Schienenersatzverkehr": "rail replacement service",
    "Ersatzverkehr": "replacement service",
    "Ersatzbus": "replacement bus",
    # WL's wording for the ÖBB rail replacement bus. Split up, "ÖBB" stays a
    # masked brand and "Ersatzbus" a glossary term, and the German hyphen
    # survives between them: "S80: ÖBB-replacement bus" (2026-09-26).
    "ÖBB-Ersatzbus": "ÖBB replacement bus",
    "ÖBB-Ersatzbusse": "ÖBB replacement buses",
    # --- Emergencies / events ---------------------------------------
    "Polizeieinsatz": "police operation",
    "Rettungseinsatz": "rescue operation",
    "Notarzteinsatz": "ambulance operation",
    "Feuerwehreinsatz": "fire-brigade operation",
    "Verkehrsunfall": "traffic accident",
    # Measured gaps in the disruption vocabulary, each with its published
    # mistranslation. They are ordinary German compounds, so the model
    # renders them literally and lands somewhere between odd and wrong:
    #
    #   Fremdunfall          → "Foreign accident"   (9 items)
    #   Falschparker         → "False Parker" /
    #                          "wrong parker"       (7 items, two renderings)
    #   Verunreinigung       → "Impurity" /
    #                          "contamination"      (4 items, two renderings)
    #   Wasserrohrgebrechen  → "Water pipe fractures" (2 items)
    #   Klapprampensperre    → "Folding ramp lock"  (3 items)
    #
    # ``Fremdunfall`` is WL's term for an accident caused by someone outside
    # the network; "Foreign accident" reads as one that happened abroad.
    # ``Falschparker`` is a vehicle parked in the way — "False Parker" reads
    # as a surname. ``Klapprampensperre`` means the wheelchair ramps cannot
    # be extended, which "lock" does not convey.
    "Fremdunfall": "third-party accident",
    # The long form of the same WL reason, and the most frequent reason the
    # glossary did not know: 185 WL titles since July. The model guessed a
    # new adjective every time, "foreign", "external", "other", "stranger",
    # "Foreigner", and a label record kept it German ("Reason: Fremder
    # traffic accident", 2026-10-03 11:31, under epoch 19).
    "Fremder Verkehrsunfall": "third-party traffic accident",
    "Fremdem Verkehrsunfall": "third-party traffic accident",
    "Falschparker": "illegally parked vehicle",
    "Verunreinigung": "contamination",
    "Wasserrohrgebrechen": "burst water pipe",
    "Klapprampensperre": "folding ramps out of service",
    # Consistent with ``Betriebsstörung`` → "service disruption" above; the
    # model said "Traffic disturbance".
    "Verkehrsstörung": "traffic disruption",
    "Verkehrsüberlastung": "traffic congestion",
    # The rest of WL's reason vocabulary the glossary lacked, measured over
    # the DE/EN pairs of every published feed since 2026-05-20 (EN audit
    # 2026-10-03). Count of WL titles, then what the model made of it:
    #
    #   Stromstörung                    55  "current disturbance",
    #                                       "Current Disruption"
    #   Beschädigte Oberleitung         47  "Corrupted overhead line"
    #   Störung an einem Bahnübergang   21  "Interference at a level crossing"
    #   Weichenschaden                  15  "Soft damage"
    #   Gasrohrgebrechen                 8  "gas pipe fractures"
    #   Fahrleitungsgebrechen            3  dropped ("service obstruction")
    #
    # In a label record ("Grund: Stromstörung im Haltestellenbereich …")
    # nothing renders an unknown term, so it stayed German there as well.
    "Stromstörung": "power failure",
    "Stromstörungen": "power failures",
    "Beschädigte Oberleitung": "damaged overhead line",
    "Störung an einem Bahnübergang": "level crossing fault",
    "Weichenschaden": "switch damage",
    "Gasrohrgebrechen": "burst gas pipe",
    "Fahrleitungsgebrechen": "overhead-line fault",
    "Wasserrohrbruch": "burst water pipe",
    "Tiere im Gleis": "animals on the track",
    "Fahrzeug im Gleis": "vehicle on the track",
    "Gegenstand im Gleis": "object on the track",
    "Gegenstände im Gleis": "objects on the track",
    "Gegenstand auf der Fahrleitung": "object on the overhead line",
    "Witterung": "weather conditions",
    "Vandalismus": "vandalism",
    "Staatsbesuch": "state visit",
    "Veranstaltung": "event",
    # A stadium's name, not a word: "Alliance Stadion" (2026-10-03) and
    # "Allianz Stadium". Kept as one name, the genitive folded in.
    "Allianz Stadion": "Allianz Stadion",
    "Allianz Stadions": "Allianz Stadion",
    "Demonstration": "demonstration",
    # --- State / mode adjectives ------------------------------------
    "Schadhaftes Fahrzeug": "defective vehicle",
    "Schadhafter Fahrzeug": "defective vehicle",
    "Schadhaftem Fahrzeug": "defective vehicle",
    "Schadhafter LKW": "defective truck",
    "Schadhaftem LKW": "defective truck",
    # The same family, and the gap was visible: without these the model
    # rendered ``Schadhafter Zug`` as "Harmful train", ``Schadhafter Bus`` as
    # "Harmful bus" and ``Schadhafter PKW`` as "Harmful car" — 7 published
    # items telling English readers the vehicle is dangerous rather than
    # broken. Both inflections, mirroring how ``LKW`` above is handled.
    "Schadhafter Zug": "defective train",
    "Schadhaftem Zug": "defective train",
    "Schadhafter Bus": "defective bus",
    "Schadhaftem Bus": "defective bus",
    "Schadhafter PKW": "defective car",
    "Schadhaftem PKW": "defective car",
    "Hauptfahrbahn": "main carriageway",
    "Aufgelassen": "Discontinued",
    "Aufgelassene": "Discontinued",
    # The full WL phrase for a stop withdrawn for good. Longest match wins
    # over the bare "Aufgelassen" above, whose fixed capital otherwise lands
    # mid-sentence: the N31 title reached the EN feed as "Stammersdorf
    # replacementless Discontinued" (2026-09-24).
    "ersatzlos aufgelassen": "closed without replacement",
    # "bis Betriebsschluss": the model rendered it "operation close" (77A,
    # 2026-08); inside a label record nothing renders it at all.
    "Betriebsschluss": "end of service",
    # WL's other two spellings behind "Voraussichtliche Dauer:" (four texts
    # since July), left German in the EN record ("Expected until
    # Betriebsschluß").
    "Betriebsschluß": "end of service",
    "Betriebschluss": "end of service",
    "Personen im Gleisbereich": "persons on the tracks",
    # --- Boarding / stop-access vocabulary ---------------------------
    # WL Störung items are headlined with the bare noun ("Einstieg bei
    # Vorgartenstraße vor Meiereistraße"). Without a glossary entry the
    # token reaches the ENTITY masker, where it matches the street
    # suffix ``…stieg`` of :data:`_STREET_SUFFIX_RE` and is preserved
    # verbatim — the EN feed published the half-German "Einstieg for
    # Vorgartenstraße before Meiereistraße"
    # (``docs/archive/audits/audit-2026-09-10.md``). The glossary runs
    # BEFORE the masker, so mapping the word here both fixes the
    # translation and keeps it out of the street-name shield.
    "Einstieg": "boarding",
    "Ausstieg": "alighting",
    "Umstieg": "interchange",
    "Zustieg": "boarding",
    # --- Area / section phrasing -------------------------------------
    # "Bereich" in front of a street name marks a section of the
    # network, not a numeric range; Marian's "range" reading produced
    # "Hernalser range Hauptstraße" for "Bereich Hernalser
    # Hauptstraße" (``docs/archive/audits/audit-2026-09-09.md``). The
    # longest-first alternation makes the "im Bereich" phrase win over
    # the bare noun.
    "im Bereich": "in the area of",
    "Bereich": "area",
    # The stop-side sibling, in 1 of 3 WL incident reasons. In a label record
    # it stayed German ("Reason: rescue operation im Haltestellenbereich
    # Taubstummengasse", 2026-10-03 10:23); as prose the model said "in the
    # stop area X", "in the X stop area" or dropped the noun ("traffic
    # accident in the Kaisermühlen-VIC U").
    "im Haltestellenbereich": "in the stop area of",
    "Haltestellenbereich": "stop area",
    # Vienna's numbered districts. Marian renders the bare noun as "Area" or
    # "District" depending on the surrounding sentence — the SAME stop text
    # came out as "in direction 21. District" and, after the sentence was
    # shortened by the glue repair, as "in the direction of 21. Area". The
    # glossary entry makes it deterministic.
    "Bezirk": "district",
    "Bezirke": "districts",
    "Bezirken": "districts",
    # --- Events ------------------------------------------------------
    # "Straßenfest" is a compound of two words Marian knows separately
    # ("Straße" + "fest"), which is how "Währinger Straßenfest" became
    # "maintenance of the road fence"
    # (``docs/archive/audits/audit-2026-09-06.md``).
    "Straßenfest": "street festival",
    "Straßenfestes": "street festival",
    "Umleitung": "diversion",
    "Unregelmäßige Intervalle": "irregular intervals",
    # --- EN sense errors, check of 2026-10-08 ---------------------------
    # Every distinct German text of the feed since July went through the
    # real model; these phrases came out with a wrong or no sense. Count of
    # texts, then what the model made of it:
    #
    #   unterschiedlichen Intervallen   46  "different intervals" (WL's
    #                                       second phrase for irregular
    #                                       intervals, beside the one above)
    #   über Gleis 1 / 2                 9  "via 2 track", "via 1 track"
    #   kurzgeführt / kurz geführt      13  "line 5A operating short-
    #                                       running", "line 14A is short"
    #   Ausgang, Sperre, gesperrt        6  "Lock output", "Curfew", "must
    #                                       be locked"
    #   Weichentausch                    2  "Switch", "switchover"
    #   betriebslose Zeit                3  "busy time", "unoccupied time"
    #   Nebenfahrbahn                    1  "sidecar"
    #   aufrecht(er)halten               4  "held upright"
    #   Umleitung / verlegt nach         4  "according to" for "nach"
    #
    # The masked forms ("nicht angefahren", "unmittelbar nach", "Nr.",
    # "Bahnsteig") are what ``_normalise_for_translation`` writes for WL's
    # "nicht eingehalten", a cross street behind "nach", "ONr." and "Steig".
    "unterschiedlichen Intervallen": "irregular intervals",
    "unterschiedliche Intervalle": "irregular intervals",
    "über Gleis 1": "on track 1",
    "über Gleis 2": "on track 2",
    "Gleis 1": "track 1",
    "Gleis 2": "track 2",
    "kurzgeführt": "curtailed",
    "kurz geführt": "curtailed",
    "Ausgangssperre": "exit closure",
    "Sperre Ausgang": "exit closure",
    "Ausgang": "exit",
    "Sperre": "closure",
    "gesperrt": "closed",
    "Weichentausch": "switch replacement",
    "in der betriebslosen Zeit": "outside operating hours",
    "betriebslosen Zeit": "non-operating hours",
    "betriebslose Zeit": "non-operating hours",
    "Nebenfahrbahn": "service road",
    "aufrecht gehalten": "maintained",
    "aufrechtgehalten": "maintained",
    "aufrecht erhalten": "maintained",
    "aufrechterhalten": "maintained",
    # A glossed participle alone loses its auxiliary in the model ("line 5A
    # curtailed", "The stops … become not served", "can become maintained").
    # ``_normalise_for_translation`` puts "wird"/"werden" right in front of
    # the participle (:data:`_GLOSSED_PARTICIPLE_RE`), so the whole verb is
    # one term; after a modal the infinitive closes it.
    **{
        f"{aux} {participle}": f"{en_aux} {english}"
        for participle, english in (
            ("kurzgeführt", "curtailed"),
            ("kurz geführt", "curtailed"),
            ("gesperrt", "closed"),
            ("nicht angefahren", "not served"),
            ("aufrecht gehalten", "maintained"),
            ("aufrechtgehalten", "maintained"),
            ("aufrecht erhalten", "maintained"),
            ("aufrechterhalten", "maintained"),
        )
        for aux, en_aux in (("wird", "is"), ("werden", "are"))
    },
    **{
        f"{modal} {participle} werden": f"{en_modal} be {english}"
        for participle, english in (
            ("kurzgeführt", "curtailed"),
            ("kurz geführt", "curtailed"),
            ("gesperrt", "closed"),
            ("nicht angefahren", "not served"),
            ("aufrecht gehalten", "maintained"),
            ("aufrechtgehalten", "maintained"),
            ("aufrecht erhalten", "maintained"),
            ("aufrechterhalten", "maintained"),
        )
        for modal, en_modal in (
            ("kann", "can"), ("können", "can"), ("muss", "must"), ("müssen", "must"),
        )
    },
    "bleiben aufrecht": "remain open",
    "bleibt aufrecht": "remains open",
    "Umleitung nach": "diversion to",
    "Haltestelle verlegt nach": "stop moved to",
    "verlegt nach": "moved to",
    "unmittelbar nach": "just after",
    "nicht angefahren": "not served",
    "Bahnsteig": "platform",
    "Nr.": "No.",
    "auf nahegelegene Haltestellen ausweichen": "switch to nearby stops",
    # "im Bereich der Wollzeile" came out "in the area of of Wollzeile": the
    # phrase above takes "im Bereich" and the model renders the article again.
    "im Bereich der": "in the area of the",
    "im Bereich des": "in the area of the",
    "Eingeschränkter Betrieb": "restricted service",
    # --- Compound idioms specific to ÖBB/WL ticker text ------------
    "Betrieb ab": "service from",
    # WL's ticker verbs. The model rendered "Kein Betrieb" as "No holding"
    # (2026-09-26), "Züge halten in Schleife" as "Trains keep in loop"
    # (still in the feed on 2026-10-03) and "Fahrtbehinderung wegen …" with
    # "because of", "for" or "due to" from one build to the next.
    "Kein Betrieb": "no service",
    "Betrieb nur zwischen": "service only between",
    # The heads of the short form of a disruption on several lines
    # (``_lines_in_short``): "Umleitungen: 36B A – B; 30A …", "37: Betrieb
    # nur A – B; …".
    "Umleitungen": "diversions",
    "Betrieb nur": "service only",
    "Fahrtbehinderung wegen": "service obstruction due to",
    "Züge halten in Schleife": "trains stop in the loop",
    "Züge halten": "trains stop",
    "Züge halte": "trains stop",
    "Busse halten": "buses stop",
    "Ersatzbusse halten": "replacement buses stop",
    "Ersatzbus hält": "replacement bus stops",
    # The place forms ``_normalise_stop_verbs`` writes ("Busse halten an
    # Salvatorianerplatz", "Züge halten bei den Haltestellen der Linien 6
    # und 18"); the model left the preposition out or picked "for".
    "Züge halten an": "trains stop at",
    # "Züge halten nicht in der Station Hietzing" came out of the plain
    # "Züge halten" entry as "trains stop nicht …", which the model may
    # read either way.
    "Züge halten nicht": "trains do not stop",
    "Busse halten nicht": "buses do not stop",
    "Busse halten an": "buses stop at",
    "Ersatzbusse halten an": "replacement buses stop at",
    "Ersatzbus hält an": "replacement bus stops at",
    # With the line between noun and verb ("Ersatzbus 26E hält an …", see
    # ``_STOP_VERB``) the verb stands alone. A place follows it; "hält an"
    # at the end of a clause ("Die Störung hält an.") is rewritten before
    # the glossary sees it (``_PERSISTS_RE``).
    "hält an": "stops at",
    "hält gegenüber": "stops opposite",
    "hält bei Haltestelle der Linie": "stops at the stop of line",
    "hält bei den Haltestellen der Linien": "stops at the stops of lines",
    "Haltestelle aufgelassen": "stop closed",
    "bei Haltestelle der Linie": "at the stop of line",
    "bei den Haltestellen der Linien": "at the stops of lines",
    "Bussteig": "bus platform",
    # WL's stock referral. ``_normalise_for_translation`` drops the "aus"
    # that closes it, so the line list is the end of the sentence; the model
    # used to lose a line of a long list and fail the whole item back to
    # German (tram D, 49 builds on 2026-10-01).
    "Weichen Sie ersatzweise auf die Linien": "alternatively use lines",
    "Weichen Sie ersatzweise auf die Linie": "alternatively use line",
    # --- Street-addressing abbreviations ----------------------------
    # "ggü." (gegenüber) is the Baustellen-feed shorthand for
    # "opposite house number N" (e.g. "Simonygasse ggü. 2B"). Marian
    # leaves the unknown abbreviation verbatim, so the EN feed showed
    # the German "ggü." untranslated. Map it to the English "opp."
    # (opposite). Longest-first ordering makes the dotted form win
    # over the bare "ggü" so the period is preserved as "opp.".
    "ggü.": "opp.",
    "ggü": "opp",
}


# Operator-specific glossary overlays. Each key is the literal
# ``source`` value emitted by the matching provider (see
# ``src/providers/*.py`` and ``src/feed/stammstrecke.py``). An item's
# active glossary is :data:`_GLOSSARY_BASE` ∪ this overlay; entries
# here override base entries when both define the same key (rare —
# the overlays focus on terms that do NOT appear in the base set so
# they would be too narrow to apply universally).
_GLOSSARY_BY_SOURCE: dict[str, dict[str, str]] = {
    # Urban transit (metros, trams, buses). Scope: disruption-core
    # vocabulary only (Störungen / Verspätungen / Ausfälle). Facility
    # vocabulary (Aufzug / Rolltreppe / Niederflur) is intentionally
    # OUT — those items are not feed content. "Kurzführung" stays
    # because it describes an operational truncation of a line in
    # response to a disruption (the line short-runs to a substitute
    # terminus); Marian otherwise renders it as the literal "short
    # conduct" which is meaningless in transit English.
    "Wiener Linien": {
        "Kurzführung": "short-running service",
    },
    # Long-distance + regional rail. Scope: disruption-core
    # vocabulary only — train-type names (so the EN feed can phrase
    # "Personenzug 5072 verspätet" naturally), platform / connection
    # disruption phrases, line-section closures. Service-info
    # vocabulary (Reservierungspflicht / Fahrkartenpflicht / etc.) is
    # OUT — those are not disruption content.
    "ÖBB": {
        "Personenzug": "passenger train",
        "Personenzüge": "passenger trains",
        "Regionalzug": "regional train",
        "Regionalzüge": "regional trains",
        "Fernverkehr": "long-distance service",
        "Nahverkehr": "regional service",
        "Bahnsteigwechsel": "platform change",
        "Anschlussverlust": "missed connection",
        "Tunnelsperre": "tunnel closure",
        # ÖBB ticker shorthand for "Bahnhaltestelle". Marian has never
        # seen the abbreviation and left it verbatim, so the EN feed
        # carried the German jargon
        # (``docs/archive/audits/audit-2026-09-07.md``).
        "Bahnhst.": "station",
        "Bahnhst": "station",
        # Where an ÖBB cause happened. The model left "am Bahnhof" German:
        # "Due to an interlocking fault am Bahnhof" (2026-10-02).
        "am Bahnhof": "at the station",
        "am Gleis": "on the track",
        "am Zug": "on the train",
        "auf der Strecke": "on the line",
        # All-clear titles. The model read the noun as an action,
        # "Removing traffic restrictions: St. Pölten Hauptbahnhof"; ÖBB
        # announces a state (audit 2026-09-25, A.9).
        "Aufhebung Verkehrseinschränkung": "traffic restriction lifted",
        "Aufhebung Streckenunterbrechung": "line closure lifted",
    },
    # Road construction sites (Stadt Wien open-data Baustellen feed).
    # Talks about lane closures and routing in road-traffic vocabulary
    # that does NOT appear in WL/ÖBB items, so the WL elevator/tram
    # vocabulary would never apply here and vice versa.
    "Stadt Wien – Baustellen": {
        "Vollsperre": "full closure",
        "Vollsperrung": "full closure",
        "Teilsperre": "partial closure",
        "Teilsperrung": "partial closure",
        "Spurbeschränkung": "lane restriction",
        "Spurreduktion": "lane reduction",
        "Bauphase": "construction phase",
        "Verkehrsführung": "traffic routing",
    },
    # VOR/VAO Stammstrecke-Verspätungsmonitor emits highly templated
    # CSV-driven items; the surface vocabulary is already covered by
    # the base glossary. Empty overlay kept for symmetry with the
    # other operators (and so the resolver finds the key without
    # falling through to ``.get(..., {})``).
    "VOR/VAO": {},
}


# Category-specific glossary overlays. Architectural extension point:
# the project currently has a tight 1:1 source↔category coupling
# (Stadt Wien ↔ Baustelle; WL/ÖBB/VOR ↔ Störung), so source-driven
# entries cover today's vocabulary needs. The category axis activates
# once an operator publishes a new category (e.g. WL also emitting a
# "Baustelle" category for tram-replacement works) and we want
# category-specific overrides regardless of which source emits them.
_GLOSSARY_BY_CATEGORY: dict[str, dict[str, str]] = {}
