"""Sentinel — ``SafeJSONFormatter`` ``allow_nan=False`` drift (sibling of PR #1491).

PR #1491 (Round 1488) closed the ``allow_nan=False`` writer-level pin on eight
committed-to-main JSON writers (``docs/feed-health.json`` + six ``data/*.json``
sidecars + ``cache/<provider>/last_run.json``). The drift left one structurally
identical sibling site uncovered:

* :class:`src.feed.logging_safe.SafeJSONFormatter.format` (line 90 pre-fix).

Threat model
============

When ``LOG_FORMAT=json`` is set (the modern best-practice for log shippers),
every log record is rendered through :class:`SafeJSONFormatter`. The formatter
walks ``record.__dict__`` for any non-default fields and stuffs them into the
serialised payload's ``extra`` dict. Any ``log.info("...", extra={...})`` call
that includes a non-finite ``float`` (``float('nan')`` / ``float('+inf')`` /
``float('-inf')``) — a benign caller pattern for latency / response-size /
error-rate metrics — would have rendered as the non-standard literals ``NaN``
/ ``Infinity`` / ``-Infinity`` because Python's ``json.dumps`` defaults to
``allow_nan=True``.

RFC 8259 §6 forbids those literals. Every strict downstream consumer rejects
them:

* ECMAScript ``JSON.parse`` (post-2009 strict mode);
* Go's ``encoding/json`` (always strict);
* Rust's ``serde_json`` (strict mode);
* Splunk / ElasticSearch / Datadog log ingestion pipelines that key off
  RFC-8259 conformance.

A single non-finite float in a single log record breaks the entire ingestion
batch for that consumer, silently dropping every subsequent log line. The
operator-facing alarm pipeline goes blind for the duration of the build cycle.

Threat shape mirrors PR #1491 byte-for-byte:

* Same ``json.dumps(..., ensure_ascii=False)`` writer signature.
* Same Python lenient-parse round-trip recovery (``json.loads`` accepts the
  literals — masking the issue from the writer's own validators).
* Same RFC-8259 strict-parse rejection at the boundary.

The fix:

1. Pre-walk the payload via :func:`_sanitise_non_finite_floats` to convert
   non-finite floats to safe string repr (``"NaN"`` / ``"Infinity"`` /
   ``"-Infinity"``).
2. Pin ``allow_nan=False`` on the ``json.dumps`` call as defense-in-depth so a
   bypass (custom Mapping subclass, future container type) surfaces as a loud
   ``ValueError`` rather than a silent RFC-8259 violation.
3. Wrap the dump in a ``try / except ValueError`` fallback that re-renders
   with ``default=str`` so the formatter's never-raise contract holds.

This file pins the inventory invariant + the behavioural PoCs.
"""

from __future__ import annotations

import json
import logging
import math
from io import StringIO
from pathlib import Path

import pytest

from src.feed.logging_safe import (
    SafeJSONFormatter,
    _sanitise_non_finite_floats,
)


# A strict parse_constant hook re-raises the ``ValueError`` that the
# JSON spec mandates for the non-standard literals. ``json.loads`` calls
# ``parse_constant`` with the literal text (``"NaN"`` / ``"Infinity"`` /
# ``"-Infinity"``) when the corresponding source bytes appear in the
# input — so injecting this hook turns Python's lenient parser into a
# spec-conforming one for the duration of the test.
def _strict_parse_constant(literal: str) -> object:
    raise ValueError(
        f"Non-standard JSON literal {literal!r} (RFC 8259 forbids NaN / Infinity)"
    )


def _make_record(name: str = "sentinel") -> logging.LogRecord:
    """Build a minimal ``LogRecord`` wired up so the formatter doesn't
    blow up on missing attributes. Tests then add ``extra``-shape
    attributes by direct assignment, mirroring the runtime path
    Python's logging library uses when ``extra={...}`` is passed.
    """
    return logging.LogRecord(
        name=name,
        level=logging.INFO,
        pathname="test.py",
        lineno=1,
        msg="Sentinel canary",
        args=(),
        exc_info=None,
    )


