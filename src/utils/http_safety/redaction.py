"""Redaction of secrets in URLs, headers, exception texts and redirect auth.

Moved verbatim from ``src/utils/http.py`` (700-line rule, 2026-11-02);
import from ``src.utils.http``, which re-exports every name.
"""

from __future__ import annotations

import re
from typing import Any, cast
from collections.abc import Mapping, MutableMapping
from urllib.parse import parse_qsl, urlencode, urlparse

import requests

from ..logging import sanitize_log_message
from .limits import MAX_URL_LENGTH

def _normalize_key(key: str) -> str:
    """Normalize key for loose matching (lowercase, no hyphens/underscores)."""
    # Security: Use a stricter normalization (strip everything except a-z0-9)
    # to catch variations like "api.key", "api key", "Client.ID", etc.
    return re.sub(r"[^a-z0-9]", "", key.lower())


# Regex to detect credentials in URLs that might be missed by urlparse
# (e.g. missing ``//``, or a JDBC outer-scheme prefix that hides the inner
# credentialled URL from ``urlparse``).
#
# Matches both ``scheme:user:pass@host`` (malformed, no ``//``) and the
# canonical ``scheme://user:pass@host`` shape. The scheme alternation
# covers:
#
# * ``https?|ftp`` - the legacy HTTP / FTP coverage. ``[^/\s]+`` allows
#   user-only forms (``https:user@host``) so the existing
#   ``test_sanitize_url_missing_slash_group`` invariant holds.
# * The 13+ database / message-broker / mail schemes from the
#   2026-05-16 ``Database Connection String`` secret-scanner round
#   (PostgreSQL / MySQL / MariaDB / MongoDB / MongoDB+SRV / Redis /
#   AMQP / AMQPS / Kafka / ClickHouse / Cassandra / ElasticSearch /
#   SMTP / SMTPS). Without these, a malformed URI like
#   ``postgres:admin:supersecret@db.example.com`` slipped through
#   ``_sanitize_url_for_error``: ``urlparse`` treats ``postgres`` as
#   the scheme and the rest as a path (no credentials extracted), and
#   the scheme alternation rejected the scheme literal.
# * The LDAP / SSH / SFTP / SMB / CIFS adjacent families - directory
#   service / shell / file-share schemes that also carry the
#   ``user:pass@host`` credential shape in real-world configurations.
# * Optional ``jdbc:`` outer prefix - JDBC connection strings wrap an
#   inner scheme (``jdbc:postgresql://admin:secret@host``) which
#   ``urlparse`` mishandles entirely (treats ``jdbc`` as the scheme
#   and the rest as an opaque path).
#
# Schemes NOT in the alternation (e.g. ``mailto``, ``urn``, ``file``,
# ``tel``) are NOT matched, so benign ``mailto:user@host`` patterns and
# scheme mentions in prose are preserved verbatim.
_URL_AUTH_RE = re.compile(
    r"^(?P<scheme>(?:jdbc:)?"
    r"(?:https?|ftp|"
    r"postgres(?:ql)?|mysql|mariadb|"
    r"mongodb(?:\+srv)?|redis|"
    r"amqp|amqps|kafka|clickhouse|cassandra|elasticsearch|"
    r"smtp|smtps|"
    r"ldap|ldaps|ssh|sftp|smb|cifs"
    r")):(?P<slash>//)?(?P<auth>[^/\s]+)@",
    re.IGNORECASE,
)

