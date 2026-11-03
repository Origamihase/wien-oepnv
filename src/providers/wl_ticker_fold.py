"""Folding the display tickers (``stoerungkurz``) into the full notice.

Moved verbatim from ``src/providers/wl_fetch.py`` (700-line rule,
2026-11-02). The logger keeps the name ``src.providers.wl_fetch``.
"""

from __future__ import annotations

import html
import logging
import re
from datetime import datetime
from typing import Any

from ..utils.logging import sanitize_log_arg

from .wl_item_fields import _intervals_overlap
from .wl_lines import _line_tokens_from_pairs


log = logging.getLogger("src.providers.wl_fetch")


# ---------------- Anzeigetafel-Doppel („stoerungkurz“) ----------------

# ``_fetch_traffic_infos`` fragt bewusst ZWEI WL-Feeds in einem Aufruf ab:
# ``stoerunglang`` (der ausformulierte Meldungstext) und ``stoerungkurz``
# (die Kurztexte der Anzeigetafeln). Für dieselbe Störung liefern beide
# einen Eintrag — die Kurzform je Ast sogar einen eigenen. Am 2026-09-17
# stand die Linie 49 deshalb dreifach im Feed:
#
#     49: Gleisschaden                                ← stoerunglang
#     49: Gleisschaden Betrieb ab Hütteldorfer Straße  ← stoerungkurz
#     49: Gleisschaden Betrieb ab Urban-Loritz-Platz   ← stoerungkurz
#
# Die beiden Kurzformen erschienen dabei OHNE Text. Ihre Beschreibung
# („Gleisschaden\nBetrieb ab Hütteldorfer Straße >“) wiederholt nur den
# eigenen Titel, und ``_summary_duplicates_title`` in ``build_feed`` leert
# sie folgerichtig — sichtbar blieb eine Schlagzeile über einem leeren
# Rumpf. Zwei der zehn Plätze des deutschen Feeds trugen damit null
# Information, während der Langtext danebenstand und alles sagte: „Kein
# Betrieb zwischen Hütteldorfer Straße U und Urban-Loritz-Platz. Die Züge
# fahren ab Hütteldorfer Straße bis Joachimsthalerplatz. …“
#
# Das Bucketing über ``topic_key`` fasst sie nicht: Ohne Treffer in
# ``TITLE_TOPIC_TOKENS`` fällt der Schlüssel auf den ganzen Titelkern
# zurück, und der unterscheidet sich je Ast. Jedes neue Ursachenwort
# („Gleisschaden“, „Oberleitungsschaden“, „Weichenstörung“, …) einzeln
# nachzupflegen wäre die dritte Runde desselben Spiels — die beiden
# vorigen sind bei ``TITLE_TOPIC_TOKENS`` dokumentiert.
#
# Die Regel hier braucht kein Ursachenwort. Sie stellt zweimal dieselbe
# Frage: Sagt dieser Text etwas, das jener nicht schon sagt?
#
#   1. Die Beschreibung der Meldung fügt ihrem EIGENEN Titel nichts hinzu
#      → sie ist eine reine Schlagzeile.
#   2. Ihr Titel steht bereits vollständig in der Beschreibung einer
#      anderen Meldung derselben Linien, derselben Kategorie, mit
#      überlappendem Zeitraum → jene sagt alles, was diese sagt.
#
# Nur wenn BEIDES gilt, wandert die Schlagzeile in die andere Meldung:
# Haltestellen und Extras werden übernommen, der Zeitraum geweitet, Titel
# und Text der ausführlichen Meldung bleiben. Verworfen wird nichts, was
# nicht nachweislich woanders steht.
#
# Gegenproben an den Live-Daten (Cache 2026-09-17, 37 Störungen):
#
#   * ``49A/50B: Mondweg`` und ``49A/50B: Hüttergasse`` — zwei Straßen,
#     ein Linienpaar. Beide tragen eigenen Text und scheitern schon an
#     (1). Genau der Fall, an dem das frühere pauschale
#     ``_identity``-Dedupe scheiterte (s. ``TITLE_TOPIC_TOKENS``).
#   * ``12A: Betrieb ab Johnstraße U`` — Schlagzeile ohne Langtext
#     daneben, scheitert an (2). Sie ist die einzige Information zu ihrer
#     Linie und bleibt.
#   * Über den ganzen Cache greift die Regel bei genau einer Linie: 49.
#     34 der 37 Störungen sind Schlagzeilen, aber nur dort steht eine
#     ausführliche Meldung daneben, die sie abdeckt.
#
# Am 2026-09-19 zeigte sich die Grenze dieser Fassung. Eine Demonstration
# am Ring: WL schickte EINE ausführliche Meldung für sieben Linien
# (``1/2/2A/3A/4A/71/D: Demonstration am 19.09.2026``, Kategorie
# ``Hinweis``, mit Maßnahme je Linie) und je Linie eine Kurzmeldung
# (``1: Demonstration Betrieb ab Hintere Zollamtsstraße``, Kategorie
# ``Störung``, darunter WLs Textbaustein „Nach einer Fahrtbehinderung
# kommt es zu unterschiedlichen Intervallen."). Drei Gatter, drei Nein:
# die Linienmengen waren nicht gleich, die Kategorien nicht gleich, und
# der Textbaustein galt als eigener Inhalt. Ergebnis im Feed: zehn von
# zehn Plätzen für Kurzmeldungen eines Ereignisses, die Langmeldung auf
# Platz 12, alles andere verdrängt. Seither:
#
#   1. Die Linien der Schlagzeile müssen in denen der Langmeldung
#      ENTHALTEN sein, nicht gleich. Was die Langmeldung zu genau dieser
#      Linie sagt, deckt die Schlagzeile ab; der Wortvergleich bleibt.
#   2. Eine ``Störung``-Schlagzeile darf in einen ``Hinweis`` wandern —
#      nur in diese Richtung (``_categories_compatible``).
#   3. Der Textbaustein zählt als nichts (``_is_headline_only``).
#
# Gemessen am Cache des 2026-09-19 (47 Kurzmeldungen): 9 falten, vier in
# die Demonstration, fünf in ``25/26/27: Gleisbauarbeiten``. Keine davon
# sagt etwas, das ihre Langmeldung nicht sagt — das prüft weiterhin der
# Wortvergleich, jetzt mit aufgelösten HTML-Entities, weil der Langtext
# als HTML im Bucket liegt (``Zollamtsstra&szlig;e``).

