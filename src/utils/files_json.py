"""Strict JSON parsing: reject non-finite number literals.

Holds the two ``json.loads`` hooks and :func:`loads_finite`. Moved out of
:mod:`src.utils.files` (700-line rule); that module re-exports every name
and uses the hooks in ``read_capped_json``.
"""
from __future__ import annotations

import json
import math

__all__ = [
    "_reject_non_finite_constant",
    "_reject_non_finite_float",
    "loads_finite",
]


# Reader-side defence-in-depth: reject non-finite ``NaN`` / ``Infinity`` /
# ``-Infinity`` JSON literals (plus overflow-produced ``±inf`` via
# scientific-notation tokens like ``1e1000``). Mirrors the writer-side
# ``allow_nan=False`` pin closed at every committed-to-main writer in
# Round 1485 / Round 1487 / Round 1488. Without the reader-side hooks
# a planted non-standard literal in an on-disk state file (compromised
# CI runner, parallel orchestrator atomic state swap, partial flush +
# power loss, hostile PR landing a tampered fixture) propagates silently
# as ``float('nan')`` / ``float('inf')`` into Python-level computation:
#
#   * ``nan != nan`` returns ``True`` — breaks every dedup invariant
#     and timestamp comparison that uses ``!=``.
#   * ``nan + 5`` returns ``nan`` — silently poisons every downstream
#     arithmetic chain (latency averages, retention-cutoff windows).
#   * ``inf - inf`` returns ``nan`` — same silent poison shape.
#   * Round-trip back to a writer hits the ``allow_nan=False`` pin and
#     raises ``ValueError`` — the cron pipeline crashes mid-write with
#     no recovery, instead of detecting the corruption at read time and
#     starting from a clean (empty) state per the canonical
#     ``read_capped_json`` recovery pattern.
#
# Both hooks raise :class:`json.JSONDecodeError` (a :class:`ValueError`
# subclass) so callers' existing ``except json.JSONDecodeError`` handlers
# catch the rejection transparently — no per-callsite ``except`` widening
# is needed. The planted bytes are reported back to the operator log
# alongside the existing depth/size/encoding diagnostics under the same
# "corrupt file, treat as missing" recovery shape.


def _reject_non_finite_constant(constant: str) -> float:
    """``parse_constant`` hook: reject ``NaN`` / ``Infinity`` / ``-Infinity``
    literal tokens (invalid per RFC 8259 §6).

    Python's lenient mode accepts these three tokens as JSON values; this
    hook overrides the default behaviour so the rejection surfaces as
    ``json.JSONDecodeError`` at the parse boundary. Used as the canonical
    ``parse_constant`` argument to :func:`json.loads` across every
    committed-state-file reader in the project.
    """
    raise json.JSONDecodeError(
        f"Non-finite JSON literal {constant!r}; "
        f"RFC 8259 forbids NaN/Infinity tokens",
        constant, 0,
    )


def _reject_non_finite_float(value: str) -> float:
    """``parse_float`` hook: reject overflow-produced non-finite floats.

    Sibling defence to :func:`_reject_non_finite_constant`. Python's
    ``json.loads`` parses syntactically-valid scientific-notation tokens
    like ``"1e1000"`` (NOT a constant, NOT caught by ``parse_constant``)
    by calling ``float(value)`` — which IEEE-754 overflows to ``+inf`` /
    ``-inf`` silently. Without this hook a planted ``1e1000`` literal
    bypasses the ``parse_constant`` defence and lands ``float('inf')``
    in the parsed structure exactly the same as a ``NaN`` /
    ``Infinity`` literal would.

    Returns the parsed finite float unchanged; raises
    :class:`json.JSONDecodeError` on overflow / underflow to a
    non-finite value. Underflow to ``0.0`` (e.g. ``"1e-1000"``) is
    finite and accepted — loss of precision is a separate threat
    model from non-finite propagation.
    """
    parsed = float(value)
    if not math.isfinite(parsed):
        raise json.JSONDecodeError(
            f"Non-finite JSON number {value!r}; "
            f"RFC 8259 requires finite IEEE-754 doubles",
            value, 0,
        )
    return parsed


def loads_finite(raw: str | bytes | bytearray) -> object:
    """Parse *raw* JSON, rejecting non-finite literals at the parse boundary.

    Canonical wrapper for :func:`json.loads` that bakes in the
    :func:`_reject_non_finite_constant` + :func:`_reject_non_finite_float`
    hooks the on-disk readers already use (Round 1503). Closes the
    network-tainted / env-tainted parse boundary that PR #1503 left open:

    * Upstream HTTP responses (Wiener Linien, Google Places, OSM Overpass,
      HAFAS, ÖBB VAO, VOR, GitHub API, Wien Baustellen) parsed via
      ``json.loads(content)`` accept ``NaN`` / ``Infinity`` /
      ``-Infinity`` literal tokens AND scientific-notation overflow
      (``1e1000`` → ``+inf``) under Python's lenient default
      ``json.loads`` settings. A compromised upstream / MITM / DNS-hijack
      can plant non-finite floats into the in-memory data structure,
      poisoning every downstream comparison (``nan != nan`` is ``True``
      — breaks every dedup invariant), arithmetic chain (``nan + 5 ==
      nan``), and round-trip back to the writer's ``allow_nan=False``
      pin (Round 1485/1487/1488 — ``ValueError`` crashes the cron
      pipeline mid-write).
    * Env-controlled JSON (``BOUNDINGBOX_VIENNA``) parsed via
      ``json.loads(env_str)`` accepts the same literals under the same
      threat model — a leaked CI env / compromised secret store / hostile
      operator can plant non-finite coordinates that propagate through
      every downstream bounding-box computation.

    Both hooks raise :class:`json.JSONDecodeError` (a :class:`ValueError`
    subclass) so callers' existing ``except (ValueError,
    json.JSONDecodeError, RecursionError)`` handlers catch the rejection
    transparently — no per-callsite ``except`` widening is needed.
    """
    # The enclosing try/except is required to satisfy the JSON-parser
    # audit walker (``tests/test_sentinel_json_audit_walker.py``) which
    # enforces RecursionError tolerance at every ``json.loads`` callsite.
    # ``loads_finite`` re-raises RecursionError unchanged so the caller's
    # ``except RecursionError`` handler runs identically to the pre-fix
    # bare ``json.loads(raw)`` shape — no behaviour change for the
    # caller, just a structural pin for the walker invariant.
    try:
        return json.loads(
            raw,
            parse_constant=_reject_non_finite_constant,
            parse_float=_reject_non_finite_float,
        )
    except RecursionError:
        raise
