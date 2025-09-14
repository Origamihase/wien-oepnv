#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
ÖBB/VOR-RSS (Fahrplan-Portal) – Meldungen für Wien & nahe Pendelstrecken.

- Secret OEBB_RSS_URL (Fallback: offizielle ÖBB-RSS-URL)
- Titel-Kosmetik: Kategorie-Vorspann (bis Doppelpunkt) weg, Pfeile → „↔“,
  „Bahnhof (U)/Bahnhst/Hbf/Bf“ entfernen
- Plain-Text-Description (HTML/Word raus, Entities decodiert)
- Strenger GEO-Filter: Behalte NUR Meldungen, deren Endpunkte in Wien
  oder definierter Pendler-Region liegen
"""

from __future__ import annotations

import hashlib
import html
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from email.utils import parsedate_to_datetime

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from xml.etree import ElementTree as ET

log = logging.getLogger(__name__)

OEBB_URL = (os.getenv("OEBB_RSS_URL", "").strip()
            or "https://fahrplan.oebb.at/bin/help.exe/dnl?protocol=https:&tpl=rss_WI_oebb&")

# ---------------- HTTP ----------------
def _session() -> requests.Session:
    s = requests.Session()
    retry = Retry(total=4, backoff_factor=0.6, status_forcelist=(429,500,502,503,504),
                  allowed_methods=("GET",))
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.headers.update({"User-Agent":"Origamihase-wien-oepnv/2.9 (+https://github.com/Origamihase/wien-oepnv)"})
    return s

S = _session()

# ---------------- HTML → Text ----------------
_BR_RE = re.compile(r"(?i)<\s*br\s*/?\s*>")
_BLOCK_CLOSE_RE = re.compile(r"(?is)</\s*(p|div|li|ul|ol|h\d|table|tr|td)\s*>")
_BLOCK_OPEN_RE  = re.compile(r"(?is)<\s*(p|div|ul|ol|h\d|table|tr|td)\b[^>]*>")
_LI_OPEN_RE     = re.compile(r"(?is)<\s*li\b[^>]*>")
_TAG_RE         = re.compile(r"(?is)<[^>]+>")
_WS_RE          = re.compile(r"[ \t\r\f\v]+")

def _html_to_text(s: str) -> str:
    if not s:
        return ""
    txt = html.unescape(s)
    txt = _BR_RE.sub("\n", txt)
    txt = _BLOCK_CLOSE_RE.sub("\n", txt)
    txt = _LI_OPEN_RE.sub("• ", txt)
    txt = _BLOCK_OPEN_RE.sub("", txt)
    txt = _TAG_RE.sub("", txt)
    # Einheitlicher Trenner:
    txt = re.sub(r"\s*\n\s*", " • ", txt)
    # „2025Wegen“ -> „2025 Wegen“
    txt = re.sub(r"(\d)([A-Za-zÄÖÜäöüß])", r"\1 \2", txt)
    txt = _WS_RE.sub(" ", txt)
    return re.sub(r"\s{2,}", " ", txt).strip()

# ---------------- Titel + Endpunkte ----------------
BAHNHOF_TRIM_RE = re.compile(r"\s*(?:Bahnhof|Bahnhst|Hbf|Bf)(?:\s*\(U\))?", re.IGNORECASE)
ARROW_ANY_RE    = re.compile(r"\s*(?:<=>|<->|<>|→|↔|=>|=|–|-)\s*")
COLON_PREFIX_RE = re.compile(
    r"""^\s*(?:Update\s*\d+\s*\([^)]*\)\s*)?
        (?:DB\s*↔\s*)?
        (?:[A-Za-zÄÖÜäöüß/ \-]+:\s*)+
    """, re.IGNORECASE | re.VERBOSE
)
MULTI_ARROW_RE  = re.compile(r"(?:\s*↔\s*){2,}")

def _clean_title_keep_places(t: str) -> str:
    t = (t or "").strip()
    t = COLON_PREFIX_RE.sub("", t)  # Vorspann bis Doppelpunkt entfernen
    parts = [p for p in ARROW_ANY_RE.split(t) if p.strip()]
    if len(parts) >= 2:
        t = f"{parts[0].strip()} ↔ {parts[1].strip()}"
        if len(parts) > 2:
            t += " " + " ".join(parts[2:]).strip()
    t = MULTI_ARROW_RE.sub(" ↔ ", t)
    t = BAHNHOF_TRIM_RE.sub("", t)
    t = re.sub(r"\s{2,}", " ", t)
    t = re.sub(r"[<>«»‹›]+", "", t)
    return t.strip()

def _split_endpoints(title: str) -> Optional[List[str]]:
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
            n = re.sub(r"\s*\([^)]*\)\s*", "", n)
            n = re.sub(r"\s{2,}", " ", n).strip(" .")
            if n:
                names.append(n)
        return names
    return explode(left) + explode(right)

# ---------------- Pendler-Region ----------------
def _norm(s: str) -> str:
    s = (s or "").casefold()
    for a,b in (("ä","a"),("ö","o"),("ü","u"),("ß","ss")):
        s = s.replace(a,b)
    return re.sub(r"[^a-z0-9 ]+", " ", s).strip()

W_VIENNA = {
    "wien", "wien hbf", "wien meidling", "wien floridsdorf", "wien praterstern",
    "wien handelskai", "wien heiligenstadt", "wien spittelau", "wien mitte",
    "wien simmering", "wien stadlau", "wien huetteldorf", "wien liesing",
}

# Ergänzt: Korneuburg/Stockerau-Ast (+ 2–3 typische Nachbarn)
W_NEAR = {
    # Nördlicher/östlicher Gürtel
    "gaenserndorf", "strasshof", "deutsch wagram", "gerasdorf", "marchegg", "wolkersdorf",
    # Donau/Nordwest
    "klosterneuburg", "kritzendorf", "greifenstein altenberg", "langenzersdorf",
    "korneuburg", "bisamberg", "leobendorf", "spillern", "stockerau",
    # West/Süd
    "purkersdorf", "pressbaum", "rekawinkel", "tulln", "tullnerfeld",
    "moedling", "wiener neudorf", "guntramsdorf", "baden", "bad voeslau",
    "leobersdorf", "wr neustadt", "wiener neustadt",
    # Südost
    "schwechat", "flughafen wien", "fischamend", "bruck an der leitha", "bruck leitha",
    "hainburg", "wolfsthal", "neusiedl am see", "petronell carnuntum", "bad deutsch altenburg",
}

def _is_near(name: str) -> bool:
    n = _norm(name)
    if not n:
        return False
    return n in W_VIENNA or n in W_NEAR or n.startswith("wien ")

def _keep_by_region(title: str, desc: str) -> bool:
    endpoints = _split_endpoints(title)
    if endpoints:
        return all(_is_near(x) for x in endpoints)
    blob = f"{title} {desc}"
    if any(_is_near(w) for w in re.split(r"\W+", blob)):
        if re.search(r"\b(salzburg|innsbruck|villach|bregenz|linz|graz|klagenfurt|bratislava|muenchen|passau|freilassing)\b",
                     blob, re.IGNORECASE):
            return False
        return True
    return False

# ---------------- Fetch/Parse ----------------
def _fetch_xml(url: str) -> ET.Element:
    r = S.get(url, timeout=25)
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
        root = _fetch_xml(OEBB_URL)
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
        desc = _html_to_text(desc_html)
        pub = _parse_dt_rfc2822(_get_text(item, "pubDate"))

        if not _keep_by_region(title, desc):
            continue

        out.append({
            "source": "ÖBB",
            "category": "Störung",
            "title": title,
            "description": desc,
            "link": link,
            "guid": guid,
            "pubDate": pub,
            "starts_at": pub,
            "ends_at": None,
            "_identity": f"oebb|{guid}",
        })

    log.info("ÖBB: %d Items nach Region/Titel-Kosmetik", len(out))
    return out
