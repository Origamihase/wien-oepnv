"""Rules E and F: aggregate and subset notices of the WL feed.

Moved verbatim from ``src/providers/wl_fetch.py`` (700-line rule,
2026-11-02).
"""

from __future__ import annotations

import re
from typing import Any
from collections.abc import Sequence

from .wl_item_fields import _intervals_overlap
from .wl_ticker_fold import _covered_by, _gate_text


# ---------------- Sammel- und Teilmeldungen (E, F) ----------------
#
# Zwei ältere Regeln räumen nach der Bündelung noch einmal auf: E entfernt
# eine Meldung für mehrere Linien, wenn jede ihrer Linien eine eigene Meldung
# hat; F entfernt eine Meldung, deren Linien in einer anderen Meldung
# derselben Kategorie stecken (2025-12-24, Anlass „Silvesterlauf“ neben
# „Silvesterpfad“). Beide fragten nur nach Linien, Kategorie und Zeitraum,
# nie nach dem Inhalt. Die Filterprüfung vom 2026-10-03 fand in der
# WL-Cache-Historie seit April 108 Meldungen, die verschwanden, während eine
# solche „Obermenge“ lief, und danach unverändert wiederkamen, obwohl sie
# etwas anderes sagten:
#
#   * „2A: Bauarbeiten Renngasse“ zehn Tage lang, solange die Regenbogenparade
#     („1/1A/2/2A/31/3A/59A/71/74A/D: Regenbogenparade 2026“) angekündigt war;
#   * „77A: Umleitung wegen Veranstaltungen“ drei Wochen neben
#     „77A/80A: Ende der Gleisbauarbeiten“;
#   * „N8: Nußdorfer Straße U“ (Haltestellenverlegung) neben der Verlegung
#     „59A/N8: Dörfelstraße“;
#   * „11A: Gleisbauarbeiten Stadion U“ neben „11A/18: Veranstaltung am
#     12.09.2026“.
#
# Eine Meldung, die etwas anderes sagt, ist keine Doppelung. Seither fällt
# eine Meldung nur noch, wenn die andere sie inhaltlich abdeckt: Jedes Wort
# ihres Titels (ohne die eigenen Liniennummern) steht schon dort — die Frage,
# die auch ``_fold_display_tickers`` stellt —, oder, nur bei F, sie nennt das
# Thema der anderen (``_same_topic``). Das Zweite hält die
# Anzeigetafel-Kurzmeldungen großer Baustellen draußen, die F bisher zu Recht
# entfernte: Ohne diese Bedingung kämen „5: Betrieb ab Franz-Josefs-Bahnhof“,
# „12: …“ und „37: Betrieb ab Nußdorfer Straße“ neben
# „5/12/37/38/40/41/42: Gleisbauarbeiten“ jede Nacht neu nach vorn.
# „Silvesterlauf“ und „Silvesterpfad“ fasst heute ``deduplicate_fuzzy`` im
# Feed-Bau zusammen (``tests/test_feed_merge.py``).
#
# Eine Anzeigetafel-Kurzmeldung deckt nie eine ausführliche Störungsmeldung
# ab (Prüfung der verworfenen Meldungen, 2026-10-07). Ihr Titel ist die
# Ursache, und die steht auch im Titel der ausführlichen; nach Wörtern
# „deckten“ deshalb zwei Kurzmeldungen „10: Fahrtbehinderung wegen
# Polizeieinsatz“ und „60: Polizeieinsatz Betrieb ab Anschützgasse“ die
# Meldung „10, 60: Polizeieinsatz“ ab, und E entfernte sie samt
# „Linie 10: Betrieb nur zwischen Dornbach und Linzer Straße …
# Voraussichtliche Dauer: 09:50 Uhr“ (Rohdaten 2026-10-05 09:31 MESZ; der
# Feed zeigte „10“ und „60“ auf zwei Plätzen). Seit 04.10. traf das fünf
# Störungen (10/60, D/71, 2/12, 13A/14A, 11/O) und täglich die
# Baustellenmeldungen 25/26/27 und 46/49/52. Was Kurzmeldungen neben ihrer
# Störung zeigen, entscheidet der Feed-Bau (``_merge_wl_ticker_clusters``).


_H2_RE = re.compile(r"<h2[^>]*>(.*?)</h2>", re.IGNORECASE | re.DOTALL)


def _lead_line(desc: str) -> str:
    """Worum es in *desc* geht: die ``<h2>``-Überschrift oder die erste Zeile.

    Bei einer Anzeigetafel-Kurzmeldung steht dort die Ursache
    (``"Gleisbauarbeiten\nBetrieb ab Franz-Josefs-Bahnhof"``), bei einer
    ausführlichen Meldung die Überschrift.
    """
    match = _H2_RE.search(desc or "")
    if match:
        return match.group(1)
    return (desc or "").split("\n", 1)[0]


_STOP_NOTICE_RE = re.compile(
    r"^\s*Haltestellen(?:verlegung|auflassung)\b.*?\bHaltestelle:\s*(.{1,80}?)\s+(?:Von|Nach|Dauer|Ersatzlos)\b",
    re.IGNORECASE | re.DOTALL,
)


def _stop_of_notice(description: str) -> str | None:
    """The stop a WL relocation or closure notice is about, or ``None``.

    WL writes these notices in one form ("Haltestellenverlegung der Linie 3A
    in Richtung Oper, Karlsplatz / Haltestelle: Schellinggasse / Von: …").
    """
    match = _STOP_NOTICE_RE.search(_gate_text(description[:4000]))
    return " ".join(match.group(1).casefold().split()) if match else None


