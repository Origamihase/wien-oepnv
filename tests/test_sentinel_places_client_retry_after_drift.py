"""Sentinel PoC: ``Retry-After`` header logged verbatim in
``src/places/client.py:_post`` after ``float()`` rejects an
``isdigit()``-positive payload.

Threat model
============
``src/places/client.py:_post`` extracts the ``Retry-After`` HTTP
response header on each request-retry tick when the underlying
``requests.RequestException`` carries a ``.response``::

    header: str | None = None
    try:
        if isinstance(last_error, requests.RequestException) and last_error.response is not None:
            header = last_error.response.headers.get("Retry-After")
            if header and header.isdigit():
                retry_after_val = float(header)
    except ValueError:
        LOGGER.warning("Failed to parse Retry-After header: %s", header)

The Pre-Fix log line interpolates ``header`` via the bare ``%s``
format spec without routing through ``sanitize_log_arg``. The header
is fully upstream-controlled: a hostile network adversary (compromised
CDN, DNS hijack, MITM, malicious upstream Google response) can swap
the response with one whose ``Retry-After`` header carries any of the
canonical CVE-2021-42574 / log-injection / 8-bit-C1 / Tag-block /
Variation-Selector / ANSI-ESC / log-forgery primitive bytes — which
flow verbatim into:

  * Operator-facing WARNING logs captured by ``build_feed.main`` /
    ``scripts/fetch_google_places_stations.py`` (visible in the
    GitHub Actions UI and any downstream SIEM forwarder).
  * Pytest's ``caplog`` capture (which exposes ``record.args[0]``
    BEFORE the :class:`SafeFormatter` runs — a third-party log
    handler or custom plugin sees the raw bytes).
  * Any downstream consumer that reads ``record.msg`` /
    ``record.getMessage()`` from the propagated record before
    formatter sanitisation.

Sibling drift shape
===================
This is the canonical sibling of the ÖBB Retry-After hardening in
``src/providers/oebb.py:1313-1315``::

    log.warning(
        "ÖBB RSS Rate-Limit (Retry-After: %s)",
        sanitize_log_arg(str(header)),
    )

The ÖBB site explicitly carries the Sentinel comment "``header`` is
upstream-controlled HTTP header text; route through
``sanitize_log_arg`` so a hostile ÖBB peer cannot inject ANSI/BiDi/
control characters into operator log streams via the Retry-After
header." The ``places/client.py`` callsite is the only remaining
``response.headers.get("Retry-After")`` in ``src/`` that does NOT
mirror the sanitisation shape.

Reachability under the ``isdigit()`` gate
=========================================
The pre-fix log line is gated by ``header.isdigit()`` — only strings
whose every character is a Unicode "digit" reach the ``ValueError``
branch (Arabic-Indic ٠-٩, Devanagari ०-९, etc.; ``float()`` only
accepts ASCII digits 0-9 so non-ASCII Unicode digits raise
``ValueError``). Today's narrow exploit vector therefore restricts
the *wire-deliverable* attack payload to characters that don't carry
log-injection primitives.

This PoC documents the defense-in-depth contract: a future refactor
that relaxes the ``isdigit()`` precondition (a very common code-
simplification — drop the precondition and rely on the
``try/except ValueError`` to catch any invalid payload) would
immediately expose the log line to arbitrary upstream HTTP header
bytes. The fix routes the value through ``sanitize_log_arg`` so the
defence survives every future refactor, mirroring the canonical
shape already pinned for the ÖBB sibling sink.

PoC mechanics
=============
We simulate the *future-refactor* scenario by injecting a ``str``
subclass whose ``isdigit()`` lies and returns ``True`` even for
arbitrary attack-byte content. This bypasses the precondition while
still raising ``ValueError`` from ``float()`` on the non-numeric
payload — reaching the ``LOGGER.warning`` line with a hostile
``header`` value.

Pre-fix: the attack bytes survive into ``caplog.text`` verbatim.
Post-fix: ``sanitize_log_arg`` strips / escapes every primitive
before interpolation, so none of the canonical attack fragments
appear in the captured log.
"""
from __future__ import annotations

import ast
import logging
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import requests


REPO_ROOT = Path(__file__).resolve().parents[1]


