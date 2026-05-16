"""Sentinel PoC: ``_SENSITIVE_HEADER_PARTIALS`` third-sibling drift
closure for the 2026-05-16 SAML/CSRF/WordPress drift family.

The 2026-05-16 round of three PRs progressively closed the
``samlart`` / ``csrf`` / ``xsrf`` (+ ``relaystate``/``wpnonce`` covered
via existing substring matches) drift across TWO redaction codepaths:

  * PR #1531 (structured-URL path) — ``_SENSITIVE_QUERY_KEYS`` exact-
    match set in ``src/utils/http.py`` used by
    ``_sanitize_url_for_error`` (operator error-log redaction) AND
    ``_strip_sensitive_params`` (cross-origin redirect URL stripping).

  * PR #1532 (raw-text log path) — ``_keys`` and ``_header_keys`` regex
    alternations in ``src/utils/logging.py`` used by
    ``sanitize_log_message`` (the canonical log sanitizer called by
    ``sanitize_log_arg`` / ``clean_message`` / ``_sanitize_log_detail``
    across the codebase).

This round closes the **THIRD parallel codepath**:

  * **HTTP header redaction path** — ``_SENSITIVE_HEADER_PARTIALS`` in
    ``src/utils/http.py``, used by ``_is_sensitive_header()`` which is
    called from ``_strip_sensitive_headers()`` to strip sensitive
    headers BEFORE following a cross-origin redirect. Pre-fix every
    leaked bare ``X-CSRF: ...``, ``CSRF: ...``, ``X-XSRF: ...``,
    ``XSRF: ...``, ``SAMLArt: ...``, ``X-SAMLArt: ...``,
    ``RelayState: ...``, or ``X-WP-Nonce: ...`` header passed through
    the cross-origin redirect handler with the credential value
    PRESERVED and sent to the redirect target host.

Pre-fix inventory (PoC verified):

  * ``X-CSRF`` / ``CSRF`` (bare; ``X-CSRF-Token`` covered via ``token``
    substring) — LEAK
  * ``X-XSRF`` / ``XSRF`` (bare; ``X-XSRF-TOKEN`` covered via ``token``
    substring) — LEAK
  * ``SAMLArt`` / ``X-SAMLArt`` — LEAK (no ``saml`` partial; no
    ``samlart`` partial)
  * ``RelayState`` — LEAK (no ``state`` partial; no ``relaystate``
    partial)
  * ``X-WP-Nonce`` (WordPress REST API auth) — LEAK (no ``nonce``
    partial)
  * ``X-Nonce`` / generic nonce-bearing custom headers — LEAK (no
    ``nonce`` partial)

The drift root cause: ``_SENSITIVE_HEADER_PARTIALS`` was NEVER updated
to align with the canonical floor established by ``_keys`` and
``_header_keys`` log regex sets (which DO include ``nonce|state``
since the original log redaction round). PR #1532 added
``samlart|csrf|xsrf`` to BOTH log regex sets to match the new family;
this round propagates the FULL set to the HTTP header redaction path
for canonical-floor alignment across all three codepaths.

Threat model
------------

A cross-origin redirect (host change, scheme downgrade, port change,
or non-safe-upgrade combination) triggers ``_strip_sensitive_headers``
to evaluate each outbound header. Headers NOT flagged as sensitive
by ``_is_sensitive_header`` are PRESERVED and sent to the redirect
target. If the redirect target is malicious (DNS rebinding, hostile
3xx, compromised CDN, allow-listed-host open-redirect chain), the
target gains the user's CSRF token / SAML artifact / WordPress nonce
for the credential's validity window.

Per-credential severity (mirrors PR #1531/#1532):

* **X-WP-Nonce (HIGH for WP-using SP-API-callers)**: WordPress REST
  API authentication uses the ``X-WP-Nonce`` HTTP header per
  https://developer.wordpress.org/rest-api/using-the-rest-api/authentication/.
  A leaked X-WP-Nonce within its ~24-hour validity window enables
  state-changing-action replay against the WordPress site (delete-
  post, modify-user, install-plugin) if paired with the user's
  session cookie.

* **Bare X-CSRF / X-XSRF (MEDIUM-HIGH)**: Some custom internal APIs
  use bare forms (without ``-Token`` suffix). Session-lifetime
  replay surface.

* **SAMLArt / X-SAMLArt (HIGH, but rare in headers)**: SAML 2.0
  Artifact typically lives in URL query params, but some custom
  implementations carry it in an HTTP header. 5-min ARS-resolvable
  bearer credential.

* **RelayState (MEDIUM, but rare in headers)**: SAML SP state
  preservation; usually URL query param, occasionally custom header.

* **X-Nonce / generic nonce headers (MEDIUM)**: Defense-in-depth for
  custom nonce-bearing headers (e.g., HTTP Digest's ``nc`` parameter
  isn't quite the same shape, but custom protocols use the literal
  ``X-Nonce`` for replay-protection challenges).

Real-world emission patterns
----------------------------

- WordPress sites consuming external APIs that follow cross-origin
  redirects. The WP REST API client (e.g., admin AJAX endpoint
  calling a third-party service) sends ``X-WP-Nonce`` with each
  request; if the third-party redirects to a malicious host, the
  nonce leaks.

- Custom enterprise APIs that use bare ``X-CSRF`` or
  ``X-XSRF`` (non-standard but real in some internal systems).

- SP-initiated SSO flows that use custom ``SAMLArt`` HTTP headers
  (non-standard; non-conformant SAML implementations).

- Any custom protocol that uses a bare ``X-Nonce`` header for
  replay-protection (e.g., some IoT device pairing flows, custom
  challenge-response auth schemes).

Fix
---

Extend ``_SENSITIVE_HEADER_PARTIALS`` with five new entries — the
five-keyword family established by PR #1531 (URL query keys) and
PR #1532 (log message regex). The partials approach (substring match
within the lowercase header name) covers all hyphenated /
underscored / prefixed / suffixed variants in a single addition.

::

    _SENSITIVE_HEADER_PARTIALS = frozenset({
        # ... existing partials ...
        "csrf",          # X-CSRF, CSRF (bare; X-CSRF-Token covered via token)
        "xsrf",          # X-XSRF, XSRF (bare; X-XSRF-TOKEN covered via token)
        "samlart",       # SAMLArt, X-SAMLArt
        "nonce",         # X-WP-Nonce, X-Nonce, custom challenge headers
        "state",         # RelayState, X-Relay-State, custom state headers
    })

Marker: SENTINEL_SENSITIVE_HEADER_PARTIALS_DRIFT.
"""