# Keys in query parameters that should be redacted in logs
# We store them normalized (lowercase, no separators) to catch variations
# like x-api-key, x_api_key, X-Api-Key, etc.
_SENSITIVE_QUERY_KEYS = frozenset({
    "accessid",
    "token",
    "key",
    "apikey",
    "password",
    "secret",
    "passphrase",
    "authorization",
    "auth",
    "clientsecret",
    "clientid",
    "accesstoken",
    "refreshtoken",
    "idtoken",
    "code",
    "sig",
    "signature",
    "session",
    "sessionid",
    "cookie",
    "sid",
    "ticket",
    # Additional common sensitive keys
    "jwt",
    "bearertoken",
    "authtoken",
    "jsessionid",
    "phpsessid",
    "aspnetsessionid",
    "cfduid",
    "tenant",
    "tenantid",
    "subscription",
    "subscriptionid",
    "oid",
    "objectid",
    "dsn",
    "otp",
    "pass",
    "pwd",
    "userpass",
    "glpat",
    "ghp",
    "codechallenge",
    "codeverifier",
    "xapikey",
    "ocpapimsubscriptionkey",
    "subscriptionkey",
    # OAuth 2.0 / OIDC / SAML critical parameters
    "state",
    "nonce",
    "clientassertion",
    "clientassertiontype",
    "samlrequest",
    "samlresponse",
    # OAuth 2.0 Device Authorization Grant (RFC 8628). `device_code` is a
    # bearer-style secret that the client polls with; `user_code` is short-lived
    # but still pairs the user with an in-flight grant. Neither is caught by
    # the substring list (no "token"/"secret"/etc. in the normalized form),
    # so they need an exact entry here.
    "devicecode",
    "usercode",
    # Additional sensitive tokens
    "bearer",
    # AWS and other cloud tokens
    "xauthtoken",
    "xamzsecuritytoken",
    "xamzsignature",
    "xamzcredential",
    # Security: SAML 2.0 query parameters per OASIS SAML 2.0 §3.4.4 /
    # §3.6.4. These names are NOT substrings of any entry in
    # ``_SENSITIVE_KEY_SUBSTRINGS`` below, so without explicit
    # exact-match entries here they leak verbatim across both
    # ``_sanitize_url_for_error`` (operator error-log streams) AND
    # ``_strip_sensitive_params`` (URLs carried through cross-origin
    # redirect chains to potentially-malicious target hosts):
    #   * ``samlart`` — SAML 2.0 Artifact (HTTP-Artifact binding).
    #     A 5-minute-validity bearer credential resolvable to the
    #     full ``SAMLResponse`` via the IdP's Artifact Resolution
    #     Service. Leak = full user authentication on every
    #     IdP-trusted SP for the assertion's lifetime (typically 1h).
    #   * ``relaystate`` — SAML 2.0 SP-supplied state preservation
    #     string. Often contains the post-auth landing URL or
    #     serialised session context. Context leak / targeted-
    #     phishing reconnaissance.
    "samlart",
    "relaystate",
    # Security: CSRF / XSRF token names whose bare normalised form
    # (``csrf`` / ``xsrf``) is NOT a substring of any entry in
    # ``_SENSITIVE_KEY_SUBSTRINGS`` (the ``token`` substring catches
    # ``csrf_token`` / ``csrfmiddlewaretoken`` but not bare ``csrf``
    # or Spring Security's ``_csrf``). The canonical leak shapes:
    #   * ``_csrf`` — Spring Security's GET-based CSRF protection
    #     (param name normalises to bare ``csrf``).
    #   * ``xsrf`` / ``XSRF-TOKEN`` — Angular's default XSRF cookie /
    #     header name (the bare ``xsrf`` form appears in some legacy
    #     AJAX bootstrap flows). ``XSRF-TOKEN`` normalises to
    #     ``xsrftoken`` which matches via the ``token`` substring
    #     ALREADY, so only the bare ``xsrf`` form needs explicit
    #     coverage here.
    # Replayable within session lifetime (minutes to hours,
    # framework- and config-dependent). Enables state-changing-
    # action replay if the attacker also has session cookie access.
    "csrf",
    "xsrf",
    # Security: WordPress nonce protection. ``_wpnonce`` (and the
    # rare ``wpnonce`` variant) carries WP's per-action nonce in GET
    # parameters for state-changing actions (delete-post, install-
    # plugin, modify-user). Normalised form is ``wpnonce`` — which
    # doesn't contain any substring match, so the bare form needs an
    # explicit exact-match entry. Validity: typically 24 hours (WP's
    # default ``DAY_IN_SECONDS / 2`` lifetime). A leaked
    # ``_wpnonce`` within the window enables arbitrary WP action
    # replay if the attacker also has session cookie access.
    "wpnonce",
})

