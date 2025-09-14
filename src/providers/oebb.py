#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
ÖBB/VOR-RSS (Fahrplan-Portal) – Meldungen für Wien & nahe Pendelstrecken.

Was diese Version macht:
- Quelle per Secret OEBB_RSS_URL (Fallback: offizielle ÖBB-RSS-URL)
- Titel-Kosmetik: Kategorie-Vorspann bis zum Doppelpunkt entfernen,
  Pfeile normalisieren (ein einziges „↔“), „Bahnhof (U)/Bahnhst/Hbf/Bf“ weg
- Plain-Text-Description (HTML/Word-Markup raus, Entities decodiert)
- Strenger GEO-Filter: Behalte NUR Meldungen, bei denen ALLE Endpunkte
  im Titel in Wien oder definierter Pendler-Region liegen

Damit verschwinden nationale Fernverkehrs-Baustellen.
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

# ------------------------------------------------------------
# HTTP
# ------------------------------------------------------------
def _session() -> requests.Session:
    s = requests.Session()
    retry = Retry(total=4, backoff_factor=0.6, status_forcelist=(429,500,502,503,504),
                  allowed_methods=("GET",))
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.headers.update({"User-Agent":"Origamihase-wien-oepnv/2.8 (+https://github.com/Origamihase/wien-oepnv)"})
    return s

S = _session()

# ------------------------------------------------------------
# HTML → Text
# ------------------------------------------------------------
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
    # einheitlich: Zeilen zu „ | “
    txt = re.sub(r"\s*\n\s*", " | ", txt)
    # „2025Wegen“ -> „2025 Wegen“
    txt = re.sub(r"(\d)([A-Za-zÄÖÜäöüß])", r"\1 \2", txt)
    txt = _WS_RE.sub(" ", txt)
    return re.sub(r"\s{2,}", " ", txt).strip()

# ------------------------------------------------------------
# Titel-Kosmetik + Endpunkt-Erkennung
# ------------------------------------------------------------
BAHNHOF_TRIM_RE = re.compile(
    r"\s*(?:Bahnhof|Bahnhst|Hbf|Bf)(?:\s*\(U\))?", re.IGNORECASE
)
ARROW_ANY_RE  = re.compile(r"\s*(?:<=>|<->|<>|→|↔|=>|=|–|-)\s*")
COLON_PREFIX_RE = re.compile(
    # Entfernt alles bis zum letzten Doppelpunkt in einem Vorspann,
    # der nur aus Kategorie-/Wortblöcken besteht (inkl. „DB ↔“)
    r"""^\s*(?:Update\s*\d+\s*\([^)]*\)\s*)?
        (?:DB\s*↔\s*)?
        (?:[A-Za-zÄÖÜäöüß/ \-]+:\s*)+
    """,
    re.IGNORECASE | re.VERBOSE
)
MULTI_ARROW_RE = re.compile(r"(?:\s*↔\s*){2,}")

def _clean_title_keep_places(t: str) -> str:
    t = (t or "").strip()
    # Kategorie-Vorspann vor dem Doppelpunkt weg
    t = COLON_PREFIX_RE.sub("", t)
    # Pfeile normalisieren
    parts = [p for p in ARROW_ANY_RE.split(t) if p.strip()]
    if len(parts) >= 2:
        t = f"{parts[0].strip()} ↔ {parts[1].strip()}"
        if len(parts) > 2:
            t += " " + " ".join(parts[2:]).strip()
    t = MULTI_ARROW_RE.sub(" ↔ ", t)
    # Bahnhof-Suffixe aus Namen entfernen
    t = BAHNHOF_TRIM_RE.sub("", t)
    t = re.sub(r"\s{2,}", " ", t)
    t = re.sub(r"[<>«»‹›]+", "", t)
    return t.strip()