_WORD_SPLIT_RE = re.compile(r"[^\w]+", re.UNICODE)
_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _gate_text(*parts: str) -> str:
    """The text the keyword gates (``KW_EXCLUDE``, ``KW_RESTRICTION``) read.

    WL sends the news descriptions as HTML with the umlauts as entities
    ("wird die Linie 18 kurz gef&uuml;hrt", "Einschr&auml;nkung"). Read raw,
    no keyword with an umlaut could match in a description, only in the
    plain-text title; "18: LCC-Herbstmarathon am 11.10.2026" was dropped
    for that reason (2026-10-04). Tags become spaces so "<h2>Sperre</h2>"
    stays a word of its own.
    """
    return " ".join(html.unescape(_HTML_TAG_RE.sub(" ", " ".join(parts))).split())


def _content_tokens(text: str) -> frozenset[str]:
    """Kleingeschriebene Wortmenge von *text*, Satzzeichen entfernt.

    Trägt die Umbrüche und Pfeile der Anzeigetafel-Texte mit ab
    (``"Gleisschaden\\nBetrieb ab Hütteldorfer Straße >"``) und macht
    ``Urban-Loritz-Platz`` mit ``Urban Loritz Platz`` vergleichbar — WL
    schreibt denselben Ort in Titel und Fließtext verschieden. HTML-Tags
    und -Entities werden vorher aufgelöst: Die ausführliche Meldung liegt
    als HTML im Bucket, und ohne Auflösung fände sich ``zollamtsstraße``
    aus einem Kurztitel im Langtext ``Zollamtsstra&szlig;e`` nie wieder.
    """
    if not text:
        return frozenset()
    plain = html.unescape(_HTML_TAG_RE.sub(" ", text))
    return frozenset(
        tok for tok in _WORD_SPLIT_RE.sub(" ", plain).casefold().split() if tok
    )


def _covered_by(tokens: frozenset[str], text: str) -> bool:
    """True, wenn jedes Wort aus *tokens* schon in *text* vorkommt.

    Eine leere Wortmenge ergibt False: Fehlender Text ist keine
    Redundanz, sondern fehlende Information. Eine Meldung ohne Titel oder
    ohne Beschreibung darf darüber nicht stillschweigend verschwinden.
    """
    return bool(tokens) and tokens <= _content_tokens(text)