# High-risk substrings that trigger redaction even if the key isn't an exact match in _SENSITIVE_QUERY_KEYS.
# Normalized keys containing these substrings will be redacted in error messages.
_SENSITIVE_KEY_SUBSTRINGS = frozenset({
    "token",
    "secret",
    "password",
    "credential",
    "passphrase",
    "apikey",
    "accesskey",
    "privatekey",
    "signature",
    "email",
    "webhook",
    "glpat",
    # SAML 2.0 / JWT Bearer Authorization Grant (RFC 7521/7522/7523):
    # the `assertion` parameter carries a signed identity assertion (SAML XML
    # or JWT). `client_assertion` is already an exact match above, but plain
    # `assertion`, `saml_assertion`, etc. would otherwise slip past redaction.
    "assertion",
    # Additional broad matching
    "session",
    "cookie",
    "clientid",
    "clientsecret",
    "authorization",
})

# Headers that must be stripped on cross-origin redirects or scheme downgrades
_SENSITIVE_HEADERS = frozenset({
    "Authorization",
    "Proxy-Authorization",
    "X-Goog-Api-Key",
    "X-Api-Key",
    "X-Auth-Token",
    "Private-Token",
    "Cookie",
    "Set-Cookie",
    "Ocp-Apim-Subscription-Key",
    "X-Amz-Security-Token",
    "X-Gitlab-Token",
    "X-GitHub-Token",
    "X-Vault-Token",
    "X-Sentry-Token",
    "DD-API-KEY",
    "X-Figma-Token",
    "X-Plex-Token",
    "X-Shopify-Access-Token",
    "X-Slack-Token",
    "X-HubSpot-API-Key",
    "X-Postmark-Server-Token",
    "X-Postmark-Account-Token",
    "X-RapidAPI-Key",
    "X-Service-Token",
    "X-Access-Token",
    "X-CSRF-Token",
    "X-CSRFToken",
    "X-XSRF-TOKEN",
})

# Normalized (lowercase) set of sensitive headers for case-insensitive matching
_SENSITIVE_HEADERS_LOWER = frozenset(h.lower() for h in _SENSITIVE_HEADERS)

# Partial matches for dynamic sensitive header detection (normalized to lowercase)
_SENSITIVE_HEADER_PARTIALS = frozenset({
    "token",
    "key",
    "secret",
    "password",
    "passphrase",
    "credential",
    "signature",
    "session",
    "cookie",
    "auth-",
    "authorization",
    "client-id",
    "client-secret",
    "access-token",
    "access-key",
    "access-id",
    # SAML/JWT bearer assertions are sometimes carried in headers (e.g.
    # `Saml-Assertion`, `X-Subject-Assertion`); strip on cross-origin redirect.
    "assertion",
    # Security (third-sibling-drift closure of PR #1531
    # ``_SENSITIVE_QUERY_KEYS`` + PR #1532 ``_keys``/``_header_keys``
    # log regex extensions): align the HTTP-header redaction path's
    # canonical floor with the URL-query-param and log-message paths
    # for the SAML 2.0 / CSRF / WordPress credential family.
    #
    # The token-suffixed forms (``X-CSRF-Token``, ``X-XSRF-TOKEN``,
    # ``X-WP-Nonce-Token``) are already covered via the ``token``
    # substring above. The bare forms (``X-CSRF``, ``CSRF``,
    # ``X-XSRF``, ``XSRF``, ``X-WP-Nonce``, ``X-Nonce``, ``Nonce``,
    # ``SAMLArt``, ``X-SAMLArt``, ``RelayState``, ``X-OAuth-State``)
    # are NOT covered by any existing partial. Pre-fix every leaked
    # bare-form header value passed through ``_strip_sensitive_headers``
    # to the redirect target on cross-origin redirects (host change,
    # scheme downgrade, port change). The credential value reached
    # the attacker's access logs.
    #
    #   * ``csrf`` — bare X-CSRF / CSRF / X-Custom-Csrf-Field /
    #     csrf-anything custom protocol headers.
    #   * ``xsrf`` — bare X-XSRF / XSRF / Angular-style custom XSRF.
    #   * ``samlart`` — SAML 2.0 Artifact in HTTP header form
    #     (non-standard but real in some custom SAML SP shims; 5-min
    #     ARS-resolvable bearer credential).
    #   * ``nonce`` — X-WP-Nonce (WordPress REST API authentication
    #     header per https://developer.wordpress.org/rest-api/...) +
    #     X-Nonce / generic challenge-response nonce headers.
    #   * ``state`` — RelayState (SAML 2.0 §3.4.4 in non-canonical
    #     header form) + X-OAuth-State / generic state-preservation
    #     headers.
    #
    # The partial-substring approach handles hyphenated, underscored,
    # X-prefixed, and case-shifted variants in a single addition per
    # the existing convention (``token`` matches ``Bearer-Token``,
    # ``api_token``, ``X-API-Token``, etc.). On cross-origin
    # redirects over-stripping is the safer default (the alternative
    # is leaking credentials to the redirect target).
    "csrf",
    "xsrf",
    "samlart",
    "nonce",
    "state",
})


