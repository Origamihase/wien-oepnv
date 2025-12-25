from __future__ import annotations

import base64
import json
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Iterable, Iterator, List, Mapping, MutableMapping, Sequence

import requests
from requests import RequestException, Session
from zoneinfo import ZoneInfo

if TYPE_CHECKING:  # pragma: no cover - prefer package imports during type checks
    from ..utils.http import session_with_retries, validate_http_url, fetch_content_safe
    from ..utils.stations import vor_station_ids
else:  # pragma: no cover - allow running via package or src layout
    try:
        from utils.http import session_with_retries, validate_http_url, fetch_content_safe
    except ModuleNotFoundError:
        from ..utils.http import session_with_retries, validate_http_url, fetch_content_safe  # type: ignore

    try:
        from utils.stations import vor_station_ids
    except ModuleNotFoundError:
        from ..utils.stations import vor_station_ids  # type: ignore

log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = BASE_DIR / "data"
DEFAULT_INFO_LINK = "https://www.vor.at/"

DEFAULT_VERSION = "v1.11.0"
DEFAULT_BASE = "https://routenplaner.verkehrsauskunft.at/vao/restproxy"
DEFAULT_BASE_URL = f"{DEFAULT_BASE}/{DEFAULT_VERSION}/"
DEFAULT_USER_AGENT = "wien-oepnv/1.0 (+https://github.com/Origamihase/wien-oepnv)"

DEFAULT_BOARD_DURATION_MIN = 60
DEFAULT_HTTP_TIMEOUT = 15
DEFAULT_MAX_STATIONS_PER_RUN = 2
DEFAULT_ROTATION_INTERVAL_SEC = 1800
DEFAULT_MAX_REQUESTS_PER_DAY = 1000
RETRY_AFTER_FALLBACK_SEC = 5.0
REQUEST_LOCK_TIMEOUT_SEC = 5.0
REQUEST_LOCK_RETRY_DELAY = 0.05

DEFAULT_BUS_INCLUDE_PATTERN = r"(?i)^(?:Regionalbus|Bus|AST)"
DEFAULT_BUS_EXCLUDE_PATTERN = r"(?i)Ersatzverkehr"

ZONE_VIENNA = ZoneInfo("Europe/Vienna")

VOR_USER_AGENT = os.getenv("VOR_USER_AGENT", DEFAULT_USER_AGENT)
VOR_RETRY_OPTIONS: Dict[str, Any] = {
    "total": 3,
    "backoff_factor": 0.5,
    "raise_on_status": False,
}

VOR_ACCESS_ID = ""
_VOR_ACCESS_TOKEN_RAW = ""
_VOR_AUTHORIZATION_HEADER = ""



def _sanitize_message(text: str) -> str:
    sanitized = text or ""
    patterns = [
        (r"(?i)(accessid%3d)([^&\s]+)", r"\1***"),
        (r"(?i)(accessid=)([^&\s]+)", r"\1***"),
        (r"(?i)(\"accessId\"\s*:\s*\")(.*?)(\")", r"\1***\3"),
        (r"(?i)('accessId'\s*:\s*')(.*?)(')", r"\1***\3"),
        (r"(?i)(Authorization:\s*Bearer\s+)(\S+)", r"\1***"),
        (r"(?i)(Authorization:\s*Basic\s+)(\S+)", r"\1***"),
        (r"(?i)(\"Authorization\"\s*:\s*\"Bearer\s+)([^\"\s]+)", r"\1***"),
        (r"(?i)(\"Authorization\"\s*:\s*\"Basic\s+)([^\"\s]+)", r"\1***"),
        (r"(?i)('Authorization'\s*:\s*'Bearer\s+)([^'\s]+)", r"\1***"),
        (r"(?i)('Authorization'\s*:\s*'Basic\s+)([^'\s]+)", r"\1***"),
    ]
    for pattern, repl in patterns:
        sanitized = re.sub(pattern, repl, sanitized)

    for secret in {VOR_ACCESS_ID, _VOR_ACCESS_TOKEN_RAW}:
        if secret:
            sanitized = sanitized.replace(secret, "***")

    if _VOR_AUTHORIZATION_HEADER:
        auth_parts = _VOR_AUTHORIZATION_HEADER.split(" ", 1)
        if len(auth_parts) == 2:
            sanitized = sanitized.replace(
                _VOR_AUTHORIZATION_HEADER, f"{auth_parts[0]} ***"
            )
        else:
            sanitized = sanitized.replace(_VOR_AUTHORIZATION_HEADER, "***")

    return sanitized