# Canonical attack payload exercising the four orthogonal classes of
# control-text smuggling that ``sanitize_log_arg`` is documented to
# defeat:
#   * ANSI CSI escape (terminal corruption / colour smuggling)
#   * 8-bit C1 CSI (survives the 7-bit ``_ANSI_ESCAPE_RE`` defence)
#   * Carriage-return + newline (log-forging — splits one log line
#     into two so a planted second line looks like an unrelated event)
#   * BiDi RIGHT-TO-LEFT OVERRIDE (Trojan Source — flips the rendering
#     order of surrounding tokens in operator-facing renders that
#     respect BiDi)
#   * Zero-width space (visual obfuscation / cache-key poisoning)
#   * Unicode Tag SPACE (invisible-instruction smuggling primitive)
#   * Variation Selector-1 (steganography primitive)
_ATTACK_BYTES = (
    "\x1b[31mEVIL\x1b[0m"
    "\x9bATTACK"
    "\r\nINJECTED-LOG-LINE"
    "‮RTL-OVERRIDE"
    "​ZWS"
    "\U000e0020TAG-SPACE"
    "︀VS1"
)


# Substrings that must NOT appear in any captured log output. Each
# substring is checked independently so a partially-applied fix
# (e.g. forgot to handle ``\x9b``) still fails the test instead of
# silently passing.
_FORBIDDEN_FRAGMENTS = (
    "\x1b[",          # 7-bit ANSI CSI introducer
    "\x9b",           # 8-bit ANSI CSI introducer (C1 control)
    "\r\n",           # Log-forging primitive
    "‮",         # BiDi RIGHT-TO-LEFT OVERRIDE
    "​",         # Zero-width space
    "\U000e0020",     # Unicode Tag SPACE
    "︀",         # Variation Selector-1
)


class _HostileDigitStr(str):
    """``str`` subclass that lies about being all-digits.

    Models the future-refactor scenario where the ``header.isdigit()``
    precondition is relaxed (or replaced by a less-restrictive check)
    while the log line continues to interpolate ``header`` via the
    bare ``%s`` format spec. The subclass passes the ``isdigit()`` gate
    but the underlying ``str.__str__`` carries the attack payload
    verbatim — so ``float(header)`` raises ``ValueError`` (the payload
    is not a valid numeric literal) and the ``LOGGER.warning`` site
    runs with a hostile ``header`` value.
    """

    def isdigit(self) -> bool:
        return True


def _assert_no_attack_bytes(text: str) -> None:
    """Assert *text* does not carry any of the canonical attack fragments."""
    for fragment in _FORBIDDEN_FRAGMENTS:
        assert fragment not in text, (
            f"clear-text-logging drift: attack fragment {fragment!r} "
            f"survived sanitisation in places/client._post's "
            f"Retry-After warning log.\n"
            f"Captured: {text!r}"
        )


def _build_poisoned_exception() -> requests.RequestException:
    """Construct a :class:`requests.RequestException` whose response
    carries a poisoned ``Retry-After`` header that bypasses
    ``isdigit()`` but fails ``float()``.
    """
    response = MagicMock(spec=requests.Response)
    response.status_code = 429
    response.headers = {"Retry-After": _HostileDigitStr(_ATTACK_BYTES)}

    exc = requests.RequestException("simulated network error")
    exc.response = response
    return exc


