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

import base64
import hashlib
import html
import json
import logging
import os
import re
import tempfile
import time
import threading
from urllib.parse import unquote
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo
from collections.abc import Mapping
from typing import Any, Dict, Iterable, List, Optional
from types import MethodType
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

try:  # pragma: no cover - support both package layouts
    from utils.ids import make_guid
except ModuleNotFoundError:  # pragma: no cover
    from src.utils.ids import make_guid  # type: ignore

try:  # pragma: no cover - support both package layouts
    from utils.env import get_int_env, get_bool_env, load_default_env_files
except ModuleNotFoundError:  # pragma: no cover
    from src.utils.env import get_int_env, get_bool_env, load_default_env_files  # type: ignore

try:
    from utils.text import html_to_text
except ModuleNotFoundError:
    from src.utils.text import html_to_text  # type: ignore

try:  # pragma: no cover - support both package layouts
    from utils.stations import canonical_name, vor_station_ids
except ModuleNotFoundError:  # pragma: no cover
    from src.utils.stations import canonical_name, vor_station_ids  # type: ignore

try:  # pragma: no cover - support both package layouts
    from utils.http import session_with_retries
except ModuleNotFoundError:  # pragma: no cover
    from src.utils.http import session_with_retries  # type: ignore

import requests
from requests.exceptions import RequestException

load_default_env_files()

log = logging.getLogger(__name__)

REQUEST_COUNT_FILE = Path(__file__).resolve().parents[2] / "data" / "vor_request_count.json"
REQUEST_COUNT_LOCK = threading.Lock()
MAX_REQUESTS_PER_DAY = 100
# Nach welcher Zeit (Sekunden) ein Lock als veraltet gilt und übernommen
# wird. Über ``VOR_REQUEST_LOCK_TIMEOUT_SEC`` konfigurierbar.
REQUEST_LOCK_TIMEOUT_SEC = max(0.0, float(get_int_env("VOR_REQUEST_LOCK_TIMEOUT_SEC", 10)))