def _split_endpoints(title: str) -> Optional[List[str]]:
    """Gibt eine Liste von Endpunkt-Namen zurück (links/rechts),
    jeweils ohne Bahnhof/Hbf/Bf, und ohne Zusätze wie '(U)'.
    """
    if "↔" not in title and "<=>" not in title and "=>" not in title:
        return None
    parts = [p for p in re.split(r"\s*(?:↔|<=>|=>|<|->|—|-)\s*", title) if p.strip()]
    if len(parts) < 2:
        return None
    # nur die beiden ersten Enden betrachten
    left, right = parts[0], parts[1]
    def explode(side: str) -> List[str]:
        # „A/B bzw. C, D und E“ -> ['A','B','C','D','E']
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

# ------------------------------------------------------------
# Pendler-Region (Whitelist)
# ------------------------------------------------------------
def _norm(s: str) -> str:
    s = (s or "").casefold()
    repl = (("ä","a"),("ö","o"),("ü","u"),("ß","ss"))
    for a,b in repl: s = s.replace(a,b)
    return re.sub(r"[^a-z0-9 ]+", " ", s).strip()

# zentrale Knoten Wien
W_VIENNA = {
    "wien", "wien hbf", "wien meidling", "wien floridsdorf", "wien praterstern",
    "wien handelskai", "wien heiligenstadt", "wien spittelau", "wien mitte",
    "wien simmering", "wien stadlau", "wien huetteldorf", "wien huetteldorf",
    "wien atzgersdorf", "wien liesing", "wien donauinsel", "wien suessenbrunn",
    "wien aspern nord", "wien donaustadtbruecke", "wien quartiertsdorf"  # tolerant
}

# sehr naheliegende Pendlerorte (heuristische Auswahl, bewusst konservativ)
W_NEAR = {
    # NÖ/Nord + Ost (Marchfeld, Weinviertel)
    "gaenserndorf", "strasshof", "deutsch wagram", "siebenhirten", "gerasdorf",
    "marchegg", "wolkersdorf", "pillichsdorf", "gross enrzersdorf", "ennisdorf",
    # NÖ/West
    "tulln", "tullnerfeld", "klosterneuburg", "kritzendorf", "greifenstein altenberg",
    "koenigstetten", "purkersdorf", "pressbaum", "rekawinkel",
    # NÖ/Sued
    "moedling", "wiener neudorf", "guntramsdorf", "traiskirchen", "baden",
    "bad voeslau", "leobersdorf", "wiener neustadt", "wr neustadt",
    # NÖ/Suedost + Bruck/Leitha
    "schwechat", "flughafen wien", "mannswoerth", "fischamend",
    "gotzendorf", "bruck an der leitha", "bruck leitha", "hainburg", "wolfsthal",
    "neusiedl am see", "petronell carnuntum", "bad deutsch altenburg"
}

def _is_near(name: str) -> bool:
    n = _norm(name)
    if not n: return False
    return n in W_VIENNA or n in W_NEAR or n.startswith("wien ")

def _keep_by_region(title: str, desc: str) -> bool:
    endpoints = _split_endpoints(title)
    if endpoints:
        # Behalte nur, wenn JEDER genannte Endpunkt „nah“ ist
        return all(_is_near(x) for x in endpoints)
    # Fallback: wenn kein Pfeil erkannt – nur behalten, wenn klar Wien/nah
    blob = f"{title} {desc}"
    # mindestens ein Nah-Treffer und kein offensichtlicher Fernort
    if any(_is_near(w) for w in re.split(r"\W+", blob)):
        if re.search(r"\b(salzburg|innsbruck|villach|bregenz|linz|graz|klagenfurt|bratislava|muenchen|passau|freilassing|st\.?\s*margrethen)\b",
                     blob, re.IGNORECASE):
            return False
        return True
    return False

# ------------------------------------------------------------
# Fetch/Parse
# ------------------------------------------------------------
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

# ------------------------------------------------------------
# Public
# ------------------------------------------------------------
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

        # Region-Filter: nur Wien + sehr nahe Pendlerstrecken
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