def _covers_stop_notice(item: dict[str, Any], others: Sequence[dict[str, Any]]) -> bool:
    """False if *item* is a stop notice that none of *others* is about.

    The title of a relocation or closure is just its stop ("3A: Oper,
    Karlsplatz"), and other notices name that stop as a direction ("in
    Richtung Oper, Karlsplatz"): by word count alone (E, F) the relocation
    of Schellinggasse removed the one of Oper, Karlsplatz, and "12A:
    Geibelgasse" the one of "12A, N8: Längenfeldgasse U" (raw data
    2026-10-04). Only a notice about the same stop covers a stop notice.
    """
    stop = item.get("_stop")
    if not stop:
        return True
    return any(other.get("_stop") == stop for other in others)


def _is_display_ticker(item: dict[str, Any]) -> bool:
    """True für eine Störung nur aus Anzeigetafel-Kurzmeldungen (keine WL-Nummer)."""
    return item.get("category") == "Störung" and not item.get("_wl_ids")


def _may_cover(cover: dict[str, Any], item: dict[str, Any]) -> bool:
    """False, wenn *cover* eine Kurzmeldung und *item* eine ausführliche Meldung ist."""
    return not (item.get("_wl_ids") and _is_display_ticker(cover))


def _says_nothing_beyond(item: dict[str, Any], others: Sequence[dict[str, Any]]) -> bool:
    """True, wenn der Titel von *item* ganz in den Texten von *others* steht."""
    text = " ".join(str(other.get("_text", "")) for other in others)
    return _covered_by(item.get("_title_tokens") or frozenset(), text)


def _same_topic(item: dict[str, Any], other: dict[str, Any]) -> bool:
    """True, wenn *item* das Thema von *other* nennt.

    Thema sind die Wörter im Titel von *other* ohne Liniennummern, Zahlen
    und Wörter unter fünf Buchstaben (``Gleisbauarbeiten``,
    ``Regenbogenparade``); gesucht wird im Titel und in der ersten Zeile
    bzw. Überschrift von *item* (:func:`_lead_line`).
    """
    topic = {
        tok for tok in other.get("_title_tokens") or frozenset()
        if len(tok) >= 5 and tok.isalpha()
    }
    return bool(topic & (item.get("_lead_tokens") or frozenset()))


def _drop_covered_aggregates(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """E) Meldung für mehrere Linien entfernen, wenn die Einzelmeldungen sie abdecken.

    Abdecken heißt: Jede ihrer Linien hat eine Einzelmeldung derselben
    Kategorie mit überlappendem Zeitraum, und diese Einzelmeldungen sagen
    zusammen alles, was ihr Titel sagt. Ohne den Kategorie-Schlüssel löschte
    ein Hinweis je Linie eine echte Störung; ohne die Zeitprüfung löschten
    zeitlich getrennte Einzelmeldungen ein Aggregat für einen dritten
    Zeitraum; ohne den Wortvergleich löschte „1: Rettungseinsatz“ neben
    „2: Falschparker“ die Meldung „1/2: Demonstration“. Eine
    Anzeigetafel-Kurzmeldung zählt für eine ausführliche Störung nicht als
    Einzelmeldung (:func:`_may_cover`).
    """
    singles: dict[tuple[Any, str], list[dict[str, Any]]] = {}
    for it in items:
        ls = it.get("_lines_set") or set()
        if len(ls) == 1:
            singles.setdefault((it.get("category"), next(iter(ls))), []).append(it)

    kept: list[dict[str, Any]] = []
    for it in items:
        ls = it.get("_lines_set") or set()
        if len(ls) >= 2:
            covering = [
                [
                    single
                    for single in singles.get((it.get("category"), ln), [])
                    if _may_cover(single, it)
                    and _intervals_overlap(
                        it.get("starts_at"), it.get("ends_at"),
                        single.get("starts_at"), single.get("ends_at"),
                    )
                ]
                for ln in ls
            ]
            flat = [single for group in covering for single in group]
            if all(covering) and _says_nothing_beyond(it, flat) and _covers_stop_notice(it, flat):
                continue
        kept.append(it)
    return kept


def _drop_covered_subsets(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """F) Meldung entfernen, die eine Meldung für mehr Linien schon ganz enthält.

    Die andere Meldung trägt eine echte Obermenge der Linien, dieselbe
    Kategorie und einen überlappenden Zeitraum, und entweder enthält ihr
    Text jedes Wort aus dem Titel der kleineren (:func:`_says_nothing_beyond`)
    oder die kleinere nennt ihr Thema (:func:`_same_topic`). Das Zweite
    trifft die Anzeigetafel-Kurzmeldungen einer Baustelle
    („37: Betrieb ab Nußdorfer Straße“ mit „Gleisbauarbeiten“ in der ersten
    Zeile neben „5/12/37/38/40/41/42: Gleisbauarbeiten“): Sie sagen mit
    anderen Worten, was die Baustellenmeldung ausführlich sagt. Eine
    Kurzmeldung entfernt keine ausführliche Störung (:func:`_may_cover`).
    """
    removed: set[int] = set()
    for i, item_a in enumerate(items):
        lines_a = item_a.get("_lines_set") or set()
        if not lines_a:
            continue
        for j, item_b in enumerate(items):
            if i == j or j in removed:
                continue
            lines_b = item_b.get("_lines_set") or set()
            if not (lines_a < lines_b and item_a.get("category") == item_b.get("category")):
                continue
            if not _may_cover(item_b, item_a):
                continue
            if not _intervals_overlap(
                item_a.get("starts_at"), item_a.get("ends_at"),
                item_b.get("starts_at"), item_b.get("ends_at"),
            ):
                continue
            if (
                _says_nothing_beyond(item_a, [item_b]) or _same_topic(item_a, item_b)
            ) and _covers_stop_notice(item_a, [item_b]):
                removed.add(i)
                break
    return [it for i, it in enumerate(items) if i not in removed]