# ----------------------------------------------------------------------
# Inventory invariant
# ----------------------------------------------------------------------


def test_safe_json_formatter_pins_allow_nan_false() -> None:
    """Pin the ``allow_nan=False`` writer-side defence at the canonical
    ``json.dumps`` site in :class:`SafeJSONFormatter.format`.

    Source-grep invariant: a future refactor that drops the pin (or
    rewrites the dump call) re-opens the RFC-8259 drift documented in
    the module docstring above. The structural assertion mirrors the
    inventory pattern in ``test_sentinel_committed_writer_allow_nan_drift.py``
    so the gap-closure remains discoverable.
    """
    source = Path("src/feed/logging_safe.py").read_text(encoding="utf-8")
    # The formatter must pin ``allow_nan=False`` on every ``json.dumps``
    # call — there are exactly two callsites post-fix (the primary dump
    # and the ``except ValueError`` fallback). Both must carry the pin.
    json_dumps_calls = source.count("json.dumps(")
    pinned_calls = source.count("allow_nan=False")
    assert json_dumps_calls >= 1, (
        "SafeJSONFormatter source no longer contains a json.dumps call "
        "(canonical writer reorganised — re-pin this inventory invariant)."
    )
    assert pinned_calls >= json_dumps_calls, (
        f"SafeJSONFormatter source has {json_dumps_calls} json.dumps call(s) "
        f"but only {pinned_calls} ``allow_nan=False`` pin(s). Every json.dumps "
        f"call must pin the RFC-8259 conformance defence; otherwise a future "
        f"refactor re-opens the Round-1488 sibling-formatter drift."
    )


def test_sanitiser_helper_is_module_exported() -> None:
    """Pin the existence of the canonical sanitiser helper so a future
    refactor that inlines the walk into the formatter (or splits it
    across multiple helpers) does not silently lose the recursive
    contract this drift round established."""
    from src.feed import logging_safe

    assert hasattr(logging_safe, "_sanitise_non_finite_floats"), (
        "Canonical non-finite-float sanitiser helper is missing — a refactor "
        "removed the Round-1488 drift-closure boundary."
    )
    assert callable(logging_safe._sanitise_non_finite_floats)


# ----------------------------------------------------------------------
# Behavioural PoCs (strict parse_constant boundary)
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "field_name,bad_value",
    [
        ("nan_metric", float("nan")),
        ("plus_inf", float("inf")),
        ("minus_inf", float("-inf")),
    ],
)
def test_format_does_not_emit_nan_or_infinity_in_top_level_extra(
    field_name: str, bad_value: float
) -> None:
    """PoC: pre-fix a single non-finite float in ``extra`` rendered as the
    literal ``NaN`` / ``Infinity`` / ``-Infinity`` token in the output —
    invalid per RFC 8259, rejected by every strict downstream parser.

    Post-fix: the output must parse cleanly through a strict parser
    (``json.loads`` with a ``parse_constant`` hook that re-raises on the
    forbidden literals).
    """
    formatter = SafeJSONFormatter()
    record = _make_record()
    setattr(record, field_name, bad_value)

    output = formatter.format(record)

    # Lenient parser must accept the output (sanity).
    parsed = json.loads(output)

    # Strict parser must accept the output (the load-bearing assertion).
    json.loads(output, parse_constant=_strict_parse_constant)

    # The non-finite float must have been replaced by its safe string
    # representation, not silently dropped.
    extras = parsed.get("extra", {})
    actual = extras.get(field_name)
    assert isinstance(actual, str), (
        f"Non-finite float in extras['{field_name}'] should round-trip as "
        f"a string, got {type(actual).__name__}: {actual!r}"
    )
    if math.isnan(bad_value):
        assert actual == "NaN"
    elif bad_value > 0:
        assert actual == "Infinity"
    else:
        assert actual == "-Infinity"