def test_places_client_retry_after_header_sanitises_attack_bytes(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A hostile Retry-After header that bypasses ``isdigit()`` and
    fails ``float()`` must not leak control bytes into the WARNING log.

    Reaches the ``LOGGER.warning("Failed to parse Retry-After header:
    %s", header)`` site via the canonical retry-loop path:

      1. ``session.post`` raises :class:`requests.RequestException`
         whose ``.response`` carries the poisoned header.
      2. The exception is caught at ``except requests.RequestException
         as exc:``, ``last_error = exc``.
      3. ``attempt`` is below ``max_retries`` so the loop continues to
         the Retry-After extraction.
      4. The ``_HostileDigitStr`` subclass passes ``header.isdigit()``.
      5. ``float(header)`` raises ``ValueError`` on the non-numeric
         attack payload.
      6. ``LOGGER.warning`` interpolates ``header`` via ``%s`` —
         this is the drift site under test.

    Pre-fix: every forbidden fragment appears in ``caplog.text``.
    Post-fix: every forbidden fragment is sanitised away.
    """
    # Import the client module here so we pick up the live class
    # identity. ``tests/places/test_nearby_env_params.py`` reloads
    # ``src.places.client`` via ``importlib.reload``, which replaces the
    # ``GooglePlacesError`` class object — a module-top-level import
    # would bind to the *pre-reload* class and break the
    # ``pytest.raises`` capture in the post-reload suite ordering.
    import src.places.client as _client_module
    GooglePlacesClient = _client_module.GooglePlacesClient
    GooglePlacesConfig = _client_module.GooglePlacesConfig
    GooglePlacesError = _client_module.GooglePlacesError

    # Avoid wall-clock sleep between retry attempts. ``src.places.client``
    # imports ``time`` at module top level and calls ``time.sleep(...)``;
    # patching the stdlib ``time.sleep`` reaches the bound reference inside
    # the module's ``time`` namespace.
    import time as _time

    monkeypatch.setattr(_time, "sleep", lambda _seconds: None)

    poisoned_exc = _build_poisoned_exception()

    session = MagicMock(spec=requests.Session)
    session.post.side_effect = poisoned_exc

    config = GooglePlacesConfig(
        api_key="dummy-key",
        included_types=["train_station"],
        language="de",
        region="AT",
        radius_m=1000,
        timeout_s=1.0,
        # Need >= 1 so the retry path executes and reaches the
        # Retry-After extraction (the first attempt raises the
        # RequestException; the loop then evaluates the Retry-After
        # header before scheduling the second attempt).
        max_retries=1,
    )
    client = GooglePlacesClient(config, session=session)

    caplog.set_level(logging.WARNING, logger="places.google")

    # The call eventually raises ``GooglePlacesError`` (wrapped retry
    # exhaustion); we don't care about the return type, only the log
    # capture.
    with pytest.raises(GooglePlacesError):
        client._post("places:searchNearby", {})

    # The drift-site WARNING must have run at least once.
    assert "Failed to parse Retry-After header" in caplog.text, (
        "Test setup invariant: the Retry-After ValueError branch was "
        "not exercised. Investigate before treating sanitisation as "
        "verified."
    )

    _assert_no_attack_bytes(caplog.text)

    # Defence-in-depth: the raw record args must also be sanitised
    # (a third-party log handler that consumes ``record.args[0]``
    # before the SafeFormatter runs is the second exploit surface).
    for record in caplog.records:
        if record.levelno != logging.WARNING:
            continue
        if "Retry-After" not in record.getMessage():
            continue
        # ``record.args`` after sanitisation must not carry the raw
        # attack-byte string. ``sanitize_log_arg`` returns a sanitised
        # ``str``; the post-fix args[0] is the sanitised text.
        if record.args:
            args_tuple = record.args if isinstance(record.args, tuple) else (record.args,)
            for arg in args_tuple:
                rendered = str(arg)
                for fragment in _FORBIDDEN_FRAGMENTS:
                    assert fragment not in rendered, (
                        f"clear-text-logging drift: attack fragment "
                        f"{fragment!r} survived in raw record.args of "
                        f"Retry-After warning. Rendered: {rendered!r}"
                    )


def test_places_client_module_uses_sanitize_log_arg_on_retry_after() -> None:
    """AST inventory invariant: the ``LOGGER.warning(...,  header)``
    call in ``src/places/client.py`` MUST route ``header`` through
    ``sanitize_log_arg`` (or one of the recognised sanitisers).

    Mirrors the canonical-fix invariant pattern from
    ``test_sentinel_clear_text_logging_drift_utils.py`` /
    ``test_sentinel_path_log_sanitisation_scripts_round3.py`` — a
    regression here means a future contributor unwrapped the
    sanitisation and re-exposed the drift.
    """
    source_path = REPO_ROOT / "src" / "places" / "client.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))

    findings: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        # Match ``LOGGER.warning(...)`` calls.
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "warning"):
            continue
        if not (
            isinstance(func.value, ast.Name)
            and func.value.id == "LOGGER"
        ):
            continue
        if not node.args:
            continue
        fmt_arg = node.args[0]
        if not isinstance(fmt_arg, ast.Constant) or not isinstance(fmt_arg.value, str):
            continue
        if "Retry-After" not in fmt_arg.value:
            continue
        # Found the target LOGGER.warning call. Inspect the
        # interpolated args — each must be a sanitiser call.
        for arg in node.args[1:]:
            if isinstance(arg, ast.Call):
                inner = arg.func
                if isinstance(inner, ast.Name) and inner.id in {
                    "sanitize_log_arg",
                    "sanitize_log_message",
                }:
                    continue
                if (
                    isinstance(inner, ast.Attribute)
                    and inner.attr in {"sanitize_log_arg", "sanitize_log_message", "_sanitize_arg"}
                ):
                    continue
            findings.append(
                f"{source_path.relative_to(REPO_ROOT)}:{node.lineno}: "
                f"LOGGER.warning(...) interpolates raw {ast.dump(arg)} "
                f"in Retry-After header log — must route through "
                f"sanitize_log_arg (sibling drift of "
                f"src/providers/oebb.py Retry-After hardening)."
            )

    if findings:
        rendered = "\n".join(findings)
        pytest.fail(
            "Sibling drift in places/client.py Retry-After log:\n"
            f"{rendered}\n\n"
            "Each upstream-controlled HTTP header value must route "
            "through ``sanitize_log_arg`` so a hostile peer cannot "
            "inject ANSI/BiDi/control characters into operator log "
            "streams. Mirrors ``src/providers/oebb.py`` line 1313-1315 "
            "and the canonical SafeFormatter defence."
        )
