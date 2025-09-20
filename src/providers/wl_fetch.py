"""Fetching and assembling events from the Wiener Linien API."""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import requests
from dateutil import parser as dtparser

try:  # pragma: no cover - support both package layouts
    from utils.http import session_with_retries
except ModuleNotFoundError:  # pragma: no cover
    from src.utils.http import session_with_retries  # type: ignore

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

WL_USER_AGENT = "Origamihase-wien-oepnv/3.1 (+https://github.com/Origamihase/wien-oepnv)"
WL_SESSION_HEADERS = {"Accept": "application/json"}


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


# ---------------- Kontext für Titel ----------------

def _normalize_whitespace(value: str) -> str:
    return re.sub(r"\s{2,}", " ", value or "").strip()


def _split_extra(extra: str) -> Optional[Tuple[str, str]]:
    if not extra or ":" not in extra:
        return None
    head, tail = extra.split(":", 1)
    head = head.strip()
    tail = _normalize_whitespace(tail)
    if not head or not tail:
        return None
    return head, tail


def _context_values_from_stop_names(
    stop_names: Iterable[str], base_title: str
) -> List[str]:
    base_cf = base_title.casefold()
    seen: set[str] = set()
    values: List[str] = []
    for name in sorted(stop_names, key=lambda x: x.casefold()):
        clean = _normalize_whitespace(str(name))
        if not clean:
            continue
        key = clean.casefold()
        if key in seen:
            continue
        seen.add(key)
        if key in base_cf:
            continue
        values.append(clean)
    return values


def _context_values_from_extras(
    extras: Sequence[str], base_title: str
) -> Tuple[List[str], List[str]]:
    base_cf = base_title.casefold()
    values: List[str] = []
    used: List[str] = []
    seen: set[str] = set()
    for extra in extras:
        parsed = _split_extra(extra)
        if not parsed:
            continue
        label, value = parsed
        if label.casefold() not in {"station", "location"}:
            continue
        key = value.casefold()
        if not value or key in seen or key in base_cf:
            continue
        seen.add(key)
        values.append(value)
        used.append(extra)
    return values, used


def _title_quality_key(title: str, title_core: str) -> Tuple[int, int, int]:
    """Score titles so that informative variants win over short generics."""

    normalized_title = _normalize_whitespace(title)
    core = _normalize_whitespace(title_core)
    tokens = [tok for tok in core.split() if tok]
    informative_tokens = [tok for tok in tokens if len(tok) >= 4]
    return (
        len(informative_tokens),
        len(core),
        -len(normalized_title),
    )


def _description_info_score(
    desc: str,
    *,
    title: str,
    stop_names: Iterable[str],
    extras: Sequence[str],
) -> Tuple[int, int, int, int]:
    """Return a tuple describing how informative a description is."""

    normalized = _normalize_whitespace(desc)
    if not normalized:
        return (0, 0, 0, 0)

    desc_cf = normalized.casefold()
    title_norm = _normalize_whitespace(title).casefold()
    non_title = 0 if desc_cf and desc_cf == title_norm else 1

    info_hits = 0
    seen: set[str] = set()

    for name in stop_names:
        clean = _normalize_whitespace(str(name))
        if len(clean) < 3:
            continue
        key = clean.casefold()
        if key in seen:
            continue
        if key and key in desc_cf:
            info_hits += 1
            seen.add(key)

    for extra in extras:
        parsed = _split_extra(extra)
        value = parsed[1] if parsed else _normalize_whitespace(str(extra))
        if len(value) < 3:
            continue
        key = value.casefold()
        if key in seen:
            continue
        if key and key in desc_cf:
            info_hits += 1
            seen.add(key)

    length = len(normalized)
    word_count = len(re.findall(r"\w+", normalized, flags=re.UNICODE))
    return (non_title, info_hits, length, word_count)


def _format_context(values: Sequence[str], limit: int = 2) -> Tuple[str, int]:
    if not values:
        return "", 0
    trimmed = list(values[:limit])
    if not trimmed:
        return "", 0
    context = ", ".join(trimmed)
    if len(values) > limit:
        context += " …"
    return context, len(trimmed)