def load_request_count() -> tuple[Optional[str], int]:
    """Lese den persistierten Tageszähler für VOR-Anfragen.

    Der Zähler wird aus ``REQUEST_COUNT_FILE`` geladen und liefert das
    gespeicherte Datum (ISO-Format) und den bereits verbrauchten
    Request-Wert zurück. Kann die Datei nicht geöffnet oder der Inhalt nicht
    interpretiert werden (fehlend, beschädigt oder unerwarteter Typ), wird
    ``(None, 0)`` zurückgegeben und ein Debug-Logeintrag vermerkt. Es werden
    keine Ausnahmen weitergereicht.

    Returns:
        tuple[str | None, int]: Das gespeicherte Datum und der Zählerstand;
        bei Problemen ``(None, 0)``.

    Nebenwirkungen:
        Greift lesend auf ``REQUEST_COUNT_FILE`` zu.
    """
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
    """Erhöhe und speichere den Tageszähler für VOR-Anfragen.

    Die Funktion verwendet ``REQUEST_COUNT_LOCK``, um gleichzeitige Zugriffe
    abzusichern, liest den bestehenden Wert mit :func:`load_request_count`
    ein und erhöht ihn für das übergebene lokale Datum. Der aktualisierte
    Zähler wird atomar in ``REQUEST_COUNT_FILE`` geschrieben, indem zunächst
    eine temporäre Datei erzeugt und anschließend ersetzt wird. Bleibt eine
    Lock-Datei liegen, wird sie nach ``REQUEST_LOCK_TIMEOUT_SEC`` als veraltet
    betrachtet, protokolliert und entfernt beziehungsweise übernommen, damit
    die Funktion nicht dauerhaft blockiert. Tritt beim Schreiben ein
    ``OSError`` auf, bleibt der bisherige Zähler erhalten und es wird eine
    Warnung geloggt.

    Args:
        now_local: Ein datetime-Objekt mit lokalem Datum, das für den
            Tageswechsel herangezogen wird.

    Returns:
        int: Der neue Zählerstand nach der Erhöhung.

    Nebenwirkungen:
        Greift schreibend auf ``REQUEST_COUNT_FILE`` zu und hält dabei
        ``REQUEST_COUNT_LOCK``.
    """
    today = now_local.date().isoformat()

    with REQUEST_COUNT_LOCK:
        lock_path = REQUEST_COUNT_FILE.with_suffix(".lock")
        lock_fd: int | None = None
        lock_acquired = False
        tmp_path: str | None = None
        result = 0

        try:
            wait_started = time.monotonic()
            while True:
                try:
                    lock_fd = os.open(
                        lock_path,
                        os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                        0o600,
                    )
                    lock_acquired = True
                    break
                except FileExistsError:
                    if REQUEST_LOCK_TIMEOUT_SEC > 0:
                        elapsed = time.monotonic() - wait_started
                        if elapsed >= REQUEST_LOCK_TIMEOUT_SEC:
                            try:
                                stat = lock_path.stat()
                            except FileNotFoundError:
                                wait_started = time.monotonic()
                                continue
                            lock_age = max(0.0, time.time() - stat.st_mtime)
                            if lock_age >= REQUEST_LOCK_TIMEOUT_SEC:
                                log.warning(
                                    "VOR: Request-Zähler-Lock seit %.2fs veraltet – entferne.",
                                    lock_age,
                                )
                                try:
                                    os.unlink(lock_path)
                                except FileNotFoundError:
                                    wait_started = time.monotonic()
                                    continue
                                except OSError as cleanup_exc:
                                    log.warning(
                                        "VOR: Konnte veraltetes Request-Zähler-Lock nicht entfernen: %s",
                                        cleanup_exc,
                                    )
                                    try:
                                        lock_fd = os.open(lock_path, os.O_WRONLY)
                                    except OSError as steal_exc:
                                        log.warning(
                                            "VOR: Konnte veraltetes Request-Zähler-Lock nicht übernehmen: %s",
                                            steal_exc,
                                        )
                                        break
                                    else:
                                        lock_acquired = True
                                        break
                                else:
                                    wait_started = time.monotonic()
                                    continue
                            else:
                                wait_started = time.monotonic()
                    time.sleep(0.05)
                    continue
                except FileNotFoundError:
                    try:
                        REQUEST_COUNT_FILE.parent.mkdir(parents=True, exist_ok=True)
                    except OSError as mkdir_exc:
                        log.warning(
                            "VOR: Konnte Request-Zähler-Lock nicht erstellen: %s",
                            mkdir_exc,
                        )
                        break
                    continue
                except OSError as lock_exc:
                    log.warning(
                        "VOR: Konnte Request-Zähler-Lock nicht erstellen: %s",
                        lock_exc,
                    )
                    break

            stored_date, stored_count = load_request_count()
            if stored_date != today:
                stored_count = 0
            result = stored_count

            if lock_acquired:
                new_count = stored_count + 1
                try:
                    REQUEST_COUNT_FILE.parent.mkdir(parents=True, exist_ok=True)
                    fd, tmp_path = tempfile.mkstemp(
                        prefix=f"{REQUEST_COUNT_FILE.stem}-",
                        suffix=REQUEST_COUNT_FILE.suffix or ".tmp",
                        dir=str(REQUEST_COUNT_FILE.parent),
                    )
                    with os.fdopen(fd, "w", encoding="utf-8") as fh:
                        json.dump({"date": today, "count": new_count}, fh)
                        fh.flush()
                        try:
                            os.fsync(fh.fileno())
                        except OSError as sync_exc:
                            log.warning(
                                "VOR: Konnte Request-Zähler nicht synchronisieren: %s",
                                sync_exc,
                            )
                            raise
                    os.replace(tmp_path, REQUEST_COUNT_FILE)
                    tmp_path = None
                except OSError as exc:
                    log.warning("VOR: Konnte Request-Zähler nicht speichern: %s", exc)
                    if tmp_path is not None:
                        try:
                            os.unlink(tmp_path)
                        except OSError as cleanup_exc:
                            log.debug(
                                "VOR: Temporäre Request-Zähler-Datei konnte nicht gelöscht werden: %s",
                                cleanup_exc,
                            )
                else:
                    result = new_count
        finally:
            if lock_fd is not None:
                try:
                    os.close(lock_fd)
                except OSError as close_exc:
                    log.debug(
                        "VOR: Konnte Request-Zähler-Lock nicht schließen: %s",
                        close_exc,
                    )
            if lock_acquired:
                try:
                    os.unlink(lock_path)
                except FileNotFoundError:
                    pass
                except OSError as cleanup_exc:
                    log.debug(
                        "VOR: Konnte Request-Zähler-Lock nicht entfernen: %s",
                        cleanup_exc,
                    )
        return result