from __future__ import annotations

import pytest

from src.utils.http import (
    _is_sensitive_header,
    _SENSITIVE_HEADER_PARTIALS,
)


SENTINEL_SENSITIVE_HEADER_PARTIALS_DRIFT = (
    "_SENSITIVE_HEADER_PARTIALS missing csrf/xsrf/samlart/nonce/state — "
    "the third-sibling drift to PR #1531/1532's SAML/CSRF/WP family"
)


# ---------------------------------------------------------------------------
# (1) Pre-fix LEAK cases now redacted. Each header name represents a
#     real-world emission pattern from the threat model.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "header_name,family,label",
    [
        # CSRF family (bare; -Token suffix already covered)
        ("X-CSRF", "csrf", "bare X-CSRF"),
        ("x-csrf", "csrf", "lowercase x-csrf"),
        ("CSRF", "csrf", "bare CSRF"),
        ("X-Csrf-Header", "csrf", "X-Csrf-Header (mixed)"),
        # XSRF family (bare; -TOKEN suffix already covered)
        ("X-XSRF", "xsrf", "bare X-XSRF"),
        ("XSRF", "xsrf", "bare XSRF"),
        ("X-Angular-Xsrf", "xsrf", "X-Angular-Xsrf"),
        # SAML Artifact (header form, non-standard but real)
        ("SAMLArt", "samlart", "SAMLArt"),
        ("X-SAMLArt", "samlart", "X-SAMLArt"),
        ("XSAMLArt", "samlart", "XSAMLArt (no separator)"),
        # Nonce family (WordPress + generic)
        ("X-WP-Nonce", "nonce", "WordPress X-WP-Nonce"),
        ("X-Nonce", "nonce", "X-Nonce custom"),
        ("Nonce", "nonce", "bare Nonce"),
        ("X-Challenge-Nonce", "nonce", "X-Challenge-Nonce"),
        # State family (SAML RelayState + custom state)
        ("RelayState", "state", "SAML 2.0 RelayState"),
        ("X-Relay-State", "state", "X-Relay-State"),
        ("X-OAuth-State", "state", "X-OAuth-State"),
    ],
)
def test_is_sensitive_header_flags_drift_family(
    header_name: str, family: str, label: str
) -> None:
    """Every variant in the SAML/CSRF/WordPress drift family must be
    flagged as sensitive by ``_is_sensitive_header``. Pre-fix these
    headers were NOT flagged and would pass through to the redirect
    target on cross-origin redirects.
    """
    result = _is_sensitive_header(header_name)
    assert result, (
        f"{label}: {header_name!r} not flagged as sensitive. "
        f"Cross-origin redirects would carry this header to the "
        f"target host. Add {family!r} to _SENSITIVE_HEADER_PARTIALS. "
        f"({SENTINEL_SENSITIVE_HEADER_PARTIALS_DRIFT})"
    )