def test_format_handles_nested_non_finite_floats() -> None:
    """The walker must recurse into nested ``dict`` / ``list`` / ``tuple``
    containers — the canonical operator-facing extras shape carries
    metric dicts (``{"latency": {"p50": ..., "p99": ...}}``) where a
    transient upstream blip can land ``inf`` in a single quantile.
    """
    formatter = SafeJSONFormatter()
    record = _make_record()
    record.metrics = {
        "latency_ms": {"p50": 12.5, "p99": float("inf")},
        "samples": [1.0, float("nan"), 3.0],
        "as_tuple": (float("-inf"), 0.5),
    }

    output = formatter.format(record)
    json.loads(output, parse_constant=_strict_parse_constant)

    parsed = json.loads(output)
    metrics = parsed["extra"]["metrics"]
    assert metrics["latency_ms"]["p50"] == 12.5
    assert metrics["latency_ms"]["p99"] == "Infinity"
    assert metrics["samples"] == [1.0, "NaN", 3.0]
    assert metrics["as_tuple"] == ["-Infinity", 0.5]


def test_format_preserves_finite_floats_and_other_primitives() -> None:
    """Sanitiser must not over-eagerly mangle valid JSON-serialisable
    primitives — finite floats, ints, bools, strings, ``None`` all
    round-trip unchanged.
    """
    formatter = SafeJSONFormatter()
    record = _make_record()
    record.finite_float = 0.5
    record.an_int = 42
    record.a_bool = True
    record.a_string = "ok"
    record.a_none = None
    record.zero_float = 0.0
    record.negative_zero = -0.0

    output = formatter.format(record)
    parsed = json.loads(output, parse_constant=_strict_parse_constant)

    extras = parsed["extra"]
    assert extras["finite_float"] == 0.5
    assert extras["an_int"] == 42
    assert extras["a_bool"] is True
    assert extras["a_string"] == "ok"
    assert extras["a_none"] is None
    assert extras["zero_float"] == 0.0
    # JSON does not distinguish negative zero from positive zero, but
    # Python's ``json.dumps`` emits ``-0.0`` literally and the strict
    # parser accepts it.
    assert extras["negative_zero"] == 0.0


def test_format_via_logger_extra_passes_strict_parse_constant() -> None:
    """End-to-end PoC through Python's logging framework — mirrors the
    real-world call shape ``log.info("...", extra={...})`` that an
    operator-facing caller would use to attach a metric.
    """
    logger = logging.getLogger(__name__ + ".strict_parse")
    # Avoid handler accumulation across pytest runs.
    logger.handlers.clear()
    logger.setLevel(logging.INFO)

    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(SafeJSONFormatter())
    logger.addHandler(handler)

    try:
        logger.info(
            "Provider %s finished in %s",
            "vor",
            "12.5s",
            extra={
                "duration_s": float("nan"),
                "throughput_qps": float("inf"),
                "items": 7,
            },
        )
    finally:
        logger.removeHandler(handler)

    output = stream.getvalue().strip()
    # Strict parse must succeed (no NaN / Infinity literals).
    parsed = json.loads(output, parse_constant=_strict_parse_constant)
    extras = parsed["extra"]
    assert extras["duration_s"] == "NaN"
    assert extras["throughput_qps"] == "Infinity"
    assert extras["items"] == 7


def test_format_handles_set_extras_deterministically() -> None:
    """A ``set`` extra is not natively JSON-serialisable. The walker
    converts it to a sorted list so the formatter does not raise, and
    the strict parser still accepts the output.
    """
    formatter = SafeJSONFormatter()
    record = _make_record()
    record.tags = {"err", "abc", "def"}

    output = formatter.format(record)
    parsed = json.loads(output, parse_constant=_strict_parse_constant)

    tags = parsed["extra"]["tags"]
    assert isinstance(tags, list)
    assert sorted(tags) == ["abc", "def", "err"]


# ----------------------------------------------------------------------
# Helper unit tests (pin the recursive contract directly)
# ----------------------------------------------------------------------