def _determine_access_id() -> str:
    return (os.getenv("VOR_ACCESS_ID") or os.getenv("VAO_ACCESS_ID") or "").strip()


VOR_ACCESS_ID: str = _determine_access_id()
_VOR_ACCESS_TOKEN_RAW: str = VOR_ACCESS_ID
_VOR_AUTHORIZATION_HEADER: Optional[str] = None


def refresh_access_credentials() -> str:
    """Reload the VOR access token from the environment variables."""

    global VOR_ACCESS_ID, _VOR_ACCESS_TOKEN_RAW, _VOR_AUTHORIZATION_HEADER

    configured = (os.getenv("VOR_ACCESS_ID") or os.getenv("VAO_ACCESS_ID") or "").strip()
    candidate = configured or _VOR_ACCESS_TOKEN_RAW or VOR_ACCESS_ID or ""

    access_id, header = _parse_access_credentials(candidate)
    if not access_id:
        VOR_ACCESS_ID = ""
        _VOR_ACCESS_TOKEN_RAW = ""
        _VOR_AUTHORIZATION_HEADER = None
        return VOR_ACCESS_ID

    VOR_ACCESS_ID = access_id
    _VOR_ACCESS_TOKEN_RAW = candidate
    _VOR_AUTHORIZATION_HEADER = header
    return VOR_ACCESS_ID
VOR_STATION_IDS: List[str] = [s.strip() for s in (os.getenv("VOR_STATION_IDS") or "").split(",") if s.strip()]
VOR_STATION_NAMES: List[str] = [s.strip() for s in (os.getenv("VOR_STATION_NAMES") or "").split(",") if s.strip()]


def _load_station_ids_from_file() -> List[str]:
    """Load VOR station IDs from configured sources."""

    candidates: List[Path] = []
    env_path = os.getenv("VOR_STATION_IDS_FILE")
    if env_path:
        candidates.append(Path(env_path).expanduser())
    candidates.append(Path(__file__).resolve().parents[2] / "data" / "vor_station_ids_wien.txt")

    for candidate in candidates:
        try:
            raw = candidate.read_text(encoding="utf-8")
        except (FileNotFoundError, OSError):
            continue
        ids = [part.strip() for part in re.split(r"[\s,]+", raw) if part.strip()]
        if ids:
            return ids

    directory_ids = list(vor_station_ids())
    if directory_ids:
        return directory_ids
    return []


if not VOR_STATION_IDS:
    _fallback_ids = _load_station_ids_from_file()
    if _fallback_ids:
        VOR_STATION_IDS = _fallback_ids
_DEFAULT_VOR_BASE = "https://routenplaner.verkehrsauskunft.at/vao/restproxy"
_DEFAULT_VOR_VERSION = "v1.11.0"


def _normalize_base_url(url: str) -> str:
    url = url.strip()
    if not url:
        return ""
    return url if url.endswith("/") else f"{url}/"


_VERSION_RE = re.compile(r"^v\d+\.\d+\.\d+$", re.IGNORECASE)


def _infer_version_from_url(url: str) -> Optional[str]:
    path = re.sub(r"[?#].*$", "", url).rstrip("/")
    if not path:
        return None
    candidate = path.split("/")[-1]
    if _VERSION_RE.match(candidate):
        return candidate
    return None


