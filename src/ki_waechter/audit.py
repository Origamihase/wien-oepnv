"""KI-Wächter, the check side: each finding of the model checked mechanically, and the audit file.

Moved verbatim out of ``scripts/ki_waechter.py`` (700-line rule); the
command line and the Gemini client stay there. See
``docs/reference/ki_waechter.md``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from src.ki_waechter.inputs import (
    CLASSES,
    MIN_QUOTE_CHARS,
    VIENNA,
    FeedItem,
    Prepared,
    _n,
    _vienna,
    free_text,
    normalize,
    visible,
)
from src.utils.files import loads_finite
# GitHub Pages renders the report under docs/ with Liquid (src/utils/liquid.py).
from src.utils.liquid import liquid_safe


_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


# --- checking the model's answer ------------------------------------------------


@dataclass
class Finding:
    nr: int
    klasse: str
    feed: str
    feed_zitat: str
    erwartet: str
    begruendung: str
    quelle_zitat: str = ""
    nr2: int | None = None
    feed_zitat2: str = ""


@dataclass
class Rejected:
    raw: dict[str, Any]
    grund: str


@dataclass
class Verdict:
    findings: list[Finding]
    rejected: list[Rejected]
    hypotheses: list[str]
    checked: list[int]


def extract_answer(result_text: str) -> dict[str, Any]:
    """The JSON object of the model's final answer (last fenced block, else the outermost braces)."""
    blocks = _JSON_FENCE_RE.findall(result_text or "")
    candidates = list(reversed(blocks))
    start, end = (result_text or "").find("{"), (result_text or "").rfind("}")
    if start != -1 and end > start:
        candidates.append(result_text[start:end + 1])
    for candidate in candidates:
        try:
            data = loads_finite(candidate)
        except (ValueError, RecursionError):
            continue
        if isinstance(data, dict):
            return data
    raise ValueError("Antwort der KI enthält kein JSON-Objekt")


def _item_text(item: FeedItem, feed: str) -> str:
    if feed == "EN":
        return f"{item.en_title or ''} {item.en_description or ''}"
    return f"{item.title} {item.description}"


def _contains(haystack: str, quote: str) -> bool:
    needle = normalize(quote).strip("„“\"' ")
    return len(needle) >= MIN_QUOTE_CHARS and needle in normalize(haystack)


def _check_one(raw: Mapping[str, Any], prep: Prepared, items: Mapping[int, FeedItem]) -> Finding | str:
    klasse = str(raw.get("klasse") or "").upper()
    feed = str(raw.get("feed") or "").upper()
    nr = raw.get("nr")
    if klasse not in CLASSES:
        return f"Klasse „{klasse}“ gibt es nicht"
    if not isinstance(nr, int) or nr not in items:
        return "keine gültige Meldungsnummer"
    if feed not in ("DE", "EN"):
        return "Feed muss DE oder EN sein"
    if klasse == "F6" and feed != "EN":
        return "F6 gilt nur für den englischen Feed"
    if klasse in ("F1", "F3") and feed != "DE":
        return f"{klasse} gilt nur für den deutschen Feed"
    item = items[nr]
    if feed == "EN" and item.en_title is None:
        return "die Meldung fehlt im englischen Feed"
    finding = Finding(
        nr=nr,
        klasse=klasse,
        feed=feed,
        feed_zitat=normalize(str(raw.get("feed_zitat") or "")),
        erwartet=free_text(raw.get("erwartet")),
        begruendung=free_text(raw.get("begruendung")),
        quelle_zitat=normalize(str(raw.get("quelle_zitat") or "")),
    )
    if not _contains(_item_text(item, feed), finding.feed_zitat):
        return f"Zitat steht nicht wörtlich in Meldung {nr} ({feed})"
    if klasse == "F3":
        if not _contains(prep.source_text(item), finding.quelle_zitat):
            return f"Quellzitat steht nicht wörtlich in den Quellen von Meldung {nr}"
    if klasse == "F1":
        nr2 = raw.get("nr2")
        if not isinstance(nr2, int) or nr2 not in items or nr2 == nr:
            return "F1 braucht eine zweite, andere Meldung"
        finding.nr2 = nr2
        finding.feed_zitat2 = normalize(str(raw.get("feed_zitat2") or ""))
        if not _contains(_item_text(items[nr2], "DE"), finding.feed_zitat2):
            return f"Zitat der zweiten Meldung steht nicht wörtlich in Meldung {nr2}"
        if items[nr2].guid == item.guid or not set(item.versions) & set(items[nr2].versions):
            return f"Meldung {nr} und {nr2} standen nie gleichzeitig im Feed"
    if not finding.erwartet.strip():
        return "kein erwarteter Text"
    return finding


def verify(answer: Mapping[str, Any], prep: Prepared) -> Verdict:
    items = {item.nr: item for item in prep.items}
    findings: list[Finding] = []
    rejected: list[Rejected] = []
    # One finding per item (GUID), class and feed: one error in several texts
    # of the item (a time line changed in between), several places of one class
    # in one text and one error quoted at two lengths by the two answers. A
    # duplicate (F1) is one finding for its pair of items, whichever comes first.
    seen: set[tuple[object, ...]] = set()
    for raw in answer.get("funde") or []:
        if not isinstance(raw, dict):
            continue
        result = _check_one(raw, prep, items)
        if isinstance(result, str):
            rejected.append(Rejected(dict(raw), result))
            continue
        key: tuple[object, ...] = (items[result.nr].guid, result.klasse, result.feed)
        if result.klasse == "F1" and result.nr2 is not None:
            key = ("F1", frozenset({items[result.nr].guid, items[result.nr2].guid}))
        if key not in seen:
            seen.add(key)
            findings.append(result)
    hypotheses = [free_text(h) for h in answer.get("vermutungen") or [] if str(h).strip()][:3]
    checked = sorted({n for n in answer.get("geprueft") or [] if isinstance(n, int) and n in items})
    return Verdict(findings, rejected, hypotheses, checked)


