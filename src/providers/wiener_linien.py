#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Wiener Linien Provider (OGD) – nur betriebsrelevante Störungen/Hinweise,
keine Roll-/Fahrtreppen- oder Aufzugs-Meldungen.

Neu:
- Titelkürzung am Anfang: generische Label wie „Bauarbeiten“, „Straßenbauarbeiten“,
  „Gleisbauarbeiten“, „Verkehrsinfo/-meldung“, „Störung“, „Hinweis“, „Serviceinfo“
  werden nur entfernt, wenn danach ein informativer Titelteil folgt.
  Beispiel: „Bauarbeiten Züge halten Alszeile 126“ → „Züge halten Alszeile 126“.
  „14A: Straßenbauarbeiten“ bleibt unverändert, falls nichts Sinnvolles folgt.

- Sammel vs. Einzel: Wenn *alle* in einer Sammelmeldung genannten Linien bereits
  zumindest ein Einzelevent haben, wird die Sammelmeldung verworfen.

- NEU: Titel beginnen immer mit den betroffenen Linien (aus relatedLines),
  z. B. „14A: …“ oder „49/52: …“. Ein vorhandenes korrektes Präfix wird nicht
  doppelt gesetzt.

Env:
  WL_RSS_URL   (Basis-URL, Secret empfohlen; Fallback: https://www.wienerlinien.at/ogd_realtime)
"""

from __future__ import annotations

import hashlib, html, logging, os, re
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from dateutil import parser as dtparser

WL_BASE = (
    os.getenv("WL_RSS_URL", "").strip()
    or "https://www.wienerlinien.at/ogd_realtime"
).rstrip("/")

log = logging.getLogger(__name__)

def _session() -> requests.Session:
    s = requests.Session()
    retry = Retry(
        total=4,
        backoff_factor=0.6,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
        raise_on_status=False,
    )
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.headers.update({
        "Accept": "application/json",
        "User-Agent": "Origamihase-wien-oepnv/2.1 (+https://github.com/Origamihase/wien-oepnv)"
    })
    return s

S = _session()

# ---------------- Filter & Textregeln ----------------

KW_RESTRICTION = re.compile(
    r"\b(umleitung|ersatzverkehr|unterbrech|sperr|gesperrt|störung|arbeiten|baustell|einschränk|verspät|ausfall|verkehr)\b",
    re.IGNORECASE
)

KW_EXCLUDE = re.compile(
    r"\b(willkommen|gewinnspiel|anzeiger|eröffnung|service(?:-info)?|info(?:rmation)?|fest|keine\s+echtzeitinfo)\b",
    re.IGNORECASE
)

FACILITY_ONLY = re.compile(
    r"\b(aufzug|aufzüge|lift|fahrstuhl|fahrtreppe|fahrtreppen|rolltreppe|rolltreppen|aufzugsinfo|fahrtreppeninfo)\b",
    re.IGNORECASE
)

def _is_facility_only(*texts: str) -> bool:
    t = " ".join([x for x in texts if x]).lower()
    return bool(FACILITY_ONLY.search(t))

# Label-Kürzung am Titelanfang
_LABELS = [
    r"bauarbeiten", r"straßenbauarbeiten", r"gleisbauarbeiten",
    r"verkehrsinfo", r"verkehrsinformation", r"verkehrsmeldung",
    r"störung", r"hinweis", r"serviceinfo", r"service\-info", r"information"
]
_LABEL_HEAD_RE = re.compile(
    r"^\s*(?:(?:" + "|".join(_LABELS) + r")\s*(?:[-:–—/]\s*|\s+))+",
    re.IGNORECASE
)
def _is_informative(rest: str) -> bool:
    return bool(rest and re.search(r"[A-Za-zÄÖÜäöüß0-9]{3,}", rest))

def _tidy_title_wl(title: str) -> str:
    t = (title or "").strip()
    if not t:
        return t
    stripped = _LABEL_HEAD_RE.sub("", t)
    if stripped and _is_informative(stripped):
        return re.sub(r"\s{2,}", " ", stripped).strip(" -–—:/\t")
    return t

# ---------------- Zeit & Utils ----------------

def _iso(s: Optional[str]) -> Optional[datetime]:
    if not s: return None
    s = s.replace("Z", "+00:00")
    if len(s) >= 5 and (s[-5] in "+-") and s[-3] != ":":
        s = s[:-2] + ":" + s[-2:]
    return dtparser.isoparse(s)

def _best_ts(obj: Dict[str, Any]) -> Optional[datetime]:
    t = obj.get("time") or {}
    for cand in (
        _iso(t.get("start")), _iso(t.get("end")),
        _iso(obj.get("updated")), _iso(obj.get("timestamp")),
        _iso((obj.get("attributes") or {}).get("lastUpdate")),
        _iso((obj.get("attributes") or {}).get("created")),
    ):
        if cand: return cand
    return None

def _is_active(start: Optional[datetime], end: Optional[datetime], now: datetime) -> bool:
    if start and start > now: return False
    if end and end < (now - timedelta(minutes=10)): return False
    return True

def _as_list(val) -> List[Any]:
    if val is None: return []
    return list(val) if isinstance(val, (list, tuple, set)) else [val]

def _tok(v: Any) -> str:
    return re.sub(r"[^A-Za-z0-9+]", "", str(v)).upper()

def _norm_title(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())

def _guid(*parts: str) -> str:
    return hashlib.md5("|".join(p or "" for p in parts).encode("utf-8")).hexdigest()

# Linien-Präfix bauen
LINE_DISP_CLEAN_RE = re.compile(r"\s+")
LINE_PREFIX_STRIP_RE = re.compile(r"^\s*[A-Za-z0-9]+(?:/[A-Za-z0-9]+){0,20}\s*:\s*", re.IGNORECASE)

def _display_line(s: str) -> str:
    # z. B. "u 2" -> "U2", " 14a " -> "14A", "d" -> "D"
    return LINE_DISP_CLEAN_RE.sub("", str(s or "").strip()).upper()

def _unique_lines_display(rel_lines: List[Any]) -> Tuple[List[str], List[str]]:
    """
    liefert (display_list, token_list) – Display in Originalreihenfolge (dedupliziert),
    Tokens für Dedupe/Keys.
    """
    tokens_seen = set()
    disp_list: List[str] = []
    tok_list: List[str]  = []
    for x in rel_lines:
        tok = _tok(x)
        if not tok or tok in tokens_seen:
            continue
        tokens_seen.add(tok)
        disp = _display_line(x)
        disp_list.append(disp)
        tok_list.append(tok)
    return disp_list, tok_list

def _ensure_line_prefix(title: str, lines_disp: List[str]) -> str:
    """Sorgt dafür, dass der Titel mit „L1/L2: “ beginnt (wenn Linien vorhanden).
       Vermeidet Doppelpräfixe, indem ein vorhandenes Lines-Präfix zuerst entfernt wird."""
    if not lines_disp:
        return title
    wanted = "/".join(lines_disp)
    # bereits korrekt?
    if re.match(rf"^\s*{re.escape(wanted)}\s*:\s*", title, re.IGNORECASE):
        return title
    # beliebiges vorhandenes Präfix entfernen und neu setzen
    stripped = LINE_PREFIX_STRIP_RE.sub("", title)
    return f"{wanted}: {stripped}".strip()

# ---------------- API Calls ----------------

def _get_json(path: str, params: Optional[List[tuple]] = None, timeout: int = 20) -> Dict[str, Any]:
    url = f"{WL_BASE.rstrip('/')}/{path.lstrip('/')}"
    r = S.get(url, params=params or None, timeout=timeout)
    r.raise_for_status()
    return r.json()

def _fetch_traffic_infos(timeout: int = 20) -> Iterable[Dict[str, Any]]:
    params = [("name","stoerunglang"),("name","stoerungkurz")]  # KEINE Facility-Feeds
    data = _get_json("trafficInfoList", params=params, timeout=timeout)
    return (data.get("data", {}) or {}).get("trafficInfos", []) or []

def _fetch_news(timeout: int = 20) -> Iterable[Dict[str, Any]]:
    data = _get_json("newsList", timeout=timeout)
    return (data.get("data", {}) or {}).get("pois", []) or []

# ---------------- Public API ----------------

def fetch_events(timeout: int = 20) -> List[Dict[str, Any]]:
    now = datetime.now(timezone.utc)
    raw: List[Dict[str, Any]] = []

    # A) TrafficInfos
    try:
        for ti in _fetch_traffic_infos(timeout=timeout):
            if _is_closed(ti): continue

            title_raw = (ti.get("title") or ti.get("name") or "Meldung").strip()
            title = _tidy_title_wl(title_raw)
            desc  = (ti.get("description") or "").strip()
            attrs = ti.get("attributes") or {}
            if _is_facility_only(title_raw, desc): continue

            ts_best = _best_ts(ti)
            start = _iso((ti.get("time") or {}).get("start")) or ts_best
            end   = _iso((ti.get("time") or {}).get("end"))
            if not _is_active(start, end, now): continue

            fulltext = " ".join([title_raw, desc, str(attrs.get("status") or ""), str(attrs.get("state") or "")])
            if KW_EXCLUDE.search(fulltext) and not KW_RESTRICTION.search(fulltext): continue

            rel_lines = _as_list(ti.get("relatedLines") or attrs.get("relatedLines"))
            rel_stops = _as_list(ti.get("relatedStops") or attrs.get("relatedStops"))
            lines_disp, lines_tok = _unique_lines_display(rel_lines)
            lines_str = ", ".join(str(x).strip() for x in rel_lines if str(x).strip())

            extras = []
            for k in ("status","state","station","location","reason","towards"):
                if attrs.get(k):
                    extras.append(f"{k.capitalize()}: {html.escape(str(attrs[k]))}")
            if lines_str:
                extras.append(f"Linien: {html.escape(lines_str)}")

            raw.append({
                "source": "Wiener Linien",
                "category": "Störung",
                "title": title,
                "desc": html.escape(desc),
                "extras": extras,
                "lines": set(lines_tok),
                "lines_disp": lines_disp,
                "stops": { _tok(x) for x in rel_stops if str(x).strip() },
                "pubDate": start,           # NUR Quelle (kann None sein)
                "starts_at": start,
                "ends_at": end,
            })
    except Exception as e:
        logging.exception("WL trafficInfoList fehlgeschlagen: %s", e)

    # B) News/Hinweise
    try:
        for poi in _fetch_news(timeout=timeout):
            if _is_closed(poi): continue

            title_raw = (poi.get("title") or "Hinweis").strip()
            title = _tidy_title_wl(title_raw)
            desc  = (poi.get("description") or "").strip()
            attrs = poi.get("attributes") or {}
            if _is_facility_only(title_raw, desc, poi.get("subtitle") or ""): continue

            ts_best = _best_ts(poi)
            start = _iso((poi.get("time") or {}).get("start")) or ts_best
            end   = _iso((poi.get("time") or {}).get("end"))
            if not _is_active(start, end, now): continue

            text_for_filter = " ".join([
                title_raw, poi.get("subtitle") or "", desc,
                str(attrs.get("status") or ""), str(attrs.get("state") or ""),
            ])
            if not KW_RESTRICTION.search(text_for_filter): continue

            rel_lines = _as_list(poi.get("relatedLines") or attrs.get("relatedLines"))
            rel_stops = _as_list(poi.get("relatedStops") or attrs.get("relatedStops"))
            lines_disp, lines_tok = _unique_lines_display(rel_lines)
            lines_str = ", ".join(str(x).strip() for x in rel_lines if str(x).strip())

            extras = []
            if poi.get("subtitle"):
                extras.append(html.escape(poi["subtitle"]))
            for k in ("station","location","towards"):
                if attrs.get(k):
                    extras.append(f"{k.capitalize()}: {html.escape(str(attrs[k]))}")
            if lines_str:
                extras.append(f"Linien: {html.escape(lines_str)}")

            raw.append({
                "source": "Wiener Linien",
                "category": "Hinweis",
                "title": title,
                "desc": html.escape(desc),
                "extras": extras,
                "lines": set(lines_tok),
                "lines_disp": lines_disp,
                "stops": { _tok(x) for x in rel_stops if str(x).strip() },
                "pubDate": start,
                "starts_at": start,
                "ends_at": end,
            })
    except Exception as e:
        logging.exception("WL newsList fehlgeschlagen: %s", e)

    # C) Bündelung gleicher Themen (Titel+Linien) – inkl. lines_disp zusammenführen
    buckets: Dict[str, Dict[str, Any]] = {}
    for ev in raw:
        key = _guid("wl", ev["category"], _norm_title(ev["title"]), ",".join(sorted(ev["lines"])))
        b = buckets.get(key)
        if not b:
            buckets[key] = {
                "source": "Wiener Linien",
                "category": ev["category"],
                "title": ev["title"],
                "desc_base": ev["desc"],
                "extras": list(ev["extras"]),
                "lines": set(ev["lines"]),
                "lines_disp": list(ev["lines_disp"]),
                "stops": set(ev["stops"]),
                "pubDate": ev["pubDate"],
                "starts_at": ev["starts_at"],
                "ends_at": ev["ends_at"],
            }
        else:
            b["stops"].update(ev["stops"])
            # Lines (Token) und lines_disp zusammenführen
            before = set(b["lines"])
            b["lines"].update(ev["lines"])
            # lines_disp: Reihenfolge beibehalten, neue Displays anhängen wenn Token noch nicht drin
            have = set(before)
            for tok, disp in zip(ev["lines"], ev["lines_disp"]):
                if tok not in have:
                    b["lines_disp"].append(disp)
                    have.add(tok)

            if ev["pubDate"] and (not b["pubDate"] or ev["pubDate"] < b["pubDate"]):
                b["pubDate"] = ev["pubDate"]
            be, ee = b["ends_at"], ev["ends_at"]
            b["ends_at"] = None if (be is None or ee is None) else max(be, ee)
            for x in ev["extras"]:
                if x not in b["extras"]:
                    b["extras"].append(x)

    # D) Finale Items mit Linien-Präfix im Titel
    items: List[Dict[str, Any]] = []
    for b in buckets.values():
        # Titel vorbereiten (Label gekürzt), dann Linienpräfix erzwingen
        base_title = b["title"]
        title_with_lines = _ensure_line_prefix(base_title, b.get("lines_disp") or [])
        title_final = html.escape(title_with_lines)

        desc = b["desc_base"]
        if b["extras"]:
            desc = (desc + ("<br/>" if desc else "") + "<br/>".join(b["extras"]))
        if b["stops"]:
            stops_list = sorted(b["stops"])
            stops_str = ", ".join(stops_list[:15]) + (" …" if len(stops_list) > 15 else "")
            desc += ("<br/>Betroffene Haltestellen: " + html.escape(stops_str))

        guid = _guid("wl", b["category"], _norm_title(title_with_lines), ",".join(sorted(b["lines"])))
        items.append({
            "source": "Wiener Linien",
            "category": b["category"],
            "title": title_final,
            "description": desc,
            "link": f"{WL_BASE}",
            "guid": guid,
            "pubDate": b["pubDate"],      # None erlaubt
            "starts_at": b["starts_at"],
            "ends_at": b["ends_at"],
            "_lines_set": set(b["lines"]),   # INTERN für Sammel-vs-Einzel
        })

    # E) Sammel-vs-Einzel: Aggregat entfernen, wenn *alle* Linien als Einzel vorliegen
    single_line_coverage = {}
    for it in items:
        ls = it.get("_lines_set") or set()
        if len(ls) == 1:
            ln = next(iter(ls))
            single_line_coverage.setdefault(ln, 0)
            single_line_coverage[ln] += 1

    filtered: List[Dict[str, Any]] = []
    for it in items:
        ls = it.get("_lines_set") or set()
        if len(ls) >= 2:
            all_covered = all(single_line_coverage.get(ln, 0) > 0 for ln in ls)
            if all_covered:
                continue  # Aggregat raus
        filtered.append(it)

    # F) Aufräumen interner Felder + Sortierung
    for it in filtered:
        it.pop("_lines_set", None)

    filtered.sort(key=lambda x: (0, x["pubDate"]) if x["pubDate"] else (1, hashlib.md5(x["guid"].encode()).hexdigest()))
    return filtered
