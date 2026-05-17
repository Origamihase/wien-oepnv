"""Logging utilities for sanitizing inputs and handling sensitive data."""

from __future__ import annotations

import re
from typing import Any

# Precompiled regexes for sanitization
# Strip BiDi control characters (Trojan Source: CVE-2021-42574), zero-width
# characters, and Unicode line/paragraph separators that downstream consumers
# treat as record terminators (ECMAScript-pre-2019 ``JSON.parse``/``eval``,
# the GitHub PR-comment renderer, several YAML parsers, SIEM splitters that
# key off Unicode whitespace). The character class union covers:
#   * ``\x00-\x1f`` / ``\x7f-\x9f`` \u2014 ASCII C0 + DEL + C1 controls.
#   * ``\u061c`` \u2014 Arabic Letter Mark (post-Unicode-6.3 BiDi control; same
#     display-confusion blast radius as LRM/RLM but missing from every
#     prior round of this regex).
#   * ``\u200b-\u200f`` \u2014 ZWSP / ZWNJ / ZWJ / **LRM** / **RLM**. The
#     ``\u200e``/``\u200f`` BiDi marks are the same Trojan-Source primitive
#     as the already-stripped ``\u202a-\u202e`` family: a hostile payload
#     prepends LRM/RLM to invert displayed text in a Unicode-aware terminal
#     so an operator skimming a log misreads ``user=admin drop=table`` as
#     the inverse.
#   * ``\u2028-\u202e`` \u2014 Unicode **LINE SEPARATOR** (``\u2028``) /
#     **PARAGRAPH SEPARATOR** (``\u2029``) plus the CVE-2021-42574 BiDi
#     formatting controls (LRE/RLE/PDF/LRO/RLO at ``\u202a-\u202e``).
#     ``\u2028``/``\u2029`` were the load-bearing gap \u2014 Python's regex
#     ``\\s`` matches them, but ``_CONTROL_CHARS_RE`` did not. A hostile
#     upstream JSON payload could therefore embed ``\u2028`` to forge a
#     second log record in any consumer honouring Unicode line terminators.
#   * ``\u2066-\u2069`` \u2014 LRI / RLI / FSI / PDI BiDi isolates (the second
#     half of CVE-2021-42574).
#   * ``\ufeff`` \u2014 Byte Order Mark (zero-width no-break space).
#   * ``\ufe00-\ufe0f`` \u2014 VARIATION SELECTOR-1 \u2026 VARIATION SELECTOR-16.
#     Each is a zero-width 4-bit-payload primitive used in the
#     "Sneaky Text" steganography technique to smuggle hidden bytes
#     through a visible string. Apple's emoji renderer is the only
#     legitimate consumer (VS-15 text vs. VS-16 emoji presentation);
#     every other use is steganographic.
#   * ``\U000e0000-\U000e007f`` \u2014 Unicode **Tag** block. Every
#     printable ASCII codepoint \x20-\x7E has a paired Tag character
#     in this range that renders as zero-width in every modern
#     terminal / browser / RSS reader / GitHub Web UI / IDE preview.
#     The canonical "ChatGPT invisible-instruction smuggling"
#     primitive (2024 OpenAI disclosure): an ASCII payload encoded
#     in the Tag block flows verbatim through every clear-text
#     sanitiser that stops at the BiDi-isolate band and lands in
#     downstream LLM training / RAG ingestion / chat copy-paste
#     loops. ``U+E007F`` is CANCEL TAG, the documented terminator.
#     ``U+E0001`` is the deprecated LANGUAGE TAG primitive.
#   * ``\U000e0100-\U000e01ef`` \u2014 VARIATION SELECTOR-17 \u2026
#     VARIATION SELECTOR-256 (plane 14). The supplementary half of
#     the Variation Selector steganography primitive.
# The companion regex in ``src/utils/stations_validation.py`` uses
# ``\u2028-\u202e``; this file pins the canonical UNION (incl. ALM, LRM,
# RLM) so every WARNING/ERROR site routed through the audit walker
# (``test_sentinel_clear_text_logging_drift_utils``) inherits the same
# defence floor.
_CONTROL_CHARS_RE = re.compile(
    r"[\x00-\x1f\x7f-\x9f"
    r"\u00ad\u0600-\u0605\u061c\u06dd\u070f\u0890\u0891\u08e2\u180e"
    r"\u200b-\u200f\u2028-\u202e\u2060-\u206f"
    r"\ufe00-\ufe0f\ufeff\ufff9-\ufffb"
    r"\U000110bd\U000110cd"
    r"\U00013430-\U00013438"
    r"\U0001bca0-\U0001bca3"
    r"\U0001d173-\U0001d17a"
    r"\U000e0000-\U000e007f\U000e0100-\U000e01ef]"
)
# Always-strip set: invisible Unicode characters that have NO readability
# value and are pure log-injection / Trojan-Source / terminal-escape
# primitives. The 2026-05-09 round (PR #1363) added the BiDi / zero-width
# code points to ``_CONTROL_CHARS_RE`` so the ``strip_control_chars=True``
# (default) path strips them. The drift was the ``strip_control_chars=False``
# branch \u2014 used by ``clean_message``,
# ``_sanitize_log_detail`` (``src/feed/reporting.py``),
# ``_sanitize_exception_msg`` (``src/utils/http.py``),
# ``SafeFormatter.formatException`` and ``SafeJSONFormatter.formatException``
# (``src/feed/logging_safe.py``) \u2014 which bypasses ``_CONTROL_CHARS_RE``
# entirely to preserve readable ``\n``/``\r``/``\t`` in tracebacks.
#
# 2026-05-10 (8-bit C1 / DEL Drift): the always-strip floor was widened to
# ``\x7f-\x9f`` (DEL + the 32 ECMA-48 C1 controls). The 7-bit ANSI escape
# regex ``_ANSI_ESCAPE_RE`` matches ``\x1b``-prefixed CSI/OSC/Fe sequences
# but NOT their **8-bit** equivalents \u2014 ``\x9b`` (CSI, 8-bit form of
# ``ESC [``), ``\x9d`` (OSC, 8-bit form of ``ESC ]``), ``\x90`` (DCS),
# ``\x9e`` (PM), ``\x9f`` (APC). Per ECMA-48 / ISO 6429, terminals that
# honour 8-bit C1 (xterm with ``eightBitInput``, several BSD consoles,
# ``rxvt`` in 8-bit mode) interpret ``\x9b31m`` exactly as ``\x1b[31m``.
# A hostile upstream payload (compromised provider, MITM, DNS hijack,
# poisoned cache file) carrying ``\x9b...m`` in an exception text reaches
# the operator-facing log line and the public ``docs/feed_health.json``
# artefact verbatim pre-fix \u2014 bypassing the ``_ANSI_ESCAPE_RE``
# defence at the 7-bit boundary entirely. Pinning ``\x7f-\x9f`` into the
# always-strip floor closes every ``strip_control_chars=False`` sibling
# path in one cut. ``\n``/``\r``/``\t`` (C0 ``\x09``/``\x0a``/``\x0d``)
# remain outside the always-strip floor so the readability contract for
# traceback formatting is preserved.
#
# Stripping unconditionally (independent of the flag) leaks the BiDi /
# zero-width / line-terminator / 8-bit-C1 family out of the public
# ``feed_health.json`` artefact and the GitHub Issue body submitted by
# ``submit_auto_issue`` while preserving the readable newline contract
# every ``strip_control_chars=False`` caller relies on.
# 2026-05-10 (ASCII C0 / Log-Injection Drift Round 4): widened to
# include ``\x00-\x08\x0b\x0c\x0e-\x1f`` (the ASCII C0 control set
# MINUS readable whitespace ``\x09``/``\x0a``/``\x0d``). Three of the
# four canonical sibling regexes already cover this set:
#   * ``src/utils/text.py:_MARKDOWN_NORMALISE_UNSAFE_RE``
#   * ``src/utils/stats.py:_CSV_CONTROL_CHARS_RE``
#   * ``src/build_feed.py:_CONTROL_RE``
# Only ``_INVISIBLE_DANGEROUS_RE`` was narrower. The C0 hole leaked
# NUL (content truncation), BEL (terminal-bell denial-of-attention),
# BS (visual-spoof primitive), FF (terminal-screen-wipe), bare ESC,
# SO/SI (legacy charset switch), and DC1-4 / SUB / FS / GS / RS / US
# into every ``strip_control_chars=False`` sibling sink
# (``clean_message``, ``_sanitize_log_detail``,
# ``_sanitize_exception_msg``, ``SafeFormatter.formatException``,
# ``SafeJSONFormatter.formatException``) and from there into the
# public ``docs/feed-health.md`` artefact + GitHub Issue body
# submitted by ``submit_auto_issue``. ``\x09`` (TAB), ``\x0a`` (LF),
# ``\x0d`` (CR) remain outside the always-strip floor so the
# readability contract for traceback formatting is preserved.
#
# 2026-05-11 "Tag-Character / Variation-Selector Drift": widened to
# include three orthogonal invisible-character ranges that the prior
# BiDi-Mark Drift rounds left uncovered:
#   * U+FE00..U+FE0F - VARIATION SELECTOR-1..16 (BMP, 4-bit-payload
#     steganography primitive).
#   * U+E0000..U+E007F - Unicode Tag block. The canonical
#     "ChatGPT invisible-instruction smuggling" primitive (2024
#     OpenAI disclosure). Every printable ASCII codepoint has a
#     paired Tag character rendering as zero-width in every modern
#     renderer.
#   * U+E0100..U+E01EF - VARIATION SELECTOR-17..256 (plane 14).
# Each range is documented invisible (no readability value), each
# has documented attack shapes (Trojan-Source display confusion,
# steganographic data smuggling, prompt-injection smuggling), and
# none has any legitimate consumer in this codebase. German umlauts
# (ae/oe/ue plus sharp s) and transit emoji are OUTSIDE the strip
# ranges - the widening is additive only against the invisible-
# character family. See
# tests/test_sentinel_tag_chars_variation_selectors_invisible_drift.py
# for the additive-regression invariant.
#
# 2026-05-14 "Zero-Width Format Drift": widened to close the gap
# between U+202E (RLO) and U+2066 (LRI) plus the legacy U+180E:
#   * U+180E - MONGOLIAN VOWEL SEPARATOR. Originally classified
#     Zs (Space_Separator), reclassified Cf (Format) in Unicode
#     6.3.0 but still rendered as zero-width by every conforming
#     renderer. Defeats every "strip ZWSP family" filter that
#     relies on the U+200x band.
#   * U+2060 - WORD JOINER (zero-width no-break space, the
#     non-deprecated successor of U+FEFF as a word-joining
#     primitive). Glues two visible tokens without producing
#     any visible separator.
#   * U+2061 - FUNCTION APPLICATION (mathematical zero-width).
#   * U+2062 - INVISIBLE TIMES (mathematical zero-width).
#   * U+2063 - INVISIBLE SEPARATOR (mathematical zero-width).
#   * U+2064 - INVISIBLE PLUS (mathematical zero-width).
# Every code point above is in Unicode general category Cf
# (Format) - the same category as ZWSP/ZWNJ/ZWJ already covered
# by the floor. The U+2060..U+2064 band is the canonical
# "invisible Unicode steganography" alphabet in published
# research: combinations of WJ + INVISIBLE TIMES + INVISIBLE
# SEPARATOR + INVISIBLE PLUS encode arbitrary bytes that
# survive copy-paste from a log / RSS feed / GitHub Issue body
# into an LLM context window - invisible to the human, fully
# visible to the model. The expanded U+2060..U+2069 range
# additionally folds in the existing U+2066..U+2069 BiDi
# isolate band (LRI/RLI/FSI/PDI) plus reserved U+2065; the
# unassigned slot has no defined meaning and the additive
# strip is safe.
#
# 2026-05-14 "Cf-Format Drift": widened to close every remaining
# Unicode general category Cf (Format) code point that the prior
# rounds did not enumerate. The 13 added bands cover 44 code points:
#   * U+00AD - SOFT HYPHEN. The single most-impactful omission: it
#     renders zero-width unconditionally in browsers / RSS readers /
#     terminals / IDE preview / GitHub web UI when not at a line-
#     break opportunity, but is stored as a real character in every
#     downstream byte-equality / hash / GUID dedup key. Used in
#     real-world attacks since 2018 (CVE-2018-19165 in IDN
#     homographs, CVE-2021-43616 in npm package-name spoofing).
#   * U+0600..U+0605 - ARABIC NUMBER SIGN, ARABIC SIGN SANAH,
#     ARABIC FOOTNOTE MARKER, ARABIC SIGN SAFHA, ARABIC SIGN SAMVAT,
#     ARABIC NUMBER MARK ABOVE. Zero-width prefix marks per UAX #9.
#   * U+06DD - ARABIC END OF AYAH.
#   * U+070F - SYRIAC ABBREVIATION MARK.
#   * U+0890..U+0891 - ARABIC POUND / PIASTRE MARK ABOVE.
#   * U+08E2 - ARABIC DISPUTED END OF AYAH.
#   * U+206A..U+206F - Deprecated BiDi formatting controls
#     (INHIBIT/ACTIVATE SYMMETRIC SWAPPING, INHIBIT/ACTIVATE ARABIC
#     FORM SHAPING, NATIONAL/NOMINAL DIGIT SHAPES). The expanded
#     U+2060..U+206F range folds in the existing U+2060..U+2069
#     band (WJ + Invisible-* + BiDi-isolate). The deprecated
#     controls are documented zero-width in every modern renderer.
#   * U+FFF9..U+FFFB - INTERLINEAR ANNOTATION ANCHOR / SEPARATOR /
#     TERMINATOR (Unicode "ruby annotation" formatting controls;
#     zero-width except in dedicated CJK ruby renderers, none of
#     which is in the Vienna OePNV pipeline).
#   * U+110BD, U+110CD - KAITHI NUMBER SIGN / NUMBER SIGN ABOVE.
#   * U+13430..U+13438 - EGYPTIAN HIEROGLYPH JOINERS / SEGMENT
#     formatting controls (vertical / horizontal / overlay etc.).
#   * U+1BCA0..U+1BCA3 - SHORTHAND FORMAT LETTER OVERLAP /
#     CONTINUING OVERLAP / DOWN STEP / UP STEP.
#   * U+1D173..U+1D17A - MUSICAL SYMBOL BEGIN / END BEAM / TIE /
#     SLUR / PHRASE.
# Every code point above is in Unicode general category Cf (Format),
# the same category as ZWSP/ZWNJ/ZWJ already in the floor, with
# zero advance width in every conforming renderer. None has a
# legitimate consumer in this codebase's data path - no Arabic /
# Syriac / Egyptian-hieroglyph / shorthand / musical-notation
# content flows through any provider feed, station name, sitemap
# URL, RSS title, or operator log line. The structural invariant
# pinned by
# tests/test_sentinel_cf_format_drift_round.py:test_canonical_invisible_dangerous_re_covers_every_unicode_cf_character
# enumerates every Cf code point in Unicode and asserts the floor
# matches each one - any future Unicode-spec addition of a new
# Format-category code point fails the test on the first pytest
# run after the new ``unicodedata`` ships, surfacing the next
# drift family programmatically.
_INVISIBLE_DANGEROUS_RE = re.compile(
    r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f"
    r"\u00ad\u0600-\u0605\u061c\u06dd\u070f\u0890\u0891\u08e2\u180e"
    r"\u200b-\u200f\u2028-\u202e\u2060-\u206f"
    r"\ufe00-\ufe0f\ufeff\ufff9-\ufffb"
    r"\U000110bd\U000110cd"
    r"\U00013430-\U00013438"
    r"\U0001bca0-\U0001bca3"
    r"\U0001d173-\U0001d17a"
    r"\U000e0000-\U000e007f\U000e0100-\U000e01ef]"
)
_LOG_INJECTION_RE = re.compile(r"[\n\r\t]")
# ANSI escape codes: comprehensive matching for CSI, OSC, Fe, and 2-byte sequences
# Matches:
# 1. CSI: ESC [ ...
# 2. OSC: ESC ] ... BEL/ST
# 3. Fe (excluding [ and ]): ESC [@-Z\\^_]
# 4. Two-byte sequences: ESC [space-/] [0-~]
_ANSI_ESCAPE_RE = re.compile(r'\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)|[@-Z\\^_]|[\x20-\x2f][\x30-\x7e])')


