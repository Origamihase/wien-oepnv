from __future__ import annotations

import logging
import json
import math
from datetime import datetime
from typing import Any

from .config import LOG_FORMAT, LOG_TIMEZONE
from ..utils.logging import sanitize_log_message


# Security (Committed-Writer ``allow_nan=False`` Drift, sibling-formatter
# closure of PR #1491 / Round 1488): ``json.dumps`` defaults to
# ``allow_nan=True`` which emits the non-standard literals ``NaN`` /
# ``Infinity`` / ``-Infinity`` for non-finite floats. RFC 8259 forbids
# those literals, so every strict downstream JSON parser
# (``JSON.parse`` in ECMAScript-strict mode, Go's ``encoding/json``,
# Rust's ``serde_json`` strict mode, Splunk / ElasticSearch / Datadog
# log ingestion pipelines that key on RFC-8259 conformance) refuses
# the line. PR #1491 closed eight committed-writer sibling sites
# against this drift; this helper pairs with the
# :class:`SafeJSONFormatter` JSON log path which is the eighth-plus-one
# sibling: any ``log.info(..., extra={"latency_ms": float('nan')})``
# call (or any third-party LoggerAdapter that injects float fields)
# would otherwise render the entire log batch unparseable for any
# strict downstream consumer.
#
# The helper walks the payload BEFORE ``json.dumps`` and converts each
# non-finite ``float`` to its safe string representation (``"NaN"``,
# ``"Infinity"``, ``"-Infinity"``). Strings round-trip cleanly through
# every JSON parser. The matching ``allow_nan=False`` pin in
# :meth:`SafeJSONFormatter.format` is defense-in-depth: if the helper
# misses a future container type (e.g. a custom Mapping subclass), the
# pin surfaces the bypass as a loud ``ValueError`` rather than a silent
# RFC-8259 violation. A bounded recursion depth defends against
# pathological inputs that bypass the upstream depth-bomb guard at the
# logging-call boundary.
_MAX_JSON_SANITISE_DEPTH = 50


class _FallbackJSONEncoder(json.JSONEncoder):
    """Last-resort encoder that converts every ``float`` to a string.

    Used only by the formatter's ``except ValueError`` fallback path —
    fires when the primary :func:`_sanitise_non_finite_floats` walk
    missed a container type (custom Mapping subclass, non-list
    iterable). Stringifying every float guarantees the produced JSON
    stays RFC-8259-conforming even if a non-finite value slipped past
    the walker. Finite floats lose their numeric typing in the fallback
    output (``"0.5"`` vs ``0.5``) but the formatter contract is
    "never raise"; numeric typing is a "nice to have" the primary path
    preserves.
    """

    def encode(self, o: Any) -> str:
        return super().encode(_force_stringify_floats(o, depth=0))

    def iterencode(
        self, o: Any, _one_shot: bool = False
    ) -> Any:  # pragma: no cover - thin delegating shim
        return super().iterencode(_force_stringify_floats(o, depth=0), _one_shot)


