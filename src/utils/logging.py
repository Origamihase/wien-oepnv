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
        # High-Severity Cloud / Payment / LLM / Git-Host Token Family
        # value-shape masking. Sibling-drift closure for the secret-
        # scanner ``_KNOWN_TOKENS`` entries enumerated in
        # ``src/utils/secret_scanner.py`` (AWS / Google API / Stripe /
        # Anthropic / OpenAI / GitLab PAT / NPM / SendGrid / Hugging
        # Face). The scanner closed the *detection* codepath for
        # committed source files in rounds 1-N; the log-sanitisation
        # codepath was NOT extended for these families — bare token
        # shapes in plain log text (application f-string logs, upstream
        # error responses echoing the token back, JSON values with
        # NON-sensitive key names, URL paths embedding the token, and
        # exception text routed through ``_sanitize_exception_msg``)
        # bypassed every existing key/header/URL-credential mask and
        # leaked verbatim into operator log streams plus the public
        # ``docs/feed_health.json`` artefact.
        #
        # Threat model per family:
        #   * AWS ``AKIA``/``ASIA``/``ACCA``/``ABIA<16 uppercase>`` —
        #     Cloud account access (full data plane + control plane
        #     for the issuing principal): read every S3 bucket /
        #     RDS DB / DynamoDB table the principal can see, mint
        #     STS credentials, modify IAM (with ``iam:*``), exfiltrate
        #     KMS-encrypted data via ``kms:Decrypt``. Per-prefix
        #     attribution (Personnel vs. STS vs. Federated vs. Bearer)
        #     accelerates IR triage to the right rotation flow.
        #   * Google API Key ``AIza<35 base64url-ish>`` — Per-key
        #     scope for the issuing project: Maps / Places / Geocoding
        #     / Translate / YouTube quota burn (USD 100s/day at scale),
        #     plus the project's quota-tier billing fraud.
        #   * Stripe ``sk_live_<24>`` / ``sk_test_<24>`` /
        #     ``rk_live_<24>`` / ``rk_test_<24>`` / ``whsec_<32+>`` —
        #     Payment-processing fraud (full account API access for
        #     the live secret; webhook forgery for whsec_). Live
        #     secret is the highest-severity; restricted variants
        #     carry scoped subsets.
        #   * Anthropic ``sk-ant-(api|admin)NN-<32+>`` — LLM billing
        #     fraud + prompt exfiltration. Admin-tier additionally
        #     grants console access to the org's billing /
        #     organisation members.
        #   * OpenAI ``sk-<48 alphanumeric>`` (legacy) /
        #     ``sk-proj-<40+>`` / ``sk-svcacct-<40+>`` — Same blast
        #     radius across tiers: completion API at the issuer's
        #     expense, custom-model exfiltration, fine-tune-job
        #     hijack.
        #   * GitLab PAT ``glpat-<20>`` — Mirror of the GitHub PAT
        #     family scope on the GitLab side: full repo / project
        #     access per the token's scope configuration.
        #   * NPM ``npm_<36 alphanumeric>`` — Supply-chain risk:
        #     publish malicious packages under the issuer's
        #     organisation, modify package-tarball contents, deprecate
        #     legitimate versions.
        #   * SendGrid ``SG.<22>.<43>`` — Transactional email sent
        #     FROM the project's authenticated sending domain
        #     (phishing amplification leveraging SPF / DKIM / DMARC
        #     authentication), contact-list exfiltration, webhook-
        #     redirect to attacker-controlled URLs.
        #   * Hugging Face ``hf_<32+>`` — Model hub access: read
        #     private models / datasets / Spaces, push backdoored
        #     model weights, exfiltrate fine-tuning data.
        #
        # Structural anchors mirror the scanner regexes:
        #   * ``(?<![A-Za-z0-9])`` lookbehind prevents mid-word false
        #     positives (``myAKIA<16>`` is preserved).
        #   * ``(?![A-Za-z0-9])`` lookahead bounds the body span.
        #   * Strict body lengths / alphabets per vendor canonical
        #     format reject accidental fragments while accepting
        #     every real-shape token.
        # The mask preserves issuer-specific prefixes (``AKIA***``,
        # ``sk_live_***``, ``sk-ant-api03-***`` etc.) for incident-
        # response triage — each vendor has a distinct revocation
        # flow.
        #
        # Ordering note: more-specific OpenAI prefixes
        # (``sk-ant-``/``sk-proj-``/``sk-svcacct-``) are listed BEFORE
        # the generic OpenAI legacy ``sk-<48 alphanumeric>`` so they
        # win first. The patterns are also mutually exclusive at the
        # body-alphabet level (legacy ``sk-`` requires 48 chars from
        # ``[A-Za-z0-9]`` with NO ``-``, while the prefixed siblings
        # have ``-`` after the issuer keyword), so ordering is a
        # documentation aid rather than a correctness requirement.
        #
        # Idempotence: masked forms (``AKIA***``, ``sk_live_***``,
        # ``glpat-***`` etc.) do NOT match any of these regexes
        # because ``*`` is not in any body alphabet AND the masked
        # body length (3 chars) is below every per-family floor.
        #
        # Marker: SENTINEL_MULTI_VENDOR_TOKEN_LOG_SANITIZATION_DRIFT.
        (
            r"(?<![A-Za-z0-9])(AKIA|ASIA|ACCA|ABIA)[A-Z0-9]{16}(?![A-Za-z0-9])",
            r"\1***",
        ),
        (
            r"(?<![A-Za-z0-9])(AIza)[0-9A-Za-z\-_]{35}(?![A-Za-z0-9])",
            r"\1***",
        ),
        (
            r"(?<![A-Za-z0-9])(sk_live|sk_test|rk_live|rk_test)_[0-9a-zA-Z]{24}(?![A-Za-z0-9])",
            r"\1_***",
        ),
        (
            r"(?<![A-Za-z0-9])(whsec)_[A-Za-z0-9]{32,}(?![A-Za-z0-9])",
            r"\1_***",
        ),
        (
            r"(?<![A-Za-z0-9])(sk-ant-(?:api|admin)[0-9]{2})-[A-Za-z0-9_\-]{32,}(?![A-Za-z0-9])",
            r"\1-***",
        ),
        (
            r"(?<![A-Za-z0-9])(sk-(?:proj|svcacct))-[A-Za-z0-9_\-]{40,}(?![A-Za-z0-9])",
            r"\1-***",
        ),
        # OpenRouter API Key (``sk-or-v1-<32+ alphanumeric>``). Listed
        # BEFORE the OpenAI legacy ``sk-<48 alnum>`` pattern so the
        # more-specific OpenRouter prefix wins first. The two are
        # structurally distinct (OpenRouter contains a dash inside
        # the prefix span which breaks the OpenAI legacy regex's
        # strict alphanumeric body, so collision is impossible at the
        # regex level) but explicit ordering documents the intent.
        # Part of SENTINEL_SLACK_AIML_TOKEN_LOG_SANITIZATION_DRIFT.
        (
            r"(?<![A-Za-z0-9])(sk-or-v1)-[A-Za-z0-9]{32,}(?![A-Za-z0-9])",
            r"\1-***",
        ),
        (
            r"(?<![A-Za-z0-9])(sk)-[A-Za-z0-9]{48}(?![A-Za-z0-9])",
            r"\1-***",
        ),
        (
            r"(?<![A-Za-z0-9])(glpat)-[0-9a-zA-Z_\-]{20}(?![A-Za-z0-9])",
            r"\1-***",
        ),
        (
            r"(?<![A-Za-z0-9])(npm)_[0-9a-zA-Z]{36}(?![A-Za-z0-9])",
            r"\1_***",
        ),
        (
            r"(?<![A-Za-z0-9])(SG)\.[A-Za-z0-9_\-]{22}\.[A-Za-z0-9_\-]{43}(?![A-Za-z0-9])",
            r"\1.***",
        ),
        (
            r"(?<![A-Za-z0-9])(hf)_[A-Za-z0-9]{32,}(?![A-Za-z0-9])",
            r"\1_***",
        ),
        # Slack token family value-shape masking. Sibling-drift closure
        # for the secret-scanner ``_KNOWN_TOKENS`` entries that detect
        # committed Slack tokens across SEVEN issuer prefixes:
        # ``xoxb-`` (Bot Token), ``xoxp-`` (User Token), ``xoxa-``
        # (OAuth Access), ``xoxr-`` (Refresh), ``xoxc-`` (Browser
        # Session), ``xoxd-`` (Cookie Session), ``xoxe-`` /
        # ``xoxe.xoxb-`` / ``xoxe.xoxp-`` (V2 Token Rotation
        # Refresh — direct + chained forms). The scanner closed the
        # *detection* codepath for committed source files in
        # successive rounds; the log-sanitisation codepath was NOT
        # extended in any prior round — bare Slack token shapes in
        # plain log text (Slack provider error responses, application
        # f-string logs, JSON values with NON-sensitive keys, URL
        # paths / query strings) bypassed every existing key/header/
        # URL-credential mask pattern and leaked verbatim into
        # operator log streams plus the public
        # ``docs/feed_health.json`` artefact.
        #
        # Threat model per Slack issuer tier:
        #   * ``xoxb-<digits>-<digits>-<24 alnum>`` — Bot Token. The
        #     workhorse credential for Slack automation: posts to
        #     channels, DMs, reads messages, uploads files, manages
        #     workspace members per the app's installed scopes.
        #     Highest routine-leak severity in the Slack family due
        #     to ubiquity in CI/CD secrets / ``.env`` files.
        #   * ``xoxp-<digits>-<digits>-<digits>-<32 alnum>`` — User
        #     Token. Acts AS the user — full impersonation including
        #     DMs, search, file access, channel history.
        #   * ``xoxa-<body>`` — OAuth Access Token. Configuration
        #     tokens issued via the OAuth flow.
        #   * ``xoxr-<body>`` — Refresh Token. Mints fresh ``xoxb-`` /
        #     ``xoxp-`` access tokens until the refresh token itself
        #     is revoked at slack.com/app-settings.
        #   * ``xoxc-<body>`` — Browser Session Token. Session cookie
        #     extracted via DevTools; canonical "session hijack"
        #     credential. Unattended scripted access to the user's
        #     Slack workspace.
        #   * ``xoxd-<body>`` — Cookie Session Token. Companion to
        #     ``xoxc-``; same blast radius plus 2FA-bypass if the
        #     session was established post-2FA.
        #   * ``xoxe-`` / ``xoxe.xoxb-`` / ``xoxe.xoxp-`` — V2 Token
        #     Rotation Refresh (direct + chained). The chained shape
        #     embeds the rotation chain's identity (``xoxb-`` for
        #     bot rotation, ``xoxp-`` for user rotation).
        #
        # Structural anchors mirror the scanner regexes exactly:
        #   * ``(?<![A-Za-z0-9])`` lookbehind prevents mid-word false
        #     positives (``myxoxb-``, ``0xoxe-`` are preserved).
        #   * ``(?![A-Za-z0-9])`` lookahead bounds the body span.
        #   * Strict per-segment digit lengths for the canonical
        #     ``xoxb-``/``xoxp-`` shapes (10+ digit segments) reject
        #     accidental fragments while accepting every real-shape
        #     token.
        #   * 20+ char body floor for the dash-suffixed variants.
        # The mask preserves the issuer-specific prefix
        # (``xoxb-***``, ``xoxe.xoxb-***`` etc.) for incident-response
        # triage — each tier has a distinct revocation flow
        # (slack.com/app-settings > Workspace tokens for
        # xoxb/xoxp/xoxa/xoxr; password reset + active session
        # termination for xoxc/xoxd; xoxe rotates via the parent
        # token chain).
        #
        # Ordering: ``xoxe.xox[bp]-`` chained patterns are listed
        # BEFORE the bare ``xoxb-``/``xoxp-`` patterns so the more-
        # specific chained shape wins first — preserving the full
        # ``xoxe.xoxb-***`` attribution rather than splitting at the
        # inner ``xoxb-`` boundary. Re-encoding the rotation-chain
        # identity is critical for IR triage because the chained
        # form's revocation flow (rotate the parent ``xoxb-``/
        # ``xoxp-``) differs from the direct ``xoxe-`` form
        # (revoke at refresh-token settings).
        #
        # Idempotence: masked forms (``xoxb-***``, ``xoxe.xoxb-***``)
        # do NOT match any of these regexes because ``*`` is not in
        # any body alphabet AND the masked body length (3 chars) is
        # below every per-family floor (20/24/32).
        #
        # Marker: SENTINEL_SLACK_AIML_TOKEN_LOG_SANITIZATION_DRIFT.
        (
            r"(?<![A-Za-z0-9])(xoxe\.xox[bp])-[0-9a-zA-Z\-]{20,}(?![A-Za-z0-9])",
            r"\1-***",
        ),
        (
            r"(?<![A-Za-z0-9])(xoxe)-[0-9a-zA-Z\-]{20,}(?![A-Za-z0-9])",
            r"\1-***",
        ),
        (
            r"(?<![A-Za-z0-9])(xoxb)-[0-9]{10,}-[0-9]{10,}-[a-zA-Z0-9]{24}(?![A-Za-z0-9])",
            r"\1-***",
        ),
        (
            r"(?<![A-Za-z0-9])(xoxp)-[0-9]{10,}-[0-9]{10,}-[0-9]{10,}-[a-zA-Z0-9]{32}(?![A-Za-z0-9])",
            r"\1-***",
        ),
        (
            r"(?<![A-Za-z0-9])(xox[acdr])-[0-9a-zA-Z\-]{20,}(?![A-Za-z0-9])",
            r"\1-***",
        ),
        # AI/ML Inference Platform tier value-shape masking. Sibling-
        # drift closure for the secret-scanner ``_KNOWN_TOKENS``
        # entries that detect committed AI/ML inference platform
        # tokens (Groq ``gsk_`` / Replicate ``r8_`` / Perplexity
        # ``pplx-`` / xAI ``xai-``; OpenRouter ``sk-or-v1-`` is
        # listed earlier alongside the OpenAI legacy regex to
        # guarantee precedence). The scanner closed the *detection*
        # codepath for committed source files across the 2026-05-16
        # AI/ML Inference Platform tier rounds; the log-sanitisation
        # codepath was NOT extended in any prior round — bare tokens
        # in plain log text leaked verbatim.
        #
        # Threat model per vendor:
        #   * Groq ``gsk_<32+ alnum>`` — LPU-accelerated inference
        #     (LLaMA / Mixtral / Gemma). Billing fraud + free-tier
        #     abuse for unauthorised inference.
        #   * Replicate ``r8_<40 alnum>`` — Model hosting (Stable
        #     Diffusion, custom Cog models). Compute-billing fraud
        #     (GPU inference at issuer's expense) + backdoored Cog
        #     model push if token has push scope.
        #   * Perplexity ``pplx-<32+ alnum>`` — Search-grounded
        #     inference. Billing fraud + search-result exfiltration.
        #   * xAI ``xai-<32+ alnum>`` — Grok inference API. Billing
        #     fraud (Grok-2 / Grok-2 Vision at issuer's expense).
        #   * OpenRouter ``sk-or-v1-<32+ alnum>`` — UNIQUE CROSS-
        #     PLATFORM PIVOT AMPLIFIER (handled by the earlier
        #     OpenRouter regex). A leaked OpenRouter token grants
        #     access to ALL the user's attached provider keys
        #     (Anthropic / OpenAI / Mistral / etc.) through the
        #     aggregator proxy — effectively a multi-vendor
        #     credential-chain pivot.
        #
        # Structural anchors mirror the scanner regexes exactly:
        #   * ``(?<![A-Za-z0-9])`` lookbehind prevents mid-word
        #     false positives (``agsk_``, ``frnd_`` are preserved).
        #   * ``(?![A-Za-z0-9])`` lookahead bounds the body span.
        #   * Strict per-vendor body lengths (32+ for Groq /
        #     Perplexity / xAI; EXACTLY 40 for Replicate per the
        #     scanner regex's exact-length anchor).
        #
        # Idempotence: masked forms (``gsk_***``, ``pplx-***``) do
        # NOT match any of these regexes (mask alphabet excludes
        # ``*``; mask body length 3 is below every floor).
        (
            r"(?<![A-Za-z0-9])(gsk)_[A-Za-z0-9]{32,}(?![A-Za-z0-9])",
            r"\1_***",
        ),
        (
            r"(?<![A-Za-z0-9])(r8)_[A-Za-z0-9]{40}(?![A-Za-z0-9])",
            r"\1_***",
        ),
        (
            r"(?<![A-Za-z0-9])(pplx)-[A-Za-z0-9]{32,}(?![A-Za-z0-9])",
            r"\1-***",
        ),
        (
            r"(?<![A-Za-z0-9])(xai)-[A-Za-z0-9]{32,}(?![A-Za-z0-9])",
            r"\1-***",
        ),
        # DevOps / CI/CD Pipeline + DigitalOcean Cloud token-family
        # value-shape masking. Sibling-drift closure for the secret-
        # scanner ``_KNOWN_TOKENS`` entries that detect committed CI/CD
        # tokens across 4 vendors / 13 issuer prefixes:
        #   * GitLab CI/CD pipeline tier (8): ``glrt-`` (Runner Auth),
        #     ``gldt-`` (Deploy), ``glagent-`` (KAS Agent), ``glft-``
        #     (Feature Flag client), ``glimt-`` (Incoming Mail / Service
        #     Desk), ``glcbt-`` (CI Build per-job), ``glsoat-`` (SCIM
        #     OAuth), ``glptt-`` (Pipeline Trigger).
        #   * CircleCI Personal API Token: ``CCIPAT_<32+>``.
        #   * Buildkite (2): ``bkat_`` (Agent), ``bkua_`` (User Access).
        #   * DigitalOcean cloud (2): ``dop_v1_<64 hex>`` (PAT) +
        #     ``doo_v1_<64 hex>`` (OAuth Refresh).
        # The scanner closed the *detection* codepath for committed
        # source files across the 2026-05-16 / 2026-05-17 rounds; the
        # log-sanitisation codepath was NOT extended in any prior
        # round — bare tokens in plain log text (application f-string
        # logs, upstream error responses echoing the token back, JSON
        # values with NON-sensitive key names, URL paths / query
        # strings) bypassed every existing key/header/URL-credential
        # mask pattern and leaked verbatim into operator log streams
        # plus the public ``docs/feed_health.json`` artefact.
        #
        # Threat model per family:
        #   * GitLab Runner / Deploy / Agent / Pipeline Trigger / CI
        #     Build / Feature Flag / Incoming Mail / SCIM OAuth — CI/CD
        #     code-execution on the project's runners, secrets
        #     exfiltration, cluster control plane (KAS), feature-flag
        #     manipulation, SCIM user-provisioning hijack.
        #   * CircleCI ``CCIPAT_`` — full user-scoped REST-API access:
        #     read every accessible pipeline's build logs (which often
        #     include masked-but-echoed env vars), trigger pipelines
        #     with arbitrary parameters, modify project settings.
        #   * Buildkite ``bkat_`` (Agent) — highest leak surface in the
        #     modern CI stack: rogue agents drain the job queue with
        #     whatever build-secret env vars the pipeline exposes.
        #   * Buildkite ``bkua_`` (User Access) — full user-scoped
        #     access across every accessible Buildkite organisation.
        #   * DigitalOcean ``dop_v1_`` — full account API access
        #     (Droplets, Spaces, Databases, Kubernetes clusters across
        #     every project in the account). ``doo_v1_`` mints fresh
        #     ``dop_v1_`` access tokens until revoked at the OAuth
        #     app authorisation page.
        #
        # Structural anchors mirror the scanner regexes exactly:
        #   * ``(?<![A-Za-z0-9])`` lookbehind prevents mid-word false
        #     positives (``myglrt-``, ``fooCCIPAT_`` are preserved).
        #   * ``(?![A-Za-z0-9])`` lookahead bounds the body span.
        #   * Strict body lengths per vendor canonical format:
        #     glrt/gldt/glft = exactly 20 chars; glsoat = 20+; glimt =
        #     25+; glagent = 50+; glcbt = ``<alnum>_<20+>``; glptt =
        #     exactly 40; CCIPAT_ = 32+; bkat_/bkua_ = 40+ alnum;
        #     dop_v1_/doo_v1_ = exactly 64 lowercase hex.
        # The mask preserves the issuer-specific prefix
        # (``glrt-***``, ``CCIPAT_***``, ``bkat_***``, ``dop_v1_***``
        # etc.) for incident-response triage — each tier has a distinct
        # revocation flow:
        #   * GitLab CI/CD — project / group / instance access-tokens
        #     pages (each token type has its own admin sub-page).
        #   * CircleCI — app.circleci.com/settings/user/tokens > Revoke.
        #   * Buildkite Agent — buildkite.com/organizations/<org>/agents.
        #   * Buildkite User — buildkite.com/user/api-access-tokens.
        #   * DigitalOcean — cloud.digitalocean.com/account/api/tokens.
        #
        # Idempotence: masked forms (``glrt-***``, ``CCIPAT_***``,
        # ``bkat_***``, ``dop_v1_***``) do NOT match any of these
        # regexes because ``*`` is not in any body alphabet AND the
        # masked body length (3 chars) is below every per-family floor
        # (20/25/27/32/40/50/64).
        #
        # Marker: SENTINEL_CICD_DEVOPS_TOKEN_LOG_SANITIZATION_DRIFT.
        #
        # Ordering: ``glcbt-`` (special two-part body) listed BEFORE
        # the other GitLab CI/CD prefixes so the more-specific
        # ``glcbt-<alnum>_<20+>`` shape wins first — preserving the
        # full ``glcbt-***`` attribution rather than splitting the
        # body span. The other GitLab prefixes are mutually exclusive
        # at the prefix level (glrt vs gldt vs glft etc.) so order
        # among them is documentation aid only.
        (
            r"(?<![A-Za-z0-9])(glcbt)-[A-Za-z0-9]+_[A-Za-z0-9_\-]{20,}(?![A-Za-z0-9])",
            r"\1-***",
        ),
        (
            r"(?<![A-Za-z0-9])(glrt|gldt|glft)-[A-Za-z0-9_\-]{20}(?![A-Za-z0-9])",
            r"\1-***",
        ),
        (
            r"(?<![A-Za-z0-9])(glagent)-[A-Za-z0-9_\-]{50,}(?![A-Za-z0-9])",
            r"\1-***",
        ),
        (
            r"(?<![A-Za-z0-9])(glimt)-[A-Za-z0-9_\-]{25,}(?![A-Za-z0-9])",
            r"\1-***",
        ),
        (
            r"(?<![A-Za-z0-9])(glsoat)-[A-Za-z0-9_\-]{20,}(?![A-Za-z0-9])",
            r"\1-***",
        ),
        (
            r"(?<![A-Za-z0-9])(glptt)-[0-9a-zA-Z_\-]{40}(?![A-Za-z0-9])",
            r"\1-***",
        ),
        (
            r"(?<![A-Za-z0-9])(CCIPAT)_[A-Za-z0-9_\-]{32,}(?![A-Za-z0-9])",
            r"\1_***",
        ),
        (
            r"(?<![A-Za-z0-9])(bkat|bkua)_[A-Za-z0-9]{40,}(?![A-Za-z0-9])",
            r"\1_***",
        ),
        (
            r"(?<![A-Za-z0-9])(dop_v1|doo_v1)_[a-f0-9]{64}(?![A-Za-z0-9])",
            r"\1_***",
        ),
        # SaaS / Communications / Workspace / Observability / Secret-Manager
        # token tier value-shape masking. Sibling-drift closure for the
        # secret-scanner ``_KNOWN_TOKENS`` entries detecting committed
        # tokens across these high-blast-radius issuer families that the
        # log-sanitisation codepath was NOT extended for in any prior
        # round — bare tokens in plain log text (application f-string
        # logs, upstream error responses echoing the token back, JSON
        # values with non-sensitive key names, URL paths / query strings
        # with non-sensitive parameter names) bypassed every existing
        # key/header/URL-credential mask pattern and leaked verbatim
        # into operator log streams plus the public
        # ``docs/feed_health.json`` artefact.
        #
        # Families covered (10 issuers / 12 patterns):
        #
        # Universal Auth tier — the single highest-blast-radius gap:
        #   * ``eyJ<10+>.<10+>.<20+>`` — JSON Web Token (JWT). The
        #     canonical bearer credential for Auth0, Okta, AWS Cognito,
        #     Google Identity, Azure AD, custom OAuth/OIDC providers and
        #     every modern SaaS identity provider. Dots are OUTSIDE the
        #     entropy fallback's ``[A-Za-z0-9+/=_-]`` alphabet so without
        #     this specific pattern only ONE segment is matched at a
        #     time and the full-token span (the bearer credential) is
        #     silently lost.
        #
        # Workspace SaaS tier:
        #   * ``ATATT3xFfGF0<100+>`` — Atlassian API Token (Jira /
        #     Confluence / Trello REST API). Full Cloud-API scope.
        #   * ``lin_api_<32+>`` — Linear API Key. Full issue tracker /
        #     project-management GraphQL scope.
        #   * ``secret_<43>`` — Notion Integration Token (legacy
        #     format). Full workspace read/write for shared content.
        #   * ``ntn_<43+>`` — Notion Modern Integration Token. Same
        #     blast radius as the legacy ``secret_`` form.
        #   * ``PMAK-<24 hex>-<34 hex>`` — Postman API Key. Full
        #     workspace collections / environments / mocks scope.
        #
        # Observability tier:
        #   * ``sntrys_<30+>`` — Sentry Auth Token. Org-level Sentry
        #     API access (issue/event data, releases, debug files,
        #     source maps, member list, webhook configuration).
        #
        # Secret-Manager tier — **HIGHEST blast-radius amplifier**:
        #   * ``dp.<pt|st|sa|ct|scim|audit>.<43>`` — Doppler Tokens.
        #     Six role variants. One leaked Doppler token grants read
        #     access to every secret stored in the accessible
        #     projects/configs (database credentials, third-party API
        #     keys, OAuth client secrets, signing keys are all
        #     routinely stored in Doppler environments). One leak
        #     compromises every downstream credential.
        #
        # Communications tier:
        #   * ``<3-14 digits>:<35>`` — Telegram Bot Token. Full bot
        #     impersonation in every chat the bot is added to.
        #   * ``AC<32 hex>`` — Twilio Account SID. Principal credential
        #     for the project; pairs with the Auth Token for full
        #     telephony API access. 2FA-bypass primitive via SMS.
        #   * ``SK<32 hex>`` — Twilio API Key SID. Fine-grained scoped
        #     credential; pairs with a separate secret.
        #   * ``key-<32 hex>`` — Mailgun Private API Key. Mail-send
        #     access from the project's authenticated domain
        #     (phishing amplification leveraging SPF / DKIM).
        #
        # Structural anchors mirror the scanner regexes exactly:
        #   * ``(?<![A-Za-z0-9])`` lookbehind prevents mid-word false
        #     positives (``myATATT3xFfGF0``, ``foosntrys_`` are
        #     preserved).
        #   * ``(?![A-Za-z0-9])`` lookahead bounds the body span.
        #   * Strict body lengths / alphabets per vendor canonical
        #     format reject accidental fragments while accepting every
        #     real-shape token.
        # Each mask preserves the issuer-specific prefix (``eyJ***``,
        # ``ATATT3xFfGF0***``, ``sntrys_***``, ``dp.pt.***``,
        # ``PMAK-***``, ``key-***`` etc.) for incident-response triage
        # because each tier has a distinct revocation flow.
        #
        # Idempotence: masked forms (``eyJ***``, ``ATATT3xFfGF0***``,
        # ``sntrys_***``, ``dp.pt.***``, ``PMAK-***``, etc.) do NOT
        # match any of these regexes because ``*`` is not in any body
        # alphabet AND the masked body length (3 chars) is below every
        # per-family floor (20/30/32/35/43/100).
        #
        # Marker: SENTINEL_SAAS_COMMS_SECRET_MANAGER_TOKEN_LOG_SANITIZATION_DRIFT.
        #
        # Mapbox Access Token value-shape masking. Sibling-drift closure
        # for the secret-scanner ``_KNOWN_TOKENS`` entry that detects
        # committed Mapbox tokens
        # (``(?:pk|sk|tk)\.eyJ<3-segment-JWT-body>``). The scanner closes
        # the *detection* codepath via a dedicated entry placed BEFORE
        # the generic JWT entry so the more-specific Mapbox attribution
        # wins via the ``covered_ranges`` arbitration; this mask closes
        # the *sanitisation* codepath via a dedicated regex placed
        # BEFORE the generic JWT regex so the more-specific replacement
        # wins before the JWT mask strips the inner ``eyJ<body>`` span.
        #
        # ORDER REQUIREMENT: this entry MUST appear BEFORE the generic
        # JWT mask immediately below — otherwise the JWT regex matches
        # the inner ``eyJ<body>`` first and replaces it with ``eyJ***``,
        # leaving ``pk.eyJ***`` / ``sk.eyJ***`` / ``tk.eyJ***`` in the
        # output (the leading scope-tier prefix is preserved by
        # accident, but the IR triage path sees a JWT-attributed
        # finding rather than a Mapbox-attributed one — the operator
        # might not realise the leaked credential demands the
        # account.mapbox.com revocation flow rather than a generic
        # JWT-issuer revocation flow). Placing Mapbox first preserves
        # the full ``(?:pk|sk|tk)\.eyJ***`` attribution span so the
        # responder identifies the Mapbox scope tier at a glance.
        #
        # Threat model per scope tier (see the matching scanner entry
        # in ``src/utils/secret_scanner.py`` for full per-tier blast
        # radius analysis):
        #   * ``pk.`` — Public Access Token. Quota theft (overage
        #     billing fraud), DDoS-amplification against the issuing
        #     account's Mapbox quota.
        #   * ``sk.`` — **SECRET Access Token. HIGHEST blast radius in
        #     the Mapbox family.** Full account-write scopes:
        #     overwrite production tilesets (route-hijack / map-
        #     manipulation amplifier), mint new ``sk.`` tokens for
        #     persistence, exfiltrate billing / analytics data,
        #     manipulate the production maps consumed by downstream
        #     consumers and integrators.
        #   * ``tk.`` — Temporary Access Token (ephemeral scope,
        #     short TTL). Distinct attribution lets IR verify the
        #     leak window aligned with token TTL.
        #
        # Structural anchors mirror the scanner regex exactly:
        #   * ``(?<![A-Za-z0-9])`` lookbehind prevents mid-identifier
        #     false positives (``foosk.eyJ...`` is NOT matched because
        #     ``s`` is preceded by ``o`` alphanumeric — the existing
        #     JWT mask still catches the inner JWT in that pathological
        #     case so security is preserved, just via the JWT
        #     attribution rather than Mapbox).
        #   * ``(?![A-Za-z0-9])`` lookahead bounds the body span.
        #   * Strict ``[A-Za-z0-9_\-]`` per-segment base64url alphabet
        #     mirrors the JWT mask; ``{10,10,20}`` per-segment floors
        #     mirror the JWT mask AND match real-world Mapbox token
        #     shapes.
        # The mask preserves the issuer-specific scope-tier prefix
        # (``pk.eyJ***`` / ``sk.eyJ***`` / ``tk.eyJ***``) for IR
        # triage — the operator immediately identifies the Mapbox
        # scope tier and navigates to account.mapbox.com/access-tokens/
        # for the appropriate revocation flow.
        #
        # Idempotence: masked forms (``pk.eyJ***`` / ``sk.eyJ***`` /
        # ``tk.eyJ***``) do NOT re-match this regex because ``*`` is
        # OUTSIDE the per-segment alphabet ``[A-Za-z0-9_\-]`` AND the
        # masked body length (3 chars per segment) is below the
        # per-segment floor (10/10/20). The JWT mask immediately
        # below ALSO does not re-match (the ``eyJ`` anchor still
        # requires 10+ chars after, which the masked ``***`` form
        # does not satisfy).
        #
        # Marker: SENTINEL_BITBUCKET_MAPBOX_TOKEN_DRIFT.
        (
            r"(?<![A-Za-z0-9])((?:pk|sk|tk)\.eyJ)"
            r"[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{20,}"
            r"(?![A-Za-z0-9])",
            r"\1***",
        ),
        (
            r"(?<![A-Za-z0-9])(eyJ)[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{20,}(?![A-Za-z0-9])",
            r"\1***",
        ),
        # Bitbucket App Password / Repository Access Token value-shape
        # masking. Sibling-drift closure for the secret-scanner
        # ``_KNOWN_TOKENS`` entry that detects committed Bitbucket
        # tokens (``ATBB<24+ alnum body>``). The scanner closes the
        # *detection* codepath for committed source files (placed near
        # the Atlassian Cloud API Token entry); this mask closes the
        # *sanitisation* codepath via the matching value-shape regex.
        # Without this mask, a bare ``ATBB<body>`` token shape in
        # plain log text (application f-string logs, upstream
        # Bitbucket API error responses echoing the token back, JSON
        # values with non-sensitive key names, URL paths embedding
        # the token, exception messages routed through
        # ``_sanitize_exception_msg``) bypasses every existing
        # key/header/URL-credential mask pattern and leaks verbatim
        # into operator log streams and the public
        # ``docs/feed_health.json`` artefact.
        #
        # Threat model (mirror the scanner's blast-radius analysis):
        # leak grants the issuing principal's Bitbucket Cloud scope
        # per the token's configured permissions:
        #   * ``repository:read`` — code disclosure, IP exfiltration.
        #   * ``repository:write`` — backdoored commits to protected
        #     branches (supply-chain compromise primitive).
        #   * ``workspace:admin`` — modify workspace member roles,
        #     add attacker collaborators, exfiltrate every repo in
        #     the workspace.
        #   * App Password tier — additionally grants the issuing
        #     user's read access across EVERY accessible workspace
        #     (multi-workspace pivot amplifier).
        # Blast radius mirrors the GitHub PAT (``ghp_***``) and
        # GitLab PAT (``glpat-***``) families on their respective
        # platforms.
        #
        # The mask preserves the issuer-specific prefix (``ATBB***``)
        # for IR triage — the revocation flow lives at
        # bitbucket.org/account/settings/app-passwords/ (App
        # Passwords) or the per-project / per-workspace / per-repo
        # Access Tokens settings pages (resource-scoped variants).
        # All these revocation flows are DISTINCT from the Atlassian
        # Cloud API Token revocation flow
        # (id.atlassian.com/manage-profile/security/api-tokens) used
        # for the existing ``ATATT3xFfGF0***`` mask, so per-issuer
        # attribution is critical for IR triage to land on the
        # correct admin panel.
        #
        # Structural anchors mirror the scanner regex exactly:
        #   * ``(?<![A-Za-z0-9])`` lookbehind prevents mid-identifier
        #     false positives (``XATBB<body>``, ``0ATBB<body>`` are
        #     preserved).
        #   * ``(?![A-Za-z0-9])`` lookahead bounds the body span.
        #   * Strict ``[A-Za-z0-9]`` body alphabet (no ``_-``)
        #     distinguishes Bitbucket from the GitHub / GitLab / NPM
        #     token families and rejects accidental fragments.
        #   * 24-char body floor matches the permissive minimum for
        #     legacy Bitbucket Server access tokens while accepting
        #     every real-world Bitbucket Cloud Repository / Workspace
        #     / Project Access Token (canonical 32-40 char body).
        #
        # Idempotence: masked form (``ATBB***``) does NOT re-match
        # because ``*`` is OUTSIDE the body alphabet ``[A-Za-z0-9]``
        # AND the masked body length (3 chars) is below the 24-char
        # floor.
        #
        # Cross-family mutex: ``ATBB`` vs. ``ATATT3xFfGF0`` prefixes
        # are disjoint at the 5th-character level (``ATBB`` vs.
        # ``ATATT3xFfGF0`` — the latter is 12 chars and starts with
        # ``ATATT`` after the 4th char), so no token can match both
        # patterns and the existing Atlassian Cloud API Token mask
        # remains correctly attributed.
        (
            r"(?<![A-Za-z0-9])(ATBB)[A-Za-z0-9]{24,}(?![A-Za-z0-9])",
            r"\1***",
        ),
        # Discord Bot Token: ``<base64url(snowflake-id)>.<base64url(
        # timestamp)>.<HMAC>``. Three dot-separated base64url segments —
        # structurally identical to JWT but with a snowflake-ID-based
        # first segment instead of the JOSE ``eyJ`` header. Discord
        # stringifies the user ID (decimal digits) before base64-
        # encoding, so the first segment ALWAYS starts with ``[MNO]``
        # (decimal ``1``-``3`` → ``M``, ``4``-``7`` → ``N``, ``8``-``9``
        # → ``O``); JWTs ALWAYS start with ``eyJ``. The two leading-
        # character classes are disjoint, so no token can match both
        # patterns and the JWT regex above is mutually exclusive at the
        # leading-char level. Sibling-drift closure for the 2026-05-08
        # Round-3 secret-scanner round that added the Discord pattern
        # to ``_KNOWN_TOKENS`` but left the log-sanitisation codepath
        # untouched — Discord was explicitly named in the deferred
        # backlog of every 2026-05-17 multi-vendor / SaaS / supply-
        # chain log-sanitisation round but never closed until now.
        #
        # Threat model: a leaked Discord bot token grants FULL bot
        # privileges in every guild the bot is invited to (read/write
        # all visible messages, kick/ban users, edit channels and
        # roles, run any registered slash commands, read voice/DM
        # history with appropriate scopes). The revocation flow lives
        # at https://discord.com/developers/applications/ Developer
        # Portal — distinct from any other vendor's, so issuer-
        # specific attribution accelerates IR triage. Pre-fix the dots
        # are OUTSIDE the entropy fallback's ``[A-Za-z0-9+/=_-]``
        # alphabet, so without this specific pattern only ONE segment
        # at a time matches against the existing key/header/URL
        # masks, losing both the issuer attribution AND the full
        # credential span.
        #
        # The mask preserves the leading ``[MNO]`` Discord-shape
        # disambiguator AND the three-segment structure
        # (``M***.***.***``) so IR triage immediately recognises the
        # Discord shape (vs. JWT ``eyJ***`` shape). Idempotence:
        # ``M***.***.***`` does NOT re-match because ``*`` is not in
        # the body alphabet AND the masked segment length (3 chars)
        # is below every per-segment floor (22/6/27).
        #
        # Marker: SENTINEL_DISCORD_BOT_TOKEN_LOG_SANITIZATION_DRIFT.
        (
            r"(?<![A-Za-z0-9])([MNO])[A-Za-z0-9_\-]{22,27}\.[A-Za-z0-9_\-]{6,7}\.[A-Za-z0-9_\-]{27,}(?![A-Za-z0-9])",
            r"\1***.***.***",
        ),
        (
            r"(?<![A-Za-z0-9])(ATATT3xFfGF0)[A-Za-z0-9_=\-]{100,}(?![A-Za-z0-9])",
            r"\1***",
        ),
        (
            r"(?<![A-Za-z0-9])(sntrys)_[A-Za-z0-9_=\-]{30,}(?![A-Za-z0-9])",
            r"\1_***",
        ),
        (
            r"(?<![A-Za-z0-9])(lin_api)_[A-Za-z0-9]{32,}(?![A-Za-z0-9])",
            r"\1_***",
        ),
        # Notion: ``secret_<43 alnum>`` legacy + ``ntn_<43+ extended>``
        # modern. Two patterns because the bodies have different
        # alphabets (legacy strict alnum, modern includes ``_``/``-``)
        # and different floor lengths, so a single combined regex would
        # either under-match the modern form or over-match the legacy
        # one.
        (
            r"(?<![A-Za-z0-9])(secret)_[A-Za-z0-9]{43}(?![A-Za-z0-9])",
            r"\1_***",
        ),
        (
            r"(?<![A-Za-z0-9])(ntn)_[A-Za-z0-9_\-]{43,}(?![A-Za-z0-9])",
            r"\1_***",
        ),
        (
            r"(?<![A-Za-z0-9])(PMAK)-[a-fA-F0-9]{24}-[a-fA-F0-9]{34}(?![A-Za-z0-9])",
            r"\1-***",
        ),
        # Doppler: multi-segment ``dp.<role>.<43 alnum>``. The two
        # literal ``.`` separators are outside the entropy fallback's
        # alphabet — the canonical "entropy-fallback-bypass" shape.
        # Role alternation matches the six documented Doppler token
        # roles (personal / service / service-account / CLI / SCIM
        # / audit) — any other role suffix is rejected.
        (
            r"(?<![A-Za-z0-9])(dp\.(?:pt|st|sa|ct|scim|audit))\.[A-Za-z0-9]{43}(?![A-Za-z0-9])",
            r"\1.***",
        ),
        # Telegram Bot Token: ``<3-14 digits>:<35 chars from
        # [A-Za-z0-9_-]>``. The bot-ID (digits) is not secret on its
        # own (Telegram exposes bot user IDs publicly) but masking
        # only the body after ``:`` preserves the bot-identification
        # span for incident-response triage while still suppressing
        # the credential. Marker pattern: capture the digit prefix
        # and replace only the colon-separated body.
        (
            r"(?<![A-Za-z0-9])([0-9]{3,14}):[a-zA-Z0-9_\-]{35}(?![A-Za-z0-9])",
            r"\1:***",
        ),
        # Twilio Account SID + API Key SID: uppercase ``AC``/``SK``
        # + 32 lowercase hex. Case-sensitive: ``sk-<48>`` (OpenAI
        # legacy) and ``sk_live_<24>`` (Stripe) are lowercase + have
        # a dash/underscore inside the prefix, so they are mutually
        # exclusive with the Twilio shape. The 32 lowercase-hex body
        # is the canonical Twilio SID format.
        (
            r"(?<![A-Za-z0-9])(AC|SK)[a-f0-9]{32}(?![A-Za-z0-9])",
            r"\1***",
        ),
        # Mailgun Private API Key: ``key-<32 lowercase hex>``. The
        # ``key-`` prefix is intentionally short — strict 32-char
        # lowercase-hex body is the disambiguator. The ``(?<![A-Za-z0-9])``
        # lookbehind prevents ``api-key-<hex>``, ``foo-key-<hex>`` and
        # similar legitimate placeholder shapes from being falsely
        # masked.
        (
            r"(?<![A-Za-z0-9])(key)-[a-f0-9]{32}(?![A-Za-z0-9])",
            r"\1-***",
        ),
        # Supply-Chain / E-Commerce / PaaS / Observability / Email-Platform
        # token tier value-shape masking. Sibling-drift closure for the
        # secret-scanner ``_KNOWN_TOKENS`` entries detecting committed
        # tokens across the remaining named-but-deferred backlog from the
        # 2026-05-17 SaaS / Comms / Workspace / Observability / Secret-
        # Manager round — every bare token shape below leaked verbatim
        # through ``sanitize_log_message`` pre-fix (plain log lines, JSON
        # values with non-sensitive keys, URL paths / query strings with
        # non-sensitive parameter names, exception messages routed through
        # ``_sanitize_exception_msg``).
        #
        # Families covered (10 issuers / 11 patterns):
        #
        # Supply-Chain tier:
        #   * ``pypi-<20+ from [A-Za-z0-9_-]>`` — PyPI API Token. Issued
        #     at pypi.org/manage/account/token/. Leak grants publish
        #     access to every accessible PyPI project — canonical
        #     supply-chain compromise primitive: a hostile actor can push
        #     a backdoored wheel of a popular package and every CI run
        #     of every downstream consumer pulls the malicious version.
        #
        # E-Commerce tier (HIGH payment-fraud blast radius):
        #   * ``shpat_<32 mixed-case hex>`` — Shopify Admin API Access
        #     Token. Full storefront/admin scope: read every order /
        #     customer record (PII + payment metadata), modify product
        #     catalogue (BEC-style price manipulation), drain Shopify
        #     Payments balance via refunds-to-attacker-IBAN flow.
        #   * ``shpss_<32 hex>`` — Shopify Shared Secret. Webhook HMAC
        #     signing key — forge any webhook payload from any private
        #     Shopify app.
        #   * ``shppa_<32 hex>`` — Shopify Partner API Access Token.
        #     Cross-store: every store the Partner is installed on.
        #   * ``shpca_<32 hex>`` — Shopify Custom App Access Token.
        #     Per-store but the same Admin API scope as ``shpat_``.
        #   * ``ck_<32+ alnum>`` — WooCommerce Consumer Key. Pairs with
        #     ``cs_`` for full WooCommerce REST API access: read every
        #     order, customer, product. Drain via refund flow analogous
        #     to Shopify Admin API.
        #   * ``cs_<32+ alnum>`` — WooCommerce Consumer Secret. The
        #     paired secret for ``ck_``.
        #   * ``EAAA<60+ from [A-Za-z0-9_-]>`` — Square Access Token.
        #     Full payment processing access: read transaction history
        #     (PII + card-fingerprint metadata), issue refunds, create
        #     new charges. Direct payment fraud primitive.
        #
        # PaaS / Edge-Runtime tier (deployment hijack):
        #   * ``nfp_<40+ alnum>`` — Netlify Personal Access Token. Full
        #     account scope: redirect every site's deploys to an
        #     attacker-controlled build, exfiltrate every env-var that
        #     a build can read (the canonical landing zone for AWS /
        #     Stripe / database creds), modify DNS / SSL config to
        #     hijack the production domain (then capture every
        #     authenticated session via a forged TLS chain).
        #   * ``rnd_<40+ from [A-Za-z0-9_-]>`` — Render API Key. Same
        #     deployment-hijack blast radius as Netlify for services
        #     hosted on Render's PaaS.
        #   * ``FlyV1 (?:fm1|fm2|fo1)_<50+>`` — Fly.io Macaroon Token.
        #     Edge-runtime hijack: deploy malicious code to every Fly
        #     app the token's macaroon scope grants, modify routing /
        #     Wireguard peers / Anycast routes, rotate billing
        #     credentials. The ``FlyV1`` prefix uniquely contains a
        #     LITERAL SPACE between the issuer keyword and the
        #     macaroon-discriminator body — mirroring the scanner's
        #     exact pattern.
        #
        # Observability tier (data-exfiltration + APM leak amplifier):
        #   * ``NRAK-<27 uppercase alnum>`` — New Relic User Key.
        #     Full user-scope account access: read every APM stream's
        #     payload (which routinely contains debug-logged tokens
        #     from production traces — secondary credential leak
        #     amplifier).
        #   * ``NRRA-<40 mixed-case hex>`` — New Relic REST API Key.
        #     REST API access scoped per the key's privilege level.
        #   * ``NRII-<32 mixed-case hex>`` — New Relic Insights Insert
        #     Key. NRDB write access; can fabricate / overwrite APM
        #     events to mask intrusion artifacts.
        #
        # Email-Platform tier (phishing amplification):
        #   * ``xkeysib-<64 hex>-<16 alnum>`` — Brevo (Sendinblue) API
        #     Key. Marketing + transactional email vendor. Two-part
        #     body shape: a leak grants the attacker the ability to
        #     send mail FROM the project's authenticated sending
        #     domain (phishing amplification leveraging the account's
        #     existing SPF / DKIM / DMARC reputation), export the
        #     full subscriber CSV (PII exfiltration), and access the
        #     transactional logs.
        #   * ``<32 lowercase hex>-us<digits>`` — Mailchimp API Key.
        #     The ``us<region>`` datacenter routing identifier is
        #     PRESERVED in the mask (``***-us20``) for IR-routing
        #     attribution since mailchimp.com's API endpoints are
        #     keyed off the region. Mask preserves the suffix so the
        #     responder can locate the correct admin subdomain
        #     (``admin.mailchimp.com`` for direct-routed access).
        #
        # Structural anchors mirror the scanner regexes exactly:
        #   * ``(?<![A-Za-z0-9])`` lookbehind prevents mid-word false
        #     positives (``xpypi-``, ``foonfp_``, ``Ashpat_`` are
        #     preserved).
        #   * ``(?![A-Za-z0-9])`` lookahead bounds the body span.
        #   * Strict body lengths / alphabets per vendor canonical
        #     format reject accidental fragments while accepting every
        #     real-shape token.
        #
        # Each mask preserves the issuer-specific prefix (``pypi-***``,
        # ``shpat_***``, ``EAAA***``, ``nfp_***``, ``FlyV1 fm1_***``,
        # ``NRAK-***``, ``xkeysib-***`` etc.) for incident-response
        # triage because each tier has a distinct revocation flow:
        #   * PyPI — pypi.org/manage/account/token/ > Revoke.
        #   * Shopify Admin/Custom/Partner/Shared-Secret — shopify.com
        #     admin panel > Apps > Custom apps > Revoke.
        #   * WooCommerce — site admin > WooCommerce > Settings >
        #     Advanced > REST API > Revoke.
        #   * Square — squareup.com/dashboard > Apps > Revoke.
        #   * Netlify — app.netlify.com/user/applications > Revoke.
        #   * Render — render.com/u/settings > API Keys > Revoke.
        #   * Fly.io — fly.io/dashboard/<org>/tokens > Revoke.
        #   * New Relic — one.newrelic.com/api-keys > Revoke.
        #   * Brevo — app.brevo.com/settings/keys/api > Delete.
        #   * Mailchimp — mailchimp.com/account/api/ > Revoke.
        #
        # Idempotence: masked forms (``pypi-***``, ``shpat_***``,
        # ``EAAA***``, ``nfp_***``, ``FlyV1 fm1_***``, ``NRAK-***``,
        # ``xkeysib-***``, ``***-us20``) do NOT match any of these
        # regexes because ``*`` is not in any body alphabet AND the
        # masked body length (3 chars) is below every per-family floor
        # (20/27/32/40/50/60/64).
        #
        # Marker: SENTINEL_SUPPLY_CHAIN_ECOMMERCE_PAAS_OBSERVABILITY_EMAIL_TOKEN_LOG_SANITIZATION_DRIFT.
        #
        # Ordering: ``shpat|shpss|shppa|shpca`` are grouped (mutually
        # exclusive at the prefix level, same body alphabet/length).
        # ``ck|cs`` are grouped (mutually exclusive at the prefix level,
        # same body alphabet/length floor). ``NRAK|NRRA|NRII`` are kept
        # separate because each has a distinct body alphabet (NRAK
        # uppercase alnum, NRRA/NRII mixed-case hex).
        (
            r"(?<![A-Za-z0-9])(pypi)-[0-9a-zA-Z_\-]{20,}(?![A-Za-z0-9])",
            r"\1-***",
        ),
        (
            r"(?<![A-Za-z0-9])(xkeysib)-[a-f0-9]{64}-[A-Za-z0-9]{16}(?![A-Za-z0-9])",
            r"\1-***",
        ),
        (
            r"(?<![A-Za-z0-9])(nfp)_[A-Za-z0-9]{40,}(?![A-Za-z0-9])",
            r"\1_***",
        ),
        (
            r"(?<![A-Za-z0-9])(rnd)_[A-Za-z0-9_\-]{40,}(?![A-Za-z0-9])",
            r"\1_***",
        ),
        (
            r"(?<![A-Za-z0-9])(NRAK)-[A-Z0-9]{27}(?![A-Za-z0-9])",
            r"\1-***",
        ),
        (
            r"(?<![A-Za-z0-9])(NRRA)-[a-fA-F0-9]{40}(?![A-Za-z0-9])",
            r"\1-***",
        ),
        (
            r"(?<![A-Za-z0-9])(NRII)-[a-fA-F0-9]{32}(?![A-Za-z0-9])",
            r"\1-***",
        ),
        (
            r"(?<![A-Za-z0-9])(FlyV1 (?:fm[12]|fo1))_[A-Za-z0-9_=\-]{50,}(?![A-Za-z0-9])",
            r"\1_***",
        ),
        (
            r"(?<![A-Za-z0-9])(EAAA)[A-Za-z0-9_\-]{60,}(?![A-Za-z0-9])",
            r"\1***",
        ),
        (
            r"(?<![A-Za-z0-9])(shpat|shpss|shppa|shpca)_[a-fA-F0-9]{32}(?![A-Za-z0-9])",
            r"\1_***",
        ),
        (
            r"(?<![A-Za-z0-9])(ck|cs)_[a-zA-Z0-9]{32,}(?![A-Za-z0-9])",
            r"\1_***",
        ),
        # Mailchimp API Key: ``<32 lowercase hex>-us<digits>``. NO issuer
        # prefix — purely structural. The mask preserves the ``-us<region>``
        # datacenter routing suffix so the incident responder can locate
        # the correct admin subdomain (the ``us<region>`` selector is also
        # the per-tenant admin host like ``us20.admin.mailchimp.com``);
        # the 32-hex body span is the credential and is the part masked
        # to ``***``.
        (
            r"(?<![A-Za-z0-9])[a-f0-9]{32}-(us[0-9]{1,3})(?![A-Za-z0-9])",
            r"***-\1",
        ),
        # Figma + Tailscale token-family value-shape masking. Sibling-
        # drift closure for the secret-scanner ``_KNOWN_TOKENS`` entries
        # added in the same round — Figma Personal Access Token
        # (``figd_<43>``) and Tailscale auth/api/client/webhook keys
        # (``tskey-(?:auth|api|client|webhook)-<id>-<secret>``). The
        # log-sanitisation codepath MUST mask these alongside the
        # scanner detection codepath; without value-shape masking, a
        # bare leaked token in plain log text (application f-string
        # logs, upstream error responses echoing the token back, JSON
        # values without sensitive key names, URL paths / query strings
        # with NON-sensitive parameter names, exception messages routed
        # through ``_sanitize_exception_msg``) bypasses every existing
        # key/header/URL-credential mask pattern and leaks verbatim
        # into operator log streams and the public
        # ``docs/feed_health.json`` artefact.
        #
        # Threat model:
        #   * Figma PAT ``figd_<43>`` — Full design-collaboration scope
        #     for the issuing user across every accessible team /
        #     project / file. The Figma ``X-Figma-Token`` header is
        #     already in ``_SENSITIVE_HEADERS``; this entry closes the
        #     companion VALUE-shape gap (header name was redacted, but
        #     the VALUE embedded in JSON / URL paths / exception text
        #     leaked verbatim).
        #   * Tailscale ``tskey-auth-`` — Auth Key: attach a rogue node
        #     to the victim's private overlay network. The rogue node
        #     sees every subnet-routed service AND pivots laterally as
        #     a trusted peer.
        #   * Tailscale ``tskey-api-`` — Admin REST API access: modify
        #     ACLs (open every tailnet device to the attacker), rotate
        #     DNS configuration (DNS-rebinding amplifier), add/remove
        #     users, mint fresh auth keys.
        #   * Tailscale ``tskey-client-`` — OAuth client secret: mints
        #     fresh OAuth access tokens until revocation.
        #   * Tailscale ``tskey-webhook-`` — Webhook signing secret:
        #     forge tailnet event payloads accepted as authentic by
        #     downstream consumers (state-machine confusion attacks).
        #
        # Structural anchors mirror the scanner regexes exactly:
        #   * ``(?<![A-Za-z0-9])`` lookbehind prevents mid-word false
        #     positives (``myfigd_<body>``, ``Xtskey-auth-...`` are
        #     preserved).
        #   * ``(?![A-Za-z0-9])`` lookahead bounds the body span.
        #   * Strict body lengths per vendor canonical format: Figma
        #     EXACTLY 43 chars from ``[A-Za-z0-9_-]``; Tailscale
        #     ``<tier>`` from a strict 4-keyword alternation,
        #     ``<keyID>`` 8+ alnum, ``<keySecret>`` 20+ alnum.
        # The masks preserve issuer-specific prefixes:
        #   * Figma → ``figd_***`` (revocation flow at figma.com/
        #     settings/personal-access-tokens).
        #   * Tailscale → ``tskey-auth-***`` / ``tskey-api-***`` /
        #     ``tskey-client-***`` / ``tskey-webhook-***`` (per-tier
        #     attribution; each tier has a distinct revocation sub-page
        #     under login.tailscale.com/admin/settings).
        #
        # Idempotence: masked forms (``figd_***``, ``tskey-auth-***``,
        # etc.) do NOT re-match because ``*`` is OUTSIDE every body
        # alphabet AND the masked body length (3 chars) is below every
        # per-family floor (43 / 8 / 20).
        #
        # Cross-family mutex: ``figd_`` vs. ``tskey-`` prefixes are
        # disjoint at the leading-character level, so no token can
        # match both patterns.
        #
        # Marker: SENTINEL_FIGMA_TAILSCALE_TOKEN_DRIFT.
        (
            r"(?<![A-Za-z0-9])(figd)_[A-Za-z0-9_\-]{43}(?![A-Za-z0-9])",
            r"\1_***",
        ),
        (
            r"(?<![A-Za-z0-9])(tskey-(?:auth|api|client|webhook))-"
            r"[A-Za-z0-9]{8,}-[A-Za-z0-9]{20,}(?![A-Za-z0-9])",
            r"\1-***",
        ),
        # Dropbox + Pulumi token-family value-shape masking. Sibling-
        # drift closure for the secret-scanner ``_KNOWN_TOKENS`` entries
        # added in the same round — Dropbox Short-Lived Access Token
        # (``sl.<base64url body 40+>``) and Pulumi Access Token
        # (``pul-<40 lowercase hex>``). The log-sanitisation codepath
        # MUST mask these alongside the scanner detection codepath;
        # without value-shape masking, a bare leaked token in plain
        # log text (application f-string logs, upstream error responses
        # echoing the token back, JSON values without sensitive key
        # names, URL paths / query strings with NON-sensitive parameter
        # names, exception messages routed through
        # ``_sanitize_exception_msg``) bypasses every existing
        # key/header/URL-credential mask pattern and leaks verbatim
        # into operator log streams and the public
        # ``docs/feed_health.json`` artefact.
        #
        # Threat model:
        #   * Dropbox ``sl.<body>`` — file-storage / sharing /
        #     team-admin scope per the issuing app's permissions.
        #     File read = customer data exfiltration; file write =
        #     ransomware-style overwrite; sharing scope = create
        #     unauthorised public shared links; team-admin scope =
        #     exfiltrate team directory, revoke other admins, modify
        #     retention policies. The short-lived 4h TTL bounds the
        #     blast window but the issuing app's refresh token can
        #     re-mint short-lived tokens indefinitely — a leaked
        #     short-lived token implies the refresh token is also
        #     exposed in the same artefact.
        #   * Pulumi ``pul-<body>`` (HIGHEST blast radius — IaC
        #     control plane) — full Pulumi Cloud API access for the
        #     issuing user across every accessible org / project /
        #     stack. Read access = exfiltrate every secret persisted
        #     in stack state (cloud provider creds, database
        #     passwords, third-party API keys, TLS private keys).
        #     Write access = trigger arbitrary ``pulumi up`` modifying
        #     production infrastructure (provision attacker VMs,
        #     modify IAM, redirect DNS). The "pivot to every
        #     downstream environment via a single credential"
        #     amplifier.
        #
        # Structural anchors mirror the scanner regexes exactly:
        #   * ``(?<![A-Za-z0-9])`` lookbehind prevents mid-word false
        #     positives (``mysl.<body>``, ``Xpul-<body>`` are
        #     preserved).
        #   * ``(?![A-Za-z0-9])`` lookahead bounds the body span.
        #   * Dropbox body: 40+ chars from ``[A-Za-z0-9_-]`` (base64url
        #     alphabet); real-world bodies 130-160 chars.
        #   * Pulumi body: strict 40-char lowercase hex (SHA-1-shape
        #     digest) rejects placeholder values like
        #     ``pull-request-1234`` that contain hyphens.
        # The masks preserve issuer-specific prefixes:
        #   * Dropbox → ``sl.***`` (revocation flow at
        #     dropbox.com/developers/apps > App > "Revoke tokens").
        #   * Pulumi → ``pul-***`` (revocation flow at
        #     app.pulumi.com/account/tokens > "Revoke").
        #
        # Idempotence: masked forms (``sl.***``, ``pul-***``) do NOT
        # re-match because ``*`` is OUTSIDE every body alphabet AND
        # the masked body length (3 chars) is below every per-family
        # floor (40 / 40).
        #
        # Cross-family mutex: ``sl.`` vs. ``pul-`` prefixes are
        # disjoint at the leading-character level (``s`` vs. ``p``),
        # so no token can match both patterns.
        #
        # Marker: SENTINEL_DROPBOX_PULUMI_TOKEN_DRIFT.
        (
            r"(?<![A-Za-z0-9])sl\.[A-Za-z0-9_\-]{40,}(?![A-Za-z0-9])",
            r"sl.***",
        ),
        (
            r"(?<![A-Za-z0-9])pul-[a-f0-9]{40}(?![A-Za-z0-9])",
            r"pul-***",
        ),
        # Slack App-Level Token (``xapp-<v>-<app_id>-<seq>-<hex>``) + Databricks
        # Personal Access Token (``dapi<32 hex>(?:-<digit>)?``) value-shape
        # masking. Sibling-drift closure for the 2026-05-18 round that
        # extended ``_KNOWN_TOKENS`` in ``src/utils/secret_scanner.py`` to
        # detect each canonical token shape with vendor-specific
        # attribution. Pre-fix the scanner-side coverage was missing for
        # BOTH families AND the log-sanitisation codepath was NOT
        # extended in any prior round — bare token shapes in plain log
        # text (application f-string logs, upstream error responses
        # echoing the token back, JSON values without sensitive key
        # names, URL paths embedding the token, URL query strings with
        # NON-sensitive parameter names) bypassed every existing
        # key/header/URL-credential mask and leaked verbatim to operator
        # log streams plus the public ``docs/feed_health.json`` artefact.
        #
        # Threat model (mirror the secret-scanner round's blast radius):
        #   * Slack ``xapp-`` — Socket Mode + app-level Events API
        #     connection. Leaking grants the app's full event firehose
        #     (DMs / channel messages / slash commands / modal
        #     submissions for every app-subscribed event) AND combined
        #     with ``authorizations:read`` enumerates every workspace
        #     install of the app (cross-tenant pivot — one App-Level
        #     Token compromises every workspace the app is installed in).
        #   * Databricks ``dapi`` — full workspace-scoped data plane +
        #     job-execution plane. Leaking grants ``SELECT`` on every
        #     table the user can read (Unity Catalog data exfil),
        #     arbitrary Spark/SQL job submission on the user's clusters
        #     (compute theft on USD 100s-1000s/hour GPU clusters), AND
        #     arbitrary code execution within the cloud account via the
        #     cluster's attached IAM role (the canonical "data plane to
        #     control plane pivot" amplifier).
        #
        # Structural anchors mirror the scanner regexes exactly:
        #   * ``(?<![A-Za-z0-9])`` lookbehind prevents mid-word false
        #     positives (``Xxapp-...``, ``mydapi...`` are preserved).
        #   * ``(?![A-Za-z0-9])`` lookahead bounds the body span.
        #   * Slack: ``xapp-<digits>-[A-Z][A-Z0-9]{8,}-<digits>-<32+
        #     alnum body>`` matches the canonical Slack App-Level Token
        #     format (App ID always starts with ``A`` followed by 10+
        #     uppercase alnum chars per Slack docs; 13-digit sequence;
        #     64+ char body — 32-char floor accepts future variations).
        #   * Databricks: ``dapi[a-f0-9]{32}(?:-[0-9]+)?`` strict
        #     lowercase-hex body anchors against placeholder false
        #     positives like the Latin Lorem-Ipsum word "dapibus" and
        #     the literal ``dapi-foo`` placeholder while accepting the
        #     modern ``dapi<hex>-2`` rotation format.
        #
        # The masks preserve issuer-specific prefixes:
        #   * Slack App-Level → ``xapp-***`` (revocation flow at
        #     api.slack.com/apps/<app_id>/general > "App-Level Tokens"
        #     > "Regenerate").
        #   * Databricks → ``dapi***`` (revocation flow at Databricks
        #     workspace UI > User Settings > Developer > Access tokens
        #     > "Revoke" — distinct per workspace).
        #
        # Idempotence: masked forms (``xapp-***``, ``dapi***``) do NOT
        # re-match because ``*`` is OUTSIDE every body alphabet AND
        # the masked body length (3 chars) is below every per-family
        # floor (32 / 32).
        #
        # Cross-family mutex: ``xapp-`` vs. ``dapi`` prefixes are
        # disjoint at the leading-character level (``x`` vs. ``d``),
        # so no token can match both patterns. They are ALSO disjoint
        # from the existing Slack family entries (``xoxb-``/``xoxp-``/
        # etc.) at the second-character level (``a`` vs. ``o``).
        #
        # Marker: SENTINEL_SLACK_XAPP_DATABRICKS_TOKEN_DRIFT.
        (
            r"(?<![A-Za-z0-9])(xapp)-[0-9]+-[A-Z][A-Z0-9]{8,}-[0-9]+-[a-zA-Z0-9]{32,}(?![A-Za-z0-9])",
            r"\1-***",
        ),
        (
            r"(?<![A-Za-z0-9])(dapi)[a-f0-9]{32}(?:-[0-9]+)?(?![A-Za-z0-9])",
            r"\1***",
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