def _is_sensitive_header(header_name: str) -> bool:
    """Check if a header name is considered sensitive."""
    normalized = header_name.lower()
    if normalized in _SENSITIVE_HEADERS_LOWER:
        return True

    return any(partial in normalized for partial in _SENSITIVE_HEADER_PARTIALS)


def _strip_sensitive_params(url: str) -> str:
    """Remove sensitive query parameters from URL completely."""
    try:
        parsed = urlparse(url)
        if not parsed.query:
            return url

        query_params = parse_qsl(parsed.query, keep_blank_values=True)
        new_params = []
        modified = False

        for key, value in query_params:
            normalized = _normalize_key(key)
            if normalized in _SENSITIVE_QUERY_KEYS or any(s in normalized for s in _SENSITIVE_KEY_SUBSTRINGS):
                modified = True
                continue
            new_params.append((key, value))

        if modified:
            new_query = urlencode(new_params)
            parsed = parsed._replace(query=new_query)
            return parsed.geturl()

        return url
    except Exception:
        return url


def _replace_auth(match: re.Match[Any]) -> str:
    """Callback for explicit auth sanitization."""
    scheme = match.group("scheme")
    # Handle optional slash group which might be None or empty
    slash = match.group("slash") or ""
    return f"{scheme}:{slash}***@"


def _sanitize_exception_msg(msg: str) -> str:
    """Sanitize URLs in exception messages."""
    # First apply specific URL sanitization (handles IPv6, auth, etc.)
    msg = re.sub(
        r"(https?://[^\s'\"<>]+)",
        lambda m: _sanitize_url_for_error(m.group(1)),
        msg
    )
    # Then apply generic logging sanitization (catches relative URLs, query params, etc.)
    return sanitize_log_message(msg, strip_control_chars=False)


def _sanitize_url_for_error(url: str) -> str:
    """Strip credentials and sensitive query params from URL for safe error logging."""
    if len(url) > MAX_URL_LENGTH:
        url = url[:MAX_URL_LENGTH] + "...[TRUNCATED]"

    try:
        # 0. Pre-sanitize malformed auth (e.g. "https:user:pass@...") that urlparse misses
        # This handles cases where user forgot // or scheme is non-standard
        match = _URL_AUTH_RE.match(url)
        if match:
            # Replace the auth part with ***
            url = _URL_AUTH_RE.sub(_replace_auth, url, count=1)

        parsed = urlparse(url)

        # 1. Strip basic auth credentials (if urlparse found them)
        if parsed.username or parsed.password:
            # Reconstruct netloc without auth
            hostname = parsed.hostname or ""
            # Fix IPv6 bug (Task A): Re-wrap IPv6 addresses in brackets
            if ":" in hostname:
                netloc = f"[{hostname}]"
            else:
                netloc = hostname

            if parsed.port:
                netloc += f":{parsed.port}"
            parsed = parsed._replace(netloc=netloc)

        # 2. Redact sensitive query parameters
        if parsed.query:
            query_params = parse_qsl(parsed.query, keep_blank_values=True)
            new_params = []
            for key, value in query_params:
                normalized = _normalize_key(key)
                if normalized in _SENSITIVE_QUERY_KEYS or any(s in normalized for s in _SENSITIVE_KEY_SUBSTRINGS):
                    new_params.append((key, "***"))
                else:
                    new_params.append((key, value))

            new_query = urlencode(new_params)
            parsed = parsed._replace(query=new_query)

        # 3. Strip URL fragment entirely (e.g. OIDC implicit flow)
        import urllib.parse
        url_without_frag, _ = urllib.parse.urldefrag(parsed.geturl())
        return url_without_frag
    except Exception:
        return "invalid_url"


