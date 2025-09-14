#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Wiener Linien Provider (OGD) – betriebsrelevante Störungen/Hinweise für Wien.
Ausschlüsse: Aufzüge/Fahrtreppen (Facility-only).

Highlights:
- Titelkürzung (nur sinnvolle Labels weg)
- Robustes Linien-Präfix (keine Dopplungen/„Rufbus“ im Titel)
- Fallback-Linien aus Text (ohne Datums-/Zeit-/Adress-Fallen)
- Sammel vs. Einzel (Aggregat raus, wenn alle Einzel vorhanden)
- Plain-Text-Beschreibung (TV-tauglich) mit Trenner „ • “
- Stop-Namen: in der Beschreibung gelistet; Anzahl im Titel als „(X Halte)“
- Stabile `_identity` für first_seen
- NEU: „Kernbegriff“-Dedupe innerhalb gleicher Linien (z. B. „Fahrtbehinderung Falschparker“ ~ „Falschparker“)
"""

from __future__ import annotations

import hashlib
import html
import logging
import os
import re
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from dateutil import parser as dtparser

# Basis-URL aus Secret/ENV, Fallback: OGD-Endpoint
WL_BASE = (
    os.getenv("WL_RSS_URL", "").strip()
    or "https://www.wienerlinien.at/ogd_realtime"
).rstrip("/")

log = logging.getLogger(__name__)

# ---------------- HTTP-Session mit Retry ----------------
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
        "User-Agent": "Origamihase-wien-oepnv/2.10 (+https://github.com/Origamihase/wien-oepnv)"
    })
    return s

S = _session()

# ---------------- Relevanz-/Ausschluss-Filter ----------------
KW_RESTRICTION = re.compile(
    r"\b(umleitung|ersatzverkehr|unterbrech|sperr|gesperrt|störung|arbeiten|baustell|einschränk|verspät|ausfall|verkehr"
    r"|kurzführung|teilbetrieb|pendelverkehr|kurzstrecke)\b",
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

# ---------------- Titel-Kosmetik ----------------
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
    """Entfernt generische Label am Anfang, wenn danach informativer Text steht."""
    t = (title or "").strip()
    if not t:
        return t
    stripped = _LABEL_HEAD_RE.sub("", t)
    if stripped and _is_informative(stripped):
        t = stripped
    t = re.sub(r"[<>«»‹›]+", "", t)          # spitze Klammern u. ä. raus
    return re.sub(r"\s{2,}", " ", t).strip(" -–—:/\t")

# ---------------- HTML → Plain-Text (Beschreibung) ----------------
_BR_RE = re.compile(r"(?i)<\s*br\s*/?\s*>")
_BLOCK_CLOSE_RE = re.compile(r"(?is)</\s*(p|div|li|ul|ol)\s*>")
_BLOCK_OPEN_RE = re.compile(r"(?is)<\s*(p|div|ul|ol)\b[^>]*>")
_LI_OPEN_RE = re.compile(r"(?is)<\s*li\b[^>]*>")
_TAG_RE = re.compile(r"(?is)<[^>]+>")
_WS_RE = re.compile(r"[ \t\r\f\v]+")

def _html_to_text(s: str) -> str:
    """
    Robust: Entities decodieren, Block-/BR-Tags in Trenner verwandeln,
    restliche Tags strippen, Whitespace konsolidieren.
    Einheitlicher Trenner: „ • “.
    """
    if not s:
        return ""
    txt = html.unescape(s)
    txt = _BR_RE.sub("\n", txt)
    txt = _BLOCK_CLOSE_RE.sub("\n", txt)
    txt = _LI_OPEN_RE.sub("• ", txt)
    txt = _BLOCK_OPEN_RE.sub("", txt)
    txt = _TAG_RE.sub("", txt)
    txt = re.sub(r"\s*\n\s*", " • ", txt)  # einheitlicher Trenner
    txt = _WS_RE.sub(" ", txt)
    return re.sub(r"\s{2,}", " ", txt).strip()

# ---------------- Zeit & Utils ----------------
def _iso(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
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
        if cand:
            return cand
    return None

def _is_active(start: Optional[datetime], end: Optional[datetime], now: datetime) -> bool:
    if start and start > now:
        return False
    if end and end < (now - timedelta(minutes=10)):
        return False
    return True

def _as_list(val) -> List[Any]:
    if val is None:
        return []
    return list(val) if isinstance(val, (list, tuple, set)) else [val]

# ---------------- Linien-Aufbereitung ----------------
def _clean_line_token(s: str) -> str:
    s = str(s or "")
    s = re.sub(r"^\s*Rufbus\s+", "", s, flags=re.IGNORECASE)  # „Rufbus “ strippen
    s = re.sub(r"\s+", "", s).upper()
    return s

def _tok(v: Any) -> str:
    return _clean_line_token(re.sub(r"[^A-Za-z0-9+]", "", str(v)))

def _display_line(s: str) -> str:
    return _clean_line_token(s)

def _norm_title(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())

# Präfix-Erkennung/Entfernung:
LINE_PREFIX_STRIP_RE = re.compile(r"^\s*[A-Za-z0-9]+(?:/[A-Za-z0-9]+){0,20}\s*:\s*", re.IGNORECASE)
LINES_COMPLEX_PREFIX_RE = re.compile(
    r"""^\s*
        [A-Za-z0-9]+
        (?:\s*,\s*[A-Za-z0-9]+){1,}
        (?:\s*(?:und)?\s*(?:Rufbus\s+[A-Za-z0-9]+|\([^)]+\))\s*)*
        \s*:\s*
    """,
    re.IGNORECASE | re.VERBOSE
)
RUF_BUS_PREFIX_RE = re.compile(r"^\s*Rufbus\s+([A-Za-z0-9]+)\s*:\s*", re.IGNORECASE)

def _strip_existing_line_block(title: str) -> str:
    t = LINE_PREFIX_STRIP_RE.sub("", title)
    t = LINES_COMPLEX_PREFIX_RE.sub("", t)
    t = RUF_BUS_PREFIX_RE.sub("", t)
    if ":" in t:
        pre, post = t.split(":", 1)
        if ("," in pre) or ("Rufbus" in pre) or ("(" in pre):
            t = post.strip()
    return t

def _ensure_line_prefix(title: str, lines_disp: List[str]) -> str:
    if not lines_disp:
        return title
    wanted = "/".join(lines_disp)
    if re.match(rf"^\s*{re.escape(wanted)}\s*:\s*", title, re.IGNORECASE):
        return title
    stripped = _strip_existing_line_block(title)
    return f"{wanted}: {stripped}".strip()

# Fallback-Linien aus Titeltext — vorher Datum/Zeit/Adressen maskieren
LINE_CODE_RE = re.compile(r"\b(?:U\d{1,2}|S\d{1,2}|N\d{1,3}|[0-9]{1,3}[A-Z]?|[A-Z])\b", re.IGNORECASE)
RUF_BUS_RE = re.compile(r"Rufbus\s+([A-Za-z0-9]+)", re.IGNORECASE)
DATE_FULL_RE = re.compile(r"\b\d{1,2}\.\d{1,2}\.(?:\d{2}|\d{4})\b")
DATE_SHORT_RE = re.compile(r"\b\d{1,2}\.\d{1,2}\b")
TIME_RE = re.compile(r"\b\d{1,2}:\d{2}\b")
ADDRESS_NO_RE = re.compile(
    r"\b([A-Za-zÄÖÜäöüß\-]+(?:gasse|straße|strasse|platz|allee|weg|steig|ufer|brücke|kai|ring))\s+\d+\b",
    re.IGNORECASE
)

def _mask_dates_times_addresses(t: str) -> str:
    t = DATE_FULL_RE.sub(" ", t)
    t = DATE_SHORT_RE.sub(" ", t)
    t = TIME_RE.sub(" ", t)
    t = ADDRESS_NO_RE.sub(r"\1", t)  # Zahl nach Straßentyp entfernen
    return t

def _detect_line_pairs_from_text(text: str) -> List[Tuple[str, str]]:
    t = _mask_dates_times_addresses(text or "")
    pairs: List[Tuple[str, str]] = []
    seen: set[str] = set()
    for m in RUF_BUS_RE.findall(t):
        tok = _tok(m)
        if tok and tok not in seen:
            seen.add(tok); pairs.append((tok, _display_line(m)))
    for m in LINE_CODE_RE.findall(t):
        tok = _tok(m)
        if tok and tok not in seen:
            seen.add(tok); pairs.append((tok, _display_line(m)))
    return pairs

def _make_line_pairs_from_related(rel_lines: List[Any]) -> List[Tuple[str, str]]:
    pairs: List[Tuple[str, str]] = []; seen: set[str] = set()
    for x in rel_lines:
        tok = _tok(x)
        if not tok or tok in seen: continue
        seen.add(tok); pairs.append((tok, _display_line(x)))
    return pairs

def _merge_line_pairs(base_pairs: List[Tuple[str, str]], add_pairs: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
    existing = {tok for tok, _ in base_pairs}
    out = list(base_pairs)
    for tok, disp in add_pairs:
        if tok not in existing:
            out.append((tok, disp)); existing.add(tok)
    return out

def _line_tokens_from_pairs(pairs: List[Tuple[str, str]]) -> List[str]:
    return [tok for tok, _ in pairs]

def _line_display_from_pairs(pairs: List[Tuple[str, str]]) -> List[str]:
    return [disp for _, disp in pairs]

# ---------------- Stop-Namen extrahieren ----------------
def _stop_names_from_related(rel_stops: List[Any]) -> List[str]:
    names: List[str] = []
    for s in rel_stops:
        if isinstance(s, dict):
            for key in ("name", "stopName", "title"):
                val = s.get(key)
                if val and re.search(r"[A-Za-zÄÖÜäöüß]", str(val)):
                    names.append(str(val).strip()); break
        elif isinstance(s, str):
            if re.search(r"[A-Za-zÄÖÜäöüß]", s):
                names.append(s.strip())
    dedup: Dict[str, str] = {}
    for n in names:
        k = re.sub(r"\s+", " ", n).strip().casefold()
        dedup.setdefault(k, n)
    return sorted(dedup.values(), key=lambda x: x.casefold())

# ---------------- „Kernbegriff“ für Titel (Dedupe) ----------------
# Entfernt sehr generische Wörter; „polizeieinsatz“, „unfall“ etc. bleiben erhalten!
_TITLE_CORE_STOPWORDS = re.compile(
    r"\b(fahrtbehinderung|verkehrsbehinderung|behinderung|stoerung|störung|hinweis|information|meldung|serviceinfo|service\-info)\b",
    re.IGNORECASE
)
def _title_core(t: str) -> str:
    t = _tidy_title_wl(t)
    t = _TITLE_CORE_STOPWORDS.sub(" ", t)
    t = re.sub(r"[^\wäöüÄÖÜß]+", " ", t, flags=re.UNICODE)
    t = re.sub(r"\s{2,}", " ", t).strip().casefold()
    return t or _norm_title(t)

# ---------------- API Calls ----------------
def _get_json(path: str, params: Optional[List[tuple]] = None, timeout: int = 20) -> Dict[str, Any]:
    url = f"{WL_BASE.rstrip('/')}/{path.lstrip('/')}"
    r = S.get(url, params=params or None, timeout=timeout)
    r.raise_for_status()
    return r.json()

def _fetch_traffic_infos(timeout: int = 20) -> Iterable[Dict[str, Any]]:
    params = [("name","stoerunglang"),("name","stoerungkurz")]
    data = _get_json("trafficInfoList", params=params, timeout=timeout)
    return (data.get("data", {}) or {}).get("trafficInfos", []) or []

def _fetch_news(timeout: int = 20) -> Iterable[Dict[str, Any]]:
    data = _get_json("newsList", timeout=timeout)
    return (data.get("data", {}) or {}).get("pois", []) or []

# ---------------- Public API ----------------
def fetch_events(timeout: int = 20) -> List[Dict[str, Any]]:
    now = datetime.now(timezone.utc)
    raw: List[Dict[str, Any]] = []

    # A) TrafficInfos (Störungen)
    try:
        for ti in _fetch_traffic_infos(timeout=timeout):
            attrs = ti.get("attributes") or {}
            status_blob = " ".join([str(ti.get("status") or ""), str(attrs.get("status") or ""), str(attrs.get("state") or "")]).lower()
            if any(x in status_blob for x in ("finished","inactive","inaktiv","done","closed","nicht aktiv","ended","ende","abgeschlossen","beendet","geschlossen")):
                continue

            title_raw = (ti.get("title") or ti.get("name") or "Meldung").strip()
            title = _tidy_title_wl(title_raw)
            desc_raw = (ti.get("description") or "").strip()
            desc = _html_to_text(desc_raw)
            if _is_facility_only(title_raw, desc_raw):
                continue

            tinfo = ti.get("time") or {}
            start = _iso(tinfo.get("start")) or _best_ts(ti)
            end   = _iso(tinfo.get("end"))
            if not _is_active(start, end, now):
                continue

            blob = " ".join([title_raw, desc_raw])
            if KW_EXCLUDE.search(blob) and not KW_RESTRICTION.search(blob):
                continue

            rel_lines = _as_list(ti.get("relatedLines") or attrs.get("relatedLines"))
            line_pairs = _make_line_pairs_from_related(rel_lines) or _detect_line_pairs_from_text(title_raw)
            rel_stops = _as_list(ti.get("relatedStops") or attrs.get("relatedStops"))
            stop_names = _stop_names_from_related(rel_stops)

            extras = []
            for k in ("status","state","station","location","reason","towards"):
                if attrs.get(k): extras.append(f"{k.capitalize()}: {str(attrs[k]).strip()}")

            id_lines = ",".join(sorted(_line_tokens_from_pairs(line_pairs)))
            id_day = start.date().isoformat() if isinstance(start, datetime) else "None"
            identity = f"wl|störung|L={id_lines}|D={id_day}"

            raw.append({
                "source": "Wiener Linien",
                "category": "Störung",
                "title": title,
                "title_core": _title_core(title_raw),
                "desc": desc,
                "extras": extras,
                "lines_pairs": line_pairs,
                "stop_names": set(stop_names),
                "pubDate": start,
                "starts_at": start,
                "ends_at": end,
                "_identity": identity,
            })
    except Exception as e:
        logging.exception("WL trafficInfoList fehlgeschlagen: %s", e)

    # B) News/Hinweise
    try:
        for poi in _fetch_news(timeout=timeout):
            attrs = poi.get("attributes") or {}
            status_blob = " ".join([str(poi.get("status") or ""), str(attrs.get("status") or ""), str(attrs.get("state") or "")]).lower()
            if any(x in status_blob for x in ("finished","inactive","inaktiv","done","closed","nicht aktiv","ended","ende","abgeschlossen","beendet","geschlossen")):
                continue

            title_raw = (poi.get("title") or "Hinweis").strip()
            title = _tidy_title_wl(title_raw)
            desc_raw = (poi.get("description") or "").strip()
            desc = _html_to_text(desc_raw)
            if _is_facility_only(title_raw, desc_raw, poi.get("subtitle") or ""):
                continue

            tinfo = poi.get("time") or {}
            start = _iso(tinfo.get("start")) or _best_ts(poi)
            end   = _iso(tinfo.get("end"))
            if not _is_active(start, end, now):
                continue

            text_for_filter = " ".join([title_raw, poi.get("subtitle") or "", desc_raw, str(attrs.get("status") or ""), str(attrs.get("state") or "")])
            if not KW_RESTRICTION.search(text_for_filter):
                continue

            rel_lines = _as_list(poi.get("relatedLines") or attrs.get("relatedLines"))
            line_pairs = _make_line_pairs_from_related(rel_lines) or _detect_line_pairs_from_text(title_raw)
            rel_stops = _as_list(poi.get("relatedStops") or attrs.get("relatedStops"))
            stop_names = _stop_names_from_related(rel_stops)

            extras = []
            if poi.get("subtitle"): extras.append(str(poi["subtitle"]).strip())
            for k in ("station","location","towards"):
                if attrs.get(k): extras.append(f"{k.capitalize()}: {str(attrs[k]).strip()}")

            id_lines = ",".join(sorted(_line_tokens_from_pairs(line_pairs)))
            id_day = start.date().isoformat() if isinstance(start, datetime) else "None"
            identity = f"wl|hinweis|L={id_lines}|D={id_day}"

            raw.append({
                "source": "Wiener Linien",
                "category": "Hinweis",
                "title": title,
                "title_core": _title_core(title_raw),
                "desc": desc,
                "extras": extras,
                "lines_pairs": line_pairs,
                "stop_names": set(stop_names),
                "pubDate": start,
                "starts_at": start,
                "ends_at": end,
                "_identity": identity,
            })
    except Exception as e:
        logging.exception("WL newsList fehlgeschlagen: %s", e)

    # C) Bündelung gleicher Themen (LINIEN-SET + KERNBEGRIFF)
    buckets: Dict[str, Dict[str, Any]] = {}
    for ev in raw:
        line_toks_sorted = ",".join(sorted(_line_tokens_from_pairs(ev["lines_pairs"])))
        # WICHTIG: key nutzt title_core (Kernbegriff) statt Volltitel
        key = _guid("wl", ev["category"], ev.get("title_core",""), line_toks_sorted)
        b = buckets.get(key)
        if not b:
            buckets[key] = {
                "source": ev["source"], "category": ev["category"],
                "title": ev["title"], "title_core": ev.get("title_core",""),
                "desc_base": ev["desc"], "extras": list(ev["extras"]),
                "lines_pairs": list(ev["lines_pairs"]),
                "stop_names": set(ev["stop_names"]),
                "pubDate": ev["pubDate"], "starts_at": ev["starts_at"], "ends_at": ev["ends_at"],
                "_identity": ev["_identity"],
            }
        else:
            # Besseren Titel wählen: der KÜRZERE cleaned title gewinnt
            if len(ev["title"]) < len(b["title"]):
                b["title"] = ev["title"]
            b["lines_pairs"] = _merge_line_pairs(b["lines_pairs"], ev["lines_pairs"])
            b["stop_names"].update(ev["stop_names"])
            if ev["pubDate"] and (not b["pubDate"] or ev["pubDate"] < b["pubDate"]):
                b["pubDate"] = ev["pubDate"]
            be, ee = b["ends_at"], ev["ends_at"]; b["ends_at"] = None if (be is None or ee is None) else max(be, ee)
            for x in ev["extras"]:
                if x not in b["extras"]: b["extras"].append(x)

    # D) Finale Items mit Linien-Präfix im Titel
    items: List[Dict[str, Any]] = []
    for b in buckets.values():
        lines_disp = _line_display_from_pairs(b["lines_pairs"])
        lines_tok  = set(_line_tokens_from_pairs(b["lines_pairs"]))

        base_title = b["title"]
        title_with_lines = _ensure_line_prefix(base_title, lines_disp)

        # Anzahl Halte (falls wir Namen kennen) ins Titelende
        halt_cnt = len(b["stop_names"])
        if halt_cnt > 0 and not re.search(r"\(\d+\s+Halt(?:e)?\)$", title_with_lines):
            title_with_lines += f" ({halt_cnt} Halt{'e' if halt_cnt != 1 else ''})"
        title_final = re.sub(r"[<>«»‹›]+", "", title_with_lines).strip()

        # Beschreibung aufbauen (ohne „Linien: …“ in extras)
        desc = b["desc_base"]
        extras_clean = [x for x in b["extras"] if not x.lower().startswith("linien:")]
        if extras_clean:
            desc = (desc + (" • " if desc else "") + " • ".join(extras_clean))
        if b["stop_names"]:
            names = sorted(b["stop_names"], key=lambda x: x.casefold())
            desc += " • Betroffene Haltestellen: " + ", ".join(names)
        desc = re.sub(r"[<>]+", "", desc)
        desc = re.sub(r"\s{2,}", " ", desc).strip()

        guid = _guid("wl", b["category"], b.get("title_core","") or _norm_title(title_final), ",".join(sorted(lines_tok)))
        items.append({
            "source": b["source"], "category": b["category"],
            "title": title_final, "description": desc, "link": f"{WL_BASE}",
            "guid": guid, "pubDate": b["pubDate"], "starts_at": b["starts_at"], "ends_at": b["ends_at"],
            "_identity": b["_identity"], "_lines_set": lines_tok,
        })

    # E) Sammel-vs.-Einzel: Aggregat entfernen, wenn *alle* Linien als Einzel vorliegen
    single_cov: Dict[str, int] = {}
    for it in items:
        ls = it.get("_lines_set") or set()
        if len(ls) == 1:
            ln = next(iter(ls)); single_cov[ln] = single_cov.get(ln, 0) + 1
    filtered: List[Dict[str, Any]] = []
    for it in items:
        ls = it.get("_lines_set") or set()
        if len(ls) >= 2 and all(single_cov.get(ln, 0) > 0 for ln in ls):
            continue
        filtered.append(it)
    for it in filtered: it.pop("_lines_set", None)

    filtered.sort(key=lambda x: (0, x["pubDate"]) if x["pubDate"] else (1, hashlib.md5(x["guid"].encode()).hexdigest()))
    log.info("WL: %d Items nach Filter/Dedupe", len(filtered))
    return filtered

# ---------------- Hilfsfunktionen ----------------
def _guid(*parts: str) -> str:
    return hashlib.md5("|".join(p or "" for p in parts).encode("utf-8")).hexdigest()