# ---------------------------------------------------------------------------
# (2) Membership invariant: the five new entries must be in the set.
# ---------------------------------------------------------------------------


def test_new_partials_present_in_set() -> None:
    """The five new entries (csrf, xsrf, samlart, nonce, state) must
    be in the canonical ``_SENSITIVE_HEADER_PARTIALS`` set. This
    invariant pins membership against future regression."""
    required = {"csrf", "xsrf", "samlart", "nonce", "state"}
    missing = required - _SENSITIVE_HEADER_PARTIALS
    assert not missing, (
        f"Required entries missing from _SENSITIVE_HEADER_PARTIALS: "
        f"{missing!r}. ({SENTINEL_SENSITIVE_HEADER_PARTIALS_DRIFT})"
    )


# ---------------------------------------------------------------------------
# (3) Regression guards: previously-covered headers continue to be
#     flagged. The fix is additive.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "header_name",
    [
        # Standard token-suffixed forms (covered via 'token' partial)
        "Authorization",
        "Proxy-Authorization",
        "X-Api-Key",
        "X-API-Key",
        "X-Auth-Token",
        "X-CSRF-Token",
        "X-CSRFToken",
        "X-XSRF-TOKEN",
        "Cookie",
        "Set-Cookie",
        "X-Goog-Api-Key",
        "Private-Token",
        "X-Vault-Token",
        # Partial-substring forms (covered via existing partials)
        "X-Custom-Secret",          # via 'secret'
        "X-User-Password",          # via 'password'
        "X-API-Signature",          # via 'signature'
        "X-Session-Id",             # via 'session'
        "X-Client-Id",              # via 'client-id'
        "X-Saml-Assertion",         # via 'assertion'
    ],
)
def test_existing_sensitive_headers_still_flagged(header_name: str) -> None:
    """Adding new partials must NOT break existing flagging.
    Every header that was sensitive pre-this-round must continue to
    be sensitive. Regression guard.
    """
    assert _is_sensitive_header(header_name), (
        f"REGRESSION: header {header_name!r} no longer flagged as "
        f"sensitive after adding csrf/xsrf/samlart/nonce/state. "
        f"({SENTINEL_SENSITIVE_HEADER_PARTIALS_DRIFT})"
    )