def _strip_sensitive_headers(
    headers: MutableMapping[str, Any],
    original_url: str,
    new_url: str,
    session_headers: Mapping[str, Any] | None = None,
) -> None:
    """Remove sensitive headers if the redirect crosses security boundaries."""
    original_parsed = urlparse(original_url)
    redirect_parsed = urlparse(new_url)

    host_changed = original_parsed.hostname != redirect_parsed.hostname
    scheme_downgraded = (
        original_parsed.scheme == "https" and redirect_parsed.scheme != "https"
    )
    port_changed = _get_port(original_parsed) != _get_port(redirect_parsed)

    # Safe upgrade is HTTP port 80 to HTTPS port 443 on the exact same host
    is_safe_upgrade = (
        not host_changed and
        original_parsed.scheme == "http" and redirect_parsed.scheme == "https" and
        _get_port(original_parsed) == 80 and _get_port(redirect_parsed) == 443
    )

    if (host_changed or scheme_downgraded or port_changed) and not is_safe_upgrade:
        # If session_headers is provided, we use masking mode (set to None)
        # to ensure session headers don't leak through.
        mask_mode = session_headers is not None

        # 1. Process explicit override headers
        for header_name in list(headers.keys()):
            if _is_sensitive_header(header_name):
                if mask_mode:
                    headers[header_name] = None
                else:
                    del headers[header_name]

        # 2. Process implicit session headers (if in masking mode)
        if mask_mode and session_headers:
            for header_name in session_headers:
                if _is_sensitive_header(header_name):
                    # If it's in session headers, we must mask it in override headers
                    # unless explicitly overridden (but we just masked overrides above)
                    headers[header_name] = None


def _get_port(parsed: Any) -> int | None:
    """Get the port from a parsed URL, handling default ports."""
    try:
        if parsed.port is not None:
            return cast('int | None', parsed.port)
    except ValueError:
        pass  # unparsable port: fall back to the scheme's default below
    if parsed.scheme == "http":
        return 80
    if parsed.scheme == "https":
        return 443
    return None


def _safe_rebuild_auth(self: requests.Session, prepared_request: requests.PreparedRequest, response: requests.Response) -> None:
    """Override for requests.Session.rebuild_auth to strip sensitive headers on cross-origin redirects."""
    # Call original implementation to handle Authorization header standard logic
    requests.Session.rebuild_auth(self, prepared_request, response)

    headers = prepared_request.headers
    url = prepared_request.url

    if "Location" not in response.headers:
        # Should not happen if rebuild_auth is called correctly by requests on redirect
        return

    original_parsed = urlparse(response.request.url)
    redirect_parsed = urlparse(url)

    # Detect security risks: Hostname change, Scheme Downgrade (HTTPS -> HTTP), or Port change
    host_changed = original_parsed.hostname != redirect_parsed.hostname
    scheme_downgraded = original_parsed.scheme == "https" and redirect_parsed.scheme != "https"
    port_changed = _get_port(original_parsed) != _get_port(redirect_parsed)

    # Safe upgrade is HTTP port 80 to HTTPS port 443 on the exact same host
    is_safe_upgrade = (
        not host_changed and
        original_parsed.scheme == "http" and redirect_parsed.scheme == "https" and
        _get_port(original_parsed) == 80 and _get_port(redirect_parsed) == 443
    )

    if (host_changed or scheme_downgraded or port_changed) and not is_safe_upgrade:
        # Explicitly strip Authorization, Cookie, and X-Api-Key headers if redirect targets a different hostname/domain
        if host_changed:
            for header_name in list(headers.keys()):
                if header_name.lower() in ("authorization", "cookie", "x-api-key"):
                    del headers[header_name]

        # Dynamic check for sensitive headers based on name patterns
        # We iterate over a copy of keys to allow modification of the dict during iteration
        for header_name in list(headers.keys()):
            if _is_sensitive_header(header_name):
                del headers[header_name]

        # Also strip sensitive QUERY parameters from the redirect URL.
        # ``request_safe`` does this inside its manual redirect loop via
        # ``_strip_redirect_secrets``, but callers that hit ``Session``
        # directly (POSTs that use the session's native redirect
        # handling) only reach THIS hook. Without the
        # strip, a cross-origin redirect target receives the auth token
        # in the URL line of the next request — defeating the header
        # strip immediately above.
        if url is not None:
            prepared_request.url = _strip_sensitive_params(url)