def _determine_base_url_and_version() -> tuple[str, str]:
    env_base_url = (os.getenv("VOR_BASE_URL") or "").strip()
    if env_base_url:
        normalized = _normalize_base_url(env_base_url)
        version = (
            (os.getenv("VOR_VERSION") or "").strip()
            or _infer_version_from_url(normalized)
            or _DEFAULT_VOR_VERSION
        )
        return normalized, version

    base = (os.getenv("VOR_BASE") or _DEFAULT_VOR_BASE).strip().rstrip("/")
    version = (os.getenv("VOR_VERSION") or _DEFAULT_VOR_VERSION).strip().strip("/")
    combined = _normalize_base_url(f"{base}/{version}" if version else base)
    if not version:
        inferred = _infer_version_from_url(combined)
        version = inferred or _DEFAULT_VOR_VERSION
    return combined, version


VOR_BASE_URL, VOR_VERSION = _determine_base_url_and_version()


def refresh_base_configuration() -> tuple[str, str]:
    """Reload the base URL and version from the environment variables."""

    global VOR_BASE_URL, VOR_VERSION

    VOR_BASE_URL, VOR_VERSION = _determine_base_url_and_version()
    return VOR_BASE_URL, VOR_VERSION
BOARD_DURATION_MIN = get_int_env("VOR_BOARD_DURATION_MIN", 60)
HTTP_TIMEOUT = get_int_env("VOR_HTTP_TIMEOUT", 15)
DEFAULT_MAX_STATIONS_PER_RUN = 2
MAX_STATIONS_PER_RUN = get_int_env("VOR_MAX_STATIONS_PER_RUN", DEFAULT_MAX_STATIONS_PER_RUN)
ROTATION_INTERVAL_SEC = get_int_env("VOR_ROTATION_INTERVAL_SEC", 1800)
RETRY_AFTER_FALLBACK_SEC = 5.0

ALLOW_BUS = get_bool_env("VOR_ALLOW_BUS", False)
DEFAULT_BUS_INCLUDE_PATTERN = r"(?:\b[2-9]\d{2,4}\b)"
DEFAULT_BUS_EXCLUDE_PATTERN = r"^(?:N?\d{1,2}[A-Z]?)$"


def _compile_bus_regex(env_var: str, default_pattern: str) -> re.Pattern[str]:
    pattern = os.getenv(env_var)
    if pattern is None:
        return re.compile(default_pattern)
    try:
        return re.compile(pattern)
    except re.error as exc:
        log.warning(
            "VOR: Ungültige Regex in %s (%r): %s – verwende Standard-Regex.",
            env_var,
            pattern,
            exc,
        )
        return re.compile(default_pattern)


BUS_INCLUDE_RE = _compile_bus_regex("VOR_BUS_INCLUDE_REGEX", DEFAULT_BUS_INCLUDE_PATTERN)
BUS_EXCLUDE_RE = _compile_bus_regex("VOR_BUS_EXCLUDE_REGEX", DEFAULT_BUS_EXCLUDE_PATTERN)

RAIL_SHORT = {"S", "R", "REX", "RJ", "RJX", "IC", "EC", "EN", "D"}
RAIL_LONG_HINTS = {"S-Bahn", "Regionalzug", "Regionalexpress", "Railjet", "Railjet Express", "EuroNight"}
EXCLUDE_OPERATORS = {"Wiener Linien"}
EXCLUDE_LONG_HINTS = {"Straßenbahn", "U-Bahn"}
RAIL_PRODUCT_CLASSES: tuple[int, ...] = (0, 1, 2, 3, 4)
BUS_PRODUCT_CLASSES: tuple[int, ...] = (7,)

VOR_USER_AGENT = "Origamihase-wien-oepnv/1.2 (+https://github.com/Origamihase/wien-oepnv)"
VOR_SESSION_HEADERS = {"Accept": "application/json"}
_AUTH_HEADER_RE = re.compile(
    r"((?:[\"']?)Authorization(?:[\"']?)\s*:\s*(?:[\"']?)(?:Bearer|Basic)\s+)([^\s\"']+)",
    re.IGNORECASE,
)
VOR_RETRY_OPTIONS = {"total": 3, "backoff_factor": 0.5, "raise_on_status": False}

