"""Sentinel PoC: ``sanitize_log_message`` sibling-drift closure for the
2026-05-16 ``_SENSITIVE_QUERY_KEYS`` SAML/CSRF/WordPress round.

The 2026-05-16 PR #1531 closed the SAML 2.0 Artifact / RelayState +
bare CSRF/XSRF + WordPress ``_wpnonce`` drift in
``src/utils/http.py:_SENSITIVE_QUERY_KEYS`` (the canonical exact-match
set used by both ``_sanitize_url_for_error`` and
``_strip_sensitive_params``). That round closed the URL-path
redaction surface. BUT — the **parallel codepath** in
``src/utils/logging.py:sanitize_log_message`` (the canonical log
sanitizer, called by ``sanitize_log_arg`` and ``clean_message`` and
``_sanitize_log_detail`` across the codebase) has its OWN regex
alternation sets (``_keys`` for query/JSON param redaction,
``_header_keys`` for ``Header: Value`` redaction) — and those sets
SHARE the drift with ``_SENSITIVE_QUERY_KEYS``.

Pre-fix state inventory (PoC verified):

  * ``samlart`` (SAML 2.0 Artifact) — NOT in ``_keys`` regex, NOT in
    ``_header_keys`` regex. Leaks verbatim through every
    ``sanitize_log_message`` invocation.
  * Bare ``csrf`` (Spring ``_csrf`` normalised) — NOT in ``_keys``,
    NOT in ``_header_keys``. Leaks verbatim. Note that the existing
    ``[a-z0-9_.\\-]*token`` alternation catches the SUFFIXED form
    (``csrf_token``, ``XSRF-TOKEN``) but NOT the bare form.
  * Bare ``xsrf`` (Angular bare form) — NOT in ``_keys``, NOT in
    ``_header_keys``. Leaks verbatim. Same suffix asymmetry as csrf.
  * ``relaystate`` (SAML 2.0 RelayState) — ALREADY covered via the
    bare ``state`` alternation (which matches the ``state``
    substring inside ``RelayState``). ✓ no action needed.
  * ``_wpnonce`` (WordPress nonce) — ALREADY covered via the bare
    ``nonce`` alternation (which matches the ``nonce`` substring
    inside ``_wpnonce``). ✓ no action needed.

So the sibling-drift gap is THREE additional alternations
(``samlart``, ``csrf``, ``xsrf``) that need to be added to BOTH
``_keys`` AND ``_header_keys``.

Threat model
------------

A leaked URL / header / JSON document containing ``SAMLArt=...``,
``_csrf=...``, ``xsrf=...``, ``X-CSRF: ...``, or ``"samlart": ...``
that passes through ``sanitize_log_message`` (the canonical log
sanitizer) lands the credential verbatim in operator log streams.
This is the same threat model as the 2026-05-16 PR #1531 round, but
via a DIFFERENT consuming function.

Why two distinct redaction paths exist in the codebase:

  * ``_sanitize_url_for_error`` / ``_strip_sensitive_params`` (PR
    #1531's targets) parse the URL via ``urlparse`` + ``parse_qsl``
    and redact query params by KEY NAME LOOKUP against
    ``_SENSITIVE_QUERY_KEYS``. This is the structured path: it
    requires a valid URL parsable by ``urlparse``.

  * ``sanitize_log_message`` (this round's target) handles RAW
    LOG TEXT — multi-line tracebacks, JSON snippets embedded in
    error strings, ``Header: Value`` pairs, ``key=value&...``
    fragments embedded in prose, partial URL fragments — anything
    that ``urlparse`` would reject. It uses regex pattern matching
    against the ``_keys`` and ``_header_keys`` alternation sets
    to find ``<key>=<value>`` / ``<key>: <value>`` / ``"<key>":
    "<value>"`` patterns anywhere in the input string.

The two paths cover complementary surface area. Closing only the
URL path (PR #1531) left every log message that didn't pass through
``urlparse`` exposed. This round closes the log-sanitization sibling.

Severity per credential class (same as PR #1531):

  * ``SAMLArt``: HIGH — 5-min ARS-resolvable bearer credential.
  * Bare ``csrf`` / ``xsrf``: MEDIUM-HIGH — session-lifetime replay.

Real-world emission patterns that reach ``sanitize_log_message``:

  * ``log.exception("Request to %s failed: %s", sanitize_log_arg(url),
    sanitize_log_arg(exc))`` — when the exception message embeds a
    URL with sensitive query params.
  * ``log.warning("Cache key collision: %s", sanitize_log_arg(key))``
    — when cache keys include sensitive parameters.
  * GitHub Issue body sanitization (``feed/reporting.py:clean_message``)
    — operator-facing issue bodies carrying request URLs / tracebacks
    with embedded sensitive params.
  * Traceback sanitization (``sanitize_log_message(text,
    strip_control_chars=False)``) — Python tracebacks that include
    HTTP request URL repr() in their frames.
  * Pre-commit hook diagnostic output — when the secret scanner
    finding context (which may include URL fragments) is sanitized
    before display.

Fix
---

Extend BOTH ``_keys`` and ``_header_keys`` regex alternations with
three new entries (``samlart``, ``csrf``, ``xsrf``) — bare
alternations mirroring the existing ``nonce|state`` style. The
regex requires the alternation match to be followed by ``=`` (query
form), ``:`` (header form), or ``": "`` (JSON form), so natural-
language prose containing "csrf" / "xsrf" / "samlart" without a
key=value structure is NOT over-redacted.

Marker: SENTINEL_LOG_SANITIZE_CSRF_SAML_DRIFT.
"""