def sanitize_log_message(
    text: str, secrets: list[str] | None = None, strip_control_chars: bool = True
) -> str:
    """
    Sanitize log messages by masking secrets and removing control characters.

    This protects against:
    - Leaking credentials (access IDs, tokens) in logs.
    - Log Injection attacks (newlines, ANSI sequences).

    Args:
        text: The raw message string to sanitize.
        secrets: Optional list of specific secret strings to mask.
        strip_control_chars: If True (default), newlines and other control characters
                             are escaped or removed to prevent log injection.
                             Set to False for tracebacks where readability is needed.

    Returns:
        The sanitized string.
    """
    if not text:
        return ""

    sanitized = text

    # Remove ANSI escape codes explicitly first
    sanitized = _ANSI_ESCAPE_RE.sub("", sanitized)

    # Keys that should be redacted (regex alternation, longest match first)
    _keys = (
        r"client[-_.\s]*secret|access[-_.\s]*token|refresh[-_.\s]*token|[a-z0-9_.\-]*client[-_.\s]*id[a-z0-9_.\-]*|[a-z0-9_.\-]*signature|[a-z0-9_.\-]*password[a-z0-9_.\-]*|[a-z0-9_.\-]*e[-_.\s]*mail[a-z0-9_.\-]*|"
        r"client[-_.\s]*assertion[-_.\s]*type|client[-_.\s]*assertion|"
        # Plain `assertion` (RFC 7521/7522/7523 — SAML 2.0 / JWT Bearer Auth Grant):
        # carries a signed identity assertion that is effectively a credential.
        # The optional [a-z0-9_.\-]* prefix/suffix also captures saml_assertion,
        # subject_assertion, jwt_assertion, etc.
        r"[a-z0-9_.\-]*assertion[a-z0-9_.\-]*|"
        r"saml[-_.\s]*request|saml[-_.\s]*response|"
        r"[a-z0-9_.\-]*accessid[a-z0-9_.\-]*|id[-_.\s]*token|[a-z0-9_.\-]*session[-_.\s]*id[a-z0-9_.\-]*|session|cookie|[a-z0-9_.\-]*apikey[a-z0-9_.\-]*|[a-z0-9_.\-]*secret[a-z0-9_.\-]*|ticket|[a-z0-9_.\-]*token|code|key|sig|sid|"
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
        #     covered via the existing ``[a-z0-9_.\-]*token``
        #     alternation. Only the bare forms need explicit
        #     coverage here. The ``RelayState`` and ``_wpnonce``
        #     siblings from PR #1531 are ALREADY covered via the
        #     existing ``state`` / ``nonce`` alternations (substring
        #     match within the longer key name).
        r"samlart|csrf|xsrf|"
        r"jsessionid|phpsessid|asp\.net_sessionid|__cfduid|"
        r"authorization|auth|bearer[-_.\s]*token|bearer|[a-z0-9_.\-]*api[-_.\s]*key[a-z0-9_.\-]*|[a-z0-9_.\-]*private[-_.\s]*key|auth[-_.\s]*token|"
        r"tenant[-_.\s]*id|tenant|subscription[-_.\s]*id|subscription|object[-_.\s]*id|oid|"
        r"code[-_.\s]*challenge|code[-_.\s]*verifier|"
        r"x[-_.\s]*api[-_.\s]*key|ocp[-_.\s]*apim[-_.\s]*subscription[-_.\s]*key|"
        r"[a-z0-9_.\-]*credential|x[-_.\s]*amz[-_.\s]*credential|x[-_.\s]*amz[-_.\s]*security[-_.\s]*token|"
        r"x[-_.\s]*amz[-_.\s]*signature|x[-_.\s]*auth[-_.\s]*token|"
        r"[a-z0-9_.\-]*passphrase[a-z0-9_.\-]*|[a-z0-9_.\-]*access[-_.\s]*key[-_.\s]*id[a-z0-9_.\-]*|"
        r"[a-z0-9_.\-]*secret[-_.\s]*access[-_.\s]*key|[a-z0-9_.\-]*auth[-_.\s]*code[a-z0-9_.\-]*|"
        r"[a-z0-9_.\-]*authorization[-_.\s]*code[a-z0-9_.\-]*|"
        r"[a-z0-9_.\-]*otp(?:[-_][a-z0-9_.\-]*)?|[a-z0-9_.\-]*glpat(?:[-_][a-z0-9_.\-]*)?|[a-z0-9_.\-]*ghp(?:[-_][a-z0-9_.\-]*)?|"
        r"\bpass\b|\bpwd\b|\buser[-_.]?pass\b"
    )

    # Common header-safe keys for broad redaction in Header: Value pairs
    # Explicitly supports hyphens for header style (e.g. Api-Key)
    _header_keys = (
        r"api[-_.\s]*key|token|secret|signature|password|auth|session|cookie|private|"
        r"client[-_.\s]*assertion|[a-z0-9_.\-]*assertion[a-z0-9_.\-]*|"
        r"saml[-_.\s]*request|saml[-_.\s]*response|nonce|state|"
        # Sibling-drift closure (see ``_keys`` above): ``samlart`` /
        # ``csrf`` / ``xsrf`` bare header forms (``SAMLArt: ...``,
        # ``X-CSRF: ...``, ``XSRF: ...``) bypass the existing
        # alternations. Suffixed variants (``X-CSRF-Token``,
        # ``X-XSRF-TOKEN``) are already covered via ``token``.
        r"samlart|csrf|xsrf|"
        r"credential|client[-_.\s]*id|passphrase|access[-_.\s]*key|e[-_.\s]*mail"
    )

    # Common patterns for secrets in URLs/Headers
    patterns: list[tuple[str, str]] = [
        # PEM blocks (keys/certs) - MUST be first to prevent partial redaction by other patterns
        (r"(-----BEGIN [A-Z ]+-----)(?:.|\n)*?(-----END [A-Z ]+-----)", r"\1***\2"),
        # Explicitly mask accessId (Requirement) to ensure robust redaction in tracebacks
        (r"(?i)(accessId\s*=\s*)([^&\s]+)", r"\1***"),
        # Basic Auth in URLs (protocol://user:pass@host) - canonical form.
        (r"(?i)([a-z0-9+.-]+://)([^/@\s]+)@", r"\1***@"),
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
            r":)([^/@\s]+:[^/@\s]+)@",
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
        (rf"(?i)((?:[-a-zA-Z0-9_]*(?:{_header_keys})[-a-zA-Z0-9_]*):\s*)((?:.*)(?:\n\s+.*)*)", r"\1***"),
        # Mask potentially leaked secrets in JSON error messages
        (rf'(?i)(\"(?:{_keys})\"\s*:\s*\")((?:\\.|[^"\\\\])*)(\")', r'\1***\3'),
        (rf"(?i)('(?:{_keys})'\s*:\s*')((?:\\.|[^'\\\\])*)(')", r"\1***\3"),
        # HashiCorp Vault token family value-shape masking.
        # Sibling-drift closure for the 2026-05-17 secret-scanner round
        # that extended ``_KNOWN_TOKENS`` in
        # ``src/utils/secret_scanner.py`` to detect HashiCorp Vault
        # Service / Batch / Recovery tokens (``hvs.`` / ``hvb.`` /
        # ``hvr.`` prefixes with 30+ char base64url bodies). The
        # scanner closed the *detection* codepath for committed source
        # files but the log-sanitisation codepath was NOT extended in
        # the same round — bare Vault token shapes in plain log text
        # (application f-string logs, upstream error responses echoing
        # the token back, JSON values without sensitive key names, URL
        # paths embedding the token) bypass every existing
        # key/header/URL-credential mask pattern and leak verbatim to
        # operator log streams and the public
        # ``docs/feed_health.json`` artefact.
        #
        # Threat model (mirror the secret-scanner Round 6 / 2026-05-17
        # rounds' blast-radius analysis):
        #   * ``hvs.`` — Vault Service Token (persistent, full policy
        #     scope; HCP Vault Secrets managed + self-hosted Vault 1.10+).
        #   * ``hvb.`` — Vault Batch Token (ephemeral, full policy
        #     scope for TTL; common in CI/CD and serverless workloads).
        #   * ``hvr.`` — Vault Recovery Token (root-equivalent on
        #     sealed/HSM-backed Vault; mint new root token via
        #     ``POST /v1/sys/generate-root`` once unsealed → persistent
        #     backdoor with full administrative scope).
        #
        # Structural anchors mirror the scanner regex:
        #   * ``(?<![A-Za-z0-9])`` lookbehind prevents mid-word false
        #     positives (``obj.hvs.foo`` is preserved).
        #   * ``(?![A-Za-z0-9])`` lookahead bounds the body span.
        #   * 30+ char body floor rejects accidental fragments
        #     (attribute-access chains, filesystem paths, mid-
        #     identifier collisions) while accepting the canonical
        #     90-110 char Vault token shape.
        # The mask preserves the issuer-specific prefix (``hvs.***`` /
        # ``hvb.***`` / ``hvr.***``) for incident-response triage:
        # each tier has a distinct revocation flow (``vault token
        # revoke`` for service/batch via ``POST /v1/auth/token/revoke``
        # vs. ``POST /v1/sys/generate-recovery-token/attempt`` + Shamir
        # recovery-key re-keying for recovery).
        #
        # Marker: SENTINEL_VAULT_TOKEN_LOG_SANITIZATION_DRIFT.
        (
            r"(?<![A-Za-z0-9])(hvs|hvb|hvr)\.[A-Za-z0-9_\-]{30,}(?![A-Za-z0-9])",
            r"\1.***",
        ),
        # GitHub token family value-shape masking. Sibling-drift closure
        # for the secret-scanner ``_KNOWN_TOKENS`` entries that detect
        # committed GitHub tokens (``ghp_`` Personal Access Token,
        # ``gho_`` OAuth Access Token, ``ghu_`` App User-to-Server,
        # ``ghs_`` App Server-to-Server / ``GITHUB_TOKEN``, ``ghr_``
        # Refresh Token, ``github_pat_`` Fine-Grained PAT). The scanner
        # closed the *detection* codepath for committed source files; the
        # log-sanitisation codepath was NOT extended in the same rounds —
        # bare GitHub token shapes in plain log text (application f-string
        # logs, upstream error responses echoing the token back, JSON
        # values without sensitive key names, URL query strings / path
        # segments with NON-sensitive parameter names like ``ref`` /
        # ``commit_sha``) bypass every existing key/header/URL-credential
        # mask pattern and leak verbatim to operator log streams and the
        # public ``docs/feed_health.json`` artefact.
        #
        # Threat model (per-tier blast radius):
        #   * ``ghp_<36 alphanumeric>`` — Personal Access Token (Classic).
        #     Full scope per token configuration. Leaking grants ability
        #     to read every repo the user can read, push to every repo
        #     the user can write, exfiltrate secrets via repo files /
        #     Actions logs, create/delete repos, and (with admin:org)
        #     administer the user's organisations.
        #   * ``gho_<36 alphanumeric>`` — OAuth-App Access Token (issued
        #     via OAuth web flow). Per-OAuth-app scope.
        #   * ``ghu_<36 alphanumeric>`` — GitHub App User-to-Server Token.
        #     Per-installation scope intersected with user's repo access.
        #   * ``ghs_<36 alphanumeric>`` — **HIGHEST routine-leak severity.**
        #     Format of ``GITHUB_TOKEN`` auto-injected by GitHub Actions
        #     into every workflow run. Leaking grants full ``contents:
        #     write`` / ``packages: write`` / ``actions: write`` scope
        #     for the workflow's TTL (typically 1-6 hours, actively
        #     renewable).
        #   * ``ghr_<36 alphanumeric>`` — Refresh Token (issued alongside
        #     ``gho_``/``ghu_`` during token rotation). Mints fresh access
        #     tokens until refresh token is revoked.
        #   * ``github_pat_<22+ alphanumeric_>`` — Fine-Grained PAT.
        #     Per-repo or per-org scoped with resource-level permissions
        #     (Contents, Metadata, Actions, Pull Requests, Issues,
        #     Workflows, Webhooks). Modern replacement for ``ghp_``
        #     classic tokens. Body permits internal underscores per
        #     GitHub's canonical ``github_pat_<22>_<59>`` format.
        #
        # Structural anchors mirror the scanner regexes exactly:
        #   * ``(?<![A-Za-z0-9])`` lookbehind prevents mid-word false
        #     positives (``myghp_xxx``, ``xghs_yyy`` are preserved).
        #   * ``(?![A-Za-z0-9])`` lookahead bounds the body span.
        #   * Strict 36-char alphanumeric body for ``ghp_``/``gho_``/
        #     ``ghu_``/``ghs_``/``ghr_`` matches GitHub's canonical
        #     token shape and rejects accidental fragments.
        #   * 22+ char body with underscores allowed for ``github_pat_``
        #     matches the fine-grained format.
        # The mask preserves the issuer-specific prefix (``ghp_***`` /
        # ``ghs_***`` etc.) for incident-response triage — each tier has
        # a distinct revocation flow (Settings → Developer settings →
        # Personal access tokens for ghp_/github_pat_; Settings →
        # Applications → Authorized OAuth Apps for gho_/ghr_; per-App
        # installation rotation for ghu_/ghs_).
        #
        # Idempotent: masked forms (``ghp_***`` / ``github_pat_***``)
        # do NOT match the regex because ``*`` is not in the body
        # alphabet AND the masked body length (3 chars) is below the
        # 36/22-char floors.
        #
        # Marker: SENTINEL_GITHUB_TOKEN_LOG_SANITIZATION_DRIFT.
        (
            r"(?<![A-Za-z0-9])(ghp|gho|ghu|ghs|ghr)_[0-9a-zA-Z]{36}(?![A-Za-z0-9])",
            r"\1_***",
        ),
        (
            r"(?<![A-Za-z0-9])(github_pat)_[0-9a-zA-Z_]{22,}(?![A-Za-z0-9])",
            r"\1_***",
        ),
    ]
    for pattern, repl in patterns:
        sanitized = re.sub(pattern, repl, sanitized)

    # Mask explicit secrets provided
    if secrets:
        for secret in secrets:
            if secret:
                sanitized = sanitized.replace(secret, "***")

    # Always strip BiDi / zero-width / Unicode line-terminator characters.
    # These have no readability value but are documented log-injection
    # (CVE-2021-42574) and Trojan-Source primitives. Stripping unconditionally
    # closes the ``strip_control_chars=False`` sibling paths
    # (``clean_message``, ``_sanitize_log_detail``, ``_sanitize_exception_msg``,
    # ``SafeFormatter.formatException``, ``SafeJSONFormatter.formatException``)
    # while preserving the readable ``\n``/``\r``/``\t`` contract those
    # callers rely on for traceback formatting.
    sanitized = _INVISIBLE_DANGEROUS_RE.sub("", sanitized)

    # Prevent log injection by escaping newlines and control characters
    if strip_control_chars:
        # We escape common control chars to keep the log readable but safe
        sanitized = sanitized.replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
        sanitized = _CONTROL_CHARS_RE.sub("", sanitized)

    return sanitized


def sanitize_log_arg(arg: Any, secrets: list[str] | None = None) -> Any:
    """
    Helper to sanitize arguments passed to logging functions.

    If the argument is a string, it is sanitized. Otherwise, it is converted to string
    and then sanitized (to ensure objects with sensitive __str__ are caught, though
    primary use case is string arguments).
    """
    if isinstance(arg, int | float):
        return arg
    if isinstance(arg, str):
        return sanitize_log_message(arg, secrets)
    return sanitize_log_message(str(arg), secrets)
