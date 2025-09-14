#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
ÖBB-RSS-Provider (HAFAS „Weginformationen“), streng auf Wien + unmittelbare Nachbarorte.
Titelkürzung:
- Entfernt vorne generische Label (z. B. „Bauarbeiten - Zugausfall/geänderte Fahrzeiten:“).
- Entfernt Bahnhof-Rauschen: „Bahnhof“, „Bhf.“, Klammern wie „(U)“, „(U6)“, „(S)“.
- Normalisiert Relationen: „–/—/-“ → „↔“, „bzw.“ → „/“, bei „/“ wird vor dem ersten Slash „↔“ eingefügt.

Env:
  OEBB_RSS_URL          (Secret empfohlen; fallback intern)
  OEBB_HTTP_TIMEOUT     (Default 15)
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

HTTP_TIMEOUT = int(os.getenv("OEBB_HTTP_TIMEOUT", "15"))

def _default_rss_url() -> str:
    return "https://fahrplan.oebb.at/bin/help.exe/dnl?protocol=https:&tpl=rss_WI_oebb&"

def _candidate_urls() -> List[str]:
    urls: List[str] = []
    env = (os.getenv("OEBB_RSS_URL") or "").strip()
    urls.append(env if env else _default_rss_url())
    base = "https://fahrplan.oebb.at/bin/help.exe/dnl"
    for v in [
        "?tpl=rss_WI_oebb&protocol=https:",
        "?protocol=https:&tpl=rss_WI_oebb",
        "?tpl=rss_WI_oebb",
        "?L=vs_scotty&tpl=rss_WI_oebb",
        "?L=vs_oebb&tpl=rss_WI_oebb",
    ]:
        urls.append(base + v)
    alt = (os.getenv("OEBB_RSS_ALT_URLS") or "").strip()
    if alt:
        urls += [u.strip() for u in alt.split(",") if u.strip()]
    out: List[str] = []
    for u in urls:
        if u not in out:
            out.append(u)
    return out

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
        "User-Agent": "Origamihase-wien-oepnv/1.6 (+https://github.com/Origamihase/wien-oepnv)"
    })
    return s

S = _session()

# --- Wien + unmittelbare Nachbarschaft ---------------------------------------

_CORE_PATTERNS = [
    r"\bWien\b",
    r"\bWien\s*Hbf\b", r"\bWien\s*Hauptbahnhof\b",
    r"\bWien\s*Meidling\b", r"\bWien\s*Floridsdorf\b",
    r"\bWien\s*Handelskai\b", r"\bWien\s*Praterstern\b",
    r"\bWien\s*Heiligenstadt\b", r"\bWien\s*Spittelau\b",
    r"\bWien\s*Westbahnhof\b", r"\bWien\s*Hütteldorf\b",
    r"\bWien\s*Penzing\b", r"\bWien\s*Stadlau\b",
    r"\bWien\s*Simmering\b", r"\bWien\s*Liesing\b",
    r"\bMatzleinsdorf(?:er)?\s*Platz\b",
]
_CORE_RE = re.compile("|".join(_CORE_PATTERNS), re.IGNORECASE)

_NEAR_PATTERNS = [
    r"\bSchwechat\b", r"\bFlughafen\s+Wien\b", r"\bVienna\s*Airport\b",
    r"\bGerasdorf\b", r"\bLangenzersdorf\b",
    r"\bKlosterneuburg\b", r"\bKorneuburg\b",
    r"\bPerchtoldsdorf\b", r"\bVösendorf\b", r"\bHennersdorf\b", r"\bLeopoldsdorf\b",
    r"\bMödling\b", r"\bBrunn\s*am\s*Gebirge\b", r"\bMaria\s*Enzersdorf\b",
    r"\bPurkersdorf\b",
]
_NEAR_RE = re.compile("|".join(_NEAR_PATTERNS), re.IGNORECASE)

_FAR_PATTERNS = [
    r"\bAttnang[- ]?Puchheim\b", r"\bVöcklabruck\b", r"\bWels\b", r"\bLinz\b",
    r"\bSt\.?\s*Pölten\b", r"\bAmstetten\b", r"\bEnns\b", r"\bYbbs\b", r"\bMelk\b",
    r"\bSalzburg\b", r"\bInnsbruck\b", r"\bBregenz\b",
    r"\bKrems\b", r"\bTulln(?:erfeld)?\b",
    r"\bWiener\s*Neustadt\b", r"\bWr\.?\s*Neustadt\b", r"\bBaden\b",
    r"\bGraz\b", r"\bBruck\b.*\bMur\b", r"\bMürzzuschlag\b", r"\bLeoben\b",
    r"\bVillach\b", r"\bKlagenfurt\b",
    r"\bHamburg\b", r"\bBerlin\b", r"\bMünchen\b", r"\bNürnberg\b",
    r"\bZürich\b", r"\bBasel\b",
    r"\bPrag\b", r"\bBrno\b", r"\bBudapest\b",
    r"\bAmsterdam\b", r"\bFrankfurt\b", r"\bStuttgart\b",
    r"\bBratislava\b",
]
_FAR_RE = re.compile("|".join(_FAR_PATTERNS), re.IGNORECASE)