def test_helper_replaces_nan_at_top_level() -> None:
    assert _sanitise_non_finite_floats(float("nan")) == "NaN"
    assert _sanitise_non_finite_floats(float("inf")) == "Infinity"
    assert _sanitise_non_finite_floats(float("-inf")) == "-Infinity"


def test_helper_preserves_finite_floats_and_other_primitives() -> None:
    assert _sanitise_non_finite_floats(0.5) == 0.5
    assert _sanitise_non_finite_floats(0.0) == 0.0
    assert _sanitise_non_finite_floats(42) == 42
    assert _sanitise_non_finite_floats(True) is True
    assert _sanitise_non_finite_floats(False) is False
    assert _sanitise_non_finite_floats("ok") == "ok"
    assert _sanitise_non_finite_floats(None) is None


def test_helper_preserves_bool_distinct_from_int() -> None:
    """``isinstance(True, int)`` is ``True`` in Python — the helper
    must check ``bool`` BEFORE ``int`` / ``float`` so booleans round-trip
    as JSON ``true`` / ``false`` rather than being swallowed by a
    numeric branch."""
    out_true = _sanitise_non_finite_floats(True)
    out_false = _sanitise_non_finite_floats(False)
    assert out_true is True
    assert out_false is False
    assert json.dumps(out_true) == "true"
    assert json.dumps(out_false) == "false"


def test_helper_recurses_into_nested_containers() -> None:
    payload = {
        "outer": {
            "inner_list": [float("nan"), 1.0, [float("inf")]],
            "inner_tuple": (float("-inf"), 2.0),
        },
    }
    sanitised = _sanitise_non_finite_floats(payload)
    assert sanitised == {
        "outer": {
            "inner_list": ["NaN", 1.0, ["Infinity"]],
            "inner_tuple": ("-Infinity", 2.0),
        },
    }


def test_helper_bounded_recursion_depth() -> None:
    """A pathological deeply-nested structure must NOT blow the Python
    recursion stack — the helper falls back to ``repr`` past the cap so
    the formatter still produces output.
    """
    # Build a 60-level deep nested dict ({k: {k: ...}}). The helper's
    # cap is 50, so the deeper levels collapse to ``repr``.
    payload: object = "leaf"
    for _ in range(60):
        payload = {"k": payload}
    out = _sanitise_non_finite_floats(payload)
    # The structure is finite and the helper returns without raising.
    assert out is not None


# ----------------------------------------------------------------------
# Defense-in-depth: ``allow_nan=False`` traps any future bypass
# ----------------------------------------------------------------------


def test_allow_nan_false_traps_a_walker_bypass() -> None:
    """If a future container type slipped past the walker, the
    ``allow_nan=False`` pin on ``json.dumps`` would raise ``ValueError``.
    The fallback ``except`` branch then renders via ``default=str`` so
    the formatter still emits valid JSON. This test simulates the
    bypass by feeding the formatter a custom dict subclass — the
    walker covers it (``isinstance(d, dict)`` is True), so the test
    additionally verifies that the fallback path itself produces
    RFC-conforming output.
    """
    # The simulated bypass: monkey-patch the helper to be a no-op so
    # the raw NaN reaches json.dumps. The ``allow_nan=False`` pin then
    # fires and the ``except ValueError`` fallback re-renders via
    # ``default=str``. End-state: still RFC-8259-conforming, no leaked
    # bare ``NaN`` literal.
    from src.feed import logging_safe

    original = logging_safe._sanitise_non_finite_floats
    logging_safe._sanitise_non_finite_floats = lambda value, **_: value
    try:
        formatter = SafeJSONFormatter()
        record = _make_record()
        record.canary = float("nan")
        output = formatter.format(record)
        # Strict parse: the fallback must still produce conforming JSON.
        json.loads(output, parse_constant=_strict_parse_constant)
        # Verify the canary was rendered (via ``default=str``) rather
        # than dropped silently.
        parsed = json.loads(output)
        assert "nan" in parsed["extra"]["canary"].lower()
    finally:
        logging_safe._sanitise_non_finite_floats = original