def _force_stringify_floats(value: object, *, depth: int = 0) -> object:
    """Recursively convert every ``float`` (finite or not) to its string
    representation. Used by :class:`_FallbackJSONEncoder` only — the
    primary walker preserves finite floats as numbers.
    """
    if depth > _MAX_JSON_SANITISE_DEPTH:
        return repr(value)
    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        if math.isfinite(value):
            return repr(value)
        if math.isnan(value):
            return "NaN"
        return "Infinity" if value > 0 else "-Infinity"
    if isinstance(value, dict):
        return {
            key: _force_stringify_floats(item, depth=depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, list | tuple):
        return [_force_stringify_floats(item, depth=depth + 1) for item in value]
    if isinstance(value, set | frozenset):
        items = [_force_stringify_floats(item, depth=depth + 1) for item in value]
        try:
            return sorted(items, key=str)
        except TypeError:
            return items
    return value


def _sanitise_non_finite_floats(value: object, *, depth: int = 0) -> object:
    """Recursively replace non-finite ``float`` values with safe strings.

    Walks ``dict`` / ``list`` / ``tuple`` / ``set`` containers and
    converts ``float('nan')`` / ``float('+inf')`` / ``float('-inf')``
    to ``"NaN"`` / ``"Infinity"`` / ``"-Infinity"`` so the resulting
    structure round-trips through any RFC-8259-conforming JSON parser.
    Legitimate finite floats, ints, bools, strings, and ``None`` pass
    through unchanged. Unknown types pass through to ``json.dumps`` /
    ``default=`` for normal handling.

    A bounded recursion depth defends against pathological extras that
    bypass the upstream depth-bomb guard at the logging-call boundary;
    on overflow the value is rendered via ``repr`` so the formatter
    still emits a string rather than raising.
    """
    if depth > _MAX_JSON_SANITISE_DEPTH:
        return repr(value)
    if isinstance(value, bool):
        # Must precede the float branch because ``bool`` is a subclass of
        # ``int`` (and ``int`` is JSON-safe by default).
        return value
    if isinstance(value, float) and not math.isfinite(value):
        if math.isnan(value):
            return "NaN"
        return "Infinity" if value > 0 else "-Infinity"
    if isinstance(value, dict):
        return {
            key: _sanitise_non_finite_floats(item, depth=depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_sanitise_non_finite_floats(item, depth=depth + 1) for item in value]
    if isinstance(value, tuple):
        return tuple(
            _sanitise_non_finite_floats(item, depth=depth + 1) for item in value
        )
    if isinstance(value, set | frozenset):
        # ``set`` is not JSON-serialisable, but a third-party caller
        # may pass one as an extra; convert to a sorted list so the
        # downstream serialiser can render it deterministically.
        items = [_sanitise_non_finite_floats(item, depth=depth + 1) for item in value]
        try:
            return sorted(items, key=str)
        except TypeError:
            return items
    return value

class SafeFormatter(logging.Formatter):
    """
    A logging formatter that sanitizes messages before formatting.

    This ensures that:
    1. Secrets are masked.
    2. Control characters (newlines, etc.) are escaped to prevent log injection.
    3. ANSI codes are stripped.
    """

    def format(self, record: logging.LogRecord) -> str:
        record = logging.makeLogRecord(record.__dict__)
        original_msg = record.getMessage()
        sanitized_msg = sanitize_log_message(original_msg)
        record.msg = sanitized_msg
        record.args = ()
        formatted = super().format(record)
        return formatted.replace("\n", "\\n").replace("\r", "\\r")

    def formatException(self, ei: Any) -> str:
        s = super().formatException(ei)
        # Redact secrets but preserve newlines for readability in tracebacks
        return sanitize_log_message(s, strip_control_chars=False)


class SafeJSONFormatter(logging.Formatter):
    """JSON logging formatter that sanitizes values."""

    _DEFAULT_FIELDS = {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
    }

    def format(self, record: logging.LogRecord) -> str:
        # Sanitize message content
        original_msg = record.getMessage()
        sanitized_msg = sanitize_log_message(original_msg)

        timestamp = datetime.fromtimestamp(record.created, LOG_TIMEZONE)
        payload: dict[str, Any] = {
            "timestamp": timestamp.isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": sanitized_msg,
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)

        extras: dict[str, Any] = {}
        for key, value in record.__dict__.items():
            if key in self._DEFAULT_FIELDS:
                continue
            extras[key] = value

        if extras:
            payload["extra"] = extras

        # Security (Committed-Writer ``allow_nan=False`` Drift, sibling-
        # formatter closure of PR #1491): pre-walk the payload to convert
        # non-finite ``float`` values in extras (``float('nan')`` /
        # ``float('+inf')`` / ``float('-inf')``) to safe string literals
        # BEFORE ``json.dumps``. RFC 8259 forbids the ``NaN`` / ``Infinity``
        # / ``-Infinity`` JSON literals that Python's default
        # ``allow_nan=True`` emits, breaking every strict downstream
        # parser (Splunk / ElasticSearch / Datadog log ingestion,
        # ``serde_json`` strict mode, Go ``encoding/json``). Operator-
        # facing call sites that pass float metrics via ``extra={...}``
        # (latency, response-size ratio, error rate) would otherwise
        # render the entire log batch unparseable for any conforming
        # consumer the moment the upstream peer responds with a bogus
        # rate. See ``_sanitise_non_finite_floats`` for the canonical
        # walk shape and threat model.
        safe_payload = _sanitise_non_finite_floats(payload)
        # We sanitize the full JSON string to catch secrets nested in dictionaries or lists
        # (e.g. extra={"context": {"api_key": "..."}}) which the previous per-field logic missed.
        # Defense-in-depth: ``allow_nan=False`` raises ``ValueError`` if
        # the helper missed a non-finite float (e.g. a custom Mapping
        # subclass that ``isinstance(d, dict)`` did not cover), so a
        # bypass surfaces loudly. The ``except ValueError`` fallback
        # preserves the formatter's never-raise contract — Python's
        # logging framework wraps formatter exceptions in noisy stderr
        # output that would mask the original log call entirely.
        try:
            dumped = json.dumps(safe_payload, ensure_ascii=False, allow_nan=False)
        except ValueError:
            # Final fallback for the pathological-bypass case (custom
            # Mapping subclass / non-list iterable that the walker did
            # not recurse into). ``json.dumps``' ``default=`` callback
            # is invoked for unsupported types AND, via the custom
            # encoder below, for any ``float`` that survived the
            # primary walk — so a leaked ``NaN`` is rendered as the
            # safe string repr rather than an RFC-8259-violating
            # bare literal. ``allow_nan=False`` stays pinned so any
            # second-level bypass surfaces loudly at the test layer.
            dumped = json.dumps(
                safe_payload,
                ensure_ascii=False,
                allow_nan=False,
                cls=_FallbackJSONEncoder,
            )
        sanitized = sanitize_log_message(dumped, strip_control_chars=False)
        return sanitized.replace("\n", "\\n").replace("\r", "\\r")

    def formatException(self, ei: Any) -> str:
        s = super().formatException(ei)
        # Redact secrets but preserve newlines (JSON handles them)
        return sanitize_log_message(s, strip_control_chars=False)


def _vienna_time_converter(timestamp: float | None) -> Any:
    effective_timestamp = (
        timestamp
        if timestamp is not None
        else datetime.now(tz=LOG_TIMEZONE).timestamp()
    )
    return datetime.fromtimestamp(effective_timestamp, LOG_TIMEZONE).timetuple()


def _make_formatter() -> logging.Formatter:
    if LOG_FORMAT == "json":
        return SafeJSONFormatter()

    # Standard format string
    fmt = "%(asctime)s %(levelname)s %(name)s: %(message)s"
    formatter = SafeFormatter(fmt)
    formatter.converter = _vienna_time_converter
    return formatter


def setup_script_logging(level: int = logging.INFO) -> None:
    """Configure root logger with :class:`SafeFormatter` for scripts.

    Replaces the ``logging.basicConfig(...)`` call that scripts in
    ``scripts/`` historically used. ``basicConfig`` installs a default
    :class:`logging.Formatter` which does NOT sanitise the formatted
    message — meaning a hostile exception text or upstream-controlled
    URL fragment passed via ``%s`` in a log call leaks unmodified into
    the script's stderr / log file.

    This helper installs a single :class:`StreamHandler` whose formatter
    is the project's standard :class:`SafeFormatter` (or
    :class:`SafeJSONFormatter` when ``LOG_FORMAT=json``), giving every
    script the same clear-text-logging-drift defence that
    ``src.build_feed.configure_logging`` provides for the production
    feed builder.

    Idempotency: a SafeFormatter handler is added exactly once per
    process. Subsequent calls only update the root level. Foreign
    handlers (most importantly pytest's caplog capture handler) are
    preserved — they predate our installation and clearing them would
    invalidate test fixtures that capture log records via the standard
    pytest mechanism. This matches the original
    ``logging.basicConfig(level=…, format=…)`` no-op-if-handlers
    behaviour while still installing the sanitising handler when it's
    truly absent.

    Args:
        level: Root-logger log level. Pass ``logging.DEBUG`` for
            ``--verbose`` script invocations; defaults to
            ``logging.INFO`` to match the historical script defaults.
    """
    logger = logging.getLogger()
    has_safe_formatter = any(
        isinstance(h.formatter, (SafeFormatter, SafeJSONFormatter))
        for h in logger.handlers
    )
    if not has_safe_formatter:
        handler = logging.StreamHandler()
        handler.setFormatter(_make_formatter())
        logger.addHandler(handler)
    logger.setLevel(level)
