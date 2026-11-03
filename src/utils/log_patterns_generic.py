"""Key alternations and structural redaction patterns for ``sanitize_log_message``.

Pure data moved out of ``src/utils/logging.py``, which imports it: PEM
blocks, URL userinfo, malformed database URIs, query parameters, JSON keys,
Authorization/Cookie and other sensitive headers. ``src/utils/logging.py``
joins the four ``log_patterns_*`` lists in their original order; the order
is load-bearing (PEM first, generic key/header masks before vendor shapes).
"""

from __future__ import annotations

# The list keeps a private name; ``src/utils/logging.py`` imports it
# (code scanning counts an unlisted ``_name`` as unused).
__all__ = ["_GENERIC_PATTERNS"]

# Keys that should be redacted (regex alternation, longest match first)
_keys = (
    r"client[-_.\s]*secret|access[-_.\s]*token|refresh[-_.\s]*token|[a-z0-9_.\-]{0,64}client[-_.\s]*id[a-z0-9_.\-]{0,64}|[a-z0-9_.\-]{0,64}signature|[a-z0-9_.\-]{0,64}password[a-z0-9_.\-]{0,64}|[a-z0-9_.\-]{0,64}e[-_.\s]*mail[a-z0-9_.\-]{0,64}|"
    r"client[-_.\s]*assertion[-_.\s]*type|client[-_.\s]*assertion|"
    # Plain `assertion` (RFC 7521/7522/7523 — SAML 2.0 / JWT Bearer Auth Grant):
    # carries a signed identity assertion that is effectively a credential.
    # The optional [a-z0-9_.\-]{0,64} prefix/suffix also captures saml_assertion,
    # subject_assertion, jwt_assertion, etc.
    r"[a-z0-9_.\-]{0,64}assertion[a-z0-9_.\-]{0,64}|"
    r"saml[-_.\s]*request|saml[-_.\s]*response|"
    r"[a-z0-9_.\-]{0,64}access[-_.\s]*id[a-z0-9_.\-]{0,64}|id[-_.\s]*token|[a-z0-9_.\-]{0,64}session[-_.\s]*id[a-z0-9_.\-]{0,64}|session|cookie|[a-z0-9_.\-]{0,64}apikey[a-z0-9_.\-]{0,64}|[a-z0-9_.\-]{0,64}secret[a-z0-9_.\-]{0,64}|ticket|[a-z0-9_.\-]{0,64}token|code|key|sig|sid|"
    r"nonce|state|"
    # Security (sibling-drift closure of PR #1531's
    # ``_SENSITIVE_QUERY_KEYS`` SAML/CSRF/WordPress round):
    #   * ``samlart`` — SAML 2.0 Artifact per OASIS SAML 2.0
    #     §3.6.4. 5-min ARS-resolvable bearer credential. The
    #     bare ``saml`` substring is NOT in the existing
    #     alternation (only ``saml-request|saml-response`` are
    #     present), so a leaked ``SAMLArt=...`` query param /
    #     ``SAMLArt: ...`` header / ``"samlart": "..."`` JSON
    #     fragment passing through ``sanitize_log_message`` lands
    #     verbatim in operator log streams.
    #   * ``csrf`` / ``xsrf`` — bare CSRF/XSRF token names
    #     (Spring Security's ``_csrf`` GET-based protection,
    #     Angular's bare ``xsrf`` cookie). The token-suffixed
    #     forms (``csrf_token``, ``XSRF-TOKEN``) are ALREADY
    #     covered via the existing ``[a-z0-9_.\-]{0,64}token``
    #     alternation. Only the bare forms need explicit
    #     coverage here. The ``RelayState`` and ``_wpnonce``
    #     siblings from PR #1531 are ALREADY covered via the
    #     existing ``state`` / ``nonce`` alternations (substring
    #     match within the longer key name).
    r"samlart|csrf|xsrf|"
    # Sibling-drift closure vs ``src/utils/http.py``'s sensitive-key
    # contract (``_SENSITIVE_QUERY_KEYS``/``_SENSITIVE_KEY_SUBSTRINGS``
    # include ``webhook``/``jwt``/``dsn``; ``test_sentinel_redaction``
    # pins them). Without these, ``sanitize_log_message`` — the
    # formatter-level sanitiser applied to EVERY record — let a
    # ``webhook_url=https://hooks.slack.com/services/T.../B.../XXXX``
    # (secret carried in the URL *path*, so no value-shape pattern
    # catches it) leak verbatim into logs and the public
    # ``docs/feed_health.json``. ``cfduid`` (bare) joins ``__cfduid``.
    r"[a-z0-9_.\-]{0,64}webhook[a-z0-9_.\-]{0,64}|jwt|dsn|cfduid|"
    r"jsessionid|phpsessid|asp\.net_sessionid|__cfduid|"
    r"authorization|auth|bearer[-_.\s]*token|bearer|[a-z0-9_.\-]{0,64}api[-_.\s]*key[a-z0-9_.\-]{0,64}|[a-z0-9_.\-]{0,64}private[-_.\s]*key|auth[-_.\s]*token|"
    r"tenant[-_.\s]*id|tenant|subscription[-_.\s]*id|subscription|object[-_.\s]*id|oid|"
    r"code[-_.\s]*challenge|code[-_.\s]*verifier|"
    r"x[-_.\s]*api[-_.\s]*key|ocp[-_.\s]*apim[-_.\s]*subscription[-_.\s]*key|"
    r"[a-z0-9_.\-]{0,64}credential|x[-_.\s]*amz[-_.\s]*credential|x[-_.\s]*amz[-_.\s]*security[-_.\s]*token|"
    r"x[-_.\s]*amz[-_.\s]*signature|x[-_.\s]*auth[-_.\s]*token|"
    r"[a-z0-9_.\-]{0,64}passphrase[a-z0-9_.\-]{0,64}|[a-z0-9_.\-]{0,64}access[-_.\s]*key[-_.\s]*id[a-z0-9_.\-]{0,64}|"
    r"[a-z0-9_.\-]{0,64}secret[-_.\s]*access[-_.\s]*key|[a-z0-9_.\-]{0,64}auth[-_.\s]*code[a-z0-9_.\-]{0,64}|"
    r"[a-z0-9_.\-]{0,64}authorization[-_.\s]*code[a-z0-9_.\-]{0,64}|"
    r"[a-z0-9_.\-]{0,64}otp(?:[-_][a-z0-9_.\-]{0,64})?|[a-z0-9_.\-]{0,64}glpat(?:[-_][a-z0-9_.\-]{0,64})?|[a-z0-9_.\-]{0,64}ghp(?:[-_][a-z0-9_.\-]{0,64})?|"
    r"\bpass\b|\bpwd\b|\buser[-_.]?pass\b"
)