# --- the audit file ----------------------------------------------------------------


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


_CACHE_NAMES = {"wl": "WL", "oebb": "ÖBB", "baustellen": "Baustellen"}


def render_report(prep: Prepared, verdict: Verdict, now: datetime, model: str, run_url: str) -> str:
    local = now.astimezone(VIENNA)
    by_item: dict[int, list[str]] = {}
    for n, finding in enumerate(verdict.findings, 1):
        by_item.setdefault(finding.nr, []).append(f"Fund {n}")
        if finding.nr2 is not None:
            by_item.setdefault(finding.nr2, []).append(f"Fund {n}")
    result = (
        "keine Funde" if not verdict.findings
        else f"{len(verdict.findings)} {'Fund' if len(verdict.findings) == 1 else 'Funde'}"
    )
    caches = ", ".join(f"{_CACHE_NAMES.get(k, k)} {v}" for k, v in sorted(prep.cache_counts.items()))
    first, last = prep.versions[0], prep.versions[-1]
    window = f"{_vienna(first.built_de, '%d.%m.%Y %H:%M')} bis {_vienna(last.built_de, '%d.%m.%Y %H:%M')}"
    out = [
        f"# KI-Wächter {local:%d.%m.%Y}",
        "",
        f"- Lauf: {local:%d.%m.%Y %H:%M} Wiener Zeit, Commit {prep.commit}, Modell {model}"
        + (f", [Lauf]({run_url})" if run_url else ""),
        (f"- Zeitraum: Feed gebaut {window} Wiener Zeit, {_n(len(prep.versions), 'Fassung', 'Fassungen')}, "
         f"{_n(prep.count_items, 'Meldungstext', 'verschiedene Meldungstexte')} von "
         f"{_n(len({i.guid for i in prep.items}), 'Meldung', 'Meldungen')}"),
        f"- Zuletzt: {last.count_de} Meldungen DE, {last.count_en} EN; Ereignisse im Cache: {caches}",
        f"- Geprüft: {len(verdict.checked)} von {prep.count_items} Meldungstexten",
        f"- Ergebnis: {result}",
        "",
        "## Geprüfte Meldungen",
        "",
        "| # | Titel (DE) | GUID (Ende) | Im Feed | Quelle im Cache | Ergebnis |",
        "|---|---|---|---|---|---|",
    ]
    for item in prep.items:
        status = ", ".join(by_item.get(item.nr, [])) or ("OK" if item.nr in verdict.checked else "nicht geprüft")
        source = prep.sources[item.own_sources[0]]["titel"] if item.own_sources else "nicht gefunden"
        out.append(
            f"| {item.nr} | {_cell(item.title)} | {_cell(item.guid[-12:])} | {_cell(visible(prep, item))} "
            f"| {_cell(source)} | {status} |"
        )
    out += ["", "## Funde", ""]
    if not verdict.findings:
        out.append("Keine.")
    for n, finding in enumerate(verdict.findings, 1):
        item = prep.items[finding.nr - 1]
        out += [
            f"### Fund {n}: {CLASSES[finding.klasse]} ({finding.klasse}, {finding.feed}, Meldung {finding.nr})",
            "",
            f"- Meldung: {item.title} (GUID …{item.guid[-12:]})",
            f"- Im Feed ({finding.feed}): „{finding.feed_zitat}“",
        ]
        if finding.nr2 is not None:
            out.append(f"- Zweite Meldung {finding.nr2}: „{finding.feed_zitat2}“")
        if finding.quelle_zitat:
            out.append(f"- Quelle: „{finding.quelle_zitat}“")
        out += [f"- Erwartet: {finding.erwartet}", f"- Begründung: {finding.begruendung}", ""]
    if verdict.rejected:
        out += ["", "## Nicht belegt (vom Skript verworfen, keine Funde)", ""]
        out += [
            (f"- Meldung {free_text(r.raw.get('nr', '?'))[:4]}, {free_text(r.raw.get('klasse', '?'))[:4]}: {r.grund}. "
             f"Zitat: „{free_text(r.raw.get('feed_zitat'))[:160]}“")
            for r in verdict.rejected
        ]
    if verdict.hypotheses:
        out += ["", "## Vermutungen der KI (keine Funde)", ""]
        out += [f"- {h}" for h in verdict.hypotheses]
    out += [
        "",
        "---",
        ("Erstellt von `scripts/ki_waechter.py` (Tabelle, Kopf und Prüfung der Zitate) und Gemini "
         "(Lesen und Vergleichen); Regeln in `scripts/ki_waechter_prompt.md`, Ablauf in "
         "`docs/reference/ki_waechter.md`. Der Feed wird nicht verändert."),
        "",
    ]
    return liquid_safe("\n".join(out))