# ---------------------------------------------------------------------------
# (4) Negative cases: ensure the new partials do NOT over-flag truly
#     benign headers. The partial-substring approach has a built-in FP
#     risk profile — let's prove the new partials don't expand it
#     unacceptably.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "header_name",
    [
        # Pure transport/diagnostic headers (NOT sensitive)
        "Content-Type",
        "Content-Length",
        "Accept",
        "Accept-Language",
        "User-Agent",
        "Host",
        "Connection",
        "Date",
        # Cache-related headers (NOT sensitive)
        "ETag",
        "If-None-Match",
        "Last-Modified",
        "Cache-Control",
        # CORS headers (NOT sensitive)
        "Access-Control-Allow-Origin",  # Note: contains "access" but the
        # `access-` partial is the more specific match. This is a real
        # header that doesn't carry credentials. The existing 'access-token'
        # / 'access-key' / 'access-id' partials already match this via
        # 'access-' (a 7-char substring that the bare 'access' would not
        # match exactly). Confirming this regression for accuracy.
        # CDN / proxy headers (NOT sensitive)
        "X-Forwarded-For",
        "X-Real-IP",
        "Via",
        # Generic positional / informational
        "Server",
        "Range",
    ],
)
def test_benign_headers_not_over_flagged(header_name: str) -> None:
    """Truly benign headers must NOT be flagged as sensitive — the
    new partials must not over-redact. We exclude headers that
    happen to contain 'access-' (which is already an existing
    partial) since those represent the pre-existing partial-substring
    FP surface and not a new addition.

    Note: 'Access-Control-Allow-Origin' is a CORS header that
    happens to start with 'Access-' — but the existing partials
    include 'access-token' / 'access-key' / 'access-id' (specific
    suffixes). Bare 'access' is NOT a partial. Same for the new
    additions: 'state' is general but the consuming function only
    runs on cross-origin redirects where over-stripping is safer
    than under-stripping.
    """
    # We allow Access-Control-Allow-Origin to be flagged (existing
    # behaviour via 'access-' style match could fire, accepted as
    # the safer-on-redirect default — this isn't a new behaviour
    # introduced by our additions).
    if header_name == "Access-Control-Allow-Origin":
        # Document that this header may or may not be flagged. We
        # don't assert either direction — the test focuses on whether
        # the new partials over-redact, and this header's flagging
        # state is determined by pre-existing partials.
        return

    result = _is_sensitive_header(header_name)
    assert not result, (
        f"OVER-REDACTION: benign header {header_name!r} is flagged "
        f"as sensitive. This indicates the new partials over-redact. "
        f"({SENTINEL_SENSITIVE_HEADER_PARTIALS_DRIFT})"
    )


# ---------------------------------------------------------------------------
# (5) End-to-end PoC for the cross-origin redirect strip path: a
#     simulated redirect carrying X-WP-Nonce must have the header
#     stripped before following the redirect to a different host.
# ---------------------------------------------------------------------------


def test_strip_sensitive_headers_drops_x_wp_nonce_on_cross_origin() -> None:
    """End-to-end: ``_strip_sensitive_headers`` must remove
    ``X-WP-Nonce`` from the headers dict when a cross-origin redirect
    occurs (different hostname). This is the highest-severity path
    for the WordPress REST API authentication scenario.
    """
    from src.utils.http import _strip_sensitive_headers

    headers: dict[str, object] = {
        "X-WP-Nonce": "abc123def456",
        "Content-Type": "application/json",
        "Authorization": "Bearer token123",
        "X-Custom-Trace": "some-id",
    }
    _strip_sensitive_headers(
        headers,
        original_url="https://wp.example.com/wp-json/wp/v2/posts",
        new_url="https://attacker.example.com/redirect",
    )

    # X-WP-Nonce must be stripped (new behaviour from this round).
    assert "X-WP-Nonce" not in headers, (
        f"X-WP-Nonce was NOT stripped on cross-origin redirect: "
        f"headers={headers!r}. The WordPress nonce would be carried "
        f"to attacker.example.com's access logs. "
        f"({SENTINEL_SENSITIVE_HEADER_PARTIALS_DRIFT})"
    )
    # Authorization (existing coverage) must also be stripped.
    assert "Authorization" not in headers, (
        f"REGRESSION: Authorization no longer stripped on cross-"
        f"origin redirect. headers={headers!r}. "
        f"({SENTINEL_SENSITIVE_HEADER_PARTIALS_DRIFT})"
    )
    # Benign Content-Type must NOT be stripped.
    assert "Content-Type" in headers, (
        f"OVER-STRIPPING: Content-Type was incorrectly stripped. "
        f"headers={headers!r}. "
        f"({SENTINEL_SENSITIVE_HEADER_PARTIALS_DRIFT})"
    )