def _build_context_suffix(
    bucket: Dict[str, Any], base_title: str, lines_disp: Sequence[str]
) -> Tuple[Optional[str], List[str]]:
    if lines_disp:
        return None, []

    stop_context = _context_values_from_stop_names(
        bucket.get("stop_names", []), base_title
    )
    if stop_context:
        context, _ = _format_context(stop_context)
        if context:
            return context, []

    extras_context, used_extras = _context_values_from_extras(
        bucket.get("extras", []), base_title
    )
    if extras_context:
        context, used_count = _format_context(extras_context)
        if context:
            return context, used_extras[:used_count]

    return None, []


# ---------------- API Calls ----------------

def _get_json(
    path: str,
    params: Optional[List[tuple]] = None,
    timeout: int = 20,
    session: Optional[requests.Session] = None,
) -> Dict[str, Any]:
    url = f"{WL_BASE.rstrip('/')}/{path.lstrip('/')}"

    def _fetch(s: requests.Session) -> Dict[str, Any]:
        r = s.get(url, params=params or None, timeout=timeout)
        r.raise_for_status()
        try:
            return r.json()
        except (ValueError, json.JSONDecodeError) as exc:
            log.warning("Ungültige JSON-Antwort von %s: %s", url, exc)
            return {}

    if session is not None:
        return _fetch(session)

    with session_with_retries(WL_USER_AGENT, raise_on_status=False) as s:
        s.headers.update(WL_SESSION_HEADERS)
        return _fetch(s)


def _fetch_traffic_infos(
    timeout: int = 20, session: Optional[requests.Session] = None
) -> Iterable[Dict[str, Any]]:
    # explizit KEINE Facility-Feeds
    params = [("name", "stoerunglang"), ("name", "stoerungkurz")]
    data = _get_json("trafficInfoList", params=params, timeout=timeout, session=session)
    return (data.get("data", {}) or {}).get("trafficInfos", []) or []


def _fetch_news(
    timeout: int = 20, session: Optional[requests.Session] = None
) -> Iterable[Dict[str, Any]]:
    data = _get_json("newsList", timeout=timeout, session=session)
    return (data.get("data", {}) or {}).get("pois", []) or []


# ---------------- Public API ----------------

def fetch_events(timeout: int = 20) -> List[Dict[str, Any]]:
    now = datetime.now(timezone.utc)
    raw: List[Dict[str, Any]] = []

    with session_with_retries(WL_USER_AGENT, raise_on_status=False) as session:
        session.headers.update(WL_SESSION_HEADERS)
        # A) TrafficInfos (Störungen)
        try:
            for ti in _fetch_traffic_infos(timeout=timeout, session=session):
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
            for poi in _fetch_news(timeout=timeout, session=session):
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
            current_title = b["title"]
            current_core = b.get("title_core", "")
            current_desc = b.get("desc_base", "")

            base_score = _description_info_score(
                current_desc,
                title=current_title,
                stop_names=b["stop_names"],
                extras=b["extras"],
            )
            candidate_score = _description_info_score(
                ev.get("desc", ""),
                title=ev["title"],
                stop_names=ev["stop_names"],
                extras=ev["extras"],
            )

            if _title_quality_key(ev["title"], ev.get("title_core", "")) > _title_quality_key(
                current_title, current_core
            ):
                b["title"] = ev["title"]
                b["title_core"] = ev.get("title_core", "")

            if candidate_score > base_score:
                b["desc_base"] = ev.get("desc", "")

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
        context_suffix, extras_for_context = _build_context_suffix(
            b, base_title, lines_disp
        )
        title_with_lines = _ensure_line_prefix(base_title, lines_disp)
        if context_suffix:
            title_with_lines = (
                f"{title_with_lines} – {context_suffix}" if title_with_lines else context_suffix
            )

        # Anzahl Halte ins Titelende
        halt_cnt = len(b["stop_names"])
        if halt_cnt > 0 and not _HALT_SUFFIX_RE.search(title_with_lines):
            title_with_lines += f" ({halt_cnt} Halt{'e' if halt_cnt != 1 else ''})"

        title_final = re.sub(r"[<>«»‹›]+", "", title_with_lines).strip()

        # Beschreibung aufbauen (ohne „Linien: …“ in extras)
        desc = b["desc_base"]
        extras_clean = [
            x
            for x in b["extras"]
            if not x.lower().startswith("linien:") and x not in extras_for_context
        ]
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

