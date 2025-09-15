"""Fetching and assembling events from the Wiener Linien API."""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

import requests
from dateutil import parser as dtparser
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

try:  # pragma: no cover - support both package layouts
    from utils.text import html_to_text
except ModuleNotFoundError:  # pragma: no cover
    from src.utils.text import html_to_text  # type: ignore

try:  # pragma: no cover - support both package layouts
    from utils.ids import make_guid
except ModuleNotFoundError:  # pragma: no cover
    from src.utils.ids import make_guid  # type: ignore

try:  # pragma: no cover - support both package layouts
    from utils.stations import canonical_name
except ModuleNotFoundError:  # pragma: no cover
    from src.utils.stations import canonical_name  # type: ignore

from .wl_lines import (
    _detect_line_pairs_from_text,
    _ensure_line_prefix,
    _line_display_from_pairs,
    _line_tokens_from_pairs,
    _make_line_pairs_from_related,
    _merge_line_pairs,
)
from .wl_text import (
    KW_EXCLUDE,
    KW_RESTRICTION,
    _is_facility_only,
    _tidy_title_wl,
    _title_core,
    _topic_key_from_title,
)

# Basis-URL aus Secret/ENV, Fallback: OGD-Endpoint
WL_BASE = (
    os.getenv("WL_RSS_URL", "").strip()
    or "https://www.wienerlinien.at/ogd_realtime"
).rstrip("/")

log = logging.getLogger(__name__)

# Precompiled regex patterns
_ALPHA_RE = re.compile(r"[A-Za-zÄÖÜäöüß]")
_HALT_SUFFIX_RE = re.compile(r"\(\d+\s+Halt(?:e)?\)$")


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
    s.headers.update(
        {
            "Accept": "application/json",
            "User-Agent": "Origamihase-wien-oepnv/3.1 (+https://github.com/Origamihase/wien-oepnv)",
        }
    )
    return s


# ---------------- Zeit & Utils ----------------