from __future__ import annotations

import pytest

from src.utils.logging import sanitize_log_arg, sanitize_log_message


SENTINEL_LOG_SANITIZE_CSRF_SAML_DRIFT = (
    "sanitize_log_message _keys/_header_keys regex sibling drift: "
    "samlart/csrf/xsrf alternations missing — leaks in log message "
    "and header redaction paths"
)


# Realistic credential bodies (28+ chars to ensure they're entropy-like
# and would trigger the secret scanner's _HIGH_ENTROPY_RE in committed
# source — testing the sanitization path that would prevent the leaks
# from reaching log output).
_SAMLART_BODY = "AAQAACK4Gj1uFBjQqwbeQk5jeSrXgQAOEYRwsZA1J3GibE5oWyA89uVbiNI"
assert len(_SAMLART_BODY) >= 40
_CSRF_BODY = "abc123def456ghi789jkl012mno345pqr678"
assert len(_CSRF_BODY) >= 28
_XSRF_BODY = "AbCdEfGhIjKlMnOpQrStUvWx1234"
assert len(_XSRF_BODY) >= 24


# ---------------------------------------------------------------------------
# (1) Query-parameter form (``?key=value``) — primary leak surface for
#     URL-bearing log messages that don't pass through urlparse.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url_fragment,credential_body,label",
    [
        (
            "https://sp.example.com/saml/acs?SAMLArt=" + _SAMLART_BODY,
            _SAMLART_BODY,
            "SAML 2.0 Artifact",
        ),
        (
            "https://app.example.com/login?_csrf=" + _CSRF_BODY,
            _CSRF_BODY,
            "Spring Security _csrf",
        ),
        (
            "https://app.example.com/login?csrf=" + _CSRF_BODY,
            _CSRF_BODY,
            "bare csrf",
        ),
        (
            "https://app.example.com/login?CSRF=" + _CSRF_BODY,
            _CSRF_BODY,
            "uppercase CSRF",
        ),
        (
            "https://app.example.com/login?xsrf=" + _XSRF_BODY,
            _XSRF_BODY,
            "bare xsrf",
        ),
        # Standalone key=value (not in URL context — common in
        # traceback frames, exception messages, log prose).
        (
            "Auth failed with samlart=" + _SAMLART_BODY,
            _SAMLART_BODY,
            "standalone samlart=",
        ),
        (
            "Submitted form: csrf=" + _CSRF_BODY,
            _CSRF_BODY,
            "standalone csrf=",
        ),
        (
            "Cookie xsrf=" + _XSRF_BODY,
            _XSRF_BODY,
            "standalone xsrf=",
        ),
    ],
)
def test_sanitize_log_message_redacts_query_form(
    url_fragment: str, credential_body: str, label: str
) -> None:
    """``sanitize_log_message`` must redact ``SAMLArt=...``, ``csrf=...``,
    ``xsrf=...`` (and their underscored / case-shifted variants) when
    they appear in query-parameter / key=value form in raw log text.
    Pre-fix the credentials surface verbatim.
    """
    sanitized = sanitize_log_message(url_fragment)

    assert credential_body not in sanitized, (
        f"{label}: credential leaked verbatim through "
        f"sanitize_log_message. INPUT={url_fragment!r} "
        f"OUTPUT={sanitized!r}. "
        f"({SENTINEL_LOG_SANITIZE_CSRF_SAML_DRIFT})"
    )
    # The redacted form must contain ``***``.
    assert "***" in sanitized, (
        f"{label}: no ``***`` redaction marker present in output "
        f"{sanitized!r}. ({SENTINEL_LOG_SANITIZE_CSRF_SAML_DRIFT})"
    )


