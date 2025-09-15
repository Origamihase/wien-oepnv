#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
VOR / VAO Provider: Beeinträchtigungen (IMS/HIM) für S-Bahn & Regionalzüge
+ optional ÖBB-/Regionalbus (VOR_ALLOW_BUS="1").

Änderung: pubDate NUR aus Quelle (starts_at). Kein Fallback auf "jetzt".
Fehlt ein Datum, wird 'pubDate' = None geliefert; build_feed schreibt dann
KEIN <pubDate> und ordnet solche Items hinter datierten ein.
"""

from __future__ import annotations

import os, re, html, logging, time, hashlib, json
import threading
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo
from collections.abc import Mapping
from typing import Any, Dict, Iterable, List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

try:  # pragma: no cover - support both package layouts
    from utils.ids import make_guid
except ModuleNotFoundError:  # pragma: no cover
    from src.utils.ids import make_guid  # type: ignore

try:
    from utils.text import html_to_text
except ModuleNotFoundError:
    from src.utils.text import html_to_text  # type: ignore

try:  # pragma: no cover - support both package layouts
    from utils.stations import canonical_name
except ModuleNotFoundError:  # pragma: no cover
    from src.utils.stations import canonical_name  # type: ignore

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

log = logging.getLogger(__name__)

REQUEST_COUNT_FILE = Path(__file__).resolve().parents[2] / "data" / "vor_request_count.json"
REQUEST_COUNT_LOCK = threading.Lock()
MAX_REQUESTS_PER_DAY = 100


def load_request_count() -> tuple[Optional[str], int]:
    try:
        with REQUEST_COUNT_FILE.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return None, 0
    except (OSError, ValueError, TypeError) as exc:
        log.debug("VOR: konnte Request-Zähler nicht lesen (%s)", exc)
        return None, 0

    if not isinstance(data, Mapping):
        return None, 0

    date_raw = data.get("date")
    date_str = str(date_raw).strip() if isinstance(date_raw, str) else None

    count_raw = data.get("count", 0)
    try:
        count_int = int(count_raw)
    except (TypeError, ValueError):
        count_int = 0
    count_int = max(0, count_int)

    return date_str, count_int


def save_request_count(now_local: datetime) -> int:
    today = now_local.date().isoformat()

    with REQUEST_COUNT_LOCK:
        stored_date, stored_count = load_request_count()
        if stored_date != today:
            stored_count = 0
        new_count = stored_count + 1
        try:
            REQUEST_COUNT_FILE.parent.mkdir(parents=True, exist_ok=True)
            with REQUEST_COUNT_FILE.open("w", encoding="utf-8") as fh:
                json.dump({"date": today, "count": new_count}, fh)
        except OSError as exc:
            log.warning("VOR: Konnte Request-Zähler nicht speichern: %s", exc)
        return new_count

def _get_int_env(name: str, default: int) -> int:
    val = os.getenv(name)
    if val is None:
        return default
    try:
        return int(val)
    except ValueError:
        log.warning("%s='%s' ist kein int – verwende %s", name, val, default)
        return default


VOR_ACCESS_ID: str | None = (os.getenv("VOR_ACCESS_ID") or os.getenv("VAO_ACCESS_ID") or "").strip() or None
VOR_STATION_IDS: List[str] = [s.strip() for s in (os.getenv("VOR_STATION_IDS") or "").split(",") if s.strip()]
VOR_STATION_NAMES: List[str] = [s.strip() for s in (os.getenv("VOR_STATION_NAMES") or "").split(",") if s.strip()]
VOR_BASE = os.getenv("VOR_BASE", "https://routenplaner.verkehrsauskunft.at/vao/restproxy")
VOR_VERSION = os.getenv("VOR_VERSION", "v1.11.0")
BOARD_DURATION_MIN = _get_int_env("VOR_BOARD_DURATION_MIN", 60)
HTTP_TIMEOUT = _get_int_env("VOR_HTTP_TIMEOUT", 15)
DEFAULT_MAX_STATIONS_PER_RUN = 2
MAX_STATIONS_PER_RUN = _get_int_env("VOR_MAX_STATIONS_PER_RUN", DEFAULT_MAX_STATIONS_PER_RUN)
ROTATION_INTERVAL_SEC = _get_int_env("VOR_ROTATION_INTERVAL_SEC", 1800)
RETRY_AFTER_FALLBACK_SEC = 5.0

ALLOW_BUS = (os.getenv("VOR_ALLOW_BUS", "0").strip() == "1")
BUS_INCLUDE_RE = re.compile(os.getenv("VOR_BUS_INCLUDE_REGEX", r"(?:\b[2-9]\d{2,4}\b)"))
BUS_EXCLUDE_RE = re.compile(os.getenv("VOR_BUS_EXCLUDE_REGEX", r"^(?:N?\d{1,2}[A-Z]?)$"))

RAIL_SHORT = {"S", "R", "REX", "RJ", "RJX", "IC", "EC", "EN", "D"}
RAIL_LONG_HINTS = {"S-Bahn", "Regionalzug", "Regionalexpress", "Railjet", "Railjet Express", "EuroNight"}
EXCLUDE_OPERATORS = {"Wiener Linien"}
EXCLUDE_LONG_HINTS = {"Straßenbahn", "U-Bahn"}
RAIL_PRODUCT_CLASSES: tuple[int, ...] = (0, 1, 2, 3, 4)
BUS_PRODUCT_CLASSES: tuple[int, ...] = (7,)

def _retry() -> Retry:
    return Retry(
        total=3,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
        raise_on_status=False,
    )


def _session() -> requests.Session:
    s = requests.Session()
    s.mount("https://", HTTPAdapter(max_retries=_retry()))
    s.headers.update({
        "Accept": "application/json",
        "User-Agent": "Origamihase-wien-oepnv/1.2 (+https://github.com/Origamihase/wien-oepnv)",
    })
    return s

def _stationboard_url() -> str:
    return f"{VOR_BASE}/{VOR_VERSION}/DepartureBoard"

def _location_name_url() -> str:
    return f"{VOR_BASE}/{VOR_VERSION}/location.name"


def _desired_product_classes() -> List[int]:
    classes: set[int] = set(RAIL_PRODUCT_CLASSES)
    if ALLOW_BUS:
        classes.update(BUS_PRODUCT_CLASSES)
    return sorted(cls for cls in classes if isinstance(cls, int) and cls >= 0)


def _product_class_bitmask(classes: Iterable[int]) -> int:
    bitmask = 0
    for cls in classes:
        try:
            cls_int = int(cls)
        except (TypeError, ValueError):
            continue
        if cls_int < 0:
            continue
        bitmask |= 1 << cls_int
    return bitmask


def _product_class_from(prod: Mapping[str, Any]) -> Optional[int]:
    for key in ("productClass", "productclass", "prodClass", "class", "cls"):
        value = prod.get(key)
        if value is None:
            continue
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            continue
    return None

def resolve_station_ids(names: List[str]) -> List[str]:
    resolved: List[str] = []
    seen: set[str] = set()
    wanted: List[str] = []

    for raw in names:
        name = (raw or "").strip()
        if not name:
            continue
        canonical = canonical_name(name)
        query = canonical or name
        key = query.casefold()
        if key in seen:
            continue
        seen.add(key)
        wanted.append(query)

    if not wanted:
        return resolved

    with _session() as session:
        for name in wanted:
            params = {"format": "json", "input": name, "type": "stop"}
            if VOR_ACCESS_ID:
                params["accessId"] = VOR_ACCESS_ID
            try:
                resp = session.get(
                    _location_name_url(),
                    params=params,
                    timeout=HTTP_TIMEOUT,
                    headers={"Accept": "application/json"},
                )
            except requests.RequestException as e:
                log.warning("VOR location.name %s -> %s", name, e)
                continue

            if resp.status_code >= 400:
                log.warning("VOR location.name %s -> HTTP %s", name, resp.status_code)
                continue

            try:
                payload = resp.json()
            except ValueError:
                log.warning("VOR location.name %s -> ungültige Antwort", name)
                continue

            stops = payload.get("StopLocation")
            if isinstance(stops, dict):
                stops = [stops]
            if not isinstance(stops, list):
                log.info("VOR location.name %s -> keine StopLocation", name)
                continue

            station_id: Optional[str] = None
            for stop in stops:
                if not isinstance(stop, dict):
                    continue
                sid = stop.get("id") or stop.get("extId")
                if sid:
                    sid_str = str(sid).strip()
                    if sid_str:
                        station_id = sid_str
                        break

            if not station_id:
                log.info("VOR location.name %s -> keine Station-ID gefunden", name)
                continue

            if station_id not in resolved:
                resolved.append(station_id)

    return resolved

def _text(obj: Optional[Mapping[str, Any]], attr: str, default: str = "") -> str:
    if not isinstance(obj, Mapping):
        return default
    value = obj.get(attr, default)
    if value is None:
        return default
    return str(value)

def _parse_dt(date_str: str | None, time_str: str | None) -> Optional[datetime]:
    if not date_str: return None
    d = date_str.strip(); t = (time_str or "00:00:00").strip()
    if len(t)==5: t += ":00"
    try:
        local = datetime.fromisoformat(f"{d}T{t}").replace(tzinfo=ZoneInfo("Europe/Vienna"))
        return local.astimezone(timezone.utc)
    except Exception:
        return None

def _normalize_spaces(s: str) -> str:
    return re.sub(r"\s{2,}", " ", s).strip()

def _ensure_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _accept_product(prod: Mapping[str, Any]) -> bool:
    catOutS = _text(prod, "catOutS").strip()
    catOutL = _text(prod, "catOutL").strip().lower()
    operator = _text(prod, "operator").strip()
    operator_lower = operator.lower()
    line = _text(prod, "line").strip() or _text(prod, "displayNumber").strip() or _text(prod, "name").strip()
    if operator_lower in (o.lower() for o in EXCLUDE_OPERATORS):
        return False
    if any(h.lower() in catOutL for h in EXCLUDE_LONG_HINTS):
        return False

    desired_classes = set(_desired_product_classes())
    prod_class = _product_class_from(prod)
    if prod_class is not None:
        if prod_class not in desired_classes:
            return False
        if prod_class in BUS_PRODUCT_CLASSES:
            if not ALLOW_BUS:
                return False
            if BUS_EXCLUDE_RE.match(line):
                return False
            if (
                BUS_INCLUDE_RE.search(line)
                or ("regionalbus" in catOutL)
                or ("postbus" in operator_lower)
                or ("österreichische postbus" in operator_lower)
            ):
                return True
            return False
        if catOutS.upper() == "U":
            return False
        return True

    catOutS_upper = catOutS.upper()
    if catOutS_upper == "U":
        return False
    if (catOutS_upper in RAIL_SHORT) or any(h.lower() in catOutL for h in RAIL_LONG_HINTS):
        return True
    if not ALLOW_BUS:
        return False
    if BUS_EXCLUDE_RE.match(line):
        return False
    if (
        BUS_INCLUDE_RE.search(line)
        or ("regionalbus" in catOutL)
        or ("postbus" in operator_lower)
        or ("österreichische postbus" in operator_lower)
    ):
        return True
    return False

def _select_stations_round_robin(ids: List[str], chunk_size: int, period_sec: int) -> List[str]:
    if not ids: return []
    m = len(ids); n = max(1, min(chunk_size, m))
    slot = int(datetime.now(timezone.utc).timestamp()) // max(1, period_sec)
    total = (m + n - 1) // n
    idx = int(slot) % total
    start = idx * n; end = start + n
    return ids[start:end] if end <= m else (ids[start:] + ids[:end-m])

def _fetch_stationboard(station_id: str, now_local: datetime) -> Optional[Dict[str, Any]]:
    params = {
        "accessId": VOR_ACCESS_ID, "format":"json", "id": station_id,
        "date": now_local.strftime("%Y-%m-%d"), "time": now_local.strftime("%H:%M"),
        "duration": str(BOARD_DURATION_MIN), "rtMode": "SERVER_DEFAULT",
    }
    products_mask = _product_class_bitmask(_desired_product_classes())
    if products_mask:
        params["products"] = str(products_mask)
    req_id = f"sb-{station_id}-{int(now_local.timestamp())}"
    params["requestId"] = req_id
    try:
        with _session() as session:
            resp = session.get(_stationboard_url(), params=params, timeout=HTTP_TIMEOUT)
        if resp.status_code == 429:
            retry_after = resp.headers.get("Retry-After")
            delay: Optional[float] = None
            if retry_after:
                log.warning(
                    "VOR StationBoard %s -> HTTP 429, Retry-After %s", station_id, retry_after
                )
                try:
                    delay = float(retry_after)
                except ValueError:
                    try:
                        retry_dt = parsedate_to_datetime(retry_after)
                    except (TypeError, ValueError, IndexError):
                        log.warning(
                            "VOR StationBoard %s -> ungültiges Retry-After '%s'",
                            station_id,
                            retry_after,
                        )
                    else:
                        if retry_dt.tzinfo is None:
                            retry_dt = retry_dt.replace(tzinfo=timezone.utc)
                        now_utc = datetime.now(timezone.utc)
                        delay = (retry_dt.astimezone(timezone.utc) - now_utc).total_seconds()
            else:
                log.warning("VOR StationBoard %s -> HTTP 429 ohne Retry-After", station_id)
            if delay is not None and delay > 0:
                time.sleep(delay)
            else:
                if retry_after:
                    log.warning(
                        "VOR StationBoard %s -> Fallback-Verzögerung %.1fs (Retry-After '%s' nicht nutzbar)",
                        station_id,
                        RETRY_AFTER_FALLBACK_SEC,
                        retry_after,
                    )
                else:
                    log.warning(
                        "VOR StationBoard %s -> Fallback-Verzögerung %.1fs (Retry-After fehlt)",
                        station_id,
                        RETRY_AFTER_FALLBACK_SEC,
                    )
                time.sleep(RETRY_AFTER_FALLBACK_SEC)
            return None
        if resp.status_code >= 400:
            log.warning("VOR StationBoard %s -> HTTP %s", station_id, resp.status_code)
            return None
        payload = resp.json()
        if not isinstance(payload, dict):
            log.warning("VOR StationBoard %s -> ungültige JSON-Antwort", station_id)
            return None
        save_request_count(now_local)
        return payload
    except requests.RequestException as e:
        msg = re.sub(r"accessId=[^&]+", "accessId=***", str(e))
        log.error("VOR StationBoard Fehler (%s): %s", station_id, msg)
        return None
    except Exception as e:
        log.exception("VOR StationBoard Fehler (%s): %s", station_id, e)
        return None

def _extract_mapping_items(value: Any, nested_keys: tuple[str, ...]) -> List[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        for key in nested_keys:
            if key in value:
                return _extract_mapping_items(value[key], nested_keys)
        return [value]
    items: List[Mapping[str, Any]] = []
    for item in _ensure_list(value):
        if isinstance(item, Mapping):
            items.extend(_extract_mapping_items(item, nested_keys))
    return items


def _iter_messages(payload: Mapping[str, Any]) -> Iterable[Mapping[str, Any]]:
    if not isinstance(payload, Mapping):
        return []
    data: Mapping[str, Any] = payload
    board = payload.get("DepartureBoard")
    if isinstance(board, Mapping):
        data = board
    messages_container: Any = data.get("Messages")
    if messages_container is None:
        messages_container = data.get("messages")
    if messages_container is None:
        for key in ("Message", "message"):
            if key in data:
                messages_container = data[key]
                break
    if messages_container is None:
        return []
    return _extract_mapping_items(messages_container, ("Message", "message", "Messages", "messages"))


def _accepted_products(message: Mapping[str, Any]) -> List[Mapping[str, Any]]:
    products_container = message.get("products")
    if products_container is None:
        return []
    products = _extract_mapping_items(products_container, ("Product", "product", "Products", "products"))
    out: List[Mapping[str, Any]] = []
    for prod in products:
        if _accept_product(prod):
            out.append(prod)
    return out


def _collect_from_board(station_id: str, payload: Mapping[str, Any]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for m in _iter_messages(payload):
        msg_id = _text(m, "id").strip()
        active = _text(m, "act").strip().lower()
        if active in ("false","0","no"): continue

        prods = _accepted_products(m)
        if not prods: continue

        head_raw = html_to_text(_text(m, "head"))
        text_raw = html_to_text(_text(m, "text"))
        head = _normalize_spaces(head_raw)
        text = _normalize_spaces(text_raw)

        starts_at = _parse_dt(_text(m, "sDate"), _text(m, "sTime"))
        ends_at   = _parse_dt(_text(m, "eDate"), _text(m, "eTime"))

        lines_set: set[str] = set()
        affected_stops: List[str] = []
        for p in prods:
            name = _text(p, "name") or (_text(p, "catOutS") + _text(p, "displayNumber"))
            if name:
                name = re.sub(r"\s*\([^)]*\)", "", name)
                name = name.replace(" ", "").strip()
                if name:
                    lines_set.add(name)
        aff = m.get("affectedStops")
        if aff is not None:
            for st in _extract_mapping_items(aff, ("Stop", "stop", "Stops", "stops")):
                nm_raw = _text(st, "name").strip() or _text(st, "stop").strip()
                if not nm_raw:
                    continue
                nm_canonical = canonical_name(nm_raw)
                nm = re.sub(r"\s{2,}", " ", (nm_canonical or nm_raw)).strip()
                if nm:
                    affected_stops.append(nm)
        lines = sorted(lines_set)

        if not msg_id:
            raw = f"{head}|{text}|{starts_at}|{','.join(affected_stops)}"
            msg_id = hashlib.sha1(raw.encode("utf-8")).hexdigest()

        extras: List[str] = []
        if lines:
            extras.append(f"Linien: {html.escape(', '.join(lines))}")
        if affected_stops:
            extras.append(
                f"Betroffene Haltestellen: {html.escape(', '.join(sorted(set(affected_stops))[:20]))}"
            )

        description_html = text or head
        if extras:
            description_html += "<br/>" + "<br/>".join(extras)

        prefix = "/".join(lines)
        title = head or "Meldung"
        if prefix:
            if re.match(rf"^\s*{re.escape(prefix)}\s*:\s*", title, re.IGNORECASE):
                rest = re.sub(rf"^\s*{re.escape(prefix)}\s*:\s*", "", title, flags=re.IGNORECASE).strip()
                title = f"{prefix}: {rest}" if rest else prefix
            else:
                title = f"{prefix}: {title}" if title else prefix

        guid = make_guid("vao", msg_id)
        items.append({
            "source": "VOR/VAO",
            "category": "Störung",
            "title": title,
            "description": description_html,
            "link": "https://www.vor.at/",
            "guid": guid,
            "pubDate": starts_at,     # NUR Quelle (kann None sein)
            "starts_at": starts_at,
            "ends_at": ends_at,
        })
    return items

def fetch_events() -> List[Dict[str, Any]]:
    if not VOR_ACCESS_ID:
        log.info("VOR: kein VOR_ACCESS_ID gesetzt – Provider inaktiv.")
        return []
    station_ids = VOR_STATION_IDS or resolve_station_ids(VOR_STATION_NAMES)
    if not station_ids:
        if VOR_STATION_NAMES:
            log.info("VOR: keine Station-IDs für VOR_STATION_NAMES gefunden – Provider inaktiv.")
        else:
            log.info("VOR: keine VOR_STATION_IDS gesetzt – Provider inaktiv.")
        return []

    now_local = datetime.now().astimezone(ZoneInfo("Europe/Vienna"))
    station_chunk = _select_stations_round_robin(station_ids, MAX_STATIONS_PER_RUN, ROTATION_INTERVAL_SEC)

    seen: set[str] = set()
    out: List[Dict[str, Any]] = []

    if not station_chunk:
        return out

    stored_date, stored_count = load_request_count()
    todays_count = stored_count if stored_date == now_local.date().isoformat() else 0
    if todays_count >= MAX_REQUESTS_PER_DAY:
        log.info(
            "VOR: Tageslimit von %s StationBoard-Anfragen erreicht – überspringe Abruf.",
            MAX_REQUESTS_PER_DAY,
        )
        return []

    max_workers = min(MAX_STATIONS_PER_RUN, len(station_chunk)) or 1
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_fetch_stationboard, sid, now_local): sid for sid in station_chunk}
        for fut in as_completed(futures):
            sid = futures[fut]
            try:
                root = fut.result()
            except Exception as e:  # pragma: no cover - defensive
                log.exception("VOR StationBoard Fehler (%s): %s", sid, e)
                continue
            if root is None:
                continue
            for it in _collect_from_board(sid, root):
                if it["guid"] in seen:
                    for x in out:
                        if x["guid"] == it["guid"]:
                            if it["pubDate"] and (not x["pubDate"] or it["pubDate"] < x["pubDate"]):
                                x["pubDate"] = it["pubDate"]
                            be, ee = x.get("ends_at"), it.get("ends_at")
                            x["ends_at"] = None if (be is None or ee is None) else max(be, ee)
                            if it["description"] and it["description"] not in x["description"]:
                                x["description"] += "<br/>" + it["description"]
                            break
                    continue
                seen.add(it["guid"])
                out.append(it)

    out.sort(key=lambda x: (0, x["pubDate"]) if x["pubDate"] else (1, x["guid"]))
    return out


__all__ = ["fetch_events"]

