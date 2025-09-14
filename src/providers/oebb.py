#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
ÖBB/VOR-RSS (Fahrplan-Portal) – Meldungen für Wien & nahe Pendelstrecken.
Quelle per Secret OEBB_RSS_URL. Titel/Description werden geglättet:

- HTML/Word-Markup -> Plain-Text
- „Bahnhof (U)“/„Bahnhof“ entfernt
- Mehrfach-Pfeile „<=> ↔“ -> genau ein „↔“
- Kosmetik bei zusammengeklebten Datums-/Textteilen
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

def _session() -> requests.Session:
    s = requests.Session()
    retry = Retry(total=4, backoff_factor=0.6, status_forcelist=(429,500,502,503,504), allowed_methods=("GET",))
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.headers.update({"User-Agent":"Origamihase-wien-oepnv/2.7 (+https://github.com/Origamihase/wien-oepnv)"})
    return s

S = _session()

# ---------- HTML → Text ----------
_BR_RE = re.compile(r"(?i)<\s*br\s*/?\s*>")
_BLOCK_CLOSE_RE = re.compile(r"(?is)</\s*(p|div|li|ul|ol|h\d)\s*>")
_BLOCK_OPEN_RE  = re.compile(r"(?is)<\s*(p|div|ul|ol|h\d)\b[^>]*>")
_LI_OPEN_RE     = re.compile(r"(?is)<\s*li\b[^>]*>")
_TAG_RE         = re.compile(r"(?is)<[^>]+>")
_WS_RE          = re.compile(r"[ \t\r\f\v]+")

def _html_to_text(s: str) -> str:
    if not s: return ""
    txt = html.unescape(s)
    txt = _BR_RE.sub("\n", txt)
    txt = _BLOCK_CLOSE_RE.sub("\n", txt)
    txt = _LI_OPEN_RE.sub("• ", txt)
    txt = _BLOCK_OPEN_RE.sub("", txt)
    txt = _TAG_RE.sub("", txt)
    txt = re.sub(r"\s*\n\s*", " | ", txt)
    txt = _WS_RE.sub(" ", txt)
    txt = re.sub(r"\s{2,}", " ", txt).strip()
    # Kleber trennen: „2025Wegen“ -> „2025 Wegen“
    txt = re.sub(r"(\d)([A-Za-zÄÖÜäöüß])", r"\1 \2", txt)
    return txt

# ---------- Titel-Kosmetik ----------
BAHNHOF_RE = re.compile(r"\s*Bahnhof(?:\s*\(U\))?", re.IGNORECASE)
ARROW_ANY  = re.compile(r"\s*(?:<=>|<->|<>|↔|–|-)\s*")
MULTI_ARROW = re.compile(r"(?:\s*↔\s*){2,}")

def _clean_title(t: str) -> str:
    t = t or ""
    t = BAHNHOF_RE.sub("", t)
    # Doppelpfeile normalisieren
    parts = [p for p in ARROW_ANY.split(t) if p.strip()]
    if len(parts) >= 2:
        t = f"{parts[0].strip()} ↔ {parts[1].strip()}"
        if len(parts) > 2:  # hänge Rest konsistent an
            t += " " + " ".join(parts[2:]).strip()
    t = MULTI_ARROW.sub(" ↔ ", t)
    t = re.sub(r"[<>«»‹›]+", "", t)
    return re.sub(r"\s{2,}", " ", t).strip()

# ---------- Fetch/Parse ----------
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
        if dt is None: return None
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None

def fetch_events(timeout: int = 25) -> List[Dict[str, Any]]:
    try:
        root = _fetch_xml(OEBB_URL)
    except Exception as e:
        log.exception("ÖBB RSS abruf fehlgeschlagen: %s", e)
        return []

    channel = root.find("channel")
    if channel is None: return []

    items_out: List[Dict[str, Any]] = []
    for item in channel.findall("item"):
        title = _clean_title(_get_text(item, "title"))
        link  = _get_text(item, "link").strip() or OEBB_URL
        guid  = _get_text(item, "guid").strip() or hashlib.md5((title+link).encode("utf-8")).hexdigest()
        desc_html = _get_text(item, "description")
        desc = _html_to_text(desc_html)
        pub = _parse_dt_rfc2822(_get_text(item, "pubDate"))

        items_out.append({
            "source": "ÖBB",
            "category": "Störung",
            "title": title,                 # schon plain
            "description": desc,            # plain
            "link": link,
            "guid": guid,
            "pubDate": pub,
            "starts_at": pub,
            "ends_at": None,
            "_identity": f"oebb|{guid}",
        })

    # Sortierung belassen; build_feed sortiert final
    return items_out