# ---------------------------------------------------------------------------
# (2) Header form (``Header: Value``) — the second leak surface,
#     handled by the ``_header_keys`` regex alternation.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "header_line,credential_body,label",
    [
        (
            f"SAMLArt: {_SAMLART_BODY}",
            _SAMLART_BODY,
            "SAML 2.0 Artifact header",
        ),
        (
            f"X-SAMLArt: {_SAMLART_BODY}",
            _SAMLART_BODY,
            "X-prefixed SAMLArt header",
        ),
        (
            f"X-CSRF: {_CSRF_BODY}",
            _CSRF_BODY,
            "X-CSRF (bare; X-CSRF-Token covered via 'token')",
        ),
        (
            f"CSRF: {_CSRF_BODY}",
            _CSRF_BODY,
            "bare CSRF header",
        ),
        (
            f"X-XSRF: {_XSRF_BODY}",
            _XSRF_BODY,
            "X-XSRF (bare; X-XSRF-TOKEN covered via 'token')",
        ),
        (
            f"XSRF: {_XSRF_BODY}",
            _XSRF_BODY,
            "bare XSRF header",
        ),
    ],
)
def test_sanitize_log_message_redacts_header_form(
    header_line: str, credential_body: str, label: str
) -> None:
    """``sanitize_log_message`` must redact ``SAMLArt: ...``,
    ``X-CSRF: ...``, ``XSRF: ...`` (and their bare / X-prefixed
    variants) when they appear in HTTP-Header form in raw log text.
    The ``_header_keys`` regex alternation must include the new
    entries.
    """
    sanitized = sanitize_log_message(header_line)

    assert credential_body not in sanitized, (
        f"{label}: header value leaked verbatim through "
        f"sanitize_log_message. INPUT={header_line!r} "
        f"OUTPUT={sanitized!r}. "
        f"({SENTINEL_LOG_SANITIZE_CSRF_SAML_DRIFT})"
    )
    assert "***" in sanitized, (
        f"{label}: no ``***`` redaction marker in header output "
        f"{sanitized!r}. ({SENTINEL_LOG_SANITIZE_CSRF_SAML_DRIFT})"
    )


# ---------------------------------------------------------------------------
# (3) JSON form (``"key": "value"``) — third leak surface, also gated
#     by ``_keys`` regex (with double-quote and single-quote variants).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "json_fragment,credential_body,label",
    [
        (
            f'{{"SAMLArt": "{_SAMLART_BODY}"}}',
            _SAMLART_BODY,
            "JSON-double-quote samlart",
        ),
        (
            f"{{'samlart': '{_SAMLART_BODY}'}}",
            _SAMLART_BODY,
            "JSON-single-quote samlart",
        ),
        (
            f'{{"csrf": "{_CSRF_BODY}"}}',
            _CSRF_BODY,
            "JSON-double-quote csrf",
        ),
        (
            f'{{"xsrf": "{_XSRF_BODY}"}}',
            _XSRF_BODY,
            "JSON-double-quote xsrf",
        ),
    ],
)
def test_sanitize_log_message_redacts_json_form(
    json_fragment: str, credential_body: str, label: str
) -> None:
    """``sanitize_log_message`` must redact JSON-style ``"key":
    "value"`` and ``'key': 'value'`` patterns when the key matches
    the ``_keys`` regex. This is the third sink alongside query and
    header forms.
    """
    sanitized = sanitize_log_message(json_fragment)

    assert credential_body not in sanitized, (
        f"{label}: JSON value leaked verbatim through "
        f"sanitize_log_message. INPUT={json_fragment!r} "
        f"OUTPUT={sanitized!r}. "
        f"({SENTINEL_LOG_SANITIZE_CSRF_SAML_DRIFT})"
    )


