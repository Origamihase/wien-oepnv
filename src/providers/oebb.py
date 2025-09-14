#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
ÖBB/VOR-RSS (Fahrplan-Portal) – Meldungen für Wien & nahe Pendelstrecken.

- Secret OEBB_RSS_URL (Fallback: offizielle ÖBB-RSS-URL)
- Titel-Kosmetik:
  • Kategorie-Vorspann (bis Doppelpunkt) entfernen
  • „Wien X und Wien Y“ → „Wien X ↔ Wien Y“
  • Pfeile normalisieren (ein „↔“), Bahnhof/Hbf/Bf entfernen
  • Spitze Klammern etc. entfernen
- Plain-Text-Description (HTML/Word raus, Entities decodiert; Trenner „ • “)
- Strenger GEO-Filter: Behalte NUR Meldungen, deren Endpunkte in Wien
  oder definierter Pendler-Region (Whitelist) liegen
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from email.utils import parsedate_to_datetime

try:  # pragma: no cover - support both package layouts
    from utils.text import html_to_text
except ModuleNotFoundError:  # pragma: no cover
    from src.utils.text import html_to_text  # type: ignore

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from defusedxml import ElementTree as ET

log = logging.getLogger(__name__)

OEBB_URL = (os.getenv("OEBB_RSS_URL", "").strip()
            or "https://fahrplan.oebb.at/bin/help.exe/dnl?protocol=https:&tpl=rss_WI_oebb&")

# Optional strenger Filter: Nur Meldungen mit Endpunkten in Wien behalten.
# Aktiviert durch Umgebungsvariable ``OEBB_ONLY_VIENNA`` ("1"/"0").
OEBB_ONLY_VIENNA = os.getenv("OEBB_ONLY_VIENNA", "").strip() not in {"", "0", "false", "False"}

# ---------------- HTTP ----------------
def _session() -> requests.Session:
    s = requests.Session()
    retry = Retry(total=4, backoff_factor=0.6, status_forcelist=(429,500,502,503,504),
                  allowed_methods=("GET",))
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.headers.update({"User-Agent":"Origamihase-wien-oepnv/3.1 (+https://github.com/Origamihase/wien-oepnv)"})
    return s

S = _session()

