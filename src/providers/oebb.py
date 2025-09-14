#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
ÖBB-RSS-Provider (HAFAS „Weginformationen“):
Liest ein öffentliches RSS (Störungen/Bauarbeiten/Hinweise) und liefert Events
für den Großraum Wien – quellenreines pubDate, ohne künstliche Datumswerte.

- QUELLE: RSS 2.0 (z. B. https://fahrplan.oebb.at/bin/help.exe/dnl?protocol=https:&tpl=rss_WI_oebb&)
- Nur Großraum Wien (konfigurierbarer Keyword-/Stations-Filter)
- Titel/Beschreibung bleiben roh; die TV-Optimierung übernimmt build_feed.py
- GUID stabil (aus RSS-<guid> oder Hash aus Quelle)
- Dedupe passiert zusätzlich global über GUIDs im Build

ENV (optional):
  OEBB_RSS_URL                   Default: s. _default_rss_url()
  OEBB_RSS_ALT_URLS              Kommagetrennte Fallback-URLs/Varianten
  OEBB_HTTP_TIMEOUT              Default 15
  OEBB_ONLY_VIENNA               "1" = auf Wien filtern (Default "1")
  OEBB_VIENNA_REGEX              Eigene Regex (überschreibt Keywordliste)
"""

from __future__ import annotations

import os
import re
import html
import hashlib
import logging
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone
from xml.etree import ElementTree as ET
from email.utils import parsedate_to_datetime

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

log = logging.getLogger(__name__)

# ------------------ Konfig ------------------

HTTP_TIMEOUT = int(os.getenv("OEBB_HTTP_TIMEOUT", "15"))
ONLY_VIENNA = os.getenv("OEBB_ONLY_VIENNA", "1") == "1"

# Wien-Keywords (Titel/Beschreibung). Du kannst das via OEBB_VIENNA_REGEX überschreiben.
_VIENNA_KEYWORDS = [
    r"\bWien\b", r"\bVienna\b",
    r"\bWien\s*Hbf\b", r"\bWien\s*Hauptbahnhof\b",
    r"\bWien\s*Meidling\b", r"\bWien\s*Floridsdorf\b",
    r"\bWien\s*Handelskai\b", r"\bPraterstern\b",
    r"\bHeiligenstadt\b", r"\bHütteldorf\b", r"\bStadlau\b",
    r"\bSimmering\b", r"\bKaiserebersdorf\b", r"\bLiesing\b",
    r"\bKagran\b", r"\bDonaustadtbrücke\b", r"\bAspern\b",
    r"\bTransdanubien\b", r"\bS[0-9]{1,2}\b", r"\bS80\b", r"\bREX\b", r"\bRJX?\b",
]
_VIENNA_RE = re.compile(os.getenv("OEBB_VIENNA_REGEX", "|".join(_VIENNA_KEYWORDS)), re.IGNORECASE)

def _default_rss_url() -> str:
    # Deine Vorlage; wird zuerst probiert.
    return "https://fahrplan.oebb.at/bin/help.exe/dnl?protocol=https:&tpl=rss_WI_oebb&"

def _candidate_urls() -> List[str]:
    urls = []
    env = (os.getenv("OEBB_RSS_URL") or "").strip()
    if env:
        urls.append(env)
    else:
        urls.append(_default_rss_url())

    # Fallback-Varianten, die bei manchen HAFAS-Instanzen nötig sind
    # (kein Schaden, wenn sie 404/leer liefern – wir gehen einfach weiter):
    base = "https://fahrplan.oebb.at/bin/help.exe/dnl"
    variants = [
        "?tpl=rss_WI_oebb&protocol=https:",
        "?protocol=https:&tpl=rss_WI_oebb",
        "?tpl=rss_WI_oebb",
        "?L=vs_scotty&tpl=rss_WI_oebb",
        "?L=vs_oebb&tpl=rss_WI_oebb",
    ]
    for v in variants:
        urls.append(base + v)

    alt = (os.getenv("OEBB_RSS_ALT_URLS") or "").strip()
    if alt:
        urls.extend([u.strip() for u in alt.split(",") if u.strip()])
    # Deduplizieren, Reihenfolge bewahren
    out: List[str] = []
    for u in urls:
        if u not in out:
            out.append(u)
    return out

# ------------------ HTTP ------------------

def _session() -> requests.Session:
    s = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
        raise_on_status=False,
    )
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.headers.update({
        "Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.1",
        "User-Agent": "Origamihase-wien-oepnv/1.0 (+https://github.com/Origamihase/wien-oepnv)"
    })
    return s

S = _session()

# ------------------ Utils ------------------

def _txt(el: Optional[ET.Element], path: str) -> str:
    t = el.findtext(path) if el is not None else None
    return (t or "").strip()

def _parse_pubdate(s: str | None) -> Optional[datetime]:
    if not s:
        return None
    try:
        dt = parsedate_to_datetime(s)
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None

def _hash_guid(*parts: str) -> str:
    base = "|".join(p or "" for p in parts)
    return hashlib.md5(base.encode("utf-8")).hexdigest()

def _is_vienna(title: str, desc: str) -> bool:
    if not ONLY_VIENNA:
        return True
    text = f"{title}\n{desc}"
    return bool(_VIENNA_RE.search(text))

# ------------------ Parser ------------------

def _fetch_rss_xml() -> Optional[ET.Element]:
    for url in _candidate_urls():
        try:
            r = S.get(url, timeout=HTTP_TIMEOUT)
            if r.status_code >= 400 or not r.content:
                log.info("ÖBB-RSS: %s -> HTTP %s", url, r.status_code)
                continue
            root = ET.fromstring(r.content)
            # RSS 2.0: <rss><channel><item>…
            if root.tag.lower().endswith("rss") or root.find("./channel") is not None:
                log.info("ÖBB-RSS geladen: %s (len=%d)", url, len(r.content))
                return root
        except Exception as e:
            log.info("ÖBB-RSS Fehler bei %s: %s", url, e)
    return None

def _iter_items(root: ET.Element) -> List[ET.Element]:
    ch = root.find("./channel")
    if ch is None:
        # Manchmal ist es Atom – sehr selten bei diesem Template, aber wir prüfen:
        return list(root.findall(".//item"))
    return list(ch.findall("./item"))

# ------------------ Public API ------------------

def fetch_events() -> List[Dict[str, Any]]:
    """
    Liest ÖBB-Weginformationen aus einem RSS-Feed, filtert auf Großraum Wien und
    liefert das Standard-Event-Schema:
      source, category, title, description, link, guid, pubDate, starts_at, ends_at
    - pubDate: ausschließlich aus der Quelle (kann None sein)
    - starts_at/ends_at: i. d. R. unbekannt (None)
    """
    root = _fetch_rss_xml()
    if root is None:
        log.info("ÖBB-RSS: keine Daten erhalten.")
        return []

    items_out: List[Dict[str, Any]] = []
    seen_guids: set[str] = set()

    for it in _iter_items(root):
        title = html.unescape(_txt(it, "title"))
        desc  = _txt(it, "description") or _txt(it, "{http://purl.org/rss/1.0/modules/content/}encoded")
        desc  = desc.strip()
        link  = _txt(it, "link") or "https://www.oebb.at/"
        guid_el = it.find("guid")
        guid_val = (guid_el.text.strip() if guid_el is not None and guid_el.text else "")
        pub_s = _txt(it, "pubDate")
        pub_dt = _parse_pubdate(pub_s)

        # Wien-Filter
        if not _is_vienna(title, desc):
            continue

        # Stabile GUID
        guid = guid_val or _hash_guid("oebb_rss", title, pub_s, link)
        if guid in seen_guids:
            continue
        seen_guids.add(guid)

        # Beschreibung minimal säubern (build_feed.py macht Rest/TV-Kürzung)
        description_html = html.escape(desc) if ("<" not in desc and ">" not in desc) else desc

        items_out.append({
            "source": "ÖBB (RSS)",
            "category": "Störung",       # RSS unterscheidet nicht immer fein; „Baustelle“ etc. stehen im Text
            "title": title or "ÖBB Meldung",
            "description": description_html,
            "link": link,
            "guid": guid,
            "pubDate": pub_dt,           # nur Quelle; kann None sein
            "starts_at": pub_dt,         # besser als gar nichts – echte Enddaten liefert RSS meist nicht
            "ends_at": None,
        })

    # sortiert wird später global (build_feed.py), aber eine lokale Ordnung schadet nicht
    items_out.sort(key=lambda x: (0, -int(x["pubDate"].timestamp())) if x["pubDate"] else (1, x["guid"]))
    return items_out