_ACCESS_ID_KEY_VALUE_RE = re.compile(r"(accessId\s*[=:]\s*)([\"']?)([^\"',\s&]+)(\2)", re.IGNORECASE)
_ACCESS_ID_URLENC_RE = re.compile(r"(accessId%3D)([^&]+)", re.IGNORECASE)
_AUTH_SNIPPET_RE = re.compile(
    r"[\"']?Authorization[\"']?\s*[:=]\s*[\"']?(Bearer|Basic)\s+([^\"']+)[\"']?",
    re.IGNORECASE,
)


def _parse_access_credentials(token: str) -> tuple[str, Optional[str]]:
    """Return the access id and header value derived from *token*."""

    normalized = (token or "").strip()
    if not normalized:
        return "", None

    snippet_match = _AUTH_SNIPPET_RE.search(normalized)
    if snippet_match:
        scheme = snippet_match.group(1).strip()
        value = snippet_match.group(2).strip()
        if scheme and value:
            normalized = f"{scheme} {value}"

    access_match = _ACCESS_ID_KEY_VALUE_RE.search(normalized)
    if access_match:
        candidate = access_match.group(3).strip()
        if candidate:
            normalized = candidate
    else:
        urlenc_match = _ACCESS_ID_URLENC_RE.search(normalized)
        if urlenc_match:
            decoded = unquote(urlenc_match.group(2))
            decoded = decoded.strip()
            if decoded:
                access_match = _ACCESS_ID_KEY_VALUE_RE.search(decoded)
                normalized = (
                    access_match.group(3).strip()
                    if access_match
                    else decoded
                )

    lowered = normalized.lower()
    if lowered.startswith("bearer "):
        payload = normalized[7:].strip()
        if not payload:
            return "", None
        return payload, f"Bearer {payload}"

    if lowered.startswith("basic "):
        payload = normalized[6:].strip()
        if not payload:
            return "", None
        if ":" in payload:
            encoded = base64.b64encode(payload.encode("utf-8")).decode("ascii")
            return payload, f"Basic {encoded}"
        return payload, f"Basic {payload}"

    if ":" in normalized:
        encoded = base64.b64encode(normalized.encode("utf-8")).decode("ascii")
        return normalized, f"Basic {encoded}"

    return normalized, f"Bearer {normalized}"


def _authorization_header_value(token: str) -> Optional[str]:
    """Return the appropriate ``Authorization`` header value for *token*."""

    if _VOR_AUTHORIZATION_HEADER is not None:
        return _VOR_AUTHORIZATION_HEADER

    _, header = _parse_access_credentials(token)
    return header


def _sanitize_access_id(message: str) -> str:
    """Mask occurrences of the VOR access token in log messages."""

    sanitized = _ACCESS_ID_KEY_VALUE_RE.sub(
        lambda match: f"{match.group(1)}{match.group(2)}***{match.group(4)}",
        message,
    )
    sanitized = _ACCESS_ID_URLENC_RE.sub(lambda match: f"{match.group(1)}***", sanitized)
    sanitized = _AUTH_HEADER_RE.sub(lambda match: f"{match.group(1)}***", sanitized)
    if VOR_ACCESS_ID:
        sanitized = sanitized.replace(VOR_ACCESS_ID, "***")
    if _VOR_ACCESS_TOKEN_RAW and _VOR_ACCESS_TOKEN_RAW != VOR_ACCESS_ID:
        sanitized = sanitized.replace(_VOR_ACCESS_TOKEN_RAW, "***")
    return sanitized


def _inject_access_id(params: Any, access_id: str) -> Any:
    """Return *params* with the ``accessId`` query argument enforced."""

    if params is None:
        return {"accessId": access_id}

    if isinstance(params, Mapping):
        updated = dict(params)
        updated["accessId"] = access_id
        return updated

    if isinstance(params, (list, tuple)):
        found = False
        updated_list = []
        for item in params:
            if isinstance(item, tuple) and len(item) == 2 and item[0] == "accessId":
                updated_list.append((item[0], access_id))
                found = True
            else:
                updated_list.append(item)
        if not found:
            updated_list.append(("accessId", access_id))
        return updated_list

    try:
        iterable = list(params)  # type: ignore[arg-type]
    except TypeError:
        return {"accessId": access_id}

    return _inject_access_id(iterable, access_id)