# ---------------- Titel + Endpunkte ----------------
BAHNHOF_TRIM_RE = re.compile(r"\s*(?:Bahnhof|Bahnhst|Hbf|Bf)(?:\s*\(U\))?", re.IGNORECASE)
ARROW_ANY_RE    = re.compile(r"\s*(?:<=>|<->|<>|→|↔|=>|=|–|—|-)\s*")
COLON_PREFIX_RE = re.compile(
    r"""^\s*(?:Update\s*\d+\s*\([^)]*\)\s*)?
        (?:DB\s*↔\s*)?
        (?:[A-Za-zÄÖÜäöüß/ \-]+:\s*)+
    """, re.IGNORECASE | re.VERBOSE
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

def _clean_title_keep_places(t: str) -> str:
    t = (t or "").strip()
    # Vorspann bis zum Doppelpunkt entfernen
    t = COLON_PREFIX_RE.sub("", t)
    # Sonderfall: „Wien X und Wien Y“ → „Wien X ↔ Wien Y“
    t = re.sub(r"\b(Wien [^,;|]+?)\s+und\s+(Wien [^,;|]+?)\b", r"\1 ↔ \2", t)
    # Pfeile und Trennzeichen normalisieren
    parts = [p for p in ARROW_ANY_RE.split(t) if p.strip()]
    parts = [_clean_endpoint(p) for p in parts if p.strip()]
    if len(parts) >= 2:
        t = f"{parts[0]} ↔ {parts[1]}"
        if len(parts) > 2:
            rest = " ".join(parts[2:]).strip()
            if rest:
                t += f" {rest}"
    elif parts:
        t = parts[0]
    t = MULTI_ARROW_RE.sub(" ↔ ", t)
    t = re.sub(r"\s{2,}", " ", t)
    t = re.sub(r"[<>«»‹›]+", "", t)
    return t.strip()

def _split_endpoints(title: str) -> Optional[List[str]]:
    """Extrahiert Endpunktnamen links/rechts (ohne Bahnhof/Hbf/Klammern)."""
    if "↔" not in title and "<=>" not in title and "=>" not in title:
        return None
    parts = [p for p in re.split(r"\s*(?:↔|<=>|=>|<|->|—|-)\s*", title) if p.strip()]
    if len(parts) < 2:
        return None
    left, right = parts[0], parts[1]
    def explode(side: str) -> List[str]:
        tmp = re.split(r"\s*(?:/|,|bzw\.|oder|und)\s*", side, flags=re.IGNORECASE)
        names: List[str] = []
        for n in tmp:
            n = BAHNHOF_TRIM_RE.sub("", n)
            n = re.sub(r"\s*\([^)]*\)\s*", "", n)  # Klammern-Inhalte weg
            n = re.sub(r"\s{2,}", " ", n).strip(" .")
            if n:
                names.append(n)
        return names
    return explode(left) + explode(right)

# ---------------- Pendler-Region (Whitelist) ----------------
def _norm(s: str) -> str:
    s = (s or "").casefold()
    for a,b in (("ä","a"),("ö","o"),("ü","u"),("ß","ss")):
        s = s.replace(a,b)
    s = re.sub(r"(?:bahnhof|bahnhst|hbf|bf)\b", "", s).strip()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s{2,}", " ", s).strip()

# Wien-Knoten (roh → normalisiert)
W_VIENNA_RAW = [
    "Wien", "Wien Hbf", "Wien Meidling", "Wien Floridsdorf", "Wien Praterstern",
    "Wien Handelskai", "Wien Heiligenstadt", "Wien Spittelau", "Wien Mitte",
    "Wien Simmering", "Wien Stadlau", "Wien Hütteldorf", "Wien Liesing",
]
W_VIENNA = {_norm(x) for x in W_VIENNA_RAW}

# Pendelraum: Nutzerliste + Ergänzungen (normalisiert)
W_NEAR_RAW = [
    # Nutzerwunsch (alphabetisch gruppiert)
    "Baden bei Wien",
    "Bruck an der Leitha",            # deckt „Bruck/Leitha Bahnhof“
    "Deutsch Wagram Bahnhof",
    "Ebenfurth Bahnhof",
    "Ebreichsdorf",
    "Eisenstadt",
    "Flughafen Wien",
    "Gänserndorf",
    "Hollabrunn",
    "Korneuburg",
    "Mistelbach",
    "Mödling",
    "Neulengbach",
    "Neusiedl am See",
    "Parndorf",
    "Pressbaum",
    "Purkersdorf Zentrum",
    "St. Pölten",
    "Stockerau",
    "Tulln an der Donau",
    "Tullnerfeld",
    "Wiener Neustadt",
    "Wolkersdorf",
    "Wulkaprodersdorf",

    # bisherige nahe Orte (zur Sicherheit beibehalten)
    "Deutsch Wagram", "Strasshof", "Gerasdorf", "Marchegg",
    "Kritzendorf", "Greifenstein-Altenberg", "Langenzersdorf",
    "Purkersdorf", "Rekawinkel", "Tulln",
    "Schwechat", "Fischamend", "Hainburg", "Wolfsthal",
    "Petronell-Carnuntum", "Bad Deutsch-Altenburg",
]
W_NEAR = {_norm(x) for x in W_NEAR_RAW}

def _is_near(name: str) -> bool:
    n = _norm(name)
    if not n:
        return False
    if OEBB_ONLY_VIENNA:
        return n in W_VIENNA or n.startswith("wien ")
    return n in W_VIENNA or n in W_NEAR or n.startswith("wien ")

def _keep_by_region(title: str, desc: str) -> bool:
    endpoints = _split_endpoints(title)
    if endpoints:
        # Nur behalten, wenn ALLE genannten Endpunkte „nah“ sind
        return all(_is_near(x) for x in endpoints)
    # Fallback: heuristisch auf Wien-Bezug prüfen
    blob = f"{title} {desc}"
    tokens = re.split(r"\W+", blob)
    if any(_is_near(w) for w in tokens):
        if re.search(r"\b(salzburg|innsbruck|villach|bregenz|linz|graz|klagenfurt|bratislava|muenchen|passau|freilassing)\b",
                     blob, re.IGNORECASE):
            return False
        return True
    return False

# ---------------- Fetch/Parse ----------------
def _fetch_xml(url: str, timeout: int = 25) -> ET.Element:
    r = S.get(url, timeout=timeout)
    r.raise_for_status()
    return ET.fromstring(r.content)

def _get_text(elem: Optional[ET.Element], tag: str) -> str:
    e = elem.find(tag) if elem is not None else None
    return (e.text or "") if e is not None else ""

def _parse_dt_rfc2822(s: str) -> Optional[datetime]:
    try:
        dt = parsedate_to_datetime(s)
        if dt is None:
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None

# ---------------- Public ----------------
def fetch_events(timeout: int = 25) -> List[Dict[str, Any]]:
    try:
        root = _fetch_xml(OEBB_URL, timeout=timeout)
    except Exception as e:
        log.exception("ÖBB RSS abruf fehlgeschlagen: %s", e)
        return []

    channel = root.find("channel")
    if channel is None:
        return []

    out: List[Dict[str, Any]] = []
    for item in channel.findall("item"):
        raw_title = _get_text(item, "title")
        title = _clean_title_keep_places(raw_title)
        link  = _get_text(item, "link").strip() or OEBB_URL
        guid  = _get_text(item, "guid").strip() or hashlib.md5((title+link).encode("utf-8")).hexdigest()
        desc_html = _get_text(item, "description")
        desc = html_to_text(desc_html)
        pub = _parse_dt_rfc2822(_get_text(item, "pubDate"))

        # Region-Filter: nur Wien + definierter Pendelraum
        if not _keep_by_region(title, desc):
            continue

        out.append({
            "source": "ÖBB",
            "category": "Störung",
            "title": title,          # bereits kurz & ohne Bahnhof/Hbf
            "description": desc,     # plain
            "link": link,
            "guid": guid,
            "pubDate": pub,
            "starts_at": pub,
            "ends_at": None,
            "_identity": f"oebb|{guid}",
        })

    log.info("ÖBB: %d Items nach Region/Titel-Kosmetik", len(out))
    return out