def _iso(s: Optional[str]) -> Optional[datetime]:
    """Parst ISO (inkl. 'Z' / TZ ohne Doppelpunkt) robust zu aware datetime."""

    if not s:
        return None
    s = s.replace("Z", "+00:00")
    if len(s) >= 5 and (s[-5] in "+-") and s[-3] != ":":
        s = s[:-2] + ":" + s[-2:]
    dt = dtparser.isoparse(s)
    if not dt.tzinfo:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _best_ts(obj: Dict[str, Any]) -> Optional[datetime]:
    t = obj.get("time") or {}
    for cand in (
        _iso(t.get("start")),
        _iso(t.get("end")),
        _iso(obj.get("updated")),
        _iso(obj.get("timestamp")),
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


# ---------------- Stop-Namen extrahieren ----------------

def _stop_names_from_related(rel_stops: List[Any]) -> List[str]:
    dedup: Dict[str, str] = {}
    for s in rel_stops:
        raw: str | None = None
        if isinstance(s, dict):
            for key in ("name", "stopName", "title"):
                val = s.get(key)
                if val and _ALPHA_RE.search(str(val)):
                    raw = str(val).strip()
                    break
        elif isinstance(s, str):
            if _ALPHA_RE.search(s):
                raw = s.strip()
        if not raw:
            continue
        canonical = canonical_name(raw)
        final = re.sub(r"\s{2,}", " ", (canonical or raw)).strip()
        if not final:
            continue
        key = final.casefold()
        dedup.setdefault(key, final)
    return sorted(dedup.values(), key=lambda x: x.casefold())


# ---------------- API Calls ----------------

def _get_json(path: str, params: Optional[List[tuple]] = None, timeout: int = 20) -> Dict[str, Any]:
    url = f"{WL_BASE.rstrip('/')}/{path.lstrip('/')}"
    with _session() as s:
        r = s.get(url, params=params or None, timeout=timeout)
        r.raise_for_status()
        return r.json()


def _fetch_traffic_infos(timeout: int = 20) -> Iterable[Dict[str, Any]]:
    # explizit KEINE Facility-Feeds
    params = [("name", "stoerunglang"), ("name", "stoerungkurz")]
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
            status_blob = " ".join(
                [
                    str(ti.get("status") or ""),
                    str(attrs.get("status") or ""),
                    str(attrs.get("state") or ""),
                ]
            ).lower()
            if any(
                x in status_blob
                for x in (
                    "finished",
                    "inactive",
                    "inaktiv",
                    "done",
                    "closed",
                    "nicht aktiv",
                    "ended",
                    "ende",
                    "abgeschlossen",
                    "beendet",
                    "geschlossen",
                )
            ):
                continue

            title_raw = (ti.get("title") or ti.get("name") or "Meldung").strip()
            title = _tidy_title_wl(title_raw)
            desc_raw = (ti.get("description") or "").strip()
            desc = html_to_text(desc_raw)
            if _is_facility_only(title_raw, desc_raw):
                continue

            tinfo = ti.get("time") or {}
            start = _iso(tinfo.get("start")) or _best_ts(ti)
            end = _iso(tinfo.get("end"))
            if not _is_active(start, end, now):
                continue

            blob_for_relevance = " ".join([title_raw, desc_raw])
            if KW_EXCLUDE.search(blob_for_relevance) and not KW_RESTRICTION.search(
                blob_for_relevance
            ):
                continue

            rel_lines = _as_list(ti.get("relatedLines") or attrs.get("relatedLines"))
            line_pairs = _make_line_pairs_from_related(rel_lines)
            if not line_pairs:
                # Fallback: aus Titeltext (inkl. „Rufbus Nxx“, aber ohne Datum/Zeit/Adresse)
                line_pairs = _detect_line_pairs_from_text(title_raw)

            rel_stops = _as_list(ti.get("relatedStops") or attrs.get("relatedStops"))
            stop_names = _stop_names_from_related(rel_stops)

            extras = []
            for k in ("status", "state", "station", "location", "reason", "towards"):
                if attrs.get(k):
                    extras.append(f"{k.capitalize()}: {str(attrs[k]).strip()}")

            # stabile Identity für first_seen
            id_lines = ",".join(sorted(_line_tokens_from_pairs(line_pairs)))
            id_day = start.date().isoformat() if isinstance(start, datetime) else "None"
            identity = f"wl|störung|L={id_lines}|D={id_day}"

            raw.append(
                {
                    "source": "Wiener Linien",
                    "category": "Störung",
                    "title": title,
                    "title_core": _title_core(title_raw),
                    "topic_key": _topic_key_from_title(title_raw),
                    "desc": desc,
                    "extras": extras,
                    "lines_pairs": line_pairs,  # [(tok, disp), …]
                    "stop_names": set(stop_names),
                    "pubDate": start,  # ggf. None
                    "starts_at": start,
                    "ends_at": end,
                    "_identity": identity,
                }
            )
    except requests.RequestException as e:  # pragma: no cover - network errors
        log.exception("WL trafficInfoList fehlgeschlagen: %s", e)

    # B) News/Hinweise
    try:
        for poi in _fetch_news(timeout=timeout):
            attrs = poi.get("attributes") or {}
            status_blob = " ".join(
                [
                    str(poi.get("status") or ""),
                    str(attrs.get("status") or ""),
                    str(attrs.get("state") or ""),
                ]
            ).lower()
            if any(
                x in status_blob
                for x in (
                    "finished",
                    "inactive",
                    "inaktiv",
                    "done",
                    "closed",
                    "nicht aktiv",
                    "ended",
                    "ende",
                    "abgeschlossen",
                    "beendet",
                    "geschlossen",
                )
            ):
                continue

            title_raw = (poi.get("title") or "Hinweis").strip()
            title = _tidy_title_wl(title_raw)
            desc_raw = (poi.get("description") or "").strip()
            desc = html_to_text(desc_raw)
            if _is_facility_only(title_raw, desc_raw, poi.get("subtitle") or ""):
                continue

            tinfo = poi.get("time") or {}
            start = _iso(tinfo.get("start")) or _best_ts(poi)
            end = _iso(tinfo.get("end"))
            if not _is_active(start, end, now):
                continue

            text_for_filter = " ".join(
                [
                    title_raw,
                    poi.get("subtitle") or "",
                    desc_raw,
                    str(attrs.get("status") or ""),
                    str(attrs.get("state") or ""),
                ]
            )
            if not KW_RESTRICTION.search(text_for_filter):
                continue

            rel_lines = _as_list(poi.get("relatedLines") or attrs.get("relatedLines"))
            line_pairs = _make_line_pairs_from_related(rel_lines)
            if not line_pairs:
                line_pairs = _detect_line_pairs_from_text(title_raw)

            rel_stops = _as_list(poi.get("relatedStops") or attrs.get("relatedStops"))
            stop_names = _stop_names_from_related(rel_stops)

            extras = []
            if poi.get("subtitle"):
                extras.append(str(poi["subtitle"]).strip())
            for k in ("station", "location", "towards"):
                if attrs.get(k):
                    extras.append(f"{k.capitalize()}: {str(attrs[k]).strip()}")

            id_lines = ",".join(sorted(_line_tokens_from_pairs(line_pairs)))
            id_day = start.date().isoformat() if isinstance(start, datetime) else "None"
            identity = f"wl|hinweis|L={id_lines}|D={id_day}"

            raw.append(
                {
                    "source": "Wiener Linien",
                    "category": "Hinweis",
                    "title": title,
                    "title_core": _title_core(title_raw),
                    "topic_key": _topic_key_from_title(title_raw),
                    "desc": desc,
                    "extras": extras,
                    "lines_pairs": line_pairs,  # [(tok, disp), …]
                    "stop_names": set(stop_names),
                    "pubDate": start,
                    "starts_at": start,
                    "ends_at": end,
                    "_identity": identity,
                }
            )
    except requests.RequestException as e:  # pragma: no cover - network errors
        log.exception("WL newsList fehlgeschlagen: %s", e)

    # C) Bündelung: LINIEN-SET + TOPIC
    buckets: Dict[str, Dict[str, Any]] = {}
    for ev in raw:
        line_toks_sorted = ",".join(sorted(_line_tokens_from_pairs(ev["lines_pairs"])))
        key = make_guid(
            "wl",
            ev["category"],
            ev["topic_key"],
            line_toks_sorted,
        )
        b = buckets.get(key)
        if not b:
            buckets[key] = {
                "source": ev["source"],
                "category": ev["category"],
                "title": ev["title"],
                "title_core": ev.get("title_core", ""),
                "topic_key": ev["topic_key"],
                "desc_base": ev["desc"],
                "extras": list(ev["extras"]),
                "lines_pairs": list(ev["lines_pairs"]),  # geordnete Paare
                "stop_names": set(ev["stop_names"]),
                "pubDate": ev["pubDate"],
                "starts_at": ev["starts_at"],
                "ends_at": ev["ends_at"],
                "_identity": ev["_identity"],  # stabil weiterreichen
            }
        else:
            # Kürzeren Titel bevorzugen (klarer)
            if len(ev["title"]) < len(b["title"]):
                b["title"] = ev["title"]
            b["lines_pairs"] = _merge_line_pairs(b["lines_pairs"], ev["lines_pairs"])
            b["stop_names"].update(ev["stop_names"])
            if ev["pubDate"] and (
                not b["pubDate"] or ev["pubDate"] < b["pubDate"]
            ):
                b["pubDate"] = ev["pubDate"]
            be, ee = b["ends_at"], ev["ends_at"]
            b["ends_at"] = None if (be is None or ee is None) else max(be, ee)
            for x in ev["extras"]:
                if x not in b["extras"]:
                    b["extras"].append(x)

    # D) Finale Items mit Linien-Präfix im Titel
    items: List[Dict[str, Any]] = []
    for b in buckets.values():
        lines_disp = _line_display_from_pairs(b["lines_pairs"])
        lines_tok = set(_line_tokens_from_pairs(b["lines_pairs"]))

        base_title = b["title"]
        title_with_lines = _ensure_line_prefix(base_title, lines_disp)

        # Anzahl Halte ins Titelende
        halt_cnt = len(b["stop_names"])
        if halt_cnt > 0 and not _HALT_SUFFIX_RE.search(title_with_lines):
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

        guid = make_guid(
            "wl",
            b["category"],
            b["topic_key"],
            ",".join(sorted(lines_tok)),
        )
        items.append(
            {
                "source": b["source"],
                "category": b["category"],
                "title": title_final,  # plain text
                "description": desc,  # plain text
                "link": f"{WL_BASE}",
                "guid": guid,
                "pubDate": b["pubDate"],  # None erlaubt
                "starts_at": b["starts_at"],
                "ends_at": b["ends_at"],
                "_identity": b["_identity"],  # stabil für first_seen
                "_lines_set": lines_tok,  # für Sammel-vs.-Einzel
            }
        )

    # E) Sammel-vs.-Einzel: Aggregat entfernen, wenn *alle* Linien als Einzel vorliegen
    single_line_coverage: Dict[str, int] = {}
    for it in items:
        ls = it.get("_lines_set") or set()
        if len(ls) == 1:
            ln = next(iter(ls))
            single_line_coverage[ln] = single_line_coverage.get(ln, 0) + 1

    filtered: List[Dict[str, Any]] = []
    for it in items:
        ls = it.get("_lines_set") or set()
        if len(ls) >= 2 and all(single_line_coverage.get(ln, 0) > 0 for ln in ls):
            continue  # Aggregat raus
        filtered.append(it)

    # Aufräumen interner Felder + Sortierung
    for it in filtered:
        it.pop("_lines_set", None)

    filtered.sort(
        key=lambda x: (0, x["pubDate"]) if x["pubDate"] else (1, x["guid"])
    )
    log.info("WL: %d Items nach Filter/Dedupe", len(filtered))
    return filtered


__all__ = ["fetch_events"]

