#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import os, re, html, hashlib, logging
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone
from xml.etree import ElementTree as ET
from email.utils import parsedate_to_datetime

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

log = logging.getLogger(__name__)

HTTP_TIMEOUT = int(os.getenv("OEBB_HTTP_TIMEOUT", "15"))

def _default_rss_url() -> str:
    return "https://fahrplan.oebb.at/bin/help.exe/dnl?protocol=https:&tpl=rss_WI_oebb&"

def _candidate_urls() -> List[str]:
    urls: List[str] = []
    env = (os.getenv("OEBB_RSS_URL") or "").strip()
    urls.append(env if env else _default_rss_url())
    base = "https://fahrplan.oebb.at/bin/help.exe/dnl"
    for v in ["?tpl=rss_WI_oebb&protocol=https:",
              "?protocol=https:&tpl=rss_WI_oebb",
              "?tpl=rss_WI_oebb",
              "?L=vs_scotty&tpl=rss_WI_oebb",
              "?L=vs_oebb&tpl=rss_WI_oebb"]:
        urls.append(base + v)
    alt = (os.getenv("OEBB_RSS_ALT_URLS") or "").strip()
    if alt:
        urls += [u.strip() for u in alt.split(",") if u.strip()]
    out = []
    for u in urls:
        if u not in out: out.append(u)
    return out

def _session() -> requests.Session:
    s = requests.Session()
    retry = Retry(total=3, backoff_factor=0.5, status_forcelist=(429,500,502,503,504), allowed_methods=("GET",), raise_on_status=False)
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.headers.update({"Accept":"application/rss+xml, application/xml;q=0.9, */*;q=0.1",
                      "User-Agent":"Origamihase-wien-oepnv/1.2 (+https://github.com/Origamihase/wien-oepnv)"})
    return s

S = _session()

# ----------- Wien & unmittelbare Nachbarorte (sehr strenger Filter) ------------

_CORE_PATTERNS = [
    r"\bWien\b", r"\bWien\s*Hbf\b", r"\bWien\s*Hauptbahnhof\b", r"\bWien\s*Meidling\b",
    r"\bWien\s*Floridsdorf\b", r"\bWien\s*Handelskai\b", r"\bWien\s*Praterstern\b",
    r"\bWien\s*Heiligenstadt\b", r"\bWien\s*Spittelau\b", r"\bWien\s*Westbahnhof\b",
    r"\bWien\s*Hütteldorf\b", r"\bWien\s*Penzing\b", r"\bWien\s*Stadlau\b",
    r"\bWien\s*Simmering\b", r"\bWien\s*Liesing\b", r"\bMatzleinsdorf(?:er)?\s*Platz\b",
]
_CORE_RE = re.compile("|".join(_CORE_PATTERNS), re.IGNORECASE)

_NEAR_PATTERNS = [
    r"\bSchwechat\b", r"\bFlughafen\s+Wien\b",
    r"\bGerasdorf\b", r"\bLangenzersdorf\b",
    r"\bKlosterneuburg\b", r"\bKorneuburg\b",
    r"\bPerchtoldsdorf\b", r"\bVösendorf\b", r"\bHennersdorf\b", r"\bLeopoldsdorf\b",
    r"\bMödling\b", r"\bBrunn\s*am\s*Gebirge\b", r"\bMaria\s*Enzersdorf\b",
    r"\bPurkersdorf\b",
]
_NEAR_RES = [re.compile(p, re.IGNORECASE) for p in _NEAR_PATTERNS]

# Fernziele, die wir trotz "Wien" im Text explizit ausschließen
_FAR_PATTERNS = [
    r"\bSalzburg\b", r"\bLinz\b", r"\bWels\b", r"\bAmstetten\b", r"\bSt\.?\s*Pölten\b",
    r"\bKrems\b", r"\bTulln(?:erfeld)?\b",
    r"\bGraz\b", r"\bBruck/?\s*an\s*der\s*Mur\b", r"\bBruck\s*/?\s*Mur\b", r"\bMürzzuschlag\b",
    r"\bVillach\b", r"\bKlagenfurt\b", r"\bLeoben\b", r"\bInnsbruck\b", r"\bBregenz\b",
    r"\bFreilassing\b", r"\bSt\.?\s*Valentin\b", r"\bYbbs\b", r"\bMelk\b",
    r"\bWiener\s*Neustadt\b", r"\bWr\.?\s*Neustadt\b",
]
_FAR_RE = re.compile("|".join(_FAR_PATTERNS), re.IGNORECASE)

def _is_wien_and_very_close(title: str, desc: str) -> bool:
    """
    Zulassen, wenn:
      - es einen WIEN-Kern-Treffer gibt UND
      - KEIN Fernziel vorkommt UND
      - (optional) nahe Umgebung erwähnt sein darf.
    Damit fallen z. B. „Wien – Linz/Graz/Salzburg …“ raus.
    """
    text = f"{title}\n{desc}"
    if not _CORE_RE.search(text):
        return False
    if _FAR_RE.search(text):
        return False
    return True  # Wien-only oder Wien + unmittelbare Nachbarn

# ---------------- Utils ----------------

def _txt(el: Optional[ET.Element], path: str) -> str:
    t = el.findtext(path) if el is not None else None
    return (t or "").strip()

def _parse_pubdate(s: str | None) -> Optional[datetime]:
    if not s: return None
    try:
        dt = parsedate_to_datetime(s)
        if dt is None: return None
        if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None

def _hash_guid(*parts: str) -> str:
    return hashlib.md5("|".join(p or "" for p in parts).encode("utf-8")).hexdigest()

# --------------- Parser ----------------

def _fetch_rss_xml() -> Optional[ET.Element]:
    for url in _candidate_urls():
        try:
            r = S.get(url, timeout=HTTP_TIMEOUT)
            if r.status_code >= 400 or not r.content:
                log.info("ÖBB-RSS: %s -> HTTP %s", url, r.status_code); continue
            root = ET.fromstring(r.content)
            if root.tag.lower().endswith("rss") or root.find("./channel") is not None:
                log.info("ÖBB-RSS geladen: %s (len=%d)", url, len(r.content))
                return root
        except Exception as e:
            log.info("ÖBB-RSS Fehler bei %s: %s", url, e)
    return None

def _iter_items(root: ET.Element) -> List[ET.Element]:
    ch = root.find("./channel")
    return list(ch.findall("./item")) if ch is not None else list(root.findall(".//item"))

# --------------- Public API ------------

def fetch_events() -> List[Dict[str, Any]]:
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

        # Sehr strenger Wien-Filter
        if not _is_wien_and_very_close(title, desc):
            continue

        guid = guid_val or _hash_guid("oebb_rss", title, pub_s, link)
        if guid in seen_guids:
            continue
        seen_guids.add(guid)

        description_html = html.escape(desc) if ("<" not in desc and ">" not in desc) else desc

        items_out.append({
            "source": "ÖBB (RSS)",
            "category": "Störung",
            "title": title or "ÖBB Meldung",
            "description": description_html,
            "link": link,
            "guid": guid,
            "pubDate": pub_dt,   # nur Quelle
            "starts_at": pub_dt, # falls vorhanden
            "ends_at": None,
        })

    items_out.sort(key=lambda x: (0, -int(x["pubDate"].timestamp())) if x["pubDate"] else (1, x["guid"]))
    return items_out