def _says_nothing_new(text: str, *, beyond: str) -> bool:
    """True, wenn *text* kein Wort enthält, das nicht schon in *beyond* steht."""
    return _covered_by(_content_tokens(text), beyond)


# WLs Textbaustein unter Anzeigetafel-Kurzmeldungen. Er stand am
# 2026-09-19 unter 7 von 47 Kurzmeldungen wortgleich, nennt keine Linie,
# keinen Ort und keine Maßnahme — die Information ist der Titel. Das
# vorangestellte ``Linie`` deckt das ``Linie 2:``-Präfix ab, das WL dem
# Baustein voranstellt; die Nummer dahinter steht ohnehin im Titel.
_WL_TICKER_BOILERPLATE = (
    "Linie Nach einer Fahrtbehinderung kommt es zu unterschiedlichen Intervallen"
)


def _is_headline_only(bucket: dict[str, Any]) -> bool:
    """Bedingung (1): Die Beschreibung sagt nichts über den Titel hinaus.

    WLs Textbaustein (``_WL_TICKER_BOILERPLATE``) zählt dabei als nichts,
    ebenso die eigenen Liniennummern: Die Beschreibung trägt sie als
    ``Linie 1:``-Präfix, der Bucket-Titel je nach Quelle mit oder ohne
    Präfix — welche Linien gemeint sind, steht in ``lines_pairs``.
    """
    lines = " ".join(_line_tokens_from_pairs(bucket.get("lines_pairs", [])))
    return _says_nothing_new(
        bucket.get("desc_base", ""),
        beyond=f"{bucket.get('title', '')} {lines} {_WL_TICKER_BOILERPLATE}",
    )


def _categories_compatible(src_category: str, cand_category: str) -> bool:
    """Darf eine Schlagzeile der Kategorie *src* in *cand* wandern?

    Gleiche Kategorie immer. Dazu die eine Richtung, die die Daten
    verlangen: WL führt die ausformulierte Meldung zu einem Ereignis als
    ``Hinweis`` (Vorankündigung aus ``_fetch_news``) und die Kurztexte der
    Anzeigetafeln am Tag selbst als ``Störung``. Die Kategorie erreicht
    das Display nie — der RSS-Eintrag trägt kein ``<category>`` —, sie
    bricht nur Gleichstände in der Sortierung nach ``first_seen``. Der
    umgekehrte Weg bleibt zu: Ein ``Hinweis`` ist eine Ankündigung, und
    eine ``Störung``-Langmeldung sagt nichts über ihn aus.
    """
    return src_category == cand_category or (
        src_category == "Störung" and cand_category == "Hinweis"
    )


def _ticker_fold_target(
    src: dict[str, Any], buckets: dict[str, dict[str, Any]], *, skip: str
) -> str | None:
    """Schlüssel der Meldung, die alles sagt, was die Schlagzeile *src* sagt.

    ``None``, wenn es keine gibt. Kandidaten müssen mindestens die Linien
    der Schlagzeile tragen (``_line_tokens_from_pairs``, Obermenge), eine
    verträgliche Kategorie haben (``_categories_compatible``), sich
    zeitlich überlappen und selbst mehr als eine Schlagzeile sein — sonst
    würden zwei inhaltsleere Kurzmeldungen einander „abdecken“ und eine
    davon grundlos verschwinden. Bei mehreren Treffern gewinnt die wortreichste
    Beschreibung; der Schlüssel bricht den Gleichstand, damit dieselbe
    Eingabe immer dasselbe Ergebnis liefert.
    """
    lines = frozenset(_line_tokens_from_pairs(src["lines_pairs"]))
    if not lines:
        return None
    # Die Liniennummer trägt der Titel als Präfix („49: Gleisschaden …“),
    # der Zieltext muss sie nicht wiederholen — welche Linien gemeint sind,
    # ist über den Linien-Vergleich unten bereits abschließend geklärt.
    # Bliebe sie im Vergleich, hinge die Regel daran, ob WL den Langtext
    # zufällig mit „Linie 49: …“ eröffnet; tut er es nicht, bliebe die
    # Doppelmeldung stehen.
    title_tokens = _content_tokens(src.get("title", "")) - {
        tok.casefold() for tok in lines
    }
    matches: list[tuple[int, str]] = []
    for key, cand in buckets.items():
        if key == skip or not _categories_compatible(
            src["category"], cand["category"]
        ):
            continue
        if not lines <= frozenset(_line_tokens_from_pairs(cand["lines_pairs"])):
            continue
        if _is_headline_only(cand):
            continue
        if not _intervals_overlap(
            src.get("starts_at"),
            src.get("ends_at"),
            cand.get("starts_at"),
            cand.get("ends_at"),
        ):
            continue
        desc = cand.get("desc_base", "")
        if not _covered_by(title_tokens, desc):
            continue
        matches.append((len(_content_tokens(desc)), key))
    if not matches:
        return None
    return max(matches)[1]