# --- Titelkürzung ------------------------------------------------------------

_LABELS = [
    r"bauarbeiten", r"zugausfall(?:e)?", r"geänderte\s*fahrzeiten", r"fahrplanänderung",
    r"einschränkungen?", r"störung", r"verkehrsmeldung", r"baustelle", r"verkehrsinfo",
]
_LABEL_RE = re.compile(r"^\s*(?:(?:" + "|".join(_LABELS) + r")\s*(?:[-:–—]|/\s*)\s*)+", re.IGNORECASE)

# Bahnhof-/Klammer-Rauschen
PAREN_U_S_RE   = re.compile(r"\s*\((?:U\d*|S\d*)\)", re.IGNORECASE)  # (U), (U6), (S), (S45) ...
BAHNHOF_RE     = re.compile(r"\bBahnhof\b\.?", re.IGNORECASE)        # „Bahnhof“
BHF_RE         = re.compile(r"\bBhf\.?\b", re.IGNORECASE)            # „Bhf“ (nicht „Hbf“!)
DASH_RE        = re.compile(r"\s[-–—]\s")                             # -, –, —
BZW_RE         = re.compile(r"\s*bzw\.?\s*", re.IGNORECASE)
SPACES_RE      = re.compile(r"\s{2,}")

def _tidy_title(title: str) -> str:
    t = title or ""
    # 1) Führende Labels entfernen
    t = _LABEL_RE.sub("", t)

    # 2) Bahnhof-/Klammer-Rauschen entfernen
    t = PAREN_U_S_RE.sub("", t)        # (U), (U6), (S), (S45) ...
    t = BAHNHOF_RE.sub("", t)          # Bahnhof
    t = BHF_RE.sub("", t)              # Bhf (Hbf bleibt erhalten)

    # 3) Relationen & Verbinder normalisieren
    t = DASH_RE.sub(" ↔ ", t)          # -/–/— → ↔
    t = BZW_RE.sub("/", t)             # bzw. → /

    # 4) Wenn ein Slash vorkommt, vor dem ersten Slash einen Pfeil einfügen
    if "/" in t and "↔" not in t:
        idx = t.find("/")
        left = t[:idx]
        if " " in left:
            li = left.rfind(" ")
            if li >= 0:
                t = left[:li] + " ↔ " + left[li+1:] + t[idx:]

    # 5) Aufräumen von Mehrfach-Leerzeichen & Rändern
    t = SPACES_RE.sub(" ", t).strip(" -–—:/\t")
    return t or (title or "ÖBB Meldung")

# --- Utils -------------------------------------------------------------------

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

# --- Fetch/Parse -------------------------------------------------------------

def _fetch_rss_xml() -> Optional[ET.Element]:
    for url in _candidate_urls():
        try:
            r = S.get(url, timeout=HTTP_TIMEOUT)
            if r.status_code >= 400 or not r.content:
                log.info("ÖBB-RSS: %s -> HTTP %s", url, r.status_code)
                continue
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

# --- Wien + Facility Filter --------------------------------------------------

FACILITY_ONLY = re.compile(
    r"\b(aufzug|aufzüge|lift|fahrstuhl|fahrtreppe|fahrtreppen|rolltreppe|rolltreppen)\b",
    re.IGNORECASE
)

def _is_wien_and_near_only(title: str, desc: str) -> bool:
    text = f"{title}\n{desc}"
    if not _CORE_RE.search(text):
        return False
    if _FAR_RE.search(text):
        return False
    if re.search(r"[=\/–—\-]", text) and not _NEAR_RE.search(text):
        return False
    return True

def _is_facility_only(*texts: str) -> bool:
    return bool(FACILITY_ONLY.search(" ".join([t for t in texts if t]) or ""))

# --- Public ------------------------------------------------------------------

def fetch_events() -> List[Dict[str, Any]]:
    root = _fetch_rss_xml()
    if root is None:
        return []

    items_out: List[Dict[str, Any]] = []
    seen_guids: set[str] = set()

    for it in _iter_items(root):
        raw_title = _txt(it, "title")
        title = _tidy_title(html.unescape(raw_title))
        desc  = _txt(it, "description") or _txt(it, "{http://purl.org/rss/1.0/modules/content/}encoded")
        desc  = desc.strip()
        link  = _txt(it, "link") or "https://www.oebb.at/"
        guid_el = it.find("guid")
        guid_val = (guid_el.text.strip() if guid_el is not None and guid_el.text else "")
        pub_s = _txt(it, "pubDate")
        pub_dt = _parse_pubdate(pub_s)

        # Ausschlüsse
        if _is_facility_only(title, desc):
            continue
        if not _is_wien_and_near_only(title, desc):
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