def _sanitize_arg(arg: Any) -> Any:
    if isinstance(arg, (int, float)):
        return arg
    if isinstance(arg, str):
        return _sanitize_message(arg)
    return _sanitize_message(str(arg))


def _log_warning(message: str, *args: Any) -> None:
    if args:
        sanitized_args = tuple(_sanitize_arg(arg) for arg in args)
        log.warning(message, *sanitized_args)
    else:
        log.warning("%s", _sanitize_message(message))


def _log_error(message: str, *args: Any) -> None:
    if args:
        sanitized_args = tuple(_sanitize_arg(arg) for arg in args)
        log.error(message, *sanitized_args)
    else:
        log.error("%s", _sanitize_message(message))

def _get_env(name: str) -> str:
    return (os.getenv(name) or "").strip()


def _load_int_env(name: str, default: int) -> int:
    raw = _get_env(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        _log_warning("Ungültiger Wert für %s: %s – verwende Standard %s", name, raw, default)
        return default
    if value <= 0:
        _log_warning("Ungültiger Wert für %s: %s – verwende Standard %s", name, raw, default)
        return default
    return value


def _compile_regex(name: str, default_pattern: str) -> re.Pattern[str]:
    raw = _get_env(name)
    if not raw:
        return re.compile(default_pattern)
    try:
        return re.compile(raw)
    except re.error as exc:
        _log_warning("Ungültiges Regex für %s (%s) – verwende Standard", name, exc)
        return re.compile(default_pattern)


BOARD_DURATION_MIN = _load_int_env("VOR_BOARD_DURATION_MIN", DEFAULT_BOARD_DURATION_MIN)
HTTP_TIMEOUT = _load_int_env("VOR_HTTP_TIMEOUT", DEFAULT_HTTP_TIMEOUT)
MAX_STATIONS_PER_RUN = _load_int_env("VOR_MAX_STATIONS_PER_RUN", DEFAULT_MAX_STATIONS_PER_RUN)
if MAX_STATIONS_PER_RUN <= 0:
    MAX_STATIONS_PER_RUN = DEFAULT_MAX_STATIONS_PER_RUN
ROTATION_INTERVAL_SEC = _load_int_env("VOR_ROTATION_INTERVAL_SEC", DEFAULT_ROTATION_INTERVAL_SEC)
MAX_REQUESTS_PER_DAY = _load_int_env("VOR_MAX_REQUESTS_PER_DAY", DEFAULT_MAX_REQUESTS_PER_DAY)

ALLOW_BUS = _get_env("VOR_ALLOW_BUS").lower() in {"1", "true", "yes"}
BUS_INCLUDE_RE = _compile_regex("VOR_BUS_INCLUDE_REGEX", DEFAULT_BUS_INCLUDE_PATTERN)
BUS_EXCLUDE_RE = _compile_regex("VOR_BUS_EXCLUDE_REGEX", DEFAULT_BUS_EXCLUDE_PATTERN)

def _resolve_path(candidate: str | None, *, default: Path) -> Path:
    text = (candidate or "").strip()
    if not text:
        return default
    path = Path(text)
    if not path.is_absolute():
        resolved = (BASE_DIR / path).resolve()
    else:
        resolved = path.resolve()

    try:
        resolved.relative_to(BASE_DIR)
    except ValueError:
        _log_warning("Pfad-Traversal erkannt oder Pfad außerhalb des Projekts: %s. Nutze Standard.", text)
        return default
    return resolved


REQUEST_COUNT_FILE = _resolve_path(
    _get_env("VOR_REQUEST_COUNT_FILE"), default=DATA_DIR / "vor_request_count.json"
)


MAPPING_FILE = _resolve_path(_get_env("VOR_STATION_NAME_MAP"), default=DATA_DIR / "vor-haltestellen.mapping.json")
DEFAULT_STATION_ID_FILE = _resolve_path(_get_env("VOR_STATION_IDS_DEFAULT"), default=DATA_DIR / "vor-haltestellen.csv")


def _load_station_name_map() -> Dict[str, str]:
    try:
        data = json.loads(MAPPING_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        _log_warning("Konnte Stations-Mapping nicht laden (%s)", exc)
        return {}
    mapping: Dict[str, str] = {}
    for entry in data:
        if not isinstance(entry, dict):
            continue
        raw = str(entry.get("station_name") or "").strip()
        resolved = str(entry.get("resolved_name") or "").strip() or raw
        if raw:
            mapping[raw] = resolved
    return mapping


STATION_NAME_MAP = _load_station_name_map()


def _load_station_ids_from_file(path: Path) -> List[str]:
    try:
        content = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    entries = re.split(r"[\s,;]+", content)
    result: List[str] = []
    for entry in entries:
        token = entry.strip()
        if token and token not in result:
            result.append(token)
    return result


def _load_station_ids_default() -> List[str]:
    ids: List[str] = []
    try:
        ids = list(vor_station_ids())
    except Exception as exc:  # pragma: no cover - defensive guard
        _log_warning("Konnte Pendler-Stationsliste nicht laden: %s", exc)
    if ids:
        return ids

    try:
        lines = DEFAULT_STATION_ID_FILE.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    result: List[str] = []
    for line in lines[1:]:
        parts = line.split(";")
        if not parts:
            continue
        token = parts[0].strip()
        if token and token not in result:
            result.append(token)
    return result


def _load_station_ids_from_env() -> List[str]:
    direct = _get_env("VOR_STATION_IDS")
    if direct:
        return [item.strip() for item in re.split(r",|\n", direct) if item.strip()]

    ids_file = _get_env("VOR_STATION_IDS_FILE")
    if ids_file:
        return _load_station_ids_from_file(_resolve_path(ids_file, default=DEFAULT_STATION_ID_FILE))

    return _load_station_ids_default()


VOR_STATION_IDS: List[str] = _load_station_ids_from_env()
VOR_STATION_NAMES: List[str] = [name.strip() for name in re.split(r",|\n", _get_env("VOR_STATION_NAMES")) if name.strip()]


def refresh_base_configuration() -> str:
    base_url_env = _get_env("VOR_BASE_URL")
    base_env = _get_env("VOR_BASE")
    version_env = _get_env("VOR_VERSION")

    version = version_env or DEFAULT_VERSION

    # Pre-validate base env vars to avoid injection risks
    validated_base_url_env = validate_http_url(base_url_env)
    validated_base_env = validate_http_url(base_env)

    base_url = DEFAULT_BASE_URL

    if validated_base_url_env:
        base_url = validated_base_url_env.rstrip("/") + "/"
        last_segment = base_url.rstrip("/").split("/")[-1]
        if last_segment.startswith("v"):
            version = last_segment
    elif validated_base_env:
        base = validated_base_env.rstrip("/")
        if version_env:
            base_url = f"{base}/{version_env.strip('/')}/"
        else:
            candidate_last = base.split("/")[-1]
            if candidate_last.startswith("v"):
                version = candidate_last
                base_url = base.rstrip("/") + "/"
            else:
                base_url = f"{base}/{version}/"
    else:
        # Fallback to default if envs are invalid or empty
        base_url = f"{DEFAULT_BASE.rstrip('/')}/{version}/"

    global VOR_BASE_URL, VOR_VERSION
    VOR_BASE_URL = base_url
    VOR_VERSION = version
    return VOR_BASE_URL


VOR_BASE_URL = DEFAULT_BASE_URL
VOR_VERSION = DEFAULT_VERSION
refresh_base_configuration()


def _normalise_access_token(raw: str) -> tuple[str, str]:
    token = raw.strip()
    if not token:
        return "", ""
    normalized = token
    header = ""
    if token.lower().startswith("basic "):
        normalized = token[6:].strip()
    if ":" in normalized:
        encoded = base64.b64encode(normalized.encode("utf-8")).decode("ascii")
        header = f"Basic {encoded}"
    else:
        header = f"Bearer {normalized}"
    return normalized, header


def refresh_access_credentials() -> str:
    raw = _get_env("VOR_ACCESS_ID")
    if not raw:
        raw = _get_env("VAO_ACCESS_ID")
    token, header = _normalise_access_token(raw)

    global VOR_ACCESS_ID, _VOR_ACCESS_TOKEN_RAW, _VOR_AUTHORIZATION_HEADER
    VOR_ACCESS_ID = token
    _VOR_ACCESS_TOKEN_RAW = raw
    _VOR_AUTHORIZATION_HEADER = header
    return VOR_ACCESS_ID


refresh_access_credentials()


def _inject_access_id(params: Any) -> Any:
    if not VOR_ACCESS_ID:
        return params
    if params is None:
        return {"accessId": VOR_ACCESS_ID}
    if isinstance(params, MutableMapping):
        if "accessId" in params:
            return params
        updated = dict(params)
        updated.setdefault("accessId", VOR_ACCESS_ID)
        return updated
    return params


def apply_authentication(session: Session) -> None:
    refresh_access_credentials()
    session.headers.setdefault("Accept", "application/json")
    if _VOR_AUTHORIZATION_HEADER:
        session.headers["Authorization"] = _VOR_AUTHORIZATION_HEADER

    if hasattr(session, "request") and not getattr(session, "_vor_auth_wrapped", False):
        original_request = session.request  # type: ignore[assignment]

        def wrapped(method: str, url: str, params: Any = None, **kwargs: Any) -> Any:
            return original_request(method, url, params=_inject_access_id(params), **kwargs)

        session.request = wrapped  # type: ignore[assignment]
        setattr(session, "_vor_auth_wrapped", True)
    elif not hasattr(session, "request") and hasattr(session, "get") and not getattr(
        session, "_vor_auth_get_wrapped", False
    ):
        original_get = session.get  # type: ignore[attr-defined]

        def wrapped_get(url: str, params: Any = None, **kwargs: Any) -> Any:
            return original_get(url, params=_inject_access_id(params), **kwargs)

        session.get = wrapped_get  # type: ignore[assignment]
        setattr(session, "_vor_auth_get_wrapped", True)


def _extract_stop_container(message: Mapping[str, Any]) -> Iterable[Any]:
    container = message.get("affectedStops") or message.get("Stops")
    if isinstance(container, Mapping):
        if "Stop" in container:
            container = container["Stop"]
        elif "Stops" in container:
            container = container["Stops"]
    if isinstance(container, Mapping) and "Stop" in container:
        container = container["Stop"]
    if isinstance(container, list):
        return container
    if isinstance(container, Mapping):
        return [container]
    return []


def _normalize_stop_key(name: str) -> str:
    normalized = name.lower().replace("bahnhof", "bf")
    return re.sub(r"[\s-]", "", normalized)


def _name_score(name: str) -> tuple[int, int]:
    score = 0
    if "-" in name:
        score += 3
    if "Bf" in name:
        score += 2
    if "Bahnhof" in name:
        score -= 1
    return score, -len(name)


def _canonical_stop_names(names: Iterable[str]) -> List[str]:
    seen: Dict[str, str] = {}
    for name in names:
        text = (name or "").strip()
        if not text:
            continue
        options = [text]
        mapped = STATION_NAME_MAP.get(text)
        if mapped and mapped not in options:
            options.append(mapped)
        key = _normalize_stop_key(text)
        current = seen.get(key)
        for candidate in options:
            candidate = candidate.strip()
            if not candidate:
                continue
            if current is None or _name_score(candidate) > _name_score(current):
                current = candidate
        if current is not None:
            seen[key] = current
    return sorted(seen.values(), key=lambda value: value.lower())


def _extract_stop_names(message: Mapping[str, Any]) -> List[str]:
    raw_names: List[str] = []
    for stop in _extract_stop_container(message):
        if isinstance(stop, Mapping):
            name = stop.get("name") or stop.get("StopName")
            if isinstance(name, str) and name.strip():
                raw_names.append(name.strip())
    return _canonical_stop_names(raw_names)


def _iter_products(message: Mapping[str, Any]) -> Iterator[Mapping[str, Any]]:
    container = message.get("products") or message.get("Products")
    if isinstance(container, Mapping) and "Product" in container:
        container = container["Product"]
    if isinstance(container, list):
        iterable = container
    elif isinstance(container, Mapping):
        iterable = [container]
    else:
        iterable = []
    for entry in iterable:
        if isinstance(entry, Mapping):
            yield entry


def _extract_lines(message: Mapping[str, Any]) -> List[str]:
    lines: List[str] = []
    for product in _iter_products(message):
        cat = str(product.get("catOutS") or product.get("catOutL") or "").strip()
        number = str(product.get("displayNumber") or product.get("name") or product.get("line") or "").strip()
        if not number and cat:
            token = cat
        elif cat:
            if number.upper().startswith(cat.upper()):
                token = number
            else:
                token = f"{cat}{number}"
        else:
            token = number
        if token and token not in lines:
            if not ALLOW_BUS and BUS_INCLUDE_RE.match(token) and BUS_EXCLUDE_RE.search(token):
                continue
            lines.append(token)
    return lines


def _iter_messages(payload: Mapping[str, Any]) -> Iterator[Mapping[str, Any]]:
    container: Any = payload
    if isinstance(payload, Mapping):
        board = payload.get("DepartureBoard")
        if isinstance(board, Mapping):
            container = board
    if isinstance(container, Mapping):
        possible_messages = []
        for key, value in container.items():
            if key.lower() == "messages":
                possible_messages.append(value)
        for value in possible_messages:
            if isinstance(value, Mapping) and "Message" in value:
                value = value["Message"]
            if isinstance(value, list):
                iterable = value
            elif isinstance(value, Mapping):
                iterable = [value]
            else:
                iterable = []
            for entry in iterable:
                if isinstance(entry, Mapping):
                    act = str(entry.get("act", "true")).strip().lower()
                    if act in {"0", "false", "nein", "no"}:
                        continue
                    yield entry
            return
    if isinstance(container, Mapping):
        entry = container.get("Message")
        if isinstance(entry, list):
            for candidate in entry:
                if isinstance(candidate, Mapping):
                    act = str(candidate.get("act", "true")).strip().lower()
                    if act in {"0", "false", "nein", "no"}:
                        continue
                    yield candidate
        elif isinstance(entry, Mapping):
            act = str(entry.get("act", "true")).strip().lower()
            if act not in {"0", "false", "nein", "no"}:
                yield entry


def _parse_dt(date_str: Any, time_str: Any) -> datetime | None:
    date_txt = str(date_str or "").strip()
    if not date_txt:
        return None
    time_txt = str(time_str or "").strip()
    if time_txt:
        time_txt = time_txt[:5]
    else:
        time_txt = "00:00"
    try:
        naive = datetime.strptime(f"{date_txt} {time_txt}", "%Y-%m-%d %H:%M")
    except ValueError:
        return None
    local_dt = naive.replace(tzinfo=ZONE_VIENNA)
    return local_dt.astimezone(timezone.utc)


def _format_date_range(start: datetime | None, end: datetime | None) -> str:
    if not start and not end:
        return ""
    if start:
        start_local = start.astimezone(ZONE_VIENNA)
    if end:
        end_local = end.astimezone(ZONE_VIENNA)
    if start and not end:
        return f"Seit {start_local.strftime('%d.%m.%Y')}"
    if start and end:
        if end < start:
            end = None
            return _format_date_range(start, None)
        if start_local.date() == end_local.date():
            return f"{start_local.strftime('%d.%m.%Y')} {start_local.strftime('%H:%M')}–{end_local.strftime('%H:%M')}"
        return f"{start_local.strftime('%d.%m.%Y')}–{end_local.strftime('%d.%m.%Y')}"
    if end:
        return f"Bis {end_local.strftime('%d.%m.%Y')}"
    return ""


def _build_guid(station_id: str, message: Mapping[str, Any]) -> str:
    raw_id = str(message.get("id") or "").strip()
    if raw_id:
        return f"vor:{station_id}:{raw_id}"
    key = json.dumps({
        "station": station_id,
        "head": message.get("head"),
        "text": message.get("text"),
    }, sort_keys=True)
    fallback = base64.urlsafe_b64encode(key.encode("utf-8")).decode("ascii").rstrip("=")
    return f"vor:{station_id}:{fallback}"


def _collect_from_board(station_id: str, root: Mapping[str, Any]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for message in _iter_messages(root):
        head = str(message.get("head") or "").strip()
        text = str(message.get("text") or "").strip()
        lines = _extract_lines(message)
        stops = _extract_stop_names(message)
        start_dt = _parse_dt(message.get("sDate"), message.get("sTime"))
        end_dt = _parse_dt(message.get("eDate"), message.get("eTime"))
        if end_dt and start_dt and end_dt < start_dt:
            end_dt = None

        description_lines: List[str] = []
        if text:
            description_lines.append(text)
        elif head:
            description_lines.append(head)
        if lines:
            description_lines.append("Linien: " + ", ".join(lines))
        if stops:
            description_lines.append("Betroffene Haltestellen: " + ", ".join(stops))
        date_range = _format_date_range(start_dt, end_dt)
        if date_range:
            description_lines.append(f"[{date_range}]")

        title = head or text or "Hinweis"
        if lines:
            title = f"{lines[0]}: {title}" if title else lines[0]

        items.append(
            {
                "guid": _build_guid(station_id, message),
                "source": "VOR/VAO",
                "category": "Störung",
                "title": title,
                "description": "\n".join(description_lines),
                "link": DEFAULT_INFO_LINK,
                "pubDate": start_dt,
                "starts_at": start_dt,
                "ends_at": end_dt,
            }
        )
    return items


def _desired_product_classes() -> List[int]:
    rail = [0, 1, 2, 3, 4]
    bus = [7]
    return rail + (bus if ALLOW_BUS else [])


def _product_class_bitmask(classes: Sequence[int]) -> int:
    mask = 0
    for cls in classes:
        if cls >= 0:
            mask |= 1 << cls
    return mask


def _select_stations_round_robin(ids: Sequence[str], chunk_size: int, period_seconds: int) -> List[str]:
    if not ids or chunk_size <= 0:
        return []
    chunk = min(len(ids), chunk_size)
    period = max(1, period_seconds or 1)
    start_index = int(time.time() // period) % len(ids)
    ordered = list(ids[start_index:]) + list(ids[:start_index])
    selected: List[str] = []
    seen: set[str] = set()
    for sid in ordered:
        if sid in seen:
            continue
        seen.add(sid)
        selected.append(sid)
        if len(selected) >= chunk:
            break
    return selected


def resolve_station_ids(names: Iterable[str]) -> List[str]:
    deduped: Dict[str, str] = {}
    for raw in names:
        text = str(raw or "").strip()
        if not text:
            continue
        options = [text]
        mapped = STATION_NAME_MAP.get(text)
        if mapped and mapped not in options:
            options.append(mapped)
        key = _normalize_stop_key(text)
        current = deduped.get(key)
        for candidate in options:
            candidate = candidate.strip()
            if not candidate:
                continue
            if current is None or _name_score(candidate) > _name_score(current):
                current = candidate
        if current is not None:
            deduped[key] = current
    tokens = list(deduped.values())
    if not tokens:
        return []
    resolved: List[str] = []
    with session_with_retries(VOR_USER_AGENT, **VOR_RETRY_OPTIONS) as session:
        apply_authentication(session)
        for name in tokens:
            params = {"format": "json", "input": name, "type": "stop"}
            try:
                response = session.get(f"{VOR_BASE_URL}location.name", params=params, timeout=HTTP_TIMEOUT)
            except RequestException as exc:
                _log_warning("VOR location.name für '%s' fehlgeschlagen: %s", name, exc)
                continue
            if response.status_code >= 400:
                _log_warning("VOR location.name für '%s' -> HTTP %s", name, response.status_code)
                continue
            try:
                payload = response.json()
            except ValueError:
                _log_warning("VOR location.name für '%s' lieferte ungültiges JSON", name)
                continue
            stops = []
            if isinstance(payload, Mapping):
                if "StopLocation" in payload:
                    stops = payload["StopLocation"]
                else:
                    location_list = payload.get("LocationList")
                    if isinstance(location_list, Mapping):
                        stops = location_list.get("Stop") or []
            if isinstance(stops, Mapping):
                stops = [stops]
            if not isinstance(stops, list):
                continue
            for stop in stops:
                if not isinstance(stop, Mapping):
                    continue
                sid = str(stop.get("id") or stop.get("extId") or "").strip()
                if sid and sid not in resolved:
                    resolved.append(sid)
                    break
    return resolved


def load_request_count() -> tuple[str | None, int]:
    try:
        data = json.loads(REQUEST_COUNT_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return (None, 0)
    except json.JSONDecodeError:
        return (None, 0)
    date = data.get("date")
    count = data.get("count", 0)
    return (str(date) if date else None, int(count) if isinstance(count, int) else 0)


def _acquire_lock(lock_path: Path) -> bool:
    deadline = time.monotonic() + REQUEST_LOCK_TIMEOUT_SEC
    while True:
        try:
            # Explicit mode 0o600 for security
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
            return True
        except FileExistsError:
            try:
                mtime = lock_path.stat().st_mtime
            except FileNotFoundError:
                # Lock file vanished in the meantime, retry immediately
                continue

            # Check for stale lock
            if time.time() - mtime > REQUEST_LOCK_TIMEOUT_SEC:
                try:
                    lock_path.unlink()
                except FileNotFoundError:
                    pass
                # Retry immediately after removing stale lock
                continue

            if time.monotonic() > deadline:
                return False
            time.sleep(REQUEST_LOCK_RETRY_DELAY)
        except OSError:
            return False


def save_request_count(now_local: datetime) -> int:
    date_iso = now_local.astimezone(ZONE_VIENNA).date().isoformat()
    lock_path = REQUEST_COUNT_FILE.with_suffix(".lock")
    lock_acquired = _acquire_lock(lock_path)
    try:
        previous_date, previous_count = load_request_count()
        if previous_date != date_iso:
            previous_count = 0
        new_count = previous_count + 1
        if not lock_acquired:
            return previous_count
        REQUEST_COUNT_FILE.parent.mkdir(parents=True, exist_ok=True)
        temp_path = REQUEST_COUNT_FILE.with_suffix(".tmp")
        payload = {"date": date_iso, "count": new_count}
        try:
            # Ensure temp path is string for os.open
            fd = os.open(str(temp_path), os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
        except OSError:
            return previous_count
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, REQUEST_COUNT_FILE)
        except OSError:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass
            return previous_count
        return new_count
    finally:
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


def _parse_retry_after(response: requests.Response) -> float | None:
    header = response.headers.get("Retry-After")
    if not header:
        _log_warning("Retry-After fehlt, verwende Fallback-Verzögerung")
        return None
    header = header.strip()
    if not header:
        _log_warning("Retry-After leer, verwende Fallback-Verzögerung")
        return None
    if re.fullmatch(r"\d+(?:\.\d+)?", header):
        return float(header)
    try:
        parsed = parsedate_to_datetime(header)
    except (TypeError, ValueError):
        _log_warning("VOR lieferte ungültiges Retry-After: %s", header)
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    delay = (parsed - now).total_seconds()
    return max(delay, 0.0)


def _handle_retry_after(response: requests.Response) -> None:
    delay = _parse_retry_after(response)
    if delay is None:
        delay = RETRY_AFTER_FALLBACK_SEC
        _log_warning("Nutze Fallback-Verzögerung %s Sekunden", delay)
    time.sleep(delay)


def _fetch_stationboard(station_id: str, now_local: datetime) -> Mapping[str, Any] | None:
    params = {
        "format": "json",
        "id": station_id,
        "date": now_local.strftime("%Y-%m-%d"),
        "time": now_local.strftime("%H:%M"),
        "duration": str(BOARD_DURATION_MIN),
        "products": str(_product_class_bitmask(_desired_product_classes())),
        "rtMode": "SERVER_DEFAULT",
    }
    try:
        with session_with_retries(VOR_USER_AGENT, **VOR_RETRY_OPTIONS) as session:
            apply_authentication(session)
            attempts = max(int(VOR_RETRY_OPTIONS.get("total", 0) or 0) + 1, 1)
            for attempt in range(attempts):
                try:
                    content = fetch_content_safe(
                        session,
                        f"{VOR_BASE_URL}departureboard",
                        params=params,
                        timeout=HTTP_TIMEOUT,
                    )
                    save_request_count(now_local)
                    return json.loads(content)

                except ValueError as exc:
                    save_request_count(now_local)
                    _log_warning("VOR StationBoard %s ungültig/zu groß: %s", station_id, exc)
                    return None

                except requests.HTTPError as exc:
                    save_request_count(now_local)
                    response = exc.response
                    if response is not None:
                        if response.status_code == 429:
                            _log_warning("VOR StationBoard %s -> HTTP 429", station_id)
                            _handle_retry_after(response)
                            return None
                        if response.status_code >= 500:
                            _log_warning("VOR StationBoard %s -> HTTP %s", station_id, response.status_code)
                            if response.status_code == 503:
                                _handle_retry_after(response)
                            return None
                        if response.status_code >= 400:
                            _log_warning("VOR StationBoard %s -> HTTP %s", station_id, response.status_code)
                            return None

                    if attempt >= attempts - 1:
                        _log_error("VOR StationBoard %s fehlgeschlagen: %s", station_id, exc)
                        return None
                    _log_warning(
                        "VOR StationBoard %s fehlgeschlagen (Versuch %d/%d): %s",
                        station_id,
                        attempt + 1,
                        attempts,
                        exc,
                    )
                    continue

                except RequestException as exc:
                    save_request_count(now_local)
                    if attempt >= attempts - 1:
                        _log_error("VOR StationBoard %s fehlgeschlagen: %s", station_id, exc)
                        return None
                    _log_warning(
                        "VOR StationBoard %s fehlgeschlagen (Versuch %d/%d): %s",
                        station_id,
                        attempt + 1,
                        attempts,
                        exc,
                    )
                    continue

            return None
    except RequestException as exc:
        _log_error("VOR StationBoard %s Ausnahme: %s", station_id, exc)
        return None


def fetch_events() -> List[Dict[str, Any]]:
    token = refresh_access_credentials()
    if not token:
        log.warning("Kein VOR Access Token konfiguriert – überspringe Abruf.")
        return []

    now_local = datetime.now(ZONE_VIENNA)
    today = now_local.date().isoformat()
    stored_date, stored_count = load_request_count()
    if stored_date == today and stored_count >= MAX_REQUESTS_PER_DAY:
        log.info("Tageslimit von %s VOR-Anfragen erreicht", MAX_REQUESTS_PER_DAY)
        return []

    remaining_requests = max(MAX_REQUESTS_PER_DAY - stored_count, 0)
    if remaining_requests == 0:
        log.info(
            "Tageslimit von %s VOR-Anfragen bereits ausgeschöpft – überspringe Abruf.",
            MAX_REQUESTS_PER_DAY,
        )
        return []

    station_ids = list(VOR_STATION_IDS)
    if not station_ids and VOR_STATION_NAMES:
        station_ids = resolve_station_ids(VOR_STATION_NAMES)
    if not station_ids:
        log.info("Keine VOR Stationen konfiguriert")
        return []

    selected_ids = _select_stations_round_robin(station_ids, MAX_STATIONS_PER_RUN, ROTATION_INTERVAL_SEC)
    if not selected_ids:
        selected_ids = station_ids[: MAX_STATIONS_PER_RUN or 1]

    if remaining_requests and len(selected_ids) > remaining_requests:
        log.info(
            "Begrenze Abruf auf %s von %s Station(en) wegen Request-Limit (%s übrig).",
            remaining_requests,
            len(selected_ids),
            remaining_requests,
        )
        selected_ids = selected_ids[:remaining_requests]

    log.info(
        "Starte VOR-Abruf für %s Station(en); verbleibende Requests heute: %s",
        len(selected_ids),
        remaining_requests if remaining_requests else "unbegrenzt",
    )

    results: List[Dict[str, Any]] = []
    failures = 0
    successes = 0

    with ThreadPoolExecutor(max_workers=len(selected_ids) or 1) as executor:
        futures = {executor.submit(_fetch_stationboard, sid, now_local): sid for sid in selected_ids}
        for future in as_completed(futures):
            station_id = futures[future]
            try:
                payload = future.result()
            except Exception as exc:  # pragma: no cover - defensive guard
                _log_error("VOR StationBoard %s Fehler: %s", station_id, exc)
                failures += 1
                continue
            if payload is None:
                failures += 1
                continue
            successes += 1
            try:
                items = _collect_from_board(station_id, payload)
            except Exception as exc:  # pragma: no cover - defensive guard
                _log_error("Fehler beim Verarbeiten der Station %s: %s", station_id, exc)
                failures += 1
                continue
            message_count = len(items)
            if message_count == 0:
                log.info("VOR Station %s meldet derzeit keine Ereignisse.", station_id)
            else:
                log.info("VOR Station %s lieferte %s Ereignis(se).", station_id, message_count)
            results.extend(items)

    if successes == 0:
        raise RequestException("Keine VOR StationBoards abrufbar")

    log.info(
        "VOR-Abruf abgeschlossen: %s Station(en) erfolgreich, %s ohne Ergebnis, %s Ereignis(se) gesammelt.",
        successes,
        failures,
        len(results),
    )

    return results


__all__ = [
    "apply_authentication",
    "fetch_events",
    "load_request_count",
    "resolve_station_ids",
    "save_request_count",
    "refresh_access_credentials",
    "refresh_base_configuration",
    "RequestException",
    "ZoneInfo",
]