# Common header-safe keys for broad redaction in Header: Value pairs
# Explicitly supports hyphens for header style (e.g. Api-Key)
_header_keys = (
    r"api[-_.\s]*key|token|secret|signature|password|auth|session|cookie|private|"
    r"client[-_.\s]*assertion|[a-z0-9_.\-]{0,64}assertion[a-z0-9_.\-]{0,64}|"
    r"saml[-_.\s]*request|saml[-_.\s]*response|nonce|state|"
    # Sibling-drift closure (see ``_keys`` above): ``samlart`` /
    # ``csrf`` / ``xsrf`` bare header forms (``SAMLArt: ...``,
    # ``X-CSRF: ...``, ``XSRF: ...``) bypass the existing
    # alternations. Suffixed variants (``X-CSRF-Token``,
    # ``X-XSRF-TOKEN``) are already covered via ``token``.
    r"samlart|csrf|xsrf|"
    r"[a-z0-9_.\-]{0,64}webhook[a-z0-9_.\-]{0,64}|jwt|dsn|"
    r"credential|client[-_.\s]*id|passphrase|access[-_.\s]*key|access[-_.\s]*id|e[-_.\s]*mail"
)

# Common patterns for secrets in URLs/Headers
_GENERIC_PATTERNS: list[tuple[str, str]] = [
    # PEM blocks (keys/certs) - MUST be first to prevent partial redaction by other patterns
    (r"(-----BEGIN [A-Z ]+-----)(?:.|\n)*?(-----END [A-Z ]+-----)", r"\1***\2"),
    # Explicitly mask accessId (Requirement) to ensure robust redaction in tracebacks.
    # The value class excludes JSON/structural chars (``"`` ``'`` ``,`` ``}``) so that
    # when this runs over an already-serialised JSON record (SafeJSONFormatter sanitises
    # the dumped string a second time) the greedy match does not swallow the closing
    # quote/brace and emit unterminated/invalid JSON — ``accessId`` is the primary
    # VOR/Wiener-Linien credential name, so this path is exercised on every auth log line.
    (r"(?i)(accessId\s*=\s*)([^&\s\"',}]+)", r"\1***"),
    # Basic Auth in URLs (protocol://user:pass@host) - canonical form.
    # Security (ReDoS): the scheme class is BOUNDED ``{1,64}`` and
    # POSSESSIVE (``+`` suffix). The prior unbounded ``[a-z0-9+.-]+``
    # caused catastrophic O(n²) backtracking — on a hostile log
    # argument made of a long run of ``[a-z0-9+.-]`` chars with no
    # ``://`` (e.g. a leaked base64-ish token or hostname), the ``+``
    # consumed the whole run, ``://`` failed, and the engine
    # backtracked one char at a time at every start position
    # (~75 s for 100 KB), freezing the entire log pipeline since
    # ``sanitize_log_message`` runs on every operator log line. ``:``
    # and ``/`` are NOT in the class, so the possessive quantifier
    # never over-consumes a real ``scheme://`` — the fix is lossless
    # for every realistic URL scheme (all well under 64 chars).
    # The userinfo class admits ``@`` and the match runs to the LAST
    # ``@`` of the authority: a mail login written unencoded
    # (``smtps://noreply@example.com:pw@smtp.example.com``) is split
    # by urllib / requests at the last ``@``, so stopping at the first
    # one left the password in the log. ``/`` still ends the run, so
    # every ``://`` occurrence is scanned at most to the next slash.
    (r"(?i)([a-z0-9+.-]{1,64}+://)([^/\s]+)@", r"\1***@"),
    # Basic Auth in malformed credentialled URIs without the ``//``
    # separator (``postgres:admin:secret@host``) and JDBC inner-
    # scheme variants (``jdbc:mysql:root:pw@host``). The canonical
    # ``://`` pattern above misses both shapes entirely. The scheme
    # alternation enumerates the same 13+ database / broker / mail
    # schemes as the 2026-05-16 Database Connection String secret-
    # scanner round plus the LDAP / SSH / SFTP / SMB / CIFS adjacent
    # families. ``(?<![a-z0-9])`` lookbehind prevents mid-word
    # false positives (``mypostgres:`` is preserved). The
    # ``[^/@\s]+:[^/@\s]+`` auth fragment REQUIRES a literal ``:``
    # so benign ``mailto:user@host`` patterns are not matched
    # (``mailto`` is also absent from the scheme alternation, but
    # the auth-requires-``:`` anchor is a second defence layer).
    (
        r"(?i)(?<![a-z0-9])"
        r"((?:jdbc:)?"
        r"(?:postgres(?:ql)?|mysql|mariadb|"
        r"mongodb(?:\+srv)?|redis|"
        r"amqp|amqps|kafka|clickhouse|cassandra|elasticsearch|"
        r"smtp|smtps|"
        r"ldap|ldaps|ssh|sftp|smb|cifs)"
        r":)([^/\s]+:[^/@\s]+)@",
        r"\1***@",
    ),
    # Query parameters (key=value or key%3dvalue)
    # Improved to handle quoted values (e.g. key="val with spaces") with escaped quotes support
    # AND improved unquoted handling to stop at next key or separator (comma/ampersand/newline/quotes)
    (
        rf"(?i)((?:{_keys})\s*(?:%3d|=)\s*)"
        rf"((?:\"(?:\\.|[^\"\\\\])*\")|(?:'(?:\\.|[^'\\\\])*')|((?:(?!\s+[a-zA-Z0-9_.-]+\s*(?:%3d|=))[^&,\n'\"])+))",
        r"\1***",
    ),
    # Correctly handle escaped characters in JSON strings (regex: (?:\\.|[^"\\])* )
    (r'(?i)(\"accessId\"\s*:\s*\")((?:\\.|[^"\\\\])*)(\")', r'\1***\3'),
    (r"(?i)('accessId'\s*:\s*')((?:\\.|[^'\\\\])*)(')", r"\1***\3"),
    # Generic Authorization header (covers Bearer, Basic, and custom schemes)
    (r"(?i)(Authorization:\s*)((?:.*)(?:\n\s+.*)*)", r"\1***"),
    (r'(?i)(\"Authorization\"\s*:\s*\")((?:\\.|[^"\\\\])*)(\")', r'\1***\3'),
    (r"(?i)('Authorization'\s*:\s*')((?:\\.|[^'\\\\])*)(')", r"\1***\3"),
    # Cookie and Set-Cookie headers
    (r"(?i)((?:Set-)?Cookie:\s*)((?:.*)(?:\n\s+.*)*)", r"\1***"),
    (r'(?i)(\"(?:Set-)?Cookie\"\s*:\s*\")((?:\\.|[^"\\\\])*)(\")', r'\1***\3'),
    (r"(?i)('(?:Set-)?Cookie'\s*:\s*')((?:\\.|[^'\\\\])*)(')", r"\1***\3"),
    # Generic sensitive headers (e.g. X-Api-Key, X-Goog-Api-Key, X-Auth-Token)
    # Matches any header name containing a sensitive term. Allows underscores too.
    (rf"(?i)((?:[-a-zA-Z0-9_]{{0,64}}(?:{_header_keys})[-a-zA-Z0-9_]{{0,64}}):\s*)((?:.*)(?:\n\s+.*)*)", r"\1***"),
    # Mask potentially leaked secrets in JSON error messages
    (rf'(?i)(\"(?:{_keys})\"\s*:\s*\")((?:\\.|[^"\\\\])*)(\")', r'\1***\3'),
    (rf"(?i)('(?:{_keys})'\s*:\s*')((?:\\.|[^'\\\\])*)(')", r"\1***\3"),
]