# ---------------------------------------------------------------------------
# (4) Regression: already-covered cases must continue to redact.
#     ``relaystate`` (via ``state`` substring) and ``wpnonce`` (via
#     ``nonce`` substring) were already covered pre-this-round. Plus
#     the broader ``token`` / ``secret`` / ``password`` family.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fragment,credential_body,label",
    [
        # Already covered by existing alternations:
        (
            "?RelayState=" + "AbCdEfGhIjKlMnOpQrStUvWx0123",
            "AbCdEfGhIjKlMnOpQrStUvWx0123",
            "RelayState via state substring",
        ),
        (
            "?_wpnonce=" + "a1b2c3d4e5",
            "a1b2c3d4e5",
            "_wpnonce via nonce substring",
        ),
        (
            "?token=" + "AbCdEfGhIjKlMnOpQrStUvWx",
            "AbCdEfGhIjKlMnOpQrStUvWx",
            "token (canonical entry)",
        ),
        (
            "?csrf_token=" + _CSRF_BODY,
            _CSRF_BODY,
            "csrf_token via token substring",
        ),
        (
            "?XSRF-TOKEN=" + _XSRF_BODY,
            _XSRF_BODY,
            "XSRF-TOKEN via [a-z0-9_.\\-]*token",
        ),
        (
            "?password=" + "MyP@ssw0rd1234",
            "MyP@ssw0rd1234",
            "password (canonical entry)",
        ),
    ],
)
def test_existing_redactions_still_work(
    fragment: str, credential_body: str, label: str
) -> None:
    """Adding new alternations must NOT break existing redactions.
    Every entry that was redacted pre-this-round must continue to
    redact. Regression guard against accidental removal during the
    alternation-extension diff.
    """
    sanitized = sanitize_log_message(fragment)

    assert credential_body not in sanitized, (
        f"REGRESSION: previously-redacted {label} no longer redacted. "
        f"INPUT={fragment!r} OUTPUT={sanitized!r}. "
        f"({SENTINEL_LOG_SANITIZE_CSRF_SAML_DRIFT})"
    )


# ---------------------------------------------------------------------------
# (5) Negative cases: ensure the new alternations do NOT over-redact
#     natural-language prose containing the trigger keywords without
#     a key=value / Header: Value / JSON structure.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        # Natural-language mention without key=value structure.
        "We need to add CSRF protection to the form.",
        "XSRF vulnerability discovered in module X.",
        "Audit the SAMLArt resolver for replay vulnerabilities.",
        "The csrf token expires after 30 minutes.",
        # Mention with a different separator (no = or :).
        "csrf -> verified ok",
        "xsrf rotation policy: daily",
        # Inside an English sentence.
        "The previous round closed the csrf attack surface.",
        # Words containing the substrings but NOT followed by =/:.
        "Don't get caught up in the cross-site issue.",  # benign
        "saturday morning standups",  # contains 'art' — but NOT 'samlart'
    ],
)
def test_natural_language_not_over_redacted(text: str) -> None:
    """The regex alternations require the trigger keyword to be
    followed by ``=`` (query form), ``:`` (header form), or ``": "``
    (JSON form). Natural-language prose without these structural
    markers must NOT be redacted.
    """
    sanitized = sanitize_log_message(text)

    # The output should match the input modulo whitespace / control-
    # char stripping. Specifically the original word content must be
    # preserved (no ``***`` substitution for non-key=value mentions).
    # We don't require byte-for-byte equality because
    # ``sanitize_log_message`` also strips control chars / BiDi marks,
    # but ``***`` must NOT be present (no over-redaction triggered).
    assert "***" not in sanitized, (
        f"OVER-REDACTION: natural-language text was unnecessarily "
        f"redacted. INPUT={text!r} OUTPUT={sanitized!r}. "
        f"({SENTINEL_LOG_SANITIZE_CSRF_SAML_DRIFT})"
    )