def apply_authentication(session: requests.Session) -> None:
    """Attach authentication headers and ensure the access token query param."""

    session.headers.update(VOR_SESSION_HEADERS)

    access_id = refresh_access_credentials()
    header_value = _authorization_header_value(access_id)
    if header_value:
        session.headers["Authorization"] = header_value
    else:
        session.headers.pop("Authorization", None)

    if not hasattr(session, "request"):
        return

    original_request = getattr(session, "_vor_original_request", None)
    if original_request is None:
        original_request = session.request

        def _request(self: requests.Session, method: str, url: str, params: Any = None, **kwargs: Any):
            token = refresh_access_credentials()
            if token:
                params = _inject_access_id(params, token)
                header = _authorization_header_value(token)
                if header:
                    self.headers["Authorization"] = header
                else:
                    self.headers.pop("Authorization", None)
            else:
                self.headers.pop("Authorization", None)
            return original_request(method, url, params=params, **kwargs)

        session.request = MethodType(_request, session)
        setattr(session, "_vor_original_request", original_request)

def _stationboard_url() -> str:
    """Return the fully qualified StationBoard endpoint.

    The VAO/VOR REST documentation defines endpoints in lowercase
    (``…/departureboard``). Using the lowercase variant ensures
    compatibility with case-sensitive servers while keeping response
    parsing unchanged (responses still use the ``DepartureBoard`` key).
    """

    return f"{VOR_BASE_URL}departureboard"


def _location_name_url() -> str:
    return f"{VOR_BASE_URL}location.name"


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

    with session_with_retries(VOR_USER_AGENT, **VOR_RETRY_OPTIONS) as session:
        apply_authentication(session)
        for name in wanted:
            params = {"format": "json", "input": name, "type": "stop"}
            access_id = refresh_access_credentials()
            if access_id:
                params["accessId"] = access_id
            try:
                resp = session.get(
                    _location_name_url(),
                    params=params,
                    timeout=HTTP_TIMEOUT,
                    headers={"Accept": "application/json"},
                )
            except requests.RequestException as e:
                log.warning(
                    "VOR location.name %s -> %s",
                    name,
                    _sanitize_access_id(str(e)),
                )
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
    if not date_str:
        return None
    d = date_str.strip()
    t = (time_str or "00:00:00").strip()
    if len(t) == 5:
        t += ":00"
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
    if not ids:
        return []
    m = len(ids)
    n = max(1, min(chunk_size, m))
    slot = int(datetime.now(timezone.utc).timestamp()) // max(1, period_sec)
    total = (m + n - 1) // n
    idx = int(slot) % total
    start = idx * n
    end = start + n
    return ids[start:end] if end <= m else (ids[start:] + ids[: end - m])