# WL numbers an incident ``I20261004-0019`` and the notices that follow it
# ``I20261004-0019-F01``, ``-F02``: one per line once the incident is over
# ("6/18: Schadhaftes Fahrzeug" became "6: …" and "18: …" on 2026-10-04).
# The number stays while the messages, their lines and so their GUIDs change;
# the feed follows an item from build to build by it
# (``build_feed._carry_item_identity``). Display tickers (``R1345-143``)
# have none.
_INCIDENT_NAME_RE = re.compile(r"^(I\d{8}-\d{4})(?:-F\d+)?$")


def _incident_ids(info: dict[str, Any]) -> set[str]:
    """``{"I20261004-0019"}`` for *info* and its follow-ups, empty for a ticker."""
    match = _INCIDENT_NAME_RE.match(str(info.get("name") or "").strip())
    return {match.group(1)} if match else set()


def _earliest(a: datetime | None, b: datetime | None) -> datetime | None:
    """The earlier of *a* and *b*; an unknown one does not count."""
    if a is None or b is None:
        return a or b
    return min(a, b)


def _absorb_headline(target: dict[str, Any], src: dict[str, Any]) -> None:
    """Übernimm Haltestellen, Extras und Zeitraum von *src* nach *target*.

    Titel, Beschreibung und ``starts_at`` bleiben unangetastet: *target*
    ist die ausführliche Meldung und damit die maßgebliche. ``ends_at``
    wird nur geweitet, wenn BEIDE Enden bekannt sind — ein offenes Ende
    der Kurzmeldung darf ein bekanntes Ende der ausführlichen nicht
    aufweichen, und ein bekanntes darf ein offenes nicht verengen.
    """
    target["stop_names"].update(src["stop_names"])
    target.setdefault("wl_ids", set()).update(src.get("wl_ids") or ())
    for extra in src["extras"]:
        if extra not in target["extras"]:
            target["extras"].append(extra)
    src_pub, target_pub = src.get("pubDate"), target.get("pubDate")
    if src_pub and (not target_pub or src_pub < target_pub):
        target["pubDate"] = src_pub
    target["incident_start"] = _earliest(target.get("incident_start"), src.get("incident_start"))
    src_end, target_end = src.get("ends_at"), target.get("ends_at")
    if src_end is not None and target_end is not None:
        target["ends_at"] = max(target_end, src_end)


def _fold_display_tickers(buckets: dict[str, dict[str, Any]]) -> None:
    """Führe reine Schlagzeilen in die ausführliche Meldung derselben Störung.

    Verändert *buckets* an Ort und Stelle. Ein Ziel ist nie selbst
    Kandidat — ``_ticker_fold_target`` verlangt, dass es mehr als eine
    Schlagzeile ist —, kann also während des Laufs nicht wegfallen; welche
    Meldungen übrig bleiben, hängt daher nicht von der Reihenfolge ab. Das
    ``sorted`` ordnet allein die Logzeile, damit zwei Läufe über dieselben
    Daten dieselbe Meldung schreiben.
    """
    headlines = [key for key, b in sorted(buckets.items()) if _is_headline_only(b)]
    folded: list[str] = []
    for key in headlines:
        src = buckets.get(key)
        if src is None:  # pragma: no cover - Ziele sind nie Kandidaten
            continue
        target_key = _ticker_fold_target(src, buckets, skip=key)
        if target_key is None:
            continue
        _absorb_headline(buckets[target_key], src)
        del buckets[key]
        folded.append(str(src.get("title", "")))
    if folded:
        log.info(
            "WL: %d Anzeigetafel-Kurzmeldung(en) in die ausführliche Meldung "
            "übernommen: %s",
            len(folded),
            sanitize_log_arg(" | ".join(folded)),
        )