# ---------------------------------------------------------------------------
# (6) sanitize_log_arg wrapper PoC: the canonical entry point used by
#     every ``log.warning("...: %s", sanitize_log_arg(value))`` callsite
#     across the codebase must benefit from the alternation extension.
# ---------------------------------------------------------------------------


def test_sanitize_log_arg_redacts_samlart() -> None:
    """``sanitize_log_arg`` delegates to ``sanitize_log_message`` so
    the alternation extension propagates to every callsite using the
    wrapper. The wrapper is the dominant canonical entry point —
    every ``log.warning(...)`` call in the codebase uses it for
    user/upstream/env-controlled args."""
    url = "https://sp.example.com/saml/acs?SAMLArt=" + _SAMLART_BODY
    sanitized = sanitize_log_arg(url)
    assert _SAMLART_BODY not in sanitized, (
        f"sanitize_log_arg leaked SAML Artifact: INPUT={url!r} "
        f"OUTPUT={sanitized!r}. ({SENTINEL_LOG_SANITIZE_CSRF_SAML_DRIFT})"
    )


def test_sanitize_log_arg_redacts_csrf() -> None:
    """The same propagation invariant for bare csrf."""
    url = "Failed: form payload included _csrf=" + _CSRF_BODY
    sanitized = sanitize_log_arg(url)
    assert _CSRF_BODY not in sanitized, (
        f"sanitize_log_arg leaked CSRF token: INPUT={url!r} "
        f"OUTPUT={sanitized!r}. ({SENTINEL_LOG_SANITIZE_CSRF_SAML_DRIFT})"
    )


def test_sanitize_log_arg_redacts_xsrf() -> None:
    """The same propagation invariant for bare xsrf."""
    url = "Cookie: xsrf=" + _XSRF_BODY
    sanitized = sanitize_log_arg(url)
    assert _XSRF_BODY not in sanitized, (
        f"sanitize_log_arg leaked XSRF token: INPUT={url!r} "
        f"OUTPUT={sanitized!r}. ({SENTINEL_LOG_SANITIZE_CSRF_SAML_DRIFT})"
    )


# ---------------------------------------------------------------------------
# (7) Combined SAML 2.0 SSO PoC: real-world SP-initiated SSO error log
#     line carrying SAMLArt + RelayState side by side. Both must be
#     redacted (SAMLArt via the new alternation, RelayState via the
#     existing ``state`` substring).
# ---------------------------------------------------------------------------


def test_combined_samlart_relaystate_redacted_in_log() -> None:
    """A real-world SP-initiated SSO error log line carries both
    ``SAMLArt`` and ``RelayState`` query parameters. Both must be
    redacted simultaneously by ``sanitize_log_message`` — SAMLArt via
    this round's new alternation, RelayState via the existing
    ``state`` substring."""
    body = "https%3A%2F%2Fapp.example.com%2Fdashboard"
    line = (
        f"ACS POST failed for "
        f"https://sp.example.com/saml/acs?SAMLArt={_SAMLART_BODY}"
        f"&RelayState={body}"
    )
    sanitized = sanitize_log_message(line)

    assert _SAMLART_BODY not in sanitized, (
        f"SAMLArt leaked verbatim: OUTPUT={sanitized!r}. "
        f"({SENTINEL_LOG_SANITIZE_CSRF_SAML_DRIFT})"
    )
    assert body not in sanitized, (
        f"RelayState leaked verbatim: OUTPUT={sanitized!r}. "
        f"({SENTINEL_LOG_SANITIZE_CSRF_SAML_DRIFT})"
    )