def _fetch_stationboard(station_id: str, now_local: datetime) -> Optional[Dict[str, Any]]:
    params = {
        "accessId": refresh_access_credentials(),
        "format": "json",
        "id": station_id,
        "date": now_local.strftime("%Y-%m-%d"), "time": now_local.strftime("%H:%M"),
        "duration": str(BOARD_DURATION_MIN), "rtMode": "SERVER_DEFAULT",
    }
    products_mask = _product_class_bitmask(_desired_product_classes())
    if products_mask:
        params["products"] = str(products_mask)
    req_id = f"sb-{station_id}-{int(now_local.timestamp())}"
    params["requestId"] = req_id

    resp: Optional[requests.Response] = None
    retry_total = 0
    retry_backoff = 0.0
    session_retry_options: dict[str, Any] = {}
    if isinstance(VOR_RETRY_OPTIONS, Mapping):
        try:
            retry_total = int(VOR_RETRY_OPTIONS.get("total", 0))
        except (TypeError, ValueError):
            retry_total = 0
        retry_total = max(0, retry_total)
        try:
            retry_backoff = float(VOR_RETRY_OPTIONS.get("backoff_factor", 0.0))
        except (TypeError, ValueError):
            retry_backoff = 0.0
        retry_backoff = max(0.0, retry_backoff)
        session_retry_options = dict(VOR_RETRY_OPTIONS)
    session_retry_options.update({"total": 0, "connect": 0, "read": 0, "status": 0})

    max_attempts = 1 + retry_total
    try:
        with session_with_retries(VOR_USER_AGENT, **session_retry_options) as session:
            apply_authentication(session)
            attempt = 0
            while attempt < max_attempts:
                attempt += 1
                # Zähler für jeden tatsächlichen HTTP-Versuch erhöhen – auch bei Retries.
                save_request_count(now_local)
                try:
                    resp = session.get(_stationboard_url(), params=params, timeout=HTTP_TIMEOUT)
                except requests.RequestException as e:
                    if attempt >= max_attempts:
                        log.error(
                            "VOR StationBoard Fehler (%s): %s",
                            station_id,
                            _sanitize_access_id(str(e)),
                        )
                        return None
                    delay = retry_backoff * (2 ** (attempt - 1))
                    if delay > 0:
                        time.sleep(delay)
                    continue
                except Exception as e:
                    log.exception(
                        "VOR StationBoard Fehler (%s): %s",
                        station_id,
                        _sanitize_access_id(str(e)),
                    )
                    return None
                else:
                    break
    except requests.RequestException as e:
        log.error(
            "VOR StationBoard Fehler (%s): %s",
            station_id,
            _sanitize_access_id(str(e)),
        )
        return None
    except Exception as e:
        log.exception(
            "VOR StationBoard Fehler (%s): %s",
            station_id,
            _sanitize_access_id(str(e)),
        )
        return None

    if resp is None:
        return None

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

    return payload

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
        if active in ("false", "0", "no"):
            continue

        prods = _accepted_products(m)
        if not prods:
            continue

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

        description_html = text or head or ""
        if extras:
            extras_block = "\n".join(extras)
            if description_html:
                description_html = f"{description_html}\n{extras_block}"
            else:
                description_html = extras_block

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
    refresh_base_configuration()

    if not refresh_access_credentials():
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

    today_iso = now_local.date().isoformat()
    stored_date, stored_count = load_request_count()
    todays_count = stored_count if stored_date == today_iso else 0
    if todays_count >= MAX_REQUESTS_PER_DAY:
        log.info(
            "VOR: Tageslimit von %s StationBoard-Anfragen erreicht – überspringe Abruf.",
            MAX_REQUESTS_PER_DAY,
        )
        return []

    max_workers = min(MAX_STATIONS_PER_RUN, len(station_chunk)) or 1
    requests_inflight = 0
    last_seen_count = todays_count
    limit_reached_during_run = False

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures: dict[Any, str] = {}
        error_count = 0
        success_count = 0

        for sid in station_chunk:
            current_date, current_count = load_request_count()
            current_todays = current_count if current_date == today_iso else 0

            if current_todays < last_seen_count:
                requests_inflight = 0
            else:
                delta = current_todays - last_seen_count
                if delta > 0:
                    requests_inflight = max(0, requests_inflight - delta)
            last_seen_count = current_todays

            effective_count = current_todays + requests_inflight
            if effective_count >= MAX_REQUESTS_PER_DAY:
                limit_reached_during_run = True
                break

            futures[pool.submit(_fetch_stationboard, sid, now_local)] = sid
            requests_inflight += 1

        if limit_reached_during_run:
            log.info(
                "VOR: Tageslimit von %s StationBoard-Anfragen erreicht – überspringe Abruf.",
                MAX_REQUESTS_PER_DAY,
            )
            if not futures:
                return []

        for fut in as_completed(futures):
            sid = futures[fut]
            try:
                root = fut.result()
            except Exception as e:  # pragma: no cover - defensive
                log.exception(
                    "VOR StationBoard Fehler (%s): %s",
                    sid,
                    _sanitize_access_id(str(e)),
                )
                error_count += 1
                continue
            if root is None:
                error_count += 1
                continue
            success_count += 1
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

    if futures and success_count == 0:
        log.warning(
            "VOR: Alle %d StationBoard-Anfragen schlugen fehl (zuletzt %d Fehler).",
            len(futures),
            error_count,
        )
        raise RequestException("VOR StationBoard: keine erfolgreichen Antworten")

    out.sort(key=lambda x: (0, x["pubDate"]) if x["pubDate"] else (1, x["guid"]))
    return out


__all__ = ["fetch_events"]

