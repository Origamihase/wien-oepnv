"""The topic key of a Wiener Linien title, by which two reports count as the same event (dedupe)."""

from __future__ import annotations

import re

from .wl_title_tidy import _tidy_title_wl

# ---------------- „Kernbegriff/Topic“ für Dedupe ----------------

# Ursachen-Wörter, an denen zwei Upstream-Meldungen als DASSELBE Ereignis
# erkannt werden. Fehlt das Wort hier, fällt :func:`_topic_key_from_title` auf
# den ganzen Titel-Kern zurück — dann ist jede Formulierungsvariante ein
# eigenes Topic, ein eigener Bucket und am Ende ein eigenes Feed-Item.
#
# ``demonstration`` und ``veranstaltung`` fehlten und standen deshalb am
# 2026-09-12 doppelt im Feed:
#
#     38A: Demonstration
#     38A: Demonstration Haltestelle Kahlenberg wird nicht eingehalten
#
# Beide beschreiben dieselbe Sperre der Haltestelle Kahlenberg. Vor PR #1791
# fiel das nicht auf, weil ``_dedupe_items`` sie über den groben
# ``_identity`` (Linie + Tag) blind zusammenwarf und eines davon verwarf.
# Dieses Verwerfen war keine Lösung, sondern eine Maskierung: Es traf
# genauso Meldungen, die wirklich verschieden waren (``49A/50B: Mondweg``
# gegen ``49A/50B: Hüttergasse`` — zwei Straßen).
#
# Der richtige Ort zum Zusammenführen ist dieser hier: Im Bucketing von
# ``fetch_events`` gewinnt der bessere Titel UND die bessere Beschreibung
# (``_title_quality_key`` / ``_description_info_score``), Haltestellen und
# Extras werden vereinigt. Aus den beiden 38A-Meldungen wird dadurch ein
# Item, das beide schlägt: der informative Titel mit dem ausführlichen Text.
#
# ``feuerwehreinsatz`` fehlte aus derselben Reihe (``polizeieinsatz`` und
# ``rettungseinsatz`` standen längst hier) und kostete am 2026-09-12 zwei der
# zehn Plätze im deutschen Feed für EINEN Einsatz:
#
#     64A: Fahrtbehinderung wegen Feuerwehreinsatz
#     64A: Feuerwehreinsatz Betrieb ab Gregorygasse
#
# Neue Tokens deshalb nur mit Beleg aus den Live-Daten aufnehmen — jedes
# zusätzliche Wort führt Meldungen aggressiver zusammen. Der deutsche Feed
# (``docs/feed.xml``) hat dabei Vorrang: Er läuft auf Info-Displays mit fest
# begrenzter Item-Zahl, dort ist jeder doppelte Eintrag ein verlorener Platz
# (s. AGENTS.md, „Priorität der Ausgaben").
TITLE_TOPIC_TOKENS = {
    "falschparker",
    "polizeieinsatz",
    "rettungseinsatz",
    "feuerwehreinsatz",
    "demonstration",
    "veranstaltung",
    "unfall",
    "signalstörung",
    "signalstoerung",
    "umleitung",
    "ersatzverkehr",
    "kurzführung",
    "kurzfuehrung",
    "sperre",
    "gesperrt",
}

# Pre-fix ``betrieb\s+ab.*`` (and the sibling ``betrieb\s+nur.*``) used a
# greedy ``.*`` that swallowed every downstream token through to end-of-
# string, eating legitimate topic tokens — ``Sperre Betrieb ab Karlsplatz
# mit Unfall`` collapsed to ``sperre`` (the ``unfall`` topic token was
# lost), so two unrelated incidents that happened to follow a "Betrieb
# ab ..." preamble degenerated to the same ``topic_key`` and collided in
# the WL identity / dedup pipeline. Replacing ``.*`` with
# ``\s+\S+`` strips the canonical "Betrieb ab <Ort>" / "Betrieb nur
# <Ort>" phrasing (one location word, matching what the upstream feed
# actually emits — verified against current cache snapshots) and stops
# at the next whitespace so downstream tokens survive for the topic-key
# extractor at ``_topic_key_from_title``.
_GENERIC_FILLER = re.compile(
    r"\b(fahrtbehinderung|verkehrsbehinderung|behinderung|störung|stoerung|hinweis|meldung|serviceinfo|service\-info|"
    r"betrieb\s+ab\s+\S+|betrieb\s+nur\s+\S+)\b",
    re.IGNORECASE,
)


def _title_core(t: str) -> str:
    t2 = _tidy_title_wl(t)
    if len(t2) > 500:
        t2 = t2[:500]
    t2 = re.sub(r"[^\wäöüÄÖÜß]+", " ", t2, flags=re.UNICODE)
    t2 = re.sub(r"\s{2,}", " ", t2).strip().casefold()
    return t2


def _topic_key_from_title(raw: str) -> str:
    if raw and len(raw) > 500:
        raw = raw[:500]
    t = _GENERIC_FILLER.sub(" ", raw or "")
    t = re.sub(r"[^\wäöüÄÖÜß]+", " ", t, flags=re.UNICODE).casefold()
    toks = {w for w in t.split() if w in TITLE_TOPIC_TOKENS}
    if toks:
        return " ".join(sorted(toks))
    return _title_core(raw)


__all__ = ["TITLE_TOPIC_TOKENS", "_title_core", "_topic_key_from_title"]