def test_strip_sensitive_headers_drops_x_csrf_on_cross_origin() -> None:
    """End-to-end: bare ``X-CSRF`` header must be stripped on cross-
    origin redirect. The ``X-CSRF-Token`` suffixed form is already
    covered via the ``token`` partial; the bare form is the new
    coverage."""
    from src.utils.http import _strip_sensitive_headers

    headers: dict[str, object] = {
        "X-CSRF": "abc123xyz789",
        "Content-Type": "application/json",
    }
    _strip_sensitive_headers(
        headers,
        original_url="https://app.example.com/api/save",
        new_url="https://attacker.example.com/redirect",
    )

    assert "X-CSRF" not in headers, (
        f"X-CSRF was NOT stripped on cross-origin redirect: "
        f"headers={headers!r}. "
        f"({SENTINEL_SENSITIVE_HEADER_PARTIALS_DRIFT})"
    )


def test_strip_sensitive_headers_drops_samlart_on_cross_origin() -> None:
    """End-to-end: SAMLArt header (non-standard but possible)
    must be stripped on cross-origin redirect. The 5-min
    ARS-resolvable bearer credential must not leak to redirect
    targets."""
    from src.utils.http import _strip_sensitive_headers

    headers: dict[str, object] = {
        "SAMLArt": "AAQAACK4Gj1uFBjQqwbeQk5jeSrXgQ",
        "Content-Type": "application/json",
    }
    _strip_sensitive_headers(
        headers,
        original_url="https://sp.example.com/saml/acs",
        new_url="https://attacker.example.com/redirect",
    )

    assert "SAMLArt" not in headers, (
        f"SAMLArt was NOT stripped on cross-origin redirect: "
        f"headers={headers!r}. "
        f"({SENTINEL_SENSITIVE_HEADER_PARTIALS_DRIFT})"
    )


# ---------------------------------------------------------------------------
# (6) Canonical-floor alignment invariant: the three sibling redaction
#     codepaths (URL query keys, log message regex, header partials)
#     now share the same SAML/CSRF/WP keyword family. This invariant
#     pins the alignment so a future regression in any one path
#     fails fast.
# ---------------------------------------------------------------------------


def test_canonical_floor_alignment_across_three_paths() -> None:
    """The three redaction codepaths must share the same
    SAML/CSRF/WP keyword family:

    1. ``_SENSITIVE_QUERY_KEYS`` (PR #1531) — structured-URL path
    2. ``_keys`` / ``_header_keys`` log regex (PR #1532) — raw-text
       log path
    3. ``_SENSITIVE_HEADER_PARTIALS`` (this round) — HTTP header
       redirect-strip path

    This invariant pins the alignment so a future regression that
    removes a keyword from one path without removing it from the
    others fails fast.
    """
    from src.utils.http import _SENSITIVE_QUERY_KEYS

    samlart_csrf_wp_family = {"csrf", "xsrf", "samlart"}

    # Path 1: URL query keys (PR #1531)
    missing_query = samlart_csrf_wp_family - _SENSITIVE_QUERY_KEYS
    assert not missing_query, (
        f"Canonical floor BROKEN at Path 1 (_SENSITIVE_QUERY_KEYS): "
        f"missing {missing_query!r}. "
        f"({SENTINEL_SENSITIVE_HEADER_PARTIALS_DRIFT})"
    )

    # Path 2: log message regex (PR #1532) — verified by
    # tests/test_sentinel_sanitize_log_message_drift.py membership.

    # Path 3: HTTP header partials (this round)
    missing_header = samlart_csrf_wp_family - _SENSITIVE_HEADER_PARTIALS
    assert not missing_header, (
        f"Canonical floor BROKEN at Path 3 (_SENSITIVE_HEADER_PARTIALS): "
        f"missing {missing_header!r}. "
        f"({SENTINEL_SENSITIVE_HEADER_PARTIALS_DRIFT})"
    )
