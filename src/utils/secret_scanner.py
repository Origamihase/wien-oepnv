"""Utility helpers to detect accidentally committed secrets."""

from __future__ import annotations

import fnmatch
import logging
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Callable, Iterable, Sequence
import re
import subprocess  # nosec B404

from .files import read_capped_text

__all__ = [
    "Finding",
    "scan_repository",
    "load_ignore_file",
    "MAX_IGNORE_FILE_BYTES",
    "MAX_SCAN_FILE_BYTES",
]

# Security: per-loader byte caps for the two on-disk parsers in this
# module. Pre-fix both sites used ``Path.read_text(encoding="utf-8",
# errors="ignore")`` with NO size cap whatsoever — a planted huge file
# at the ignore-file path or any tracked file in the repo raised
# ``MemoryError`` past the surrounding handler and crashed the secret
# scanner CI gate, bypassing detection on subsequent commits.
#   - ``.secret-scan-ignore`` is a small list of glob patterns,
#     typically a few KiB; 1 MiB is ~1000x legit.
#   - Per-file scan content must accommodate large checked-in data
#     files (HTML test fixtures, mapping JSONs); 50 MiB matches the
#     ``DEFAULT_MAX_TEXT_FILE_BYTES`` ceiling for non-JSON disk reads
#     while still rejecting GiB-sized planted attacks.
MAX_IGNORE_FILE_BYTES = 1 * 1024 * 1024
MAX_SCAN_FILE_BYTES = 50 * 1024 * 1024

log = logging.getLogger(__name__)

_HIGH_ENTROPY_RE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9+/=_-]{24,}(?![A-Za-z0-9])")

# Detect sensitive variable assignments (e.g. key = "value")
# We use a broad list of keywords and allow common separators (hyphens, dots) in prefixes/suffixes
# to catch variations like my-api-key, config.client_secret, etc.
#
# Security (ReDoS): every key-affix repetition below is BOUNDED ``{0,64}``
# rather than the prior unbounded ``*``. The unbounded affixes caused
# catastrophic O(n²) backtracking — a hostile committed line holding the
# literal ``token`` followed by a long run of ``[a-z0-9_.-]`` chars with no
# trailing ``[:=]`` (a long base64-ish blob, an npm ``integrity`` hash list,
# a Tailscale ACL, …) made the greedy affix consume the whole run, the
# required ``\s*[:=]`` fail, and the engine backtrack one char at a time at
# every start position (~6 s for 4 KB, ~36 s for 10 KB), stalling the CI
# secret gate so sibling files never got scanned. ``re.sub``/``finditer``
# still advance the start position across the whole input, so a real key
# preceded by a long prefix is still found — only the *per-position*
# look-ahead is capped. 64 is far longer than any realistic key
# identifier, so the bound is lossless for genuine secrets.
_SENSITIVE_ASSIGN_RE = re.compile(
    r"""(?xis)
    (
        # Group 1: The key
        (?:
            # ReDoS guard: anchor the match at a [a-z0-9_.-] run boundary. The
            # bounded {0,64} prefix below was still retried at EVERY position
            # inside a long run (each retry attempting the ~46-branch keyword
            # alternation) -> O(N*64*alts), ~33 s/MB on a hostile committed
            # line (base64 blob / minified bundle / lockfile). With this
            # lookbehind, positions inside a run fail in O(1) and the prefix is
            # only attempted at run starts -> linear. Realistic key names start
            # at a boundary and are << 64 chars, so this is lossless for them.
            (?<![a-z0-9_.-])
            [a-z0-9_.-]{0,64}  # Prefix allowing letters, numbers, underscores, dots, hyphens
            (?:
                token|secret|password|passphrase|credential|
                accessid|accesskey|access-key|access.key|
                apikey|api-key|api.key|
                privatekey|private-key|private.key|
                secret-key|secret.key|client-secret|client.secret|
                authorization|auth-token|auth.token|auth|
                _key|ssh-key|ssh.key|id_rsa|
                clientid|client-id|client.id|client_id|
                session_id|session-id|session.id|sessionid|
                cookie|signature|bearer|jwt|
                webhook_url|webhook-url|webhook.url|webhook|
                dsn|subscriptionkey
            )
            [a-z0-9_.-]{0,64}  # Suffix allowing letters, numbers, underscores, dots, hyphens
        )
        |
        (?:
            # Strict matching for short/risky keywords to avoid false positives (e.g. throughput)
            (?<![a-z0-9_.-])  # ReDoS guard: see the boundary anchor above.
            [a-z0-9_.-]{0,64}  # Prefix
            (?:
                glpat|ghp|otp
            )
            (?:[-_][a-z0-9_.-]{0,64})?  # Strict suffix (underscore/hyphen required or end)
        )
    )
    \s*[:=]\s*  # Assignment operator (= or :) surrounded by flexible whitespace (including newlines)
    (
        (?:\"{3}.*?\"{3})|         # Triple-double-quoted value (non-greedy)
        (?:'{3}.*?'{3})|           # Triple-single-quoted value (non-greedy)
        (?:\"(?:\\.|[^\"\\])*\")|  # Double-quoted value
        (?:'(?:\\.|[^'\\])*')|     # Single-quoted value
        [^;#'\"\n]+                # Unquoted value (until comment or newline)
    )
    """
)

_AWS_ID_RE = re.compile(r"(?<![A-Za-z0-9])(AKIA|ASIA|ACCA)[A-Z0-9]{16}(?![A-Za-z0-9])")
# Security: ``Bearer`` literal is matched case-insensitively per RFC 7235
# §2.1 (HTTP auth-scheme is case-insensitive — ``Bearer`` / ``bearer`` /
# ``BEARER`` / mixed-case are all canonical HTTP forms accepted by every
# conforming server). Pre-fix the regex was case-sensitive on the
# ``Bearer`` literal, so a leaked ``BEARER <body>`` / ``bearer <body>``
# Authorization-header fragment bypassed this detector entirely. Two
# downstream failure modes:
#   1. **Attribution drift** — the body still matched the entropy
#      fallback (``_HIGH_ENTROPY_RE``) as a generic
#      ``Hochentropischer Token-String`` finding, losing the
#      Bearer-Token-specific reason that incident-response triage keys
#      off (which auth flow leaked, which revocation endpoint applies).
#   2. **Silent undetection** — for uniform-character-class bodies
#      (all-lowercase / all-uppercase / all-digit, common for legacy /
#      hash-derived / poorly-seeded-RNG tokens), the entropy fallback's
#      ``_looks_like_secret`` heuristic requires
#      ``min_categories=2`` in non-assignment mode and returns
#      ``False`` — the token slipped past BOTH detection branches and
#      remained entirely undetected. The Bearer-detector path uses
#      ``is_assignment=True`` which lowers ``min_categories`` to 1, so
#      restoring case-insensitive matching also closes the entropy-
#      bypass hole for the affected uniform-body shapes.
_BEARER_RE = re.compile(r"(?i)Bearer\s+([A-Za-z0-9\-_.]{16,})")
# Security: ``Basic`` literal is matched case-insensitively per RFC 7235
# §2.1 (referenced by RFC 7617 §2 — the HTTP Basic Authentication scheme).
# The Bearer detector closed the auth-scheme case-insensitivity contract
# for ``Bearer``; this detector extends the same contract to ``Basic``,
# the canonical companion auth-scheme. Two downstream failure modes were
# left open by the absence of this detector:
#   1. **Attribution drift** — the base64-encoded ``user:password`` body
#      matched ``_HIGH_ENTROPY_RE`` (``[A-Za-z0-9+/=_-]{24,}``) generically
#      as ``Hochentropischer Token-String``, losing the Basic-Auth-
#      specific reason that pinpoints rotation flow. Basic Auth requires
#      rotating the *user's password* (recoverable from the body via
#      base64 decode), distinct from Bearer-Token revocation (revoke at
#      the issuing IdP). Incident-response triage must guess the
#      revocation playbook without per-scheme attribution.
#   2. **Silent undetection** — short base64 bodies (16-23 chars, e.g.
#      ``YWRtaW46cGFzc3dvcmQ=`` = ``admin:password`` is only 20 chars)
#      fall BELOW the entropy fallback's 24-char minimum; AND all-letter
#      base64 bodies trip the ``candidate.isalpha()`` skip in the entropy
#      fallback loop (added to suppress LongCamelCaseClassName false
#      positives). Pre-fix every leaked ``Basic <body>`` with a short or
#      all-letter body was SILENTLY UNDETECTED entirely — the CI gate
#      passed, the plaintext ``username:password`` pair (trivially
#      recovered via base64 decode) sat committed in the public repo.
# The body alphabet ``[A-Za-z0-9+/=]`` is the canonical base64 alphabet
# per RFC 4648 §4 (standard base64; URL-safe RFC 4648 §5 uses ``_-``
# instead of ``+/`` but is rare in Authorization headers per RFC 7617
# §2). The 16+ contiguous body length is the structural disambiguator
# against natural-language false positives — sentences like "Basic
# understanding of..." do NOT have 16+ contiguous chars from the base64
# alphabet following the literal. The downstream
# ``_looks_like_secret(candidate, is_assignment=True)`` heuristic
# (``min_categories=1`` in the auth-scheme path) provides the
# second-layer filter for any token-shaped string that does happen to
# follow a ``basic``-prefixed natural-language passage.
_BASIC_AUTH_RE = re.compile(r"(?i)Basic\s+([A-Za-z0-9+/=]{16,})")
# Security: ``Negotiate`` literal is matched case-insensitively per RFC
# 7235 §2.1 (referenced by RFC 4559 §4 — the HTTP SPNEGO authentication
# scheme used for Kerberos and NTLMSSP via GSSAPI). The Basic Auth
# detector closed the auth-scheme case-insensitivity contract for
# ``Basic``; this detector extends the same contract to ``Negotiate``,
# the canonical RFC-defined HTTP auth-scheme for Kerberos/SPNEGO. Two
# downstream failure modes were left open by the absence of this
# detector:
#   1. **Attribution drift** — long Kerberos GSSAPI tokens (200+ chars
#      base64-encoded per RFC 4120's AS-REQ/AS-REP/AP-REQ ASN.1 DER
#      envelope) DO match ``_HIGH_ENTROPY_RE`` generically and land as
#      ``Hochentropischer Token-String``, losing the SPNEGO-specific
#      reason that pinpoints revocation flow (KDC ticket revocation,
#      force user re-auth, audit service principal for replay within
#      the ticket's typical 8-10h lifetime per RFC 4120 §5.3 default
#      ``EndTime``; distinct from Bearer-Token IdP revocation and from
#      Basic Auth user-password rotation). Incident-response triage
#      must guess the revocation playbook without per-scheme
#      attribution.
#   2. **Silent undetection** — all-letter base64 bodies trip the
#      ``candidate.isalpha()`` skip in the entropy fallback loop (added
#      to suppress LongCamelCaseClassName false positives). Pre-fix
#      every leaked ``Negotiate <all-letter-body>`` was SILENTLY
#      UNDETECTED entirely — the CI gate passed, the encrypted
#      Kerberos ticket sat committed in the public repo for the full
#      ticket validity window.
# The body alphabet ``[A-Za-z0-9+/=]`` is the canonical base64 alphabet
# per RFC 4648 §4 (standard base64; RFC 4559 §4 references the original
# base64 encoding per RFC 1421, which uses ``+/=`` not the URL-safe
# ``_-`` substitution). The 50+ char body floor is the structural
# disambiguator against natural-language false positives — real
# Kerberos AS-REP / AP-REQ tokens are 200-3000+ chars base64-encoded
# (the ASN.1 DER envelope alone is ~100 bytes, the encrypted ticket
# adds 200-2000+ bytes for typical service principals), so 50 is a
# safe conservative floor that catches even truncated tokens in
# fragmented log lines. The English word "negotiate" appears commonly
# in code comments and natural prose, but never followed by 50+
# contiguous chars from ``[A-Za-z0-9+/=]`` (English words break at
# whitespace and punctuation). The downstream
# ``_looks_like_secret(candidate, is_assignment=True)`` heuristic
# (``min_categories=1`` in the auth-scheme path) provides the
# second-layer filter for any token-shaped string that does happen to
# follow a ``negotiate``-prefixed natural-language passage.
#
# Real-world emission patterns include IIS HTTP request logs with
# ``--debug``, browser HAR exports (Network tab ``Save with content``),
# Wireshark / tshark capture text exports, WinRM debug logs
# (``Set-PSDebug -Trace 2``), ``curl -v --negotiate -u :`` debug logs,
# ``requests`` + ``requests-kerberos`` debug mode, Spring Security's
# ``org.springframework.security.kerberos`` debug logging, and ELK
# Stack ingest of ``WWW-Authenticate`` response headers. The structural
# anchor for Kerberos AP-REQ tokens is the ``YII`` base64 prefix
# (base64 of the ASN.1 SEQUENCE outer tag ``0x60 0x82``); for NTLMSSP-
# wrapped Negotiate tokens the prefix is ``TlRMTVNTUA`` (base64 of
# the ``NTLMSSP\0`` magic). The detector matches on the auth-scheme
# literal NOT the body prefix, so both Kerberos and NTLMSSP shapes are
# covered without per-mechanism rule explosion.
_NEGOTIATE_RE = re.compile(r"(?i)Negotiate\s+([A-Za-z0-9+/=]{50,})")
# Security: ``NTLM`` literal is matched case-insensitively per RFC 7235
# §2.1 (the HTTP auth-scheme case-insensitivity contract that every
# HTTP auth-scheme inherits; RFC 4559 §4 also lists ``NTLM`` alongside
# ``Negotiate`` as a recognised HTTP auth-scheme literal even though
# the underlying NTLMSSP wire protocol is defined by [MS-NLMP], a
# Microsoft vendor specification rather than an IETF RFC). The
# Negotiate detector closed the SPNEGO-wrapped NTLMSSP path; this
# detector covers ``NTLM`` used directly as an HTTP auth-scheme
# (without SPNEGO wrapping) — the common case for IIS with legacy
# clients, SMB-over-HTTP, WebDAV, and SharePoint Windows-only intranet
# scenarios. Two downstream failure modes were left open by the
# absence of this detector:
#   1. **Attribution drift** — long NTLMSSP Type 3 (Authenticate)
#      messages (350-1000+ bytes raw, base64-encoded to 470-1500+
#      chars) DO match ``_HIGH_ENTROPY_RE`` (``[A-Za-z0-9+/=_-]{24,}``)
#      generically and land as ``Hochentropischer Token-String``
#      findings, losing the NTLM-specific reason that pinpoints
#      revocation flow (rotate the user's password — the NTLMv2
#      challenge-response is offline-crackable with ``hashcat`` mode
#      5600 to recover the plaintext password; audit the domain
#      controller for NetNTLMv2 relay attempts via ``ntlmrelayx``;
#      force user re-authentication via password change). Distinct
#      from Bearer-Token IdP revocation, from Basic Auth user-password
#      rotation (no relay surface), and from Negotiate/Kerberos KDC
#      ticket revocation (no offline-cracking surface for the encrypted
#      ticket portion).
#   2. **Silent undetection** — all-letter base64 bodies trip the
#      ``candidate.isalpha()`` skip in the entropy fallback loop
#      (added to suppress LongCamelCaseClassName false positives).
#      Pre-fix every leaked ``NTLM <all-letter-body>`` was SILENTLY
#      UNDETECTED entirely — the CI gate passed, the NTLMSSP message
#      (potentially containing a NetNTLMv2 hash recoverable to the
#      plaintext password via offline cracking) sat committed in the
#      public repo indefinitely.
# The body alphabet ``[A-Za-z0-9+/=]`` is the canonical base64 alphabet
# per RFC 4648 §4 (standard base64; NTLMSSP messages always use
# standard base64 in HTTP Authorization headers per [MS-NLMP] §2.2
# Header — the binary protocol is rendered into the textual HTTP
# scheme via standard base64). The 50+ char body floor is the
# structural disambiguator — real NTLMSSP messages are ALL above 50
# chars (Type 1 = 40 bytes raw = 56 base64 chars minimum; Type 2
# = 80-200 bytes raw = 108-272 base64 chars; Type 3 = 350-1000+
# bytes raw = 470-1500+ base64 chars). 50 is a safe conservative
# floor that catches every realistic NTLMSSP message including
# truncated Type 1 tokens in fragmented log lines while preventing
# natural-language false positives (the acronym ``NTLM`` appears in
# code comments and documentation, but never followed by 50+
# contiguous chars from the base64 alphabet — English prose breaks at
# whitespace and punctuation). The ``(?i)`` inline flag inherits the
# RFC 7235 §2.1 case-insensitivity contract from the Bearer / Basic
# Auth / Negotiate siblings.
#
# Real-world emission patterns include IIS HTTP request logs with
# ``--debug`` flag, browser HAR exports (Network tab ``Save with
# content``) for intranet NTLM-authenticated sites, Wireshark / tshark
# text-rendered captures of SMB-over-HTTP / WebDAV / SharePoint
# traffic, WinRM debug logs (``Set-PSDebug -Trace 2``) during the NTLM
# negotiate / challenge / authenticate round trips, ``curl -v --ntlm
# -u user:pass`` debug logs, Python ``requests`` with ``requests-ntlm``
# debug mode, Spring Security ``org.springframework.security.kerberos``
# debug logging (intercepts NTLM as a fallback when SPNEGO is
# unavailable), and ELK Stack ingest of ``WWW-Authenticate: NTLM``
# response headers. The structural anchor for every NTLMSSP message is
# the ``TlRMTVNTUA`` base64 prefix (base64 of the ASCII NTLMSSP magic
# ``0x4e 0x54 0x4c 0x4d 0x53 0x53 0x50 0x00`` per [MS-NLMP] §2.2
# Header); the detector matches on the auth-scheme literal NOT the
# body prefix, so direct-NTLM (this detector) and SPNEGO-wrapped
# NTLMSSP (the Negotiate detector) are covered without per-mechanism
# rule duplication.
_NTLM_RE = re.compile(r"(?i)NTLM\s+([A-Za-z0-9+/=]{50,})")
# Security: the ``token`` literal is matched case-insensitively per the
# RFC 7235 §2.1 contract that every HTTP auth-scheme literal inherits.
# Unlike Bearer (RFC 6750), Basic (RFC 7617), Negotiate (RFC 4559), and
# NTLM ([MS-NLMP]), ``token`` is NOT a registered IANA HTTP
# Authentication Scheme — it is a de facto convention popularised by
# GitHub's REST API documentation (``Authorization: token <body>``)
# and inherited by every Git-host fork that mirrors GitHub's HTTP API
# shape (Gitea, Forgejo, Codeberg, Gogs) plus DigitalOcean's legacy
# API v1 docs and various other tools. The NTLM detector closed the
# Microsoft-vendor-specific scheme; this detector extends the same
# contract to the GitHub-vendor-specific scheme, the de facto
# standard for opaque-token HTTP auth in the Git-host ecosystem.
# Two downstream failure modes were left open by the absence of this
# detector:
#   1. **Attribution drift** — opaque API tokens (typically 36+ chars
#      from the GitHub PAT body alphabet ``[A-Za-z0-9_\-]``) DO match
#      ``_HIGH_ENTROPY_RE`` (``[A-Za-z0-9+/=_-]{24,}``) generically
#      and land as ``Hochentropischer Token-String`` findings, losing
#      the token-scheme-specific reason that pinpoints the issuer
#      family (GitHub: rotate at github.com/settings/tokens, audit
#      ``GET /user`` API calls; Gitea/Forgejo: rotate at
#      ``/user/settings/applications``; DigitalOcean: rotate at
#      cloud.digitalocean.com/account/api/tokens). Distinct from
#      Bearer-Token IdP revocation, from Basic Auth user-password
#      rotation, from Negotiate/Kerberos KDC ticket revocation, and
#      from NTLM domain-controller hash rotation — five distinct IR
#      flows that hinge on per-scheme attribution.
#   2. **Silent undetection** — all-letter bodies trip the
#      ``candidate.isalpha()`` skip in the entropy fallback loop
#      (added to suppress LongCamelCaseClassName false positives).
#      Pre-fix every leaked ``token <all-letter-body>`` was SILENTLY
#      UNDETECTED entirely (when not also matching the assignment
#      heuristic for a sensitive variable name).
# The body alphabet ``[A-Za-z0-9_\-]`` covers GitHub's PAT alphabet
# (URL-safe alphanumeric + underscore + hyphen — the same alphabet
# used by every ``gh*_``-prefixed token family), Gitea/Forgejo's PAT
# alphabet (both inherit GitHub's shape per their REST API specs), and
# DigitalOcean's hex token shape. Standard base64 padding (``+/=``)
# is NOT in the alphabet because opaque API tokens in the GitHub-
# ecosystem ``token`` scheme are URL-safe alphanumeric, NOT base64
# (Bearer's alphabet adds ``.`` for JWTs; Basic/Negotiate/NTLM use the
# standard base64 alphabet ``+/=`` for their respective payloads). The
# 36+ char body floor is the structural disambiguator against
# natural-language false positives — the word ``token`` is common in
# English prose and code comments, but is essentially never followed
# by 36+ contiguous chars from the ``[A-Za-z0-9_\-]`` alphabet in
# natural text (English words break at whitespace and punctuation).
# The floor is calibrated to the GitHub PAT body length (``ghp_<36>``
# = 40 total chars, with the 36-char body part captured when the PAT
# prefix is absent — the legacy 40-char hex GitHub token from before
# April 2021 also exceeds the 36-char floor).
#
# Cross-detector boundary note: a leaked ``Authorization: token
# ghp_xxxx...`` carries TWO matchable spans — the ``ghp_<36>`` GitHub
# PAT span (matched by ``_KNOWN_TOKENS``) and the ``token <body>`` HTTP
# auth-scheme span (matched by this detector). The ``_KNOWN_TOKENS``
# matcher runs FIRST in ``_scan_content``, so the GitHub-PAT-specific
# reason wins via the ``covered_ranges`` arbitration — this detector
# yields its attribution only for tokens that do NOT match any
# ``_KNOWN_TOKENS`` prefix (opaque non-prefixed tokens from Gitea /
# Forgejo / DigitalOcean / legacy pre-April-2021 GitHub tokens). The
# placement in ``_AUTH_SCHEME_DETECTORS`` is LAST so the more-specific
# auth-scheme detectors (Bearer / Basic / Negotiate / NTLM) win via
# the same ``covered_ranges`` arbitration when a token-shaped body
# sits inside a Bearer / Basic / etc. header.
#
# Real-world emission patterns include legacy GitHub REST API curl
# examples (``curl -H "Authorization: token ghp_xxx"``), ``hub`` CLI
# debug output, Gitea / Forgejo / Codeberg / Gogs API examples (every
# Git-host that mirrors the GitHub HTTP API shape), DigitalOcean
# legacy API v1 docs, CI/CD workflow files with hard-coded fallback
# tokens, Python ``requests`` debug logs (``logging.DEBUG`` on
# ``urllib3.connectionpool``), browser HAR exports of intranet
# self-hosted Git host API calls, and copy-pasted documentation
# snippets in READMEs and wikis.
_TOKEN_SCHEME_RE = re.compile(r"(?i)token\s+([A-Za-z0-9_\-]{36,})")
# Security: ``HOBA`` literal is matched case-insensitively per RFC 7235
# §2.1 (referenced by RFC 7486 §3 — HTTP Origin-Bound Authentication
# (HOBA)). HOBA is the IANA-registered HTTP authentication scheme for
# client-asserted public-key authentication WITHOUT TLS client
# certificates — the canonical "rare but registered" auth-scheme that
# the NTLM round (the round immediately preceding Token-scheme) and
# the Token-scheme round (the round immediately preceding this one)
# both named as a deferred next-round candidate ("rare in practice
# but a complete auth-scheme enumeration would include it"). Per
# RFC 7486 §3 the on-the-wire format is the literal ``HOBA`` followed
# by the ``result`` parameter (a quoted-string carrying four
# dot-separated base64url-encoded fields)::
#
#     Authorization: HOBA result="<KID>.<challenge>.<nonce>.<signature>"
#
# Where:
#   * ``KID`` — base64url-encoded SHA-256 hash of the client's public
#     key (Key Identifier; 43 chars for the canonical SHA-256 output);
#   * ``challenge`` — server-supplied base64url-encoded nonce
#     identifying the auth-request session (8-32 chars typically);
#   * ``nonce`` — client-supplied base64url-encoded freshness anchor
#     (8-32 chars typically);
#   * ``signature`` — base64url-encoded digital signature over
#     ``KID || challenge || nonce`` using the client's private key
#     (ECDSA P-256 = 86 base64url chars; RSA-2048 = 342 base64url
#     chars; the exact length depends on the chosen algorithm per
#     the HOBA registration in the JOSE algorithm registry).
#
# Two downstream failure modes were left open by the absence of this
# detector:
#   1. **Attribution drift** — a 100+-char base64url HOBA result
#      string with mixed character classes DOES match
#      ``_HIGH_ENTROPY_RE`` (``[A-Za-z0-9+/=_-]{24,}``) for the
#      contiguous body slices BETWEEN the dots (the dots are OUTSIDE
#      the entropy alphabet), losing the HOBA-specific issuer
#      attribution AND splitting one logical credential into multiple
#      separate findings. Pre-fix incident-response triage had to
#      guess whether the leaked dotted entropy structure was a
#      JOSE JWT (revoke at the issuing IdP), a HOBA result (rotate
#      the client's HOBA key pair at the HOBA server and reissue
#      a new ``KID``), or something else. Each has a distinct
#      revocation flow.
#   2. **Silent undetection** — all-letter base64url fields trip the
#      ``candidate.isalpha()`` skip in the entropy fallback loop
#      (added to suppress LongCamelCaseClassName false positives).
#      Pre-fix every leaked HOBA result whose fields happen to be
#      all-letter (rare in practice but possible for hand-crafted
#      test fixtures and CTF challenges) was SILENTLY UNDETECTED.
# The structural disambiguator is the FOUR dot-separated fields
# inside a quoted ``result="..."`` parameter — natural-language text
# essentially never contains the literal ``HOBA`` followed by
# ``result="<8+ chars>.<8+ chars>.<8+ chars>.<8+ chars>"`` in prose.
# The body alphabet ``[A-Za-z0-9_\-]`` is the canonical base64url
# alphabet per RFC 4648 §5 (URL-safe base64; HOBA uses base64url per
# RFC 7486 §3 to render binary key material / signatures into the
# textual HTTP scheme without ``+/`` characters that would require
# URL-percent-encoding in query-string contexts). The 8+ char per-
# field floor is a structural disambiguator chosen to reject the
# minimum example values in RFC 7486's normative text (e.g.
# ``result="kid.6.6.34"`` from §9.4) while accepting every realistic
# real-world HOBA credential (the canonical KID alone is 43 chars
# = base64url SHA-256; even truncated CTF examples typically use
# 8+ char per-field bodies). The quote alphabet ``["']`` accepts
# both canonical RFC-double-quote shape (``result="..."``) and the
# de-facto single-quote shape that appears in YAML serialisations
# of HOBA captures, Python ``requests`` debug logs with f-string
# formatting, and Postman/Insomnia request exports. The ``(?i)``
# inline flag inherits the RFC 7235 §2.1 case-insensitivity contract
# from the Bearer / Basic / Negotiate / NTLM / Token siblings.
#
# Real-world emission patterns include WebAuthn-pre-cursor HOBA
# implementations (most legacy HOBA deployments predate WebAuthn
# / FIDO2 and use the original RFC 7486 format), academic
# research papers on HTTP authentication that publish example
# captures, IoT device firmware that uses HOBA for backend auth
# in lieu of TLS client certs (constrained-device contexts where
# the TLS client-cert ladder is too heavyweight), specialised
# enterprise PKI integrations, browser dev-tools Network tab HAR
# exports of HOBA-authenticated sites, and Wireshark / tshark
# capture text exports rendering the Authorization header
# verbatim. The structural anchor for HOBA captures is the literal
# ``HOBA result="`` (case-insensitive) — no other HTTP auth-scheme
# uses this exact prefix shape. Cross-detector ordering: HOBA goes
# BEFORE the Token-scheme detector (de-facto literal, canonical
# Bearer-alias position at the END of ``_AUTH_SCHEME_DETECTORS``)
# so the more-specific IANA-registered HOBA attribution wins over
# the generic Token-scheme catch-all. HOBA can ALSO match a
# Token-scheme span (if a Token-scheme body happens to contain
# enough chars), but the ``covered_ranges`` arbitration in
# ``_scan_auth_scheme_credentials`` ensures only ONE attribution
# fires per content blob.
_HOBA_RE = re.compile(
    r'''(?i)HOBA\s+result\s*=\s*\\?["']([A-Za-z0-9_\-]{8,}(?:\.[A-Za-z0-9_\-]{8,}){3})\\?["']'''
)

# Security: ordered (regex, reason) table that ``_scan_content`` iterates
# over to detect HTTP-auth-scheme-prefixed credentials. Each entry's regex
# must capture the credential body in group(1) so the loop in
# ``_scan_content`` can read ``match.group(1)`` uniformly. Order matters
# for tie-breaking: more-specific auth-scheme literals (Bearer, Basic,
# Negotiate, NTLM, HOBA) MUST appear BEFORE the generic ``token`` scheme
# so their attribution wins via ``covered_ranges`` arbitration when a
# token-shaped body sits inside one of those headers. The
# ``is_covered`` check in ``_scan_content`` anchors the first matching
# reason at each span. New auth-scheme detectors (e.g. RFC 7616
# Digest, RFC 8120 Mutual, RFC 4559 AWS4-HMAC-SHA256)
# follow the same shape and inherit the ``(?i)`` case-insensitivity
# invariant pinned per RFC 7235 §2.1.
_AUTH_SCHEME_DETECTORS: tuple[tuple[re.Pattern[str], str], ...] = (
    (_BEARER_RE, "Bearer-Token wirkt echt"),
    (_BASIC_AUTH_RE, "HTTP Basic Authentication Credential gefunden"),
    (_NEGOTIATE_RE, "SPNEGO/Negotiate Authentication Token gefunden"),
    (_NTLM_RE, "NTLM Authentication Credential gefunden"),
    (_HOBA_RE, "HOBA Authentication Credential gefunden"),
    (_TOKEN_SCHEME_RE, "HTTP Token-Scheme Authentication Credential gefunden"),
)

_PEM_RE = re.compile(r"(-----BEGIN [A-Z ]*PRIVATE KEY-----)(?:.|\n)*?(-----END [A-Z ]*PRIVATE KEY-----)")

# Known high-value token patterns to detect specifically
# These bypass the generic entropy checks and provide specific descriptions
_KNOWN_TOKENS = [
    (re.compile(r"(?<![A-Za-z0-9])glpat-[0-9a-zA-Z_\-]{20}(?![A-Za-z0-9])"), "GitLab Personal Access Token gefunden"),
    (re.compile(r"(?<![A-Za-z0-9])ghp_[0-9a-zA-Z]{36}(?![A-Za-z0-9])"), "GitHub Personal Access Token gefunden"),
    (re.compile(r"(?<![A-Za-z0-9])github_pat_[0-9a-zA-Z_]{22,}(?![A-Za-z0-9])"), "GitHub Fine-Grained Token gefunden"),
    # GitHub OAuth-App access token (issued via the OAuth web flow).
    (re.compile(r"(?<![A-Za-z0-9])gho_[0-9a-zA-Z]{36}(?![A-Za-z0-9])"), "GitHub OAuth Access Token gefunden"),
    # GitHub App user-to-server token (App acting on behalf of an authenticated user).
    (re.compile(r"(?<![A-Za-z0-9])ghu_[0-9a-zA-Z]{36}(?![A-Za-z0-9])"), "GitHub App User-to-Server Token gefunden"),
    # GitHub App server-to-server token. This is the format of `GITHUB_TOKEN`
    # auto-injected by GitHub Actions, so leakage is high-impact.
    (re.compile(r"(?<![A-Za-z0-9])ghs_[0-9a-zA-Z]{36}(?![A-Za-z0-9])"), "GitHub App Server-to-Server Token gefunden"),
    # GitHub refresh token (issued alongside gho_/ghu_ during token rotation).
    (re.compile(r"(?<![A-Za-z0-9])ghr_[0-9a-zA-Z]{36}(?![A-Za-z0-9])"), "GitHub Refresh Token gefunden"),
    (re.compile(r"(?<![A-Za-z0-9])AIza[0-9A-Za-z\-_]{35}(?![A-Za-z0-9])"), "Google API Key gefunden"),
    (re.compile(r"(?<![A-Za-z0-9])[0-9]{3,14}:[a-zA-Z0-9_-]{35}(?![A-Za-z0-9])"), "Telegram Bot Token gefunden"),
    (re.compile(r"(?<![A-Za-z0-9])sk_live_[0-9a-zA-Z]{24}(?![A-Za-z0-9])"), "Stripe Live Secret Key gefunden"),
    # Stripe test secret key. Less catastrophic than the live counterpart but still
    # grants access to the project's test-mode dashboard, customer/PaymentIntent
    # objects and webhooks — and a leaked test key strongly signals that a live
    # key exists somewhere in the same repo. Treated as a distinct finding so the
    # report calls out *which* environment leaked.
    (re.compile(r"(?<![A-Za-z0-9])sk_test_[0-9a-zA-Z]{24}(?![A-Za-z0-9])"), "Stripe Test Secret Key gefunden"),
    # Stripe restricted API keys (``rk_live_`` / ``rk_test_``). Restricted keys
    # carry a scoped subset of permissions, but a leak still grants the API
    # access defined by that scope (charges, customers, payouts, …) and is
    # high-impact for the affected resource. Format mirrors ``sk_*``: prefix
    # plus a 24-char alphanumeric body. Distinct reasons per environment so
    # the report identifies which key tier leaked.
    (re.compile(r"(?<![A-Za-z0-9])rk_live_[0-9a-zA-Z]{24}(?![A-Za-z0-9])"), "Stripe Restricted Live Key gefunden"),
    (re.compile(r"(?<![A-Za-z0-9])rk_test_[0-9a-zA-Z]{24}(?![A-Za-z0-9])"), "Stripe Restricted Test Key gefunden"),
    # Stripe webhook signing secret (``whsec_``). Leakage is not an API
    # credential but lets an attacker forge webhook payloads that the
    # application's signature verification will accept — so any
    # webhook-driven business logic (refunds, account upgrades, fulfilment)
    # can be triggered by a network adversary. Body is base64-ish, ``32+``
    # chars in practice; pattern stays alphanumeric to match Stripe's
    # current format and avoid colliding with the ``[A-Za-z0-9+/=_-]``
    # entropy fallback's character class.
    (re.compile(r"(?<![A-Za-z0-9])whsec_[A-Za-z0-9]{32,}(?![A-Za-z0-9])"), "Stripe Webhook Signing Secret gefunden"),
    # Slack Token Rotation Refresh Token — the modern V2 rotation
    # credential issued via ``oauth.v2.access`` with
    # ``grant_type=refresh_token``. Two on-the-wire shapes per
    # https://api.slack.com/authentication/rotation:
    #   * **Direct shape** — ``xoxe-<numeric>-<base64-like body>`` for
    #     tokens issued without a parent rotation chain.
    #   * **Chained shape** — ``xoxe.xoxb-<body>`` (bot rotation) or
    #     ``xoxe.xoxp-<body>`` (user rotation), where the embedded
    #     ``xox[bp]-`` prefix anchors the rotation chain for Slack's
    #     auth server lookup.
    # A leak grants the holder the ability to mint fresh
    # ``xoxb-``/``xoxp-`` access tokens with the chain's identity and
    # scopes — multi-month blast radius (refresh tokens are long-
    # lived; the rotated access tokens have 12-hour TTL but can be
    # re-minted indefinitely until the refresh token itself is
    # revoked at https://api.slack.com/apps/<app>/oauth via the
    # "Reinstall App" / "Rotate Tokens" flow). The revocation flow
    # is distinct from the legacy ``xoxr-`` refresh-token flow (the
    # ``xoxr-`` prefix predates V2 rotation; the two are NOT
    # interchangeable). Placed BEFORE the existing ``xoxb-`` /
    # ``xoxp-`` / ``xoxa-`` / ``xoxr-`` entries so ``is_covered``
    # correctly anchors the chained shape at the larger ``xoxe.``-
    # prefixed span — the inner ``xoxb-``/``xoxp-`` span (which the
    # strict bare ``xoxb-``/``xoxp-`` regexes would NOT match anyway
    # because the rotation body shape ``1-<base64body>`` does not
    # fit ``[0-9]{10,}-[0-9]{10,}-[a-zA-Z0-9]{24,32}``) is
    # suppressed. Pre-fix the chained shape was silently undetected
    # entirely (entropy fallback misses ``xoxe.`` due to the dot
    # falling outside ``[A-Za-z0-9+/=_-]``, only the post-prefix
    # body span matches generically — losing the Slack-rotation-
    # specific issuer attribution).
    (
        re.compile(r"(?<![A-Za-z0-9])xoxe(?:-|\.xox[bp]-)[0-9a-zA-Z\-]{20,}(?![A-Za-z0-9])"),
        "Slack Token Rotation Refresh Token gefunden",
    ),
    # Slack Browser Session Token (``xoxc-<body>``). Extracted from
    # ``slack.com`` browser session cookies, granting full user-level
    # session auth with **no scope restrictions** — equivalent to the
    # user being logged in via the web client. Unofficial tools
    # (slack_cleaner, slackdump, slack-export scripts) extract
    # ``xoxc-`` via DevTools and use it for unattended scripted
    # access to the user's Slack workspace. The canonical "session
    # hijack" credential for Slack: an attacker holding ``xoxc-``
    # can browse every conversation the user can see (DMs, private
    # channels, sensitive files), post messages as the user, and
    # exfiltrate the workspace's full message history via the
    # cookie-authenticated ``/api/conversations.list`` /
    # ``conversations.history`` endpoints. The revocation flow lives
    # at https://slack.com/account/sessions ("Sign out all other
    # sessions") and is distinct from the OAuth-app revocation flow
    # at https://api.slack.com/apps/ — session-cookie auth, not
    # app-token auth.
    (
        re.compile(r"(?<![A-Za-z0-9])xoxc-[0-9a-zA-Z\-]{20,}(?![A-Za-z0-9])"),
        "Slack Browser Session Token gefunden",
    ),
    # Slack Cookie Session Token (``xoxd-<body>``). Companion to
    # ``xoxc-`` — the ``d`` cookie value from Slack web sessions,
    # used in conjunction with ``xoxc-`` for direct browser-style
    # API calls (e.g. unofficial endpoints that require
    # cookie-based auth). A leak grants the same SESSION-LEVEL
    # access as ``xoxc-`` (the two tokens typically leak together
    # via DevTools cookie extraction). Pre-fix the entropy fallback
    # caught the body span (the ``xoxd-`` prefix's dash is inside
    # the entropy alphabet and the body alphabet matches), but the
    # Slack-cookie-session-specific issuer attribution that anchors
    # the canonical revocation flow (https://slack.com/account/
    # sessions — distinct from every other Slack revocation flow)
    # was lost. Furthermore, for uniform-character-class bodies the
    # entropy fallback's ``min_categories=2`` requirement returned
    # ``False`` and the credential was silently undetected entirely.
    (
        re.compile(r"(?<![A-Za-z0-9])xoxd-[0-9a-zA-Z\-]{20,}(?![A-Za-z0-9])"),
        "Slack Cookie Session Token gefunden",
    ),
    (re.compile(r"(?<![A-Za-z0-9])xoxb-[0-9]{10,}-[0-9]{10,}-[a-zA-Z0-9]{24}(?![A-Za-z0-9])"), "Slack Bot Token gefunden"),
    (re.compile(r"(?<![A-Za-z0-9])xoxp-[0-9]{10,}-[0-9]{10,}-[0-9]{10,}-[a-zA-Z0-9]{32}(?![A-Za-z0-9])"), "Slack User Token gefunden"),
    # Slack OAuth-app access token (configuration token issued via the OAuth flow,
    # ``xoxa-`` prefix). Format mirrors the bot/user variants but is sometimes
    # shorter, so the body length is permissive while the unique prefix keeps
    # false positives essentially impossible.
    (re.compile(r"(?<![A-Za-z0-9])xoxa-[0-9a-zA-Z-]{20,}(?![A-Za-z0-9])"), "Slack OAuth Access Token gefunden"),
    # Slack refresh token (``xoxr-`` prefix), issued alongside rotating bot/user
    # tokens. Leakage grants the ability to mint fresh xoxb-/xoxp- tokens until
    # the refresh token itself is revoked.
    (re.compile(r"(?<![A-Za-z0-9])xoxr-[0-9a-zA-Z-]{20,}(?![A-Za-z0-9])"), "Slack Refresh Token gefunden"),
    (re.compile(r"(?<![A-Za-z0-9])npm_[0-9a-zA-Z]{36}(?![A-Za-z0-9])"), "NPM Access Token gefunden"),
    (re.compile(r"(?<![A-Za-z0-9])pypi-[0-9a-zA-Z_\-]{20,}(?![A-Za-z0-9])"), "PyPI API Token gefunden"),
    # SendGrid API keys: SG.<22 chars>.<43 chars>. The two dots split the token into segments
    # that the generic [A-Za-z0-9+/=_-] entropy regex cannot match across, so without this
    # specific pattern only the trailing 43-char segment is flagged (as a generic high-entropy
    # string) and the SG. prefix plus the 22-char identifier are silently dropped.
    (re.compile(r"(?<![A-Za-z0-9])SG\.[A-Za-z0-9_\-]{22}\.[A-Za-z0-9_\-]{43}(?![A-Za-z0-9])"), "SendGrid API Key gefunden"),
    # Anthropic API keys: sk-ant-api{NN}-... and sk-ant-admin{NN}-...
    # Standard format: sk-ant-api03-<93 chars>AA. Pattern stays loose to also catch
    # forthcoming version suffixes (api04, admin02, …) without missing real leaks.
    (re.compile(r"(?<![A-Za-z0-9])sk-ant-(?:api|admin)[0-9]{2}-[A-Za-z0-9_\-]{32,}(?![A-Za-z0-9])"), "Anthropic API Key gefunden"),
    # OpenAI Project API keys: sk-proj-...
    (re.compile(r"(?<![A-Za-z0-9])sk-proj-[A-Za-z0-9_\-]{40,}(?![A-Za-z0-9])"), "OpenAI Project API Key gefunden"),
    # OpenAI Service Account keys: sk-svcacct-...
    (re.compile(r"(?<![A-Za-z0-9])sk-svcacct-[A-Za-z0-9_\-]{40,}(?![A-Za-z0-9])"), "OpenAI Service Account Key gefunden"),
    # OpenAI legacy/user API keys: sk- followed by exactly 48 alphanumeric chars.
    # The strict 48-char alphanumeric body avoids overlap with sk-ant-/sk-proj-/sk-svcacct- (all contain '-').
    (re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9]{48}(?![A-Za-z0-9])"), "OpenAI API Key gefunden"),
    # Hugging Face access tokens: ``hf_<32+ alphanumeric chars>``. Issued via
    # https://huggingface.co/settings/tokens for read/write access to private
    # models, datasets and Spaces. A leak grants the token's permission scope
    # for the entire validity window (no automatic expiry on legacy tokens),
    # so credentials in committed config / notebook outputs / log artefacts
    # need precise attribution rather than a generic high-entropy hit.
    (re.compile(r"(?<![A-Za-z0-9])hf_[A-Za-z0-9]{32,}(?![A-Za-z0-9])"), "Hugging Face Access Token gefunden"),
    # Groq API keys (``gsk_<48+ alphanumeric>``). Issued via
    # console.groq.com/keys for Groq's LLM inference API (extremely
    # fast token generation via custom LPU silicon — the platform's
    # competitive edge is sub-100ms first-token latency on Llama / Mixtral
    # / Gemma deployments). The ``gsk_`` prefix is unambiguous (no
    # other major issuer uses this prefix), and the strict alphanumeric
    # body lies entirely inside the entropy fallback's
    # ``[A-Za-z0-9+/=_-]`` alphabet — so the entropy regex matches the
    # full ``gsk_<body>`` span as one generic "Hochentropischer
    # Token-String" finding, losing the Groq-specific issuer attribution
    # that incident-response keys off (revocation flow at
    # console.groq.com/keys; audit completion-API usage logs for
    # chargeback fraud / model-prompt exfiltration). Body lower bound
    # 32 chars allows future canonical length variations while
    # rejecting short ``gsk_``-prefixed fragments; real Groq keys are
    # 48-56 chars body. A leak grants the issuing account's full
    # Groq API scope: trigger inference jobs at the victim's expense
    # (USD 0.10-1.00 per completion at scale), exfiltrate proprietary
    # prompts via deployed-model queries, and potentially modify webhook
    # / billing configuration depending on the platform's account-
    # management API surface. Real-world emission patterns: Python
    # notebook outputs hardcoding ``client = Groq(api_key="gsk_...")``;
    # committed ``.env`` files; README curl examples; CI debug logs.
    (re.compile(r"(?<![A-Za-z0-9])gsk_[A-Za-z0-9]{32,}(?![A-Za-z0-9])"), "Groq API Key gefunden"),
    # Replicate API tokens (``r8_<40 alphanumeric>``). Issued via
    # replicate.com/account/api-tokens for Replicate's hosted-model
    # inference platform (Stable Diffusion, Llama, FLUX, Whisper,
    # custom Cog-packaged models). The ``r8_`` prefix is unambiguous
    # (no other major issuer uses this prefix). The 40-char alphanumeric
    # body matches Replicate's documented canonical format strictly —
    # rejecting short ``r8_``-prefixed fragments AND rejecting body
    # variations that don't fit the exact length. A leak's PRIMARY
    # attack vector is BILLING-CREDIT DRAIN — Replicate charges
    # per GPU-second for hosted inference (Stable Diffusion XL on
    # A100 = ~USD 0.01/sec; Llama-70B = ~USD 0.05/sec; long-running
    # video generation = $$$$). An attacker with the leaked token
    # can trigger arbitrary inference jobs, draining $1000s of
    # credits in hours before the victim notices. Distinct revocation
    # flow at replicate.com/account/api-tokens — must rotate the
    # specific token AND audit the recent inference job history.
    # Real-world emission: Python notebook outputs; ``REPLICATE_API_TOKEN=``
    # in committed ``.env``; HuggingFace Spaces secrets leaked to repo
    # config.
    (re.compile(r"(?<![A-Za-z0-9])r8_[A-Za-z0-9]{40}(?![A-Za-z0-9])"), "Replicate API Token gefunden"),
    # Perplexity API keys (``pplx-<32+ alphanumeric>``). Issued via
    # perplexity.ai/settings/api for Perplexity's Sonar / Sonar-Pro API
    # (AI-powered search and chat completions with real-time web
    # grounding). The ``pplx-`` prefix is unambiguous, and the strict
    # alphanumeric body lies entirely inside the entropy alphabet — so
    # the entropy regex matches the full ``pplx-<body>`` span as one
    # generic "Hochentropischer Token-String" finding, losing the
    # Perplexity-specific issuer attribution that incident-response
    # keys off (revocation flow at perplexity.ai/settings/api; audit
    # Sonar-API completion logs for chargeback fraud / prompt
    # exfiltration). Body lower bound 32 chars allows future canonical
    # length variations while rejecting short ``pplx-``-prefixed
    # fragments; real Perplexity keys are 48-56 chars body. A leak
    # grants the issuing account's full Perplexity API scope:
    # trigger Sonar / Sonar-Pro completions at the victim's expense
    # (USD 0.20-1.00 per completion for Sonar-Pro with web grounding),
    # exfiltrate proprietary prompts. Real-world emission: ``.env``
    # commits, curl examples in tutorials, CI/CD pipeline debug.
    (re.compile(r"(?<![A-Za-z0-9])pplx-[A-Za-z0-9]{32,}(?![A-Za-z0-9])"), "Perplexity API Key gefunden"),
    # xAI Grok API keys (``xai-<32+ alphanumeric>``). Issued via
    # console.x.ai/team/<id>/api-keys for xAI's Grok platform
    # (Grok-2, Grok-3, Grok-4 — Elon Musk's LLM family released
    # in 2024-2025). The ``xai-`` prefix is unambiguous (no other
    # major issuer uses it), and the strict alphanumeric body lies
    # entirely inside the entropy fallback's ``[A-Za-z0-9+/=_-]``
    # alphabet — so the entropy regex matches the full ``xai-<body>``
    # span as one generic "Hochentropischer Token-String" finding,
    # losing the xAI-specific issuer attribution that incident-
    # response keys off (revocation flow at console.x.ai/team/<id>/
    # api-keys; audit Grok completion-API usage logs for chargeback
    # fraud / model-prompt exfiltration; check the org's billing
    # dashboard for unauthorized large-context completions —
    # Grok-4's 200K-token context window makes a single hostile
    # completion expensive). Body lower bound 32 chars allows future
    # canonical length variations while rejecting short prefix-only
    # fragments; real xAI keys are 64-128 chars body. A leak grants
    # the issuing account's full xAI API scope: trigger expensive
    # Grok completions at the victim's expense (USD 5-15 per 1M
    # tokens), exfiltrate proprietary prompts. xAI Grok was the
    # named-but-deferred next-round candidate from the 2026-05-16
    # Round-1 AI/ML platform tier closure (Groq / Replicate /
    # Perplexity); this round closes it.
    (re.compile(r"(?<![A-Za-z0-9])xai-[A-Za-z0-9]{32,}(?![A-Za-z0-9])"), "xAI API Key gefunden"),
    # OpenRouter API keys (``sk-or-v1-<32+ alphanumeric>``). Issued
    # via openrouter.ai/keys for OpenRouter's unified OpenAI-
    # compatible API aggregator that proxies requests to 200+
    # different LLMs (Claude, GPT, Llama, Mixtral, Gemini, Grok,
    # DeepSeek, etc.). The ``sk-or-v1-`` prefix is structurally
    # DISTINCT from OpenAI's strict ``sk-<48 alphanumeric>`` form
    # (the embedded hyphens in ``sk-or-v1-`` prevent matching
    # OpenAI's regex because the OpenAI body alphabet excludes ``-``),
    # so the two detectors are mutually exclusive at the prefix
    # level. Pre-fix OpenRouter tokens fell to the entropy fallback
    # (the full ``sk-or-v1-<body>`` span matches the high-entropy
    # alphabet because ``-`` is in ``[A-Za-z0-9+/=_-]``) — but as
    # generic "Hochentropischer Token-String", losing the
    # OpenRouter-specific issuer attribution. UNIQUE THREAT
    # AMPLIFIER for OpenRouter: BYOK (Bring Your Own Key) — the
    # platform allows users to attach their own provider keys for
    # fallback / cost optimization. A leaked OpenRouter token grants
    # access to ALL the user's attached provider keys (visible /
    # reusable via the OpenRouter dashboard). This is a CROSS-
    # PLATFORM PIVOT amplifier unique to aggregator platforms —
    # a single OpenRouter leak can compound to leak Anthropic /
    # OpenAI / Groq / etc. keys without those being separately
    # exposed in source.
    (re.compile(r"(?<![A-Za-z0-9])sk-or-v1-[A-Za-z0-9]{32,}(?![A-Za-z0-9])"), "OpenRouter API Key gefunden"),
    # Database connection strings with embedded credentials
    # (``<scheme>://<user>:<pass>@<host>``). Pre-fix EVERY database
    # URI with credentials was SILENTLY UNDETECTED across BOTH
    # detection branches:
    #   1. **Entropy fallback** (``_HIGH_ENTROPY_RE``): the body
    #      alphabet ``[A-Za-z0-9+/=_-]`` excludes ``:``, ``/``, ``@``,
    #      so the entropy matcher splits at every URI delimiter. The
    #      fragments (``postgres``, ``admin``, ``secret123``,
    #      ``db.example.com``, ``5432``, ``prod``) are each below the
    #      24-char floor — NO finding fires.
    #   2. **Assignment heuristic** (``_SENSITIVE_ASSIGN_RE``): even
    #      with a sensitive variable name (``DATABASE_URL``,
    #      ``MONGO_URI``, ``REDIS_URL``), the unquoted-value branch
    #      SKIPS values containing ``()[]:`` characters at
    #      ``_scan_content``. Every database URI contains ``://`` AND
    #      a port-separator ``:`` — the check rejects the URI verbatim.
    # Combined effect: a committed ``.env`` file with
    # ``DATABASE_URL=postgres://admin:supersecret@prod-db.example.com:5432/prod``
    # ships to production with NO scanner alert.
    #
    # Threat model: database credentials are HIGHEST-VALUE secrets —
    # the URI password is base64-decodable in plain text (no offline
    # cracking needed), the URI typically targets production, and the
    # leak enables: (a) full read/write access to all customer data /
    # billing records / session stores; (b) lateral movement via
    # shared password reuse on related infrastructure; (c) schema
    # reconnaissance for social-engineering; (d) persistence via
    # INSERT of backdoor records.
    #
    # The regex matches the canonical RFC-3986-style URI shape for
    # the top database / broker / mail schemes. The structural
    # requirement ``user:pass@`` prevents matching URIs without
    # credentials (``postgres://localhost/db`` is benign — no match).
    # The ``(?i)`` inline flag handles case-insensitive scheme literals
    # (per RFC 3986 §3.1, URI schemes are case-insensitive). The
    # optional ``jdbc:`` prefix covers Java JDBC URL conventions.
    # Real-world emission: ``.env`` files, ``docker-compose.yml``,
    # Heroku ``app.json``, settings.py / application.yml /
    # database.yml, Python notebook output, README example URIs
    # (often live!), migration scripts, K8s ConfigMaps, Terraform
    # outputs.
    (
        re.compile(
            r"(?i)\b(?:jdbc:)?"
            r"(?:postgres(?:ql)?|mysql|mariadb|mongodb(?:\+srv)?|redis|"
            r"amqp|amqps|kafka|clickhouse|cassandra|elasticsearch|smtp|smtps)"
            r"://[^@\s/:]+:[^@\s/]+@[^\s/]+"
        ),
        "Database Connection String gefunden",
    ),
    # Directory / Shell / File-Share Connection Strings with embedded
    # credentials (``<scheme>://<user>:<pass>@<host>``) for the
    # ``ldap`` / ``ldaps`` / ``ssh`` / ``sftp`` / ``smb`` / ``cifs``
    # adjacent families. Strict-sibling drift closure for the
    # 2026-05-16 Database Connection String round above: both the
    # log-sanitisation codepath at ``src/utils/http.py:_URL_AUTH_RE``
    # AND the malformed-URI pattern at ``src/utils/logging.py``
    # already enumerate this EXACT 6-scheme adjacent family — the
    # scanner detection codepath was the third sibling left out of
    # step, so a committed ``LDAP_BIND_URL=ldap://admin:pw@dc01/``
    # shipped to production with the log path masking the credential
    # at operator-log emission time but the git-history source-of-
    # truth leaking the credential verbatim into every clone /
    # archive / search-engine cache.
    #
    # Pre-fix detection gaps (mirror the Database round's analysis):
    #   1. **Entropy fallback** (``_HIGH_ENTROPY_RE``): body alphabet
    #      ``[A-Za-z0-9+/=_-]`` excludes ``:``, ``/``, ``@`` — the
    #      matcher splits at every URI delimiter. The fragments
    #      (``ldap``, ``admin``, ``CompanyAdmin``, ``dc01``,
    #      ``389``, ``dc=example``) are each below the 24-char
    #      floor; no entropy finding fires.
    #   2. **Assignment heuristic** (``_SENSITIVE_ASSIGN_RE``):
    #      even with a sensitive variable name (``LDAP_BIND_URL``,
    #      ``SSH_DEPLOY_URL``, ``SMB_SHARE_URL``), the unquoted-
    #      value branch SKIPS values containing ``:`` (port /
    #      user-pass separator). Every URI contains ``://`` AND
    #      ``:`` — the check rejects the URI verbatim.
    #
    # Threat model (higher-severity than Database — these grant
    # infrastructure-control-plane access, not just data-plane):
    #   * **LDAP / LDAPS**: Active-Directory service-account
    #     bind credentials. Leak grants forest enumeration, user-
    #     object read, attacker-machine domain-join, privileged-
    #     account reset and — with Replicate-Directory-Changes —
    #     DCSync to extract every krbtgt / computer-account hash.
    #   * **SSH / SFTP**: interactive shell / chrooted file-system
    #     access on the target. Universal post-exploitation
    #     primitive (backdoors, exfiltration, lateral pivot).
    #     Common in Ansible inventory, Capistrano ``deploy.rb``,
    #     Fabric ``fabfile.py``, GitLab CI ``deploy_keys``,
    #     GitHub Actions secrets, Dockerfile ``RUN ssh`` patterns.
    #   * **SMB / CIFS**: Windows file-share access. Routine
    #     leak surface for HR documents, executive correspondence,
    #     source-code backups, developer roaming profiles
    #     (containing cached AD credentials).
    #
    # The regex mirrors the Database round's structural anchors:
    # optional ``jdbc:`` prefix (Java JNDI), case-insensitive scheme
    # literal, ``://`` separator, structural ``user:pass@`` shape
    # requirement (so credential-less URIs ``ssh://host`` are NOT
    # flagged). Distinct reason ``Directory/Shell/Share Connection
    # String gefunden`` routes incident-response triage to the
    # correct revocation flow (AD service-account password rotation
    # vs. SSH key rotation vs. file-server password reset are three
    # distinct playbooks). Schemes match exactly the 6-scheme set in
    # ``src/utils/http.py:_URL_AUTH_RE`` and ``src/utils/logging.py``
    # — sibling-floor alignment guarded by
    # ``test_sentinel_directory_shell_share_uri_credential_drift.
    # test_sibling_floor_alignment_with_log_sanitization``.
    (
        re.compile(
            r"(?i)\b(?:jdbc:)?"
            r"(?:ldap|ldaps|ssh|sftp|smb|cifs)"
            r"://[^@\s/:]+:[^@\s/]+@[^\s/]+"
        ),
        "Directory/Shell/Share Connection String gefunden",
    ),
    # DigitalOcean Personal Access Tokens (``dop_v1_<64 hex>``) and OAuth
    # refresh tokens (``doo_v1_<64 hex>``). The ``v1`` prefix anchors against
    # the official format; the strict 64-char lowercase-hex body avoids
    # overlap with the generic high-entropy fallback's ``[A-Za-z0-9+/=_-]``
    # alphabet (which would otherwise flag the body without preserving the
    # ``dop_v1_``/``doo_v1_`` issuer attribution). A leaked dop_v1_ grants
    # full account API access; a leaked doo_v1_ mints fresh dop_v1_'s until
    # revocation, so refresh-token leaks have multi-day blast radius.
    (re.compile(r"(?<![A-Za-z0-9])dop_v1_[a-f0-9]{64}(?![A-Za-z0-9])"), "DigitalOcean Personal Access Token gefunden"),
    (re.compile(r"(?<![A-Za-z0-9])doo_v1_[a-f0-9]{64}(?![A-Za-z0-9])"), "DigitalOcean OAuth Refresh Token gefunden"),
    # GitLab Pipeline Trigger Tokens: ``glptt-<40 chars>``. Distinct from
    # GitLab PATs (``glpat-``) — these tokens are scoped to triggering CI
    # pipelines via the API. A leaked trigger token lets a network adversary
    # kick off arbitrary pipeline runs (including any ``protected_branches``
    # secrets exposed to those pipelines), so the leak surface is the
    # repository's CI permissions rather than the user's PAT scope.
    (re.compile(r"(?<![A-Za-z0-9])glptt-[0-9a-zA-Z_\-]{40}(?![A-Za-z0-9])"), "GitLab Pipeline Trigger Token gefunden"),
    # Mapbox Access Token (``(?:pk|sk|tk)\.eyJ<3-segment-JWT-body>``).
    # Issued via account.mapbox.com/access-tokens/ for the Mapbox Maps /
    # Geocoding / Directions / Static Images / Tilesets / Vision SDK
    # APIs (the geospatial-vendor counterpart to the existing Google
    # API Key family already in this table). The on-the-wire format
    # is a 3-char prefix (``pk.`` public / ``sk.`` secret / ``tk.``
    # temporary) followed by a canonical 3-segment base64url JOSE JWT
    # body — structurally a JWT, but with a Mapbox-specific scope-
    # tier prefix that sits OUTSIDE the existing JWT detector's
    # ``eyJ`` anchor.
    #
    # ORDER REQUIREMENT: this entry MUST appear BEFORE the JWT entry
    # below so the more-specific Mapbox attribution wins via the
    # ``covered_ranges`` arbitration in ``_scan_content``. Pre-fix the
    # JWT entry matched the inner ``eyJ<body>.<body>.<body>`` span and
    # the leading ``pk.``/``sk.``/``tk.`` scope tier was LOST from
    # attribution AND from the covered span. The lookbehind
    # ``(?<![A-Za-z0-9])`` succeeds because the ``.`` before ``eyJ``
    # is non-alphanumeric, so the JWT regex match span ended one
    # character before the Mapbox span — the operator saw a generic
    # JWT finding without knowing it was a Mapbox secret token
    # demanding account.mapbox.com revocation.
    #
    # Threat model per scope tier (each maps to a DISTINCT operational
    # consequence — IR triage MUST identify the scope from the prefix
    # because Mapbox does NOT allow rotating just the scope without
    # rotating the entire token):
    #   * ``pk.`` — Public Access Token. Client-side scopes
    #     (``styles:read`` / ``fonts:read`` / ``datasets:read`` /
    #     ``vision:read``). LEAK: quota theft (third party uses your
    #     Mapbox account's monthly map-load / geocoding quota until
    #     it overflows; per-load overage charges from Mapbox start at
    #     USD 0.50 per 1k loads above the free tier). Routine in
    #     client-side JavaScript bundles (the token is published in
    #     the page's JS payload by design) but committed in source
    #     control it can still be exfiltrated by a network adversary
    #     for sustained quota abuse.
    #   * ``sk.`` — **SECRET Access Token (HIGHEST blast radius in the
    #     Mapbox family).** Full account-write scopes
    #     (``tilesets:write`` / ``uploads:write`` / ``datasets:write``
    #     / ``tokens:write`` / ``styles:write`` / ``analytics:read`` /
    #     potentially ``credentials:write`` for billing). LEAK:
    #     overwrite production tilesets with attacker-controlled
    #     content (route-hijack / map-manipulation amplifier for
    #     embedded navigation widgets), mint new ``sk.`` tokens to
    #     maintain persistence, exfiltrate the account's billing /
    #     analytics data, and modify the production maps used by
    #     downstream consumers (the Wien-OePNV project explicitly
    #     avoids Mapbox in favour of OpenStreetMap — see README §
    #     Datenquellen — but downstream forks and integrators may
    #     hold ``sk.`` tokens that this detector now protects).
    #   * ``tk.`` — Temporary Access Token (ephemeral scope, short
    #     TTL). Lower blast radius than ``pk.``/``sk.`` because the
    #     credential expires; still warrants distinct attribution
    #     so IR can verify the leak window aligned with token TTL.
    #
    # Real-world emission patterns: ``.env`` files
    # (``MAPBOX_ACCESS_TOKEN=sk.eyJ...``), client-side JavaScript
    # bundle build outputs (the public ``pk.`` is canonical, but the
    # ``sk.`` SHOULD NEVER ship to client — a leak in the bundle is
    # a high-severity finding), CI/CD pipeline debug logs, GitHub
    # Actions secrets dumped to logs by a misconfigured action,
    # Mapbox SDK error responses echoing the token back in
    # diagnostic messages, OpenStreetMap-to-Mapbox-bridge tools
    # that hard-code the secret for tileset-upload automation.
    (
        re.compile(
            r"(?<![A-Za-z0-9])(?:pk|sk|tk)\.eyJ"
            r"[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{20,}"
            r"(?![A-Za-z0-9])"
        ),
        "Mapbox Access Token gefunden",
    ),
    # JSON Web Tokens (JWTs): ``eyJ<header>.<payload>.<signature>`` where
    # each segment is base64url-encoded ``[A-Za-z0-9_-]+``. The ``eyJ``
    # prefix is the base64url encoding of ``{"`` (the start of every JOSE
    # JSON header). Multi-segment dot-separated tokens bypass the generic
    # high-entropy fallback (which uses ``[A-Za-z0-9+/=_-]`` and stops at
    # the first dot), so without this specific pattern only one segment
    # at a time would be flagged — losing the full token attribution and
    # making revocation harder. Min lengths chosen to cover realistic
    # HS256/RS256 tokens (~30-char header, ~30-char payload, ~43-char
    # signature) without flagging short base64url strings that happen to
    # have the ``eyJ`` prefix purely by collision. Order: place AFTER more
    # specific issuer-prefixed tokens so ``is_covered`` correctly anchors.
    # The Mapbox detector immediately above MUST stay before this entry
    # because Mapbox tokens have a ``(?:pk|sk|tk)\.`` scope-tier prefix
    # that this JWT pattern's ``eyJ`` anchor would otherwise strip from
    # the matched span — losing the Mapbox-specific issuer attribution
    # and the scope-tier disambiguator that anchors IR triage.
    (
        re.compile(r"(?<![A-Za-z0-9])eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{20,}(?![A-Za-z0-9])"),
        "JSON Web Token (JWT) gefunden",
    ),
    # Twilio Account SID (``AC<32 hex>``) and API Key SID (``SK<32 hex>``).
    # Twilio uses 34-char SIDs prefixed with a 2-letter resource-type code
    # followed by 32 lowercase hex chars; the Account SID is the principal
    # credential for the project and pairs with the Auth Token to make API
    # calls (call/SMS history, billing, phone-number provisioning), while
    # the API Key SID pairs with a separate secret for fine-grained scoped
    # access. Without a specific pattern these tokens fall back to the
    # generic high-entropy detector which would flag the 32-hex body as a
    # bare hash-like string, losing the issuer attribution that incident
    # response keys off (Twilio's revocation flow lives on twilio.com and
    # is distinct from any other vendor's). NOTE: lowercase ``sk_*``
    # (Stripe) does NOT collide — Stripe's prefix is ``sk_live_`` /
    # ``sk_test_`` (lowercase + underscore), Twilio's is uppercase ``SK``
    # immediately followed by hex, so the patterns are mutually exclusive.
    (re.compile(r"(?<![A-Za-z0-9])AC[a-f0-9]{32}(?![A-Za-z0-9])"), "Twilio Account SID gefunden"),
    (re.compile(r"(?<![A-Za-z0-9])SK[a-f0-9]{32}(?![A-Za-z0-9])"), "Twilio API Key SID gefunden"),
    # Notion Internal Integration Token (``secret_<43 alphanumeric>``).
    # Notion API tokens are issued via developer integrations at
    # https://www.notion.so/my-integrations and grant read/write access to
    # whatever workspace content the integration is shared with — full
    # database/page contents, including any private collaborator notes.
    # The ``secret_`` prefix is Notion's canonical issuer tag, but the
    # underscore separates the prefix from the 43-char alphanumeric body,
    # so the entropy fallback's ``[A-Za-z0-9+/=_-]`` alphabet WOULD match
    # the full token as a single span — but only flag it as a generic
    # high-entropy hit, losing the Notion-specific issuer attribution that
    # downstream revocation playbooks need. The body length is exactly
    # 43 alphanumeric chars (no underscores or hyphens) — strict body
    # match avoids colliding with operator-set ``SECRET_KEY = "..."``
    # variable assignments captured by the broader ``_SENSITIVE_ASSIGN_RE``.
    (
        re.compile(r"(?<![A-Za-z0-9])secret_[A-Za-z0-9]{43}(?![A-Za-z0-9])"),
        "Notion Integration Token gefunden",
    ),
    # Notion Modern Integration Token (``ntn_<43+ chars>``). The newer
    # token format Notion introduced for the v2024-09-API rollout. Same
    # blast radius as the legacy ``secret_`` form (workspace read/write
    # against the integration's shared content), so distinct attribution
    # matters for revocation. ``ntn_`` is unambiguous (no other major
    # issuer uses this prefix), and the 43+ alphanumeric/underscore/hyphen
    # body distinguishes the modern format from the strict-43-alphanumeric
    # legacy ``secret_`` body above.
    (
        re.compile(r"(?<![A-Za-z0-9])ntn_[A-Za-z0-9_\-]{43,}(?![A-Za-z0-9])"),
        "Notion Modern Integration Token gefunden",
    ),
    # Discord Bot Token: ``<base64url(user-id)>.<base64url(timestamp)>.<HMAC>``.
    # Three dot-separated base64url segments — structurally identical to
    # JWTs but with the snowflake-ID-based first segment instead of the
    # JOSE ``eyJ`` header. Discord stringifies the user ID (decimal
    # digits) before base64-encoding it, so the first segment ALWAYS
    # starts with the base64 encoding of the leading decimal digit:
    # ``1``-``3``→``M``, ``4``-``7``→``N``, ``8``-``9``→``O``. Every
    # snowflake user ID starts with a single decimal digit (1-9), so
    # ``[MNO]`` is a complete leading-character constraint and is the
    # disambiguator from JWTs (which always start with ``eyJ``). The
    # mutual exclusion is enforced at the leading-character level: no
    # token can match both the JWT and Discord patterns.
    #
    # A leaked bot token grants the attacker FULL bot privileges in
    # every guild the bot is invited to (read/write all visible
    # messages, kick/ban users, edit channels and roles, run any
    # registered slash commands, with appropriate scopes read voice/DM
    # history). The dots are outside the entropy fallback's
    # ``[A-Za-z0-9+/=_-]`` alphabet, so without this specific pattern
    # only ONE segment is matched at a time — the full-token span (and
    # the Discord-specific reason needed for revocation at the
    # https://discord.com/developers/applications/ Developer Portal)
    # would be lost. Body-length quantifiers: first segment 24+ chars
    # (real-world snowflake-IDs base64-encode to 24-28 chars), second
    # segment exactly 6 chars (4-byte timestamp), third segment 27+
    # chars (HMAC-SHA256 truncation). Order: place AFTER JWT so a
    # JWT-shape token whose first segment happens to start with [MNO]
    # (impossible in practice but guarded structurally) would still
    # match the JWT pattern first via ``is_covered``.
    (
        re.compile(
            r"(?<![A-Za-z0-9])[MNO][A-Za-z0-9_\-]{22,27}\.[A-Za-z0-9_\-]{6,7}\.[A-Za-z0-9_\-]{27,}(?![A-Za-z0-9])"
        ),
        "Discord Bot Token gefunden",
    ),
    # Atlassian Cloud API Token (``ATATT3xFfGF0<base64 body><CRC32 hex>``).
    # Issued via id.atlassian.com/manage-profile/security/api-tokens for
    # Jira / Confluence / Trello Cloud REST-API access. The canonical
    # format is a 12-char unique prefix (``ATATT3xFfGF0``) followed by
    # ~184 base64url-alphabet body chars and an 8-char CRC32 hex suffix
    # — total ~204 chars in observed real tokens. The body alphabet is
    # ``[A-Za-z0-9_=\-]`` (base64url + ``=`` padding); the prefix is
    # unambiguous (no other major issuer uses ``ATATT3xFfGF0``) so a
    # 100+ body length (well below the canonical ~192 chars) provides a
    # safe lower bound that rejects accidental ``ATATT3``-prefixed
    # fragments while accepting every legitimate token. A leak grants
    # the issuing user's full Cloud-API scope across every accessible
    # workspace (read every Jira issue/page, post comments, transition
    # tickets, browse Confluence pages, manipulate Trello boards) — the
    # revocation flow lives at id.atlassian.com and is distinct from
    # any other vendor's, so issuer-specific attribution accelerates
    # IR triage. Pre-fix the body matched the entropy fallback as a
    # generic high-entropy span; the prefix and the CRC32 suffix were
    # silently lost.
    (
        re.compile(r"(?<![A-Za-z0-9])ATATT3xFfGF0[A-Za-z0-9_=\-]{100,}(?![A-Za-z0-9])"),
        "Atlassian API Token gefunden",
    ),
    # Bitbucket App Password / Repository Access Token (``ATBB<24+ alnum
    # body>``). Issued via bitbucket.org/account/settings/app-passwords/
    # (App Password — user-scoped, the common shape for CLI / git-over-
    # HTTPS) or via per-repository / workspace / project settings >
    # Access Tokens (resource-scoped, used by deploy scripts and CI/CD
    # pipelines). Sibling-drift closure for the 2026-05-16 Atlassian
    # Cloud API Token (``ATATT3xFfGF0``) round above: same issuer
    # (Atlassian Corporation), distinct product (Bitbucket vs. Jira /
    # Confluence / Trello), distinct prefix (``ATBB`` vs.
    # ``ATATT3xFfGF0``), DISTINCT REVOCATION FLOW
    # (bitbucket.org/account/settings/app-passwords/ for App Passwords
    # plus the project/repo/workspace Access Tokens settings pages for
    # the resource-scoped variants; ALL distinct from
    # id.atlassian.com/manage-profile/security/api-tokens for the
    # Atlassian Cloud API Tokens). Per-issuer attribution is critical
    # for IR triage because the operator must navigate to the correct
    # admin panel — confusing Bitbucket with Atlassian Cloud sends the
    # responder to the WRONG settings page.
    #
    # Format: canonical trufflehog / gitleaks default rule for
    # Bitbucket Access Tokens is ``ATBB[a-zA-Z0-9]{32}([a-fA-F0-9]{8})?``
    # — strict 32-char alphanumeric body plus optional 8-char CRC32
    # suffix (total 36 or 44 chars after the ``ATBB`` prefix). Real-
    # world tokens land in the 36-44 char body range. The ``{24,}``
    # lower bound here is intentionally permissive to cover historical
    # variants (the legacy short Bitbucket Server access-token format
    # had a 24-char body) while still rejecting accidental fragments
    # via the strict ``[A-Za-z0-9]`` body alphabet (no underscores or
    # hyphens — distinguishes from the GitHub / GitLab / NPM token
    # families which all use ``[A-Za-z0-9_-]``).
    #
    # Pre-fix detection gaps (mirror the Figma + Tailscale round):
    #   1. **Entropy fallback** (``_HIGH_ENTROPY_RE``): the body
    #      alphabet ``[A-Za-z0-9]`` lies INSIDE the entropy alphabet
    #      ``[A-Za-z0-9+/=_-]`` so the full ``ATBB<body>`` span DID
    #      match generically as ``Hochentropischer Token-String`` —
    #      BUT the Bitbucket-specific issuer attribution that anchors
    #      the revocation flow at bitbucket.org/account/settings/
    #      app-passwords/ was LOST. The generic high-entropy reason
    #      forces the operator to manually identify the issuer from
    #      surrounding context, slowing IR triage.
    #   2. **Log sanitisation codepath**: the bare ``ATBB<body>`` token
    #      shape in plain log text (application f-string logs,
    #      Bitbucket API error responses echoing the token back, JSON
    #      values with non-sensitive keys, URL paths embedding the
    #      token) leaked verbatim — no value-shape mask existed for
    #      this token family.
    #
    # Threat model: a leaked Bitbucket Repository Access Token grants
    # the issuing principal's full Bitbucket Cloud scope per the
    # token's configured permissions (read / write / admin on the
    # specific repo / workspace / project). Common scope combinations:
    #   * ``repository:read + pullrequest:read`` — code-disclosure leak,
    #     IP exfiltration, source-control reconnaissance.
    #   * ``repository:write`` — push backdoored commits to protected
    #     branches (canonical supply-chain compromise primitive on
    #     Bitbucket-hosted projects).
    #   * ``workspace:admin`` — modify workspace member roles, add
    #     attacker accounts as collaborators, exfiltrate every repo
    #     in the workspace, rotate every other workspace token to
    #     maintain persistence.
    #   * App Password tier — additionally grants the user's read
    #     access across EVERY accessible workspace (multi-workspace
    #     pivot amplifier).
    # Blast radius mirrors the GitHub PAT (``ghp_``) and GitLab PAT
    # (``glpat-``) families on their respective platforms — the
    # repository-host control plane is the highest leak surface for
    # source-control-driven supply-chain compromise.
    (
        re.compile(r"(?<![A-Za-z0-9])ATBB[A-Za-z0-9]{24,}(?![A-Za-z0-9])"),
        "Bitbucket Access Token gefunden",
    ),
    # Sentry Auth Token (``sntrys_<base64-with-embedded-JSON>``).
    # Sentry's modern rotation-aware auth-token format (introduced
    # 2023; replaces the legacy 32/64-hex internal tokens). The body
    # encodes an embedded JSON payload describing the organisation /
    # scope plus a trailing checksum guarding against typo-induced
    # cross-token confusion. Body alphabet is ``[A-Za-z0-9_=\-]``
    # (base64url + ``=`` padding + the underscore separator between
    # body and checksum). Total length 60-100+ chars in practice; the
    # 30+ body lower bound rejects short ``sntrys_``-prefixed
    # fragments while accepting every legitimate token. A leak grants
    # access to the Sentry org-level API
    # (``/api/0/organizations/<slug>/...``) — every project's issue /
    # event data, releases, debug files, source maps, member list and
    # webhook configuration — full IR-relevant blast radius. The
    # revocation flow lives at sentry.io/settings/auth-tokens/ and is
    # distinct from any other vendor's. Pre-fix the body matched the
    # entropy fallback as a generic high-entropy span; the
    # ``sntrys_`` prefix that anchors revocation was silently lost.
    (
        re.compile(r"(?<![A-Za-z0-9])sntrys_[A-Za-z0-9_=\-]{30,}(?![A-Za-z0-9])"),
        "Sentry Auth Token gefunden",
    ),
    # Linear API Key (``lin_api_<32+ alphanumeric chars>``). Issued via
    # linear.app/settings/api for personal API access against the
    # Linear (issue tracker / project management) GraphQL API. A leak
    # grants the issuing user's full Linear scope: read/write every
    # visible issue, comment, attachment, project, team metadata and
    # webhook configuration. The ``lin_api_`` prefix is unambiguous
    # (no other major issuer uses it), and the strict alphanumeric
    # body (no ``_``/``-`` after the prefix in canonical Linear
    # format) avoids overlap with the hyphenated bodies of other
    # tokens (``glpat-``, ``ghp_`` family). Body lower bound 32 chars
    # rejects short ``lin_api_`` fragments while accepting the
    # canonical 40-char-body shape; the 32-char floor matches the
    # historic minimum observed in older Linear tokens. The
    # revocation flow lives at linear.app/settings/api and is distinct
    # from any other vendor's, so issuer-specific attribution
    # accelerates IR triage. Pre-fix the entropy fallback flagged the
    # ``lin_api_<body>`` span generically (the underscore is in the
    # alphabet) without preserving the Linear-specific issuer name
    # that incident response keys off.
    (
        re.compile(r"(?<![A-Za-z0-9])lin_api_[A-Za-z0-9]{32,}(?![A-Za-z0-9])"),
        "Linear API Key gefunden",
    ),
    # Brevo (formerly Sendinblue) v3 API Key
    # (``xkeysib-<64 lowercase hex>-<16 alphanumeric>``). Issued via
    # app.brevo.com/settings/keys/api for transactional email,
    # marketing-automation, contacts, SMS-API and webhook configuration
    # access. Total length 89 chars (8-char prefix + 64-char hex secret
    # + 1 dash + 16-char alphanumeric request-id-like suffix). The
    # ``xkeysib-`` prefix is unambiguous (no other major issuer uses
    # it), and the strict 64-hex secret + 16-alphanumeric suffix matches
    # Brevo's documented canonical format. A leak grants the issuing
    # account's full transactional-mail / contacts API scope: the
    # attacker can send mail FROM the project's domain (phishing
    # amplification leveraging existing SPF / DKIM authentication),
    # exfiltrate the contact list, register webhooks redirecting
    # delivery events to attacker-controlled endpoints, or modify
    # campaign templates. The revocation flow lives at
    # https://app.brevo.com/settings/keys/api and is distinct from
    # any other vendor's, so issuer-specific attribution accelerates
    # IR triage. Pre-fix the entropy fallback's
    # ``[A-Za-z0-9+/=_-]{24,}`` regex matches the full token span as a
    # single "Hochentropischer Token-String" finding (hyphen is in the
    # alphabet) WITHOUT preserving the Brevo-specific issuer name.
    (
        re.compile(r"(?<![A-Za-z0-9])xkeysib-[a-f0-9]{64}-[A-Za-z0-9]{16}(?![A-Za-z0-9])"),
        "Brevo (Sendinblue) API Key gefunden",
    ),
    # Postman API Key (``PMAK-<24 hex>-<34 hex>``). Issued via
    # postman.com/settings/me/api-keys for full Postman REST-API access:
    # read/write every accessible workspace's collections, environments,
    # mocks, monitors, and team membership. Total length 64 chars
    # (5-char prefix + 24-char hex + 1 dash + 34-char hex). The ``PMAK-``
    # prefix is unambiguous (no other major issuer uses uppercase
    # ``PMAK-``), and the strict hex body avoids overlap with the
    # entropy fallback's broader alphabet. A leak grants the issuing
    # user's full Postman API scope across every workspace they belong
    # to, including private API definitions and mock-server URLs that
    # may carry embedded credentials. The revocation flow lives at
    # postman.com/settings/me/api-keys and is distinct from any other
    # vendor's. Pre-fix the entropy fallback flagged the body+suffix
    # as a generic high-entropy span, losing the Postman attribution.
    (
        re.compile(r"(?<![A-Za-z0-9])PMAK-[a-fA-F0-9]{24}-[a-fA-F0-9]{34}(?![A-Za-z0-9])"),
        "Postman API Key gefunden",
    ),
    # HashiCorp Cloud Platform (HCP) Vault Secrets token (``hvs.<base64
    # body>``). Issued via portal.cloud.hashicorp.com for HCP Vault
    # Secrets API access (the managed-Vault offering — read every
    # secret stored in the namespace's apps and integrations). The
    # same ``hvs.`` prefix is also issued by self-hosted HashiCorp
    # Vault (Enterprise + Community) since the 1.10 release
    # (2022-03) for persistent ``service``-type tokens (default token
    # shape, written to Vault's storage backend; companion to the
    # ``hvb.`` batch token + ``hvr.`` recovery token siblings closed
    # in the entries immediately below this one). Total length
    # typically 95-110 chars (4-char prefix incl. dot + 90+ char
    # base64url body). The literal ``.`` separator disambiguates
    # from any alphanumeric-prefixed token already in the table. A
    # leak grants whoever holds the token full read-access to every
    # secret the issuing service principal / human user can see —
    # the highest blast-radius credential class in the modern infra
    # stack. The revocation flow lives at portal.cloud.hashicorp.com
    # (HCP) or ``vault token revoke <token>`` (self-hosted) and is
    # distinct from any other vendor's, so issuer-specific
    # attribution is critical for IR triage. Pre-fix the entropy
    # fallback flagged the body as a generic high-entropy span (the
    # ``.`` is OUTSIDE the entropy alphabet ``[A-Za-z0-9+/=_-]``, so
    # only the body span after ``hvs.`` matched), losing the
    # HCP-specific issuer attribution.
    (
        re.compile(r"(?<![A-Za-z0-9])hvs\.[A-Za-z0-9_\-]{30,}(?![A-Za-z0-9])"),
        "HCP Vault Secrets Token gefunden",
    ),
    # HashiCorp Vault Batch Token (``hvb.<base64url body>``). The
    # canonical batch-token prefix for self-hosted HashiCorp Vault
    # (Vault Enterprise + Vault Community) since the 1.10 release
    # (2022-03; replaces the legacy ``b.`` prefix). Sibling-drift
    # closure for the ``hvs.`` Vault Service Token detector above:
    # the two prefixes are issued by the SAME Vault auth-method API
    # call (``auth/token/create``) with different ``type`` parameters
    # — ``service`` produces the persistent ``hvs.`` token (default,
    # written to Vault's storage backend), ``batch`` produces the
    # ephemeral, lightweight ``hvb.`` token (NOT written to storage
    # — Vault encrypts the token's auth data into the token itself,
    # which means batch tokens scale to high-throughput workloads
    # without storage backend pressure). Pre-fix the ``hvb.`` shape
    # had NO ``_KNOWN_TOKENS`` entry and the entropy fallback
    # flagged only the body span after ``hvb.`` (the ``.`` is
    # OUTSIDE the entropy alphabet ``[A-Za-z0-9+/=_-]``) as a
    # generic ``Hochentropischer Token-String`` finding, losing the
    # Vault-specific issuer attribution that incident-response
    # triage keys off.
    #
    # Threat model: batch tokens are typically issued for CI/CD
    # pipelines, ephemeral container workloads, and serverless
    # function invocations — the exact contexts where token leaks
    # in committed source are MOST likely. A leaked batch token
    # grants the issuing policy's full Vault scope for the token's
    # TTL: read every KV secret the policy permits (database creds,
    # cloud provider keys, internal API tokens, OAuth client
    # secrets routinely stored in Vault), generate dynamic
    # secrets (database credentials, AWS STS tokens via the
    # ``aws/sts`` mount, GCP service-account tokens via the
    # ``gcp/`` mount, PKI certificates via the ``pki/`` mount),
    # and — if the policy includes ``encrypt`` capability on a
    # ``transit/`` mount — encrypt/decrypt arbitrary application
    # data. Blast radius is the same per-policy scope as
    # ``hvs.`` service tokens, scoped only by the batch token's
    # TTL (typically minutes to hours, but configurable to days).
    # The revocation flow lives at ``vault token revoke <token>``
    # (or the API equivalent ``POST /v1/auth/token/revoke``) and
    # is the same revocation flow as ``hvs.`` service tokens
    # (Vault treats both as first-class auth tokens for
    # revocation), so issuer-specific attribution accelerates IR
    # triage to the correct Vault cluster's audit log + revoke
    # endpoint.
    (
        re.compile(r"(?<![A-Za-z0-9])hvb\.[A-Za-z0-9_\-]{30,}(?![A-Za-z0-9])"),
        "HashiCorp Vault Batch Token gefunden",
    ),
    # HashiCorp Vault Recovery Token (``hvr.<base64url body>``). The
    # canonical recovery-token prefix for self-hosted HashiCorp Vault
    # since 1.10 release (replaces the legacy ``r.`` prefix). Recovery
    # tokens are issued ONLY in HSM-backed or auto-unseal Vault
    # deployments (the Enterprise tier or the Cloud KMS / AWS KMS /
    # Azure Key Vault / GCP CKMS auto-unseal flows) via the
    # ``generate-recovery-token`` API (``POST /v1/sys/generate-
    # recovery-token``). They authorise the highest-privilege
    # recovery operations on a sealed or partially-sealed Vault
    # cluster — operations a regular root token CANNOT perform when
    # Vault is in a degraded state (sealed / cluster-leader-failover
    # / lost-quorum scenarios). Sibling-drift closure for the
    # ``hvs.`` Vault Service Token detector above: same Vault
    # cluster, same on-the-wire encoding (base64url body after the
    # dotted prefix), distinct privilege tier.
    #
    # Threat model (HIGHEST severity in the HashiCorp Vault token
    # family): a leaked recovery token grants the holder root-
    # equivalent operations on the sealed Vault cluster — including
    # the ability to GENERATE A NEW ROOT TOKEN
    # (``POST /v1/sys/generate-root``) once Vault is unsealed, which
    # is then a persistent backdoor with FULL Vault administrative
    # capability. The compromised root token can subsequently:
    #   * Read every secret in every namespace / KV mount.
    #   * Modify every policy attached to every token.
    #   * Disable audit logging to cover tracks.
    #   * Add new auth methods (LDAP, Kerberos, OIDC, AppRole) for
    #     persistent attacker access independent of the root
    #     token's eventual revocation.
    #   * Mint new dynamic secrets (cloud provider credentials,
    #     database admin accounts) that outlive the Vault breach.
    # Pre-fix the ``hvr.`` shape had NO ``_KNOWN_TOKENS`` entry
    # and the entropy fallback only flagged the body span after
    # ``hvr.`` as a generic ``Hochentropischer Token-String``
    # finding, losing both the Vault-recovery-specific issuer
    # attribution AND the cluster-recovery-flow-specific incident
    # response surface (operator must immediately re-seal the
    # cluster, regenerate the recovery-key shares via Shamir
    # threshold reconstruction, and audit every operation since
    # the recovery token was issued for evidence of a generated
    # root token). The revocation flow lives at
    # ``POST /v1/sys/generate-recovery-token/attempt`` (cancel +
    # restart the recovery flow) and is distinct from regular
    # token revocation, so issuer-specific attribution is
    # critical for IR triage.
    (
        re.compile(r"(?<![A-Za-z0-9])hvr\.[A-Za-z0-9_\-]{30,}(?![A-Za-z0-9])"),
        "HashiCorp Vault Recovery Token gefunden",
    ),
    # Doppler tokens (``dp.<role>.<43 alphanumeric body>`` where
    # ``<role>`` is one of ``pt`` / ``st`` / ``sa`` / ``ct`` / ``scim``
    # / ``audit``). Issued via dashboard.doppler.com for Doppler's
    # secrets-management API. The six roles correspond to:
    # personal-token (``pt``), service-token (``st``), service-account
    # token (``sa``), CLI token (``ct``), SCIM provisioning token
    # (``scim``) and audit-log token (``audit``). Total length 49-52
    # chars (3-char ``dp.`` prefix + 2-5 char role + 1 dot + 43-char
    # alphanumeric body). The literal ``.`` separators are OUTSIDE the
    # entropy fallback's alphabet ``[A-Za-z0-9+/=_-]``, so the entropy
    # regex matches only the 43-char body span — losing both the
    # ``dp.<role>.`` prefix AND the Doppler issuer attribution that
    # incident-response triage keys off. A leak grants the issuing
    # principal's full Doppler scope across every project / config
    # they can see — read every secret (database creds, third-party
    # API keys, OAuth client secrets, signing keys are all routinely
    # stored in Doppler environments), modify config branches, and
    # exfiltrate the audit log. The revocation flow lives at
    # dashboard.doppler.com and is distinct from any other vendor's.
    # Doppler is the canonical secrets-management sibling of HCP
    # Vault Secrets (Round 6) and rounds out the secrets-management
    # sub-landscape Round 6 named but did not enumerate.
    (
        re.compile(r"(?<![A-Za-z0-9])dp\.(?:pt|st|sa|ct|scim|audit)\.[A-Za-z0-9]{43}(?![A-Za-z0-9])"),
        "Doppler Token gefunden",
    ),
    # Buildkite Agent Token (``bkat_<40+ alphanumeric body>``). Issued
    # via buildkite.com/organizations/<org>/agents for Buildkite agent
    # registration. The ``bkat_`` prefix is unambiguous (no other
    # major issuer uses it), and the strict alphanumeric body lies
    # entirely inside the entropy fallback's alphabet — so the
    # entropy regex matches the full ``bkat_<body>`` span as one
    # generic finding, losing the Buildkite-specific attribution. A
    # leak lets a network adversary register a rogue agent that
    # drains the Buildkite job queue: every CI job (with whatever
    # build-secret env vars the pipeline exposes) is delivered to
    # attacker-controlled hardware. Blast radius = the entire CI
    # estate's job-execution surface — the highest leak surface in
    # the modern CI stack. Body lower bound 40 chars matches
    # Buildkite's documented agent-token format and rejects short
    # ``bkat_``-prefixed fragments while accepting every legitimate
    # token. The revocation flow lives at
    # buildkite.com/organizations/<org>/agents and is distinct from
    # any other vendor's.
    (
        re.compile(r"(?<![A-Za-z0-9])bkat_[A-Za-z0-9]{40,}(?![A-Za-z0-9])"),
        "Buildkite Agent Token gefunden",
    ),
    # Netlify Personal Access Token (``nfp_<40+ alphanumeric body>``).
    # Issued via app.netlify.com/user/applications for full Netlify
    # REST-API access (the modern post-2023 ``nfp_``-prefixed format;
    # the legacy 40-char-hex pre-prefix tokens fall into the
    # bucket-(b) no-prefix landscape). Total length 44+ chars (4-char
    # prefix + 40+ char body). The ``nfp_`` prefix is unambiguous,
    # and the body lies entirely inside the entropy alphabet —
    # same generic-only attribution gap as Buildkite. A leak grants
    # the issuing user's full Netlify API scope: read/write every
    # site's deploys, redirect rules, environment variables, build-
    # hook URLs, edge-function code, and DNS records. The site-deploy
    # primitive in particular means an attacker can replace the live
    # site with arbitrary HTML / JS, bypassing every downstream
    # content gate. The revocation flow lives at
    # app.netlify.com/user/applications and is distinct from any
    # other vendor's. Netlify rounds out the CI/CD sub-landscape's
    # hosting-platform tier alongside Buildkite (CI execution).
    (
        re.compile(r"(?<![A-Za-z0-9])nfp_[A-Za-z0-9]{40,}(?![A-Za-z0-9])"),
        "Netlify Personal Access Token gefunden",
    ),
    # Render Personal Access Token (``rnd_<40+ alphanumeric body>``).
    # Issued via dashboard.render.com/u/settings#api-keys for full
    # Render REST-API access (the modern Render-platform token
    # format). Total length 44+ chars (4-char prefix + 40+ char
    # body). The ``rnd_`` prefix is unambiguous (no other major
    # issuer uses it), and the body lies entirely inside the
    # entropy fallback's ``[A-Za-z0-9+/=_-]`` alphabet — so the
    # entropy regex matches the full ``rnd_<body>`` span as one
    # generic finding, losing the Render-specific attribution that
    # incident-response keys off. A leak grants the issuing user's
    # full Render API scope: read/write every owned service's
    # deploys, environment variables, persistent disks, custom
    # domains, build hooks and webhook configuration; a malicious
    # deploy can replace the live application (web service, static
    # site, cron job, background worker) with arbitrary code,
    # bypassing every downstream gate. The revocation flow lives at
    # dashboard.render.com/u/settings#api-keys and is distinct from
    # any other vendor's. Render closes the named-but-deferred
    # Round-6/Round-7 hosting-platform sibling alongside Netlify
    # (Round 7) on the CI/CD hosting tier.
    (
        re.compile(r"(?<![A-Za-z0-9])rnd_[A-Za-z0-9_\-]{40,}(?![A-Za-z0-9])"),
        "Render API Key gefunden",
    ),
    # Buildkite User Access Token (``bkua_<40+ alphanumeric body>``).
    # Issued via buildkite.com/user/api-access-tokens for user-scoped
    # REST-API access (issue queries, build retries, pipeline
    # manipulation, agent management). Distinct from the Round-7
    # Buildkite Agent Token (``bkat_``): agent tokens register CI
    # workers, user tokens act on behalf of a human user. The two
    # patterns are mutually exclusive at the prefix level (``bkat_``
    # vs ``bkua_`` differ at the fourth character). The ``bkua_``
    # prefix is unambiguous, and the body lies entirely inside the
    # entropy alphabet — same generic-only attribution gap as
    # ``bkat_``. A leak grants the issuing user's full Buildkite
    # API scope across every accessible organisation: read pipeline
    # definitions (which often embed secrets in env references),
    # retry historical builds with attacker-controlled env
    # overrides, manage agents, and exfiltrate access logs. The
    # revocation flow lives at buildkite.com/user/api-access-tokens
    # (distinct from agent-token revocation), so issuer-specific
    # attribution accelerates IR triage.
    (
        re.compile(r"(?<![A-Za-z0-9])bkua_[A-Za-z0-9]{40,}(?![A-Za-z0-9])"),
        "Buildkite User Access Token gefunden",
    ),
    # New Relic User API Key (``NRAK-<27 uppercase alphanumeric body>``).
    # Issued via one.newrelic.com > API Keys > Create key (User key
    # type) for full New Relic platform API access (NerdGraph
    # queries, account configuration, alert policy / notification
    # channel management, dashboard create/update/delete, user
    # management). Total length 32 chars (5-char ``NRAK-`` prefix +
    # 27-char alphanumeric body). The ``NRAK-`` prefix is unambiguous
    # (no other major issuer uses it), and the strict alphanumeric
    # body lies entirely inside the entropy fallback's
    # ``[A-Za-z0-9+/=_-]`` alphabet — so the entropy regex matches
    # the full ``NRAK-<body>`` span as one generic finding, losing
    # the New-Relic-specific issuer attribution that incident-
    # response keys off. A leak grants the issuing user's full New
    # Relic API scope across every accessible account: query every
    # ingested metric / log / trace, modify alert routing
    # (suppressing real incidents), exfiltrate dashboard contents
    # (which often embed business metric names that reveal product
    # telemetry), and create new API keys to maintain persistence.
    # The revocation flow lives at one.newrelic.com/api-keys and is
    # distinct from any other vendor's. New Relic closes the
    # named-but-deferred Round-8 observability sub-landscape.
    (
        re.compile(r"(?<![A-Za-z0-9])NRAK-[A-Z0-9]{27}(?![A-Za-z0-9])"),
        "New Relic User API Key gefunden",
    ),
    # New Relic REST API Key (``NRRA-<40 lowercase hex body>``). The
    # legacy REST API v2 credential format (deprecated in favour of
    # NRAK since 2021 but still issued and accepted for backward
    # compatibility). Total length 45 chars (5-char ``NRRA-`` prefix
    # + 40-char lowercase hex body). The ``NRRA-`` prefix is
    # unambiguous, and the strict hex body lies entirely inside the
    # entropy fallback's alphabet. A leak grants the issuing
    # account's REST API v2 scope: read application performance
    # data, browser monitoring data, mobile monitoring data, and
    # synthetic monitoring data. The legacy key format has fewer
    # scoping controls than NRAK, so leak surfaces are typically
    # wider. Distinct revocation flow at one.newrelic.com/api-keys
    # under the "REST API Keys" tab.
    (
        re.compile(r"(?<![A-Za-z0-9])NRRA-[a-fA-F0-9]{40}(?![A-Za-z0-9])"),
        "New Relic REST API Key gefunden",
    ),
    # New Relic Insights Insert Key (``NRII-<32 lowercase hex body>``).
    # Issued via one.newrelic.com > API Keys > Create key (Insights
    # Insert key type) for ingestion-only access to the New Relic
    # Events / Insights API. Total length 37 chars (5-char ``NRII-``
    # prefix + 32-char lowercase hex body). The ``NRII-`` prefix is
    # unambiguous, and the strict hex body lies entirely inside the
    # entropy fallback's alphabet. A leak grants the issuing
    # account's event-ingestion scope: an attacker can spam the
    # account's event stream with fabricated metrics, polluting
    # dashboards, triggering false-positive alerts, and consuming
    # the account's data ingestion quota. Distinct revocation flow
    # at one.newrelic.com/api-keys under the "Insights Insert Keys"
    # tab.
    (
        re.compile(r"(?<![A-Za-z0-9])NRII-[a-fA-F0-9]{32}(?![A-Za-z0-9])"),
        "New Relic Insights Insert Key gefunden",
    ),
    # Fly.io API Token (``FlyV1 fm[12]_<base64 body>`` or
    # ``FlyV1 fo1_<base64 body>``). Issued via the ``fly auth token``
    # CLI or fly.io/dashboard/<org>/tokens for full Fly.io platform
    # API access (deploy apps, read secrets, manipulate Wireguard
    # peers, manage organisations). The canonical leak surface is the
    # Authorization-header form ``FlyV1 <token>``: the ``FlyV1 ``
    # scheme prefix (with literal space) anchors against fly.io
    # specifically. Modern macaroon tokens use ``fm2_`` (current
    # default) or ``fm1_`` (legacy macaroon), and the oldest opaque
    # tokens use ``fo1_``. Total length 200+ chars in practice
    # (the macaroon body encodes embedded JSON capability
    # descriptions plus organisation / app scope). The literal
    # space in ``FlyV1 `` and the body alphabet
    # ``[A-Za-z0-9_=\-]`` (base64url + ``=`` padding) place the
    # prefix outside the entropy fallback's contiguous-match span —
    # pre-fix the entropy regex matches only the body span after
    # the underscore, losing both the ``FlyV1 fm2_`` prefix AND the
    # Fly.io-specific issuer attribution. The 50+ body lower bound
    # rejects short ``FlyV1 fm2_``-prefixed fragments while
    # accepting every legitimate token (real Fly.io macaroons are
    # always >150 chars). A leak grants the issuing principal's
    # full Fly.io organisation scope: deploy arbitrary container
    # images (which can exfiltrate every secret in the org's apps),
    # modify networking (Wireguard peers, IP allocations, Anycast
    # routes), and rotate billing credentials. The revocation flow
    # lives at fly.io/dashboard/<org>/tokens and is distinct from
    # any other vendor's. Fly.io is the canonical PaaS / edge-
    # runtime sibling not previously covered.
    (
        re.compile(r"(?<![A-Za-z0-9])FlyV1 (?:fm[12]|fo1)_[A-Za-z0-9_=\-]{50,}(?![A-Za-z0-9])"),
        "Fly.io API Token gefunden",
    ),
    # GitLab Runner Authentication Token (``glrt-<20 chars from
    # [A-Za-z0-9_-]>``). Issued via project / group / instance Runner
    # registration in GitLab 15.6+ (the post-16.0 default replacing the
    # legacy unprefixed registration-token shape). Format mirrors
    # ``glpat-``: 5-char prefix + 20-char ``[A-Za-z0-9_-]`` body. The
    # ``glrt-`` prefix is unambiguous (no other major issuer uses it),
    # and the body lies entirely inside the entropy fallback's
    # ``[A-Za-z0-9+/=_-]`` alphabet — so the entropy regex would match
    # the full ``glrt-<body>`` span as one generic
    # ``Hochentropischer Token-String`` finding, losing the
    # GitLab-Runner-specific issuer attribution that incident-response
    # keys off. A leak grants whoever holds the token the ability to
    # **register a rogue GitLab Runner** against the issuing project /
    # group / instance scope: the rogue runner subsequently drains the
    # CI job queue, and every CI job (with whatever build secrets the
    # pipeline exposes — DEPLOYMENT_KEY, CONTAINER_REGISTRY_PASSWORD,
    # every protected-branch-scoped CI variable) is delivered to
    # attacker-controlled hardware. Blast radius = the entire CI
    # estate's job-execution surface — structurally identical to the
    # Buildkite Agent Token (``bkat_``, Round 7) covered earlier. The
    # revocation flow lives at gitlab.com/<scope>/-/runners and is
    # distinct from any other vendor's, so issuer-specific attribution
    # accelerates IR triage.
    (
        re.compile(r"(?<![A-Za-z0-9])glrt-[A-Za-z0-9_\-]{20}(?![A-Za-z0-9])"),
        "GitLab Runner Authentication Token gefunden",
    ),
    # GitLab Deploy Token (``gldt-<20 chars from [A-Za-z0-9_-]>``).
    # Issued via project / group settings > Repository > Deploy Tokens
    # in GitLab 16.0+ (the post-16.0 default with prefix; pre-16.0
    # deploy tokens were unprefixed and fall into the permanent
    # bucket-(b) shape). Format mirrors ``glpat-``: 5-char prefix +
    # 20-char ``[A-Za-z0-9_-]`` body. The ``gldt-`` prefix is
    # unambiguous, and the body lies entirely inside the entropy
    # fallback's alphabet — same generic-only attribution gap as the
    # ``glrt-`` case. A leak grants the issuing scope's **Deploy Token
    # capabilities**: read/write Container Registry images, read/write
    # Package Registry artefacts, and (for the ``write_repository``
    # scope) push to protected branches. The Container Registry
    # surface is especially dangerous: an attacker who can push a
    # tampered image to the project's registry persists their
    # compromise across every downstream deployment that pulls the
    # image, bypassing the source-repository security gate entirely.
    # The revocation flow lives at gitlab.com/<project>/-/settings/
    # repository#js-deploy-tokens and is distinct from any other
    # vendor's.
    (
        re.compile(r"(?<![A-Za-z0-9])gldt-[A-Za-z0-9_\-]{20}(?![A-Za-z0-9])"),
        "GitLab Deploy Token gefunden",
    ),
    # GitLab Cluster Agent for Kubernetes Token
    # (``glagent-<50+ chars from [A-Za-z0-9_-]>``). Issued via project
    # / group settings > Operate > Kubernetes clusters > GitLab Agent
    # in GitLab 14.0+ for registering a GitLab Agent for Kubernetes
    # inside a target cluster. Format diverges from the ``glpat-``
    # family: 8-char prefix + 50+ char body (the body is longer because
    # the registered Agent uses the token for GraphQL-level mTLS
    # handshake metadata and the extra entropy is needed for the
    # agent's identity fingerprint). The ``glagent-`` prefix is
    # unambiguous, and the body lies entirely inside the entropy
    # fallback's alphabet — same generic-only attribution gap as the
    # ``glrt-`` / ``gldt-`` cases. A leak grants whoever holds the
    # token the ability to **register a rogue GitLab Agent for
    # Kubernetes** against the issuing scope: the rogue agent
    # subsequently runs ``kubectl`` commands inside the target
    # cluster (via the configured impersonation account) and
    # reads / mutates every Kubernetes resource the agent's RBAC
    # binding permits. Blast radius = the entire connected cluster's
    # resource surface — the highest leak surface in the GitLab
    # GitOps stack, structurally analogous to the Buildkite / GitLab
    # Runner registration tokens but acting at the in-cluster
    # orchestrator boundary rather than the CI runner boundary. The
    # revocation flow lives at gitlab.com/<project>/-/settings/
    # cluster_agents and is distinct from every other vendor's.
    (
        re.compile(r"(?<![A-Za-z0-9])glagent-[A-Za-z0-9_\-]{50,}(?![A-Za-z0-9])"),
        "GitLab Cluster Agent Token gefunden",
    ),
    # GitLab Feed Token (``glft-<20 chars from [A-Za-z0-9_-]>``).
    # Issued automatically for every user via ``Settings > Access
    # Tokens > Feed token`` for personal RSS/Atom-feed authentication
    # against the GitLab REST API. Format mirrors ``glpat-``: 5-char
    # prefix + 20-char ``[A-Za-z0-9_-]`` body. The ``glft-`` prefix
    # is unambiguous, and the body lies entirely inside the entropy
    # fallback's ``[A-Za-z0-9+/=_-]`` alphabet — so the entropy regex
    # matches the full ``glft-<body>`` span as one generic
    # ``Hochentropischer Token-String`` finding, losing the GitLab-
    # Feed-Token-specific issuer attribution. A leak grants the
    # issuing user's read scope to the activity stream — visible
    # issues, merge requests, comments, project metadata; for an
    # admin user the feed exposes the entire instance's project
    # taxonomy. Blast radius lower than the CI/CD-infrastructure-
    # tier siblings (``glrt-``/``gldt-``/``glagent-``) but the leak-
    # surface is broad. The revocation flow lives at
    # gitlab.com/-/user_settings/personal_access_tokens (alongside
    # the canonical PAT revocation flow) and is distinct from any
    # other vendor's. Closes one of the four developer-tooling-tier
    # GitLab prefixes named-but-deferred by Round 10 (PR #1493).
    (
        re.compile(r"(?<![A-Za-z0-9])glft-[A-Za-z0-9_\-]{20}(?![A-Za-z0-9])"),
        "GitLab Feed Token gefunden",
    ),
    # GitLab Incoming Mail Token (``glimt-<25+ chars from
    # [A-Za-z0-9_-]>``). Embedded in the reply-by-email
    # ``Reply-To: noreply+<token>@<instance>.gitlab.com`` header,
    # used by the GitLab incoming-mail subsystem to verify that an
    # inbound reply genuinely belongs to the issuing user. Format
    # diverges slightly from ``glpat-``: 6-char prefix + 25-char
    # body (the longer body matches the upstream
    # ``Devise.friendly_token(25)`` shape used by Rails ActionMailer
    # reply-by-email scoping). A leak lets a network adversary post
    # comments / merge request replies / issue updates **as the
    # issuing user** by sending crafted email to the GitLab inbound-
    # mail relay — full impersonation within the user's commenting
    # scope. The revocation flow lives at
    # gitlab.com/-/user_settings/personal_access_tokens (alongside
    # the Feed Token) and is distinct from any other vendor's, so
    # issuer-specific attribution accelerates IR triage.
    (
        re.compile(r"(?<![A-Za-z0-9])glimt-[A-Za-z0-9_\-]{25,}(?![A-Za-z0-9])"),
        "GitLab Incoming Mail Token gefunden",
    ),
    # GitLab CI Build Token (``glcbt-<partition_prefix>_<body>``).
    # Per-build CI token issued by the GitLab Rails server when a CI
    # job starts; exposed to the job as the ``CI_JOB_TOKEN`` env var
    # (GitLab 16.0+ post-DB-partitioning rollout — pre-16.0 build
    # tokens were unprefixed and fall into the bucket-(b) shape).
    # Format diverges from every other GitLab prefix: 5-char
    # ``glcbt-`` prefix + variable-length partition prefix (1-3
    # alphanumeric chars anchoring the token to its DB partition for
    # fast lookup) + literal ``_`` + 20+ char body from
    # ``[A-Za-z0-9_-]``. The structured ``<partition>_<body>`` shape
    # is unique among GitLab prefixes and is the structural
    # disambiguator from ``glpat-`` / ``glrt-`` / ``gldt-`` (which
    # all use a flat 20-char body). A leak during the job's lifetime
    # (token is invalidated when the job completes, but the window
    # can be hours for long-running jobs) grants the attacker the
    # ability to **call the GitLab REST API as the job**: download
    # package-registry / container-registry artefacts the job had
    # access to, trigger downstream pipelines via the canonical
    # ``CI_JOB_TOKEN`` auth flow, impersonate the job to other
    # pipelines that allow inbound job-token access (the
    # ``allow_job_token_access`` setting on protected branches).
    (
        re.compile(r"(?<![A-Za-z0-9])glcbt-[A-Za-z0-9]+_[A-Za-z0-9_\-]{20,}(?![A-Za-z0-9])"),
        "GitLab CI Build Token gefunden",
    ),
    # GitLab Scoped OAuth Access Token (``glsoat-<20+ chars from
    # [A-Za-z0-9_-]>``). Issued by SCIM-integrated SSO providers
    # (Okta / OneLogin / AzureAD / Google Workspace) when an OAuth
    # application provisions a scoped access token for a GitLab
    # user. Format mirrors ``glpat-`` with a longer prefix: 7-char
    # ``glsoat-`` prefix + 20+ char ``[A-Za-z0-9_-]`` body. The
    # ``glsoat-`` prefix anchors against the OAuth-application-
    # scoped subset of token scopes (as opposed to the broader
    # ``glpat-`` user-PAT scope, which would grant the full set of
    # the user's PAT scopes). A leak grants the OAuth application's
    # scoped capabilities for the issuing user — typically
    # ``read_user`` / ``read_repository`` / ``api`` for SCIM-
    # provisioned OAuth apps in enterprise GitLab Self-Managed
    # installations. The revocation flow lives at
    # gitlab.com/-/profile/applications (distinct from the
    # gitlab.com/-/user_settings/personal_access_tokens PAT flow),
    # so issuer-specific attribution accelerates IR triage to the
    # correct dashboard / API endpoint.
    (
        re.compile(r"(?<![A-Za-z0-9])glsoat-[A-Za-z0-9_\-]{20,}(?![A-Za-z0-9])"),
        "GitLab Scoped OAuth Access Token gefunden",
    ),
    # CircleCI Personal API Token (``CCIPAT_<32+ chars from
    # [A-Za-z0-9_-]>``). Issued via
    # app.circleci.com/settings/user/tokens for full CircleCI
    # REST-API v2 access. The ``CCIPAT_`` prefix was added in
    # 2023 to replace the legacy unprefixed 40-char-alphanumeric
    # CircleCI tokens (legacy tokens fall into the bucket-(b)
    # shape; the modern ``CCIPAT_`` format anchors against the
    # entropy fallback's body span). Format: 7-char prefix + 32+
    # char ``[A-Za-z0-9_-]`` body. The body lies entirely inside
    # the entropy fallback's alphabet (the underscore is in the
    # alphabet), so pre-fix the entropy regex matches the full
    # ``CCIPAT_<body>`` span as one generic ``Hochentropischer
    # Token-String`` finding, losing the CircleCI-specific
    # attribution. A leak grants the issuing user's full CircleCI
    # organisation scope: read every project's pipeline
    # configuration (which embeds inline env-var references to
    # other vendors' tokens — AWS keys, Docker registry creds,
    # third-party API tokens), trigger arbitrary pipelines on
    # attacker-controlled branches, exfiltrate build artifacts,
    # and manage SSH keys for project deployments. Blast radius
    # is structurally identical to the Buildkite User Access
    # Token (``bkua_``, Round 8) — the personal-token tier of the
    # CI execution sub-landscape. The revocation flow lives at
    # app.circleci.com/settings/user/tokens and is distinct from
    # every other vendor's, so issuer-specific attribution
    # accelerates IR triage. Closes the CircleCI prefix named-but-
    # deferred by Round 7/8 (CI execution-tier sibling).
    (
        re.compile(r"(?<![A-Za-z0-9])CCIPAT_[A-Za-z0-9_\-]{32,}(?![A-Za-z0-9])"),
        "CircleCI Personal API Token gefunden",
    ),
    # Mailgun Private API Key (``key-<32 lowercase hex>``). Issued via
    # app.mailgun.com/app/account/security/api_keys for full Mailgun
    # transactional-mail API access. Total length 36 chars (4-char
    # ``key-`` prefix + 32-char lowercase hex body). The ``key-``
    # prefix is unambiguous in the Mailgun-issuer context: while the
    # literal substring ``key-`` is common in placeholder values
    # (``api-key``, ``foo-key-1``), the strict 32-char-lowercase-hex
    # body length guard rejects every operator-supplied placeholder
    # that doesn't accidentally land in the hex alphabet AND clock
    # in at exactly 32 chars. A leak grants the issuing account's
    # full transactional-mail / contacts API scope: the attacker can
    # send mail FROM the project's authenticated sending domain
    # (phishing amplification leveraging the project's existing SPF
    # / DKIM / DMARC authentication), exfiltrate the suppression /
    # bounce / event logs (which may carry PII), modify webhook
    # endpoints to redirect delivery events to attacker-controlled
    # URLs, and create new API keys to maintain persistence. The
    # revocation flow lives at app.mailgun.com/app/account/security/
    # api_keys and is distinct from every other vendor's (in
    # particular Brevo (Sendinblue) ``xkeysib-`` from Round 5 —
    # both are transactional-mail vendors but their revocation
    # flows live on different control planes), so issuer-specific
    # attribution accelerates IR triage. Pre-fix the entropy
    # fallback's ``[A-Za-z0-9+/=_-]{24,}`` regex matches only the
    # 32-hex body span as one generic ``Hochentropischer Token-
    # String`` finding (the trailing alphabet excludes ``key-``
    # because ``key-`` is only 4 chars — too short for the 24-char
    # entropy minimum on its own), losing the Mailgun-specific
    # attribution. Closes the named-but-deferred adjacent-prefix
    # candidate from Round 11 (PR closing GitLab developer-tooling
    # + CircleCI).
    (
        re.compile(r"(?<![A-Za-z0-9])key-[a-f0-9]{32}(?![A-Za-z0-9])"),
        "Mailgun Private API Key gefunden",
    ),
    # Square Access Token (``EAAA<60+ chars from [A-Za-z0-9_-]>``).
    # Issued via developer.squareup.com/apps for full Square REST-API
    # access. The ``EAAA`` prefix is the base64url encoding of the
    # first 3 bytes of the embedded JSON token payload's leading
    # byte sequence (structurally analogous to JWT's ``eyJ`` —
    # ``eyJ`` decodes to ``{"`` and ``EAAA`` decodes to a different
    # 3-byte sequence). Both ``EAAA`` and ``eyJ`` are 4-char base64
    # prefixes but are mutually exclusive at the leading-character
    # level — no token can match both patterns simultaneously. Total
    # length 64+ chars in modern Square tokens (4-char prefix +
    # 60+ char body). The 60-char body lower bound rejects short
    # ``EAAA``-prefixed fragments (operator placeholder values,
    # accidentally-truncated tokens) while accepting every
    # legitimate Square access token. A leak grants the issuing
    # seller's full Square dashboard scope: read every customer's
    # payment data / catalog / inventory state, initiate transactions
    # under the seller's account, refund payments, modify employee
    # permissions, exfiltrate the merchant's tax-filing data. Blast
    # radius is structurally identical to the Stripe Live Secret
    # Key (``sk_live_``, Round 1) — the payment-processor-tier
    # leak surface. The revocation flow lives at
    # developer.squareup.com/apps and is distinct from every other
    # payment-processor's (in particular Stripe's
    # dashboard.stripe.com/apikeys), so issuer-specific attribution
    # accelerates IR triage. Pre-fix the entropy fallback matched
    # the full ``EAAA<body>`` span as one generic ``Hochentropischer
    # Token-String`` finding, losing the Square-specific
    # attribution. Closes the named-but-deferred adjacent-prefix
    # candidate from Round 11.
    (
        re.compile(r"(?<![A-Za-z0-9])EAAA[A-Za-z0-9_\-]{60,}(?![A-Za-z0-9])"),
        "Square Access Token gefunden",
    ),
    # Shopify Admin API Access Token (``shpat_<32 lowercase hex>``).
    # Issued by Shopify when a custom app installs into a store via
    # the Admin API OAuth flow (post-2022 modern custom-app flow,
    # ``shpat_`` prefix). Total length 38 chars (6-char prefix +
    # 32-char hex body). The ``shpat_`` prefix is unambiguous, and
    # the strict 32-hex body length avoids overlap with the entropy
    # fallback's broader alphabet. A leak grants the app's full
    # installed scope on the store: read/write every product,
    # customer, order, inventory item, fulfilment, refund, draft
    # order, gift card, abandoned checkout, customer-segment, and
    # webhook configuration in the store; modify shop settings;
    # initiate refunds and create new staff accounts. Blast radius
    # is the highest leak surface in the e-commerce-platform tier
    # — structurally identical to the GitLab Personal Access Token
    # (``glpat-``, Round 1) for the e-commerce dashboard. The
    # revocation flow lives at admin.shopify.com/apps/<app>/edit
    # and is distinct from every other vendor's. Pre-fix the
    # entropy fallback's ``[A-Za-z0-9+/=_-]{24,}`` regex matches
    # the full ``shpat_<body>`` span (the underscore IS in the
    # entropy alphabet) as one generic ``Hochentropischer Token-
    # String`` finding, losing the Shopify-Admin-API-specific
    # attribution that determines which admin dashboard the operator
    # must visit to revoke the token. Opens the e-commerce-platform
    # sub-landscape in lockstep with the named adjacent-prefix
    # candidates (Mailgun / Square) above.
    (
        re.compile(r"(?<![A-Za-z0-9])shpat_[a-fA-F0-9]{32}(?![A-Za-z0-9])"),
        "Shopify Admin API Access Token gefunden",
    ),
    # Shopify Shared Secret (``shpss_<32 lowercase hex>``). Issued
    # alongside ``shpat_`` for HMAC-SHA256 signature verification
    # of webhook payloads delivered by Shopify to the app's callback
    # URL. Format identical to ``shpat_`` (6-char prefix + 32-char
    # hex body); the ``shpss_`` prefix is unambiguous and mutually
    # exclusive with ``shpat_``/``shppa_``/``shpca_`` at the
    # fifth-character level. A leak lets a network adversary forge
    # webhook payloads that the app's signature verification will
    # accept — every webhook-driven business logic (order fulfilment,
    # refund processing, cart-abandonment automation, customer-data
    # synchronisation, gift-card issuance) can be triggered by an
    # attacker. Blast radius is structurally identical to the
    # Stripe Webhook Signing Secret (``whsec_``, Round 1) for the
    # e-commerce dashboard — webhook-payload forgery, not direct
    # API access. The rotation flow lives via the app's webhook-
    # settings page in the admin dashboard and is distinct from
    # the access-token revocation flow.
    (
        re.compile(r"(?<![A-Za-z0-9])shpss_[a-fA-F0-9]{32}(?![A-Za-z0-9])"),
        "Shopify Shared Secret gefunden",
    ),
    # Shopify Private App API Access Token (``shppa_<32 lowercase
    # hex>``). Legacy private-app token shape (deprecated for new
    # stores 2022-01-01 but still issued for existing installations
    # that haven't migrated to the modern custom-app flow). Format
    # identical to ``shpat_``/``shpss_``/``shpca_`` (6-char prefix
    # + 32-char hex body); the ``shppa_`` prefix is unambiguous.
    # A leak grants the private app's full store scope — same
    # blast radius as ``shpat_`` for the apps that haven't migrated
    # to the modern custom-app flow. The revocation flow lives at
    # admin.shopify.com/admin/apps/private and is distinct from
    # the custom-app revocation flow at
    # admin.shopify.com/admin/settings/apps/development. The
    # per-prefix attribution lets the operator land on the correct
    # admin page in seconds during incident response.
    (
        re.compile(r"(?<![A-Za-z0-9])shppa_[a-fA-F0-9]{32}(?![A-Za-z0-9])"),
        "Shopify Private App Access Token gefunden",
    ),
    # Shopify Custom App Access Token (``shpca_<32 lowercase hex>``).
    # Modern custom-app token format (post-2022 replacement of the
    # deprecated ``shppa_`` private-app shape). Format identical to
    # the rest of the Shopify family (6-char prefix + 32-char hex
    # body); the ``shpca_`` prefix is unambiguous. A leak grants
    # the modern custom app's full store scope — same blast radius
    # as ``shpat_``. The revocation flow lives at
    # admin.shopify.com/admin/settings/apps/development and is
    # distinct from every other Shopify revocation flow (per-prefix
    # attribution is critical for IR triage so the operator lands
    # on the correct admin page instantly).
    (
        re.compile(r"(?<![A-Za-z0-9])shpca_[a-fA-F0-9]{32}(?![A-Za-z0-9])"),
        "Shopify Custom App Access Token gefunden",
    ),
    # WooCommerce REST API Consumer Key (``ck_<32+ alphanumeric>``).
    # Issued via wp-admin > WooCommerce > Settings > Advanced > REST API
    # > Add Key. The key is generated by ``wp_generate_password(32, false)``
    # (modern WooCommerce) which returns 32 chars from ``[a-zA-Z0-9]``;
    # legacy installations using ``wc_rand_hash()`` produced 64-char
    # lowercase-hex bodies, and intermediate versions used 40-char
    # alphanumeric bodies. The ``{32,}`` lower bound covers the modern
    # canonical form AND every documented legacy variant in one regex;
    # the greedy quantifier + lookahead anchor end at the natural
    # alphanumeric boundary so the longest legitimate body wins. The
    # ``ck_`` prefix is unambiguous in the WooCommerce-issuer context
    # — while the literal substring is common in placeholder values
    # (``track_id``, ``check_button``), the strict 32-char-alphanumeric
    # body length guard rejects every operator-supplied placeholder
    # that doesn't accidentally land in the alphanumeric alphabet AND
    # clock in at exactly 32+ chars after the ``ck_`` prefix. A leak
    # paired with the matching ``cs_`` consumer secret (next entry)
    # grants the issuing API user's full WooCommerce REST scope:
    # read/write every product, order, customer, coupon, refund,
    # shipping zone, tax rate, and webhook configuration on the store;
    # the role attached to the API user (read / write / read_write)
    # determines the effective blast radius, but write-tier keys are
    # the default WooCommerce documentation recommends. Pre-fix every
    # WooCommerce consumer key with a uniform character class body
    # (all-lowercase, all-uppercase, all-digits) was SILENTLY
    # UNDETECTED — the entropy fallback's ``_looks_like_secret``
    # heuristic requires multiple character categories, and a body
    # like ``ck_aaaaaa...`` (all lowercase) failed that check, so the
    # key slipped past the scanner entirely; mixed-case bodies were
    # caught only by the generic "Verdächtige Zuweisung" assignment
    # heuristic (which depends on the variable name containing a
    # sensitive keyword like ``key``/``secret`` and produces no
    # issuer-specific attribution). Post-fix every WooCommerce
    # consumer key receives the specific German attribution
    # (``WooCommerce Consumer Key gefunden``) that incident-response
    # playbooks key off (revocation flow at wp-admin > WooCommerce >
    # Settings > Advanced > REST API). The revocation flow is
    # distinct from every other e-commerce-platform vendor's (in
    # particular Shopify's admin.shopify.com/* — both WooCommerce
    # and Shopify carry the same e-commerce-tier blast radius but
    # their revocation flows live on entirely different control
    # planes), so issuer-specific attribution accelerates IR triage.
    # Closes the named-but-deferred adjacent-prefix candidate from
    # Round 12 (Shopify family + Mailgun + Square).
    (
        re.compile(r"(?<![A-Za-z0-9])ck_[a-zA-Z0-9]{32,}(?![A-Za-z0-9])"),
        "WooCommerce Consumer Key gefunden",
    ),
    # WooCommerce REST API Consumer Secret (``cs_<32+ alphanumeric>``).
    # Sibling to ``ck_`` — issued together by the same admin flow,
    # used together as Basic Auth ``ck_<key>:cs_<secret>`` for every
    # WooCommerce REST API request. Format identical to ``ck_``
    # (3-char prefix + 32+ char alphanumeric body). A leak of the
    # consumer secret alone (without the matching ``ck_``) is less
    # immediately exploitable (the key is the identifier, the secret
    # is the signing material), but the standard WooCommerce
    # documentation recommends storing both in the same config block
    # — a leak of either typically implies a leak of both, and a
    # leaked ``cs_`` strongly signals that a ``ck_`` exists somewhere
    # in the same repo (just as a leaked ``sk_test_`` Stripe key
    # signals an ``sk_live_`` exists elsewhere). Distinct reason from
    # the consumer key so the report calls out *which* half of the
    # credential pair leaked and the operator can rotate the correct
    # credential with confidence. Pre-fix detection gap identical to
    # ``ck_``: silently undetected for uniform-character-class bodies,
    # generic "Verdächtige Zuweisung" only for mixed-case bodies.
    (
        re.compile(r"(?<![A-Za-z0-9])cs_[a-zA-Z0-9]{32,}(?![A-Za-z0-9])"),
        "WooCommerce Consumer Secret gefunden",
    ),
    # Mailchimp API Key (``<32 hex>-us<datacenter>``). Issued via
    # mailchimp.com/account/api-keys for full Mailchimp REST API
    # access. Format: 32 lowercase hex chars + literal ``-us`` +
    # 1-3 digit datacenter number (``-us1``, ``-us14``, ``-us20``
    # are all valid; the datacenter shard is assigned at account
    # creation based on the account's geographic region and grows
    # over time as Mailchimp adds capacity). The ``-us<N>`` suffix
    # is the structural disambiguator: the entropy fallback's
    # ``[A-Za-z0-9+/=_-]{24,}`` regex matches the entire string
    # (every char in the alphabet, including the dash), but the
    # ``-us<digit>`` suffix is unique to Mailchimp's datacenter
    # routing convention and unambiguously identifies the issuer.
    # No other major API vendor uses the ``-us<digit>`` suffix
    # pattern in their token format. A leak grants the issuing
    # account's full Mailchimp scope: read/write every audience
    # / list / contact / campaign / automation / signup form /
    # template / report; export the full subscriber CSV (PII
    # exfiltration); send mass mailings AS the account (phishing
    # amplification leveraging the account's authenticated sender
    # domain and existing SPF/DKIM/DMARC reputation); modify
    # webhook URLs to redirect subscription/unsubscription events
    # to attacker-controlled URLs; create new transactional API
    # keys via Mandrill (Mailchimp's transactional sub-product)
    # for persistence. Blast radius is structurally identical to
    # Mailgun (``key-<32 hex>``, Round 12) for the
    # marketing-email-platform tier — both are email-vendor leak
    # surfaces but their control planes are distinct
    # (mailchimp.com vs. app.mailgun.com), so per-issuer attribution
    # is critical for IR triage. The revocation flow lives at
    # mailchimp.com/account/api/ (and the datacenter-prefixed
    # admin.mailchimp.com URLs for direct-routed UI access). Closes
    # the named-but-deferred adjacent-prefix candidate from Round 12.
    (
        re.compile(r"(?<![A-Za-z0-9])[a-f0-9]{32}-us[0-9]{1,3}(?![A-Za-z0-9])"),
        "Mailchimp API Key gefunden",
    ),
    # Figma Personal Access Token (``figd_<43 chars from [A-Za-z0-9_-]>``).
    # Issued via figma.com/settings/personal-access-tokens for full Figma
    # REST API access scoped to the issuing user's accessible teams /
    # projects / files. The Figma issuer header (``X-Figma-Token``) is
    # ALREADY enumerated in ``src/utils/http.py:_SENSITIVE_HEADERS`` (so
    # the cross-origin redirect strip path covers it), but the TOKEN
    # VALUE shape was not in this scanner table — a header/value drift
    # mirroring the pattern this round closes (header name reaches the
    # operator log redacted, value-shape leaks verbatim).
    #
    # Total length 48 chars (5-char prefix + 43-char body). The
    # ``figd_`` prefix is unambiguous (no other major issuer uses it),
    # and the body alphabet ``[A-Za-z0-9_-]`` (base64url + underscore +
    # dash) lies entirely inside the entropy fallback's
    # ``[A-Za-z0-9+/=_-]`` alphabet — so the entropy regex matches the
    # full ``figd_<body>`` span as one generic ``Hochentropischer
    # Token-String`` finding (the dash and underscore ARE in the
    # alphabet), losing the Figma-specific issuer attribution that
    # incident-response triage keys off. The strict 43-char body length
    # matches the documented canonical PAT shape (per the trufflehog /
    # gitleaks / detect-secrets default rules) and rejects accidental
    # ``figd_``-prefixed fragments.
    #
    # Threat model: a leaked Figma PAT grants the issuing user's full
    # design-collaboration scope across every accessible team / project
    # / file — read every design (including unpublished prototypes and
    # internal pitch decks that routinely contain customer-facing
    # branding before public reveal), copy proprietary design tokens
    # (which often encode business-strategic colour / typography
    # decisions), POST comments AS the user (impersonation risk for
    # social-engineering reconnaissance), and exfiltrate the team's
    # entire design-system version history. Blast radius is structurally
    # similar to Notion (``secret_<43>`` / ``ntn_<43+>``, Round 8) for
    # the workspace-collaboration tier — both are content-management
    # SaaS tokens with full-workspace read/write scope.
    #
    # Real-world emission patterns: ``.env`` files
    # (``FIGMA_TOKEN=figd_...``), CI/CD pipeline debug logs, README
    # curl examples, notebook outputs hardcoding the PAT, and the
    # canonical sibling-drift leak surface — JSON values in error
    # responses echoing the token back via a hostile / misconfigured
    # upstream. The revocation flow lives at figma.com/settings/
    # personal-access-tokens > Revoke and is distinct from every other
    # vendor's, so issuer-specific attribution accelerates IR triage.
    (
        re.compile(r"(?<![A-Za-z0-9])figd_[A-Za-z0-9_\-]{43}(?![A-Za-z0-9])"),
        "Figma Personal Access Token gefunden",
    ),
    # Tailscale Key family (``tskey-(?:auth|api|client|webhook)-<keyID>-<keySecret>``).
    # Issued via login.tailscale.com/admin/settings/keys for tailnet
    # operations across four documented tiers:
    #   * ``tskey-auth-`` — Auth Key for registering new nodes into the
    #     tailnet's overlay network (pre-auth or reusable; with or
    #     without ephemeral lifetime).
    #   * ``tskey-api-`` — API Access Token for the management REST API
    #     (manage devices, ACLs, DNS, user provisioning).
    #   * ``tskey-client-`` — OAuth client secret for programmatic
    #     access via the OAuth2 client-credentials flow.
    #   * ``tskey-webhook-`` — Webhook signing secret for verifying
    #     payload authenticity from tailnet event subscriptions.
    #
    # Format: ``tskey-<tier>-<keyID>-<keySecret>`` where keyID is 8+
    # alphanumeric chars (real-world 9-14) and keySecret is 20+
    # alphanumeric chars (real-world 30-50+). The multiple dash-
    # separated segments bypass the entropy fallback's contiguous-match
    # span (dashes ARE in the alphabet, but Tailscale tokens are
    # typically broken at the dashes by the heuristic split inside
    # ``_scan_content``) — individual ``<keyID>``/``<keySecret>``
    # fragments frequently fall below the 24-char entropy floor when
    # examined in isolation, and the issuer attribution that anchors
    # the revocation flow is lost.
    #
    # Threat model per tier (each maps to a DISTINCT revocation
    # sub-page of login.tailscale.com/admin/settings):
    #   * ``auth`` — leak lets an attacker attach a rogue NODE to the
    #     victim's private overlay network. The rogue node sees every
    #     subnet-routed service the tailnet exposes (internal admin
    #     panels, dev databases, monitoring dashboards) and can pivot
    #     laterally as a trusted peer. Auth keys can have re-use limits
    #     (single-use / N-use / unlimited) — unlimited keys are the
    #     highest-blast-radius variant.
    #   * ``api`` — leak grants admin-API access: modify ACLs (open
    #     attacker access to every tailnet device), rotate DNS
    #     configuration (DNS-rebinding amplifier), add/remove users,
    #     mint fresh auth keys until revocation.
    #   * ``client`` — OAuth client secret. Leak mints OAuth access
    #     tokens for the configured scope — same blast radius as the
    #     ``api`` tier for any operation the OAuth scope grants.
    #   * ``webhook`` — webhook signing secret. Leak lets an attacker
    #     FORGE tailnet event payloads (device-joined, device-removed,
    #     ACL-updated) that downstream consumers will accept as
    #     authentic, enabling state-machine confusion attacks against
    #     IAM-integration glue code.
    #
    # The tier keyword is the structural disambiguator AND the
    # incident-response attribution — each tier has a distinct
    # revocation sub-page (the admin settings UI has separate "Keys",
    # "OAuth clients", and "Webhooks" tabs). Distinct reason per tier
    # routes IR triage to the correct sub-page in seconds.
    #
    # Real-world emission patterns: GitHub Actions secrets
    # (``TS_AUTH_KEY``, ``TAILSCALE_API_TOKEN``), Kubernetes
    # ConfigMaps / Secrets for the ``tailscale`` sidecar / operator,
    # Docker Compose env, ``terraform.tfvars`` for the Tailscale
    # Terraform provider, Ansible inventory.
    (
        re.compile(
            r"(?<![A-Za-z0-9])tskey-(?:auth|api|client|webhook)-"
            r"[A-Za-z0-9]{8,}-[A-Za-z0-9]{20,}(?![A-Za-z0-9])"
        ),
        "Tailscale Key gefunden",
    ),
    # AWS STS Service Bearer Token (``ABIA<16 chars from [A-Z0-9]>``).
    # Issued by ``sts:GetServiceBearerToken`` for service-to-service
    # authentication on behalf of an AWS user. Same 4+16=20 char format
    # as ``AKIA``/``ASIA``/``ACCA`` (already enumerated in ``_AWS_ID_RE``)
    # but the ``ABIA`` prefix was the **fourth credential prefix in the
    # AWS unique-identifier family** that ``_AWS_ID_RE`` explicitly
    # enumerated as named-but-uncovered (Round 13 closing checklist
    # listed only ``AKIA``/``ASIA``/``ACCA``). Per the AWS IAM "Unique
    # identifiers" reference, ``ABIA`` is the canonical prefix for STS
    # service bearer tokens; per gitleaks / trufflehog / detect-secrets
    # / aws-secret-detector default rules, it is detected alongside
    # the other credential prefixes.
    #
    # Pre-fix the 20-char ``ABIA<16>`` token format falls **below**
    # ``_HIGH_ENTROPY_RE``'s 24-char minimum. A bare leaked token in
    # plaintext context (log line, JSON fixture without sensitive key,
    # documentation snippet, hostile-PR fragment) was **silently
    # undetected** by every detection branch — the CI gate passed,
    # the credential sat in the public repository indefinitely, and
    # the issuing user's full AWS scope (the bearer token's authorized
    # API access window) was exposed to every consumer. In assignment
    # context the only finding was the generic ``Verdächtige
    # Zuweisung``, losing the AWS-specific issuer attribution that
    # incident-response keys off (revocation flow at IAM > STS service
    # bearer token management — distinct from the IAM > Access keys
    # flow used to revoke ``AKIA``).
    #
    # The strict ``[A-Z0-9]{16}`` body alphabet (uppercase + digits
    # only, mirroring the canonical ``_AWS_ID_RE`` body) anchors against
    # false positives on lowercase or mixed-case strings happening to
    # start with ``ABIA`` (English words / placeholder identifiers); the
    # ``(?<![A-Za-z0-9])`` lookbehind anchor prevents matching mid-word
    # occurrences. Distinct attribution from the generic AWS Access Key
    # ID reason so the report identifies WHICH AWS credential type
    # leaked and the operator can rotate via the correct STS revocation
    # flow rather than the IAM access-key rotation flow.
    (
        re.compile(r"(?<![A-Za-z0-9])ABIA[A-Z0-9]{16}(?![A-Za-z0-9])"),
        "AWS STS Service Bearer Token gefunden",
    ),
    # Dropbox Short-Lived Access Token (``sl.<base64url body>``). The
    # canonical Dropbox OAuth2 short-lived access-token format introduced
    # in the 2021 OAuth2 rotation rollout — replaces the legacy 64-char
    # alphanumeric long-lived tokens that fall into the bucket-(b) shape
    # (no canonical prefix). Issued by the ``oauth2/token`` endpoint with
    # ``grant_type=refresh_token`` and consumed by every Dropbox HTTP API
    # endpoint (``/2/files/*``, ``/2/sharing/*``, ``/2/team/*``,
    # ``/2/users/*``) for full file-storage / sharing / team-admin access.
    # The ``sl.`` prefix is unambiguous (no other major issuer uses this
    # prefix) and the literal ``.`` separator sits OUTSIDE the entropy
    # fallback's ``[A-Za-z0-9+/=_-]`` alphabet — so pre-fix the
    # ``_HIGH_ENTROPY_RE`` match started AT the base64url body after
    # the dot, stripping the ``sl.`` prefix from the matched span and
    # losing the Dropbox-specific issuer attribution. Body alphabet
    # ``[A-Za-z0-9_-]`` (base64url) lies entirely inside the entropy
    # alphabet so the body itself matched as one generic
    # ``Hochentropischer Token-String`` finding. Body lower bound 40
    # chars rejects accidental ``sl.``-prefixed fragments (operator
    # placeholder values, ISO 639 Slovenian language code URL path
    # segments like ``/sl/about``) while accepting every legitimate
    # token — real-world Dropbox short-lived tokens are 130-160 chars
    # in the base64url body span.
    #
    # Threat model: a leaked Dropbox short-lived access token grants
    # the issuing app's full file-storage / sharing / team-admin scope
    # for the token's TTL (typically 4 hours, but the same app's
    # refresh token can re-mint short-lived tokens indefinitely — a
    # leaked short-lived token strongly signals the refresh token is
    # also exposed somewhere in the same artefact). Full file read =
    # data exfiltration (customer documents, source-code backups,
    # plain-text credential notes, scanned ID cards stored as personal
    # backups). File write = ransomware-style overwrite, malicious-
    # document injection. Sharing scope = create unauthorised shared
    # links exfiltrating the team's stored content via the public
    # internet. Team-admin scope = exfiltrate the team's member
    # directory, revoke other admins' access, modify retention
    # policies for persistence. Real-world emission patterns:
    # ``.env`` files (``DROPBOX_TOKEN=sl....``), CI/CD pipeline debug
    # logs, GitHub Actions secrets dumped to logs by a misconfigured
    # action, notebook outputs hardcoding the token, Dropbox SDK error
    # responses echoing the token back in diagnostic messages.
    # Revocation flow lives at dropbox.com/developers/apps (App console
    # > app settings > "Revoke tokens") and is distinct from every
    # other vendor's. Closes the file-storage SaaS vendor family that
    # was named-but-deferred as a "bucket-(b)" candidate by prior
    # rounds — the modern short-lived format DOES carry a distinctive
    # prefix (``sl.``) that anchors per-issuer attribution.
    (
        re.compile(r"(?<![A-Za-z0-9])sl\.[A-Za-z0-9_\-]{40,}(?![A-Za-z0-9])"),
        "Dropbox Short-Lived Access Token gefunden",
    ),
    # Pulumi Access Token (``pul-<40 lowercase hex>``). The canonical
    # Pulumi Personal Access Token format. Issued via
    # app.pulumi.com/account/tokens for full Pulumi Cloud API access:
    # read/write every accessible org / project / stack's state, trigger
    # arbitrary ``pulumi up`` operations modifying production
    # infrastructure, exfiltrate the org's complete deployment-history
    # audit log. Format matches the canonical trufflehog / gitleaks /
    # detect-secrets default rule (``pul-[a-f0-9]{40}``) — 4-char prefix
    # plus 40-char lowercase-hex body for a total of 44 chars. The
    # ``pul-`` prefix is unambiguous (no other major issuer uses this
    # prefix), and the strict 40-char lowercase-hex body (a SHA-1-shape
    # hash digest) anchors against false positives on placeholder values
    # like ``pull-request-1234`` or ``pul-foo`` (the ``-`` in placeholder
    # text is in the entropy alphabet but the body alphabet rejects
    # non-hex chars and the strict 40-char length rejects short fragments).
    # Pre-fix the entropy fallback matched the full ``pul-<body>`` span
    # as one generic ``Hochentropischer Token-String`` finding (both
    # ``-`` and the lowercase-hex body lie inside the entropy alphabet),
    # losing the Pulumi-specific issuer attribution that anchors the
    # app.pulumi.com/account/tokens revocation flow.
    #
    # Threat model (HIGHEST blast radius — IaC control plane): a leaked
    # Pulumi access token grants the issuing user's full Pulumi Cloud
    # API access for every accessible org / project / stack. Read
    # access = exfiltrate EVERY secret persisted in stack state (cloud
    # provider credentials are the canonical IaC-stored credential
    # class — AWS / Azure / GCP keys, database passwords, third-party
    # API keys, TLS private keys for issued certificates), reconstruct
    # the org's complete infrastructure topology for reconnaissance.
    # Write access = trigger arbitrary ``pulumi up`` operations
    # modifying production infrastructure (provision attacker-
    # controlled VMs in the victim's cloud account, modify IAM
    # bindings, add backdoored DNS records redirecting customer
    # traffic). The IaC control-plane breach is the canonical "pivot
    # to every downstream environment via a single credential"
    # amplifier — structurally analogous to a leaked Terraform Cloud
    # workspace token. Real-world emission patterns: ``.env`` files
    # (``PULUMI_ACCESS_TOKEN=pul-...``), GitHub Actions secrets,
    # CI/CD pipeline debug logs, notebook outputs, Pulumi SDK error
    # responses echoing the token back. The revocation flow lives at
    # app.pulumi.com/account/tokens > "Revoke" and is distinct from
    # every other vendor's. Closes the IaC SaaS vendor family that
    # was implicit in the previous rounds' coverage of cloud provider
    # secrets (AWS / Azure / GCP credentials are the canonical
    # IaC-stored secret family) but missing for the IaC control-plane
    # platform itself.
    (
        re.compile(r"(?<![A-Za-z0-9])pul-[a-f0-9]{40}(?![A-Za-z0-9])"),
        "Pulumi Access Token gefunden",
    ),
    # Slack App-Level Token (``xapp-<version>-<app_id>-<sequence>-<hex>``).
    # The canonical Slack App-Level Token format used for Socket Mode and
    # app-level Events API access. Issued via api.slack.com/apps/<app_id>/
    # general ("App-Level Tokens" section) with scopes from the
    # ``connections:write`` / ``authorizations:read`` family. Strict
    # sibling of the existing ``xoxb-`` / ``xoxp-`` / ``xoxa-`` / ``xoxc-``
    # / ``xoxd-`` / ``xoxe-`` / ``xoxr-`` Slack family entries — they each
    # have dedicated ``_KNOWN_TOKENS`` rows but the App-Level variant was
    # SILENTLY UNDETECTED entirely (NO finding at all). The multi-dash
    # multi-segment format splits the entropy match at every per-segment
    # boundary into fragments below the 24-char ``_HIGH_ENTROPY_RE``
    # floor (1-digit version, 11-char app id, 13-digit sequence are all
    # too short to trip the entropy detector independently), so the
    # FULL ``xapp-1-A...-...-...`` span escaped detection on every
    # branch including the generic entropy fallback. Real-world emission
    # patterns: ``.env`` files (``SLACK_APP_TOKEN=xapp-...``), GitHub
    # Actions secrets dumped to logs by a misconfigured action, notebook
    # outputs hardcoding the token in a Slack SDK ``socket_mode_client``
    # constructor, Slack SDK error responses echoing the token back.
    # Revocation flow lives at api.slack.com/apps/<app_id>/general
    # ("App-Level Tokens" section > "Regenerate") and is distinct from
    # every other Slack token family's revocation flow (xoxb/xoxp =
    # ``oauth.v2.revoke`` API; xoxc/xoxd = slack.com/account/sessions;
    # xapp = api.slack.com/apps/<app_id>/general).
    #
    # Threat model (HIGH blast radius — workspace event firehose plus
    # cross-tenant app management): a leaked ``xapp-`` grants the
    # holder the app's Socket Mode connection (full firehose of every
    # app-subscribed workspace event — DM contents, channel messages
    # the app can see, interactive component payloads, slash command
    # invocations, modal submissions). Combined with the
    # ``authorizations:read`` scope it enumerates every workspace
    # install of the app — one leaked App-Level Token compromises
    # every workspace the app is installed in (cross-tenant pivot).
    #
    # Structural anchors:
    #   * ``xapp-`` literal prefix (unambiguous; no other major issuer).
    #   * ``[0-9]+`` version segment (typically ``1``).
    #   * ``[A-Z][A-Z0-9]{8,}`` app_id segment — Slack App IDs always
    #     start with the literal ``A`` followed by 10+ uppercase alnum
    #     chars (Slack's documented App ID format).
    #   * ``[0-9]+`` sequence segment (typically 13 digits).
    #   * ``[a-zA-Z0-9]{32,}`` body floor — real Slack App-Level Token
    #     bodies are 64+ chars; the 32-char floor rejects accidental
    #     fragments while accepting future canonical-length variations.
    #   * ``(?<![A-Za-z0-9])`` / ``(?![A-Za-z0-9])`` boundary anchors
    #     reject mid-word collisions (``Xxapp-...`` / ``...0`` tails).
    (
        re.compile(
            r"(?<![A-Za-z0-9])xapp-[0-9]+-[A-Z][A-Z0-9]{8,}-[0-9]+-[a-zA-Z0-9]{32,}(?![A-Za-z0-9])"
        ),
        "Slack App-Level Token gefunden",
    ),
    # Databricks Personal Access Token (``dapi<32 hex>(?:-<digit>)?``).
    # The canonical Databricks PAT format. Issued via the Databricks
    # workspace UI (User Settings → Developer → Access tokens) for full
    # workspace-scoped API access (Databricks REST API ``/api/2.0/...``).
    # The body (32 lowercase hex chars after the ``dapi`` prefix) lies
    # ENTIRELY inside the entropy fallback's ``[A-Za-z0-9+/=_-]``
    # alphabet — pre-fix the entropy regex matched the full
    # ``dapi<body>`` span as one generic ``Hochentropischer
    # Token-String`` finding, losing the Databricks-specific issuer
    # attribution that anchors the per-workspace revocation flow.
    #
    # Threat model (HIGH blast radius — full workspace data plane plus
    # job-execution plane): a leaked ``dapi`` grants the issuing user's
    # full Databricks workspace-scoped API access. Read access =
    # exfiltrate EVERY table the user can SELECT (Unity Catalog tables,
    # S3/ADLS/GCS-backed Delta tables, federated tables — the canonical
    # data-warehouse credential class), export entire datasets via
    # ``/api/2.0/jobs/runs/export``, exfiltrate notebook source code
    # (which routinely embeds further credentials — cloud provider keys,
    # database connection strings, third-party API keys). Write access
    # = submit arbitrary Spark jobs / SQL queries on the user's
    # attached clusters (compute-resource theft on GPU clusters at
    # USD 100s-1000s/hour), modify Unity Catalog permissions (with
    # appropriate privileges), upload backdoored notebooks to user
    # folders for persistence. The cluster-execution capability is the
    # canonical "arbitrary code execution within the cloud account"
    # amplifier — Databricks clusters run on the customer's AWS/Azure/
    # GCP account, giving the cluster the IAM role attached to the
    # cluster (often a broad ``DatabricksDataAccess`` role with S3 /
    # Glue / Athena read).
    #
    # Real-world emission patterns: ``.env`` files (``DATABRICKS_TOKEN=
    # dapi...``), CI/CD pipeline debug logs (``terraform-provider-
    # databricks`` echoing the token in plan output), notebook output
    # cells displaying ``os.environ`` for debugging, ``databricks-cli``
    # ``--profile`` config files committed by mistake. Revocation flow
    # lives at Databricks workspace UI > User Settings > Developer >
    # Access tokens > "Revoke" — distinct per workspace, distinct from
    # every other Databricks credential class (service principals, OAuth
    # apps, basic auth).
    #
    # Structural anchors:
    #   * ``dapi`` literal prefix (unambiguous; no other major issuer).
    #   * ``[a-f0-9]{32}`` strict lowercase-hex 32-char body matches
    #     Databricks' documented canonical format and rejects placeholder
    #     values (``dapibus malesuada`` from Lorem Ipsum, ``dapi-foo``).
    #   * ``(?:-[0-9]+)?`` optional version suffix supports the modern
    #     ``dapi<hex>-2`` / ``dapi<hex>-3`` rotation format.
    #   * ``(?<![A-Za-z0-9])`` / ``(?![A-Za-z0-9])`` boundary anchors
    #     reject mid-word collisions (``Xdapi...`` / ``...G`` tails).
    (
        re.compile(r"(?<![A-Za-z0-9])dapi[a-f0-9]{32}(?:-[0-9]+)?(?![A-Za-z0-9])"),
        "Databricks Personal Access Token gefunden",
    ),
    # HubSpot Private App Token (``pat-(?:na1|na2|na3|eu1)-<UUID>``).
    # The canonical HubSpot Private App access token format. Issued via
    # the HubSpot portal UI at Settings → Account Setup → Integrations →
    # Private Apps → <App> → Auth tab. Used for the HubSpot CRM /
    # Marketing / Automation REST APIs (``/crm/v3/...``, ``/marketing/v3/
    # ...``, ``/contacts/v1/...``). The body is a canonical RFC-4122
    # UUID (8-4-4-4-12 lowercase hex with internal ``-`` separators),
    # which DOES match the entropy fallback's ``[A-Za-z0-9+/=_-]``
    # alphabet (``-`` IS in the alphabet) — pre-fix the entropy regex
    # matched the full ``pat-<region>-<UUID>`` span as one generic
    # ``Hochentropischer Token-String`` finding, losing the HubSpot-
    # specific issuer attribution that anchors the per-portal revocation
    # flow.
    #
    # Threat model (HIGH blast radius — full CRM data plane with PII):
    # a leaked ``pat-`` grants the issuing private app's configured
    # OAuth-equivalent scopes against the HubSpot portal. The canonical
    # scope set (``crm.objects.contacts.read``/``write``,
    # ``crm.objects.companies.``, ``crm.objects.deals.``,
    # ``marketing.``, ``automation.``, ``forms.``, ``files.``)
    # provides FULL access to: the portal's complete contact database
    # (names + emails + phone + addresses + custom properties —
    # GDPR-protected PII at scale), every company and deal record
    # (B2B revenue data + pipeline forecasts), every marketing email
    # campaign (recipient lists + open / click tracking — competitive
    # intelligence goldmine), every automation workflow (modify or
    # disable triggers — sabotage primitive), every form submission
    # (incoming lead capture — exfiltrate or redirect to attacker
    # endpoint). Real-world emission patterns: ``.env`` files
    # (``HUBSPOT_PRIVATE_APP_TOKEN=pat-na1-...``), CI/CD pipeline debug
    # logs, GitHub Actions secrets dumped to logs by a misconfigured
    # action, notebook outputs hardcoding ``HubSpot(access_token=
    # "pat-na1-...")``, curl examples in README files. Revocation flow
    # lives at the HubSpot portal UI > Settings > Account Setup >
    # Integrations > Private Apps > <App> > Auth tab > "Rotate"
    # (immediate) or "Delete app" (permanent) — distinct per portal,
    # distinct from every other CRM-vendor rotation flow (Salesforce,
    # Microsoft Dynamics 365, Zoho CRM).
    #
    # Structural anchors:
    #   * ``pat-`` literal prefix (HubSpot's canonical Private App
    #     prefix; no other major issuer uses this prefix with the
    #     region + UUID body shape).
    #   * ``(?:na1|na2|na3|eu1)`` strict region alternation matches
    #     HubSpot's documented data-residency regions (US East = na1,
    #     US Central = na2, US West = na3, Germany = eu1). Future
    #     regions (e.g. ap1 for Asia-Pacific, au1 for Australia) would
    #     require an additive update — the strict alternation prevents
    #     false positives on placeholder values like ``pat-foo-...``
    #     while accepting every legitimate token currently issued.
    #   * ``[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}``
    #     strict UUID body (RFC 4122 canonical 8-4-4-4-12 hex form,
    #     case-insensitive per RFC 4122 §3 ABNF — HubSpot issues
    #     lowercase but accepts either case on input).
    #   * ``(?<![A-Za-z0-9])`` / ``(?![A-Za-z0-9])`` boundary anchors
    #     reject mid-word collisions (``Xpat-na1-...`` / ``...0``
    #     tails).
    (
        re.compile(
            r"(?<![A-Za-z0-9])pat-(?:na1|na2|na3|eu1)-"
            r"[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-"
            r"[a-fA-F0-9]{4}-[a-fA-F0-9]{12}(?![A-Za-z0-9])"
        ),
        "HubSpot Private App Token gefunden",
    ),
    # PlanetScale Database Token (``pscale_(?:oauth|tkn|pw)_<43 chars>``).
    # The canonical PlanetScale credential format spanning three tiers
    # with DISTINCT revocation flows:
    #   * ``pscale_oauth_<body>`` — OAuth Client Secret. Issued via
    #     app.planetscale.com/<org>/settings/oauth-apps. Mint user-
    #     delegated OAuth access tokens against the PlanetScale API.
    #     Multi-user pivot (the OAuth flow can grant tokens for ANY
    #     PlanetScale user who has authorized the app — cross-account
    #     amplifier).
    #   * ``pscale_tkn_<body>`` — Service Token / Personal Access Token.
    #     Issued via app.planetscale.com/<org>/settings/service-tokens.
    #     Full PlanetScale API access scoped per the token's configured
    #     permissions (typically ``connect_production_branch``,
    #     ``manage_branches``, ``manage_deploy_requests``,
    #     ``read_organization``). Modify production schemas, exfiltrate
    #     every branch's DB password, trigger arbitrary deploy requests,
    #     delete branches.
    #   * ``pscale_pw_<body>`` — Database branch password. **HIGHEST
    #     data-plane blast radius:** direct MySQL-wire-protocol access
    #     to the database branch (read every table, write every table
    #     — full customer data exfiltration / ransomware-style overwrite
    #     primitive). Issued via app.planetscale.com/<org>/<db>/<branch>/
    #     passwords.
    # The body (43-char ``[A-Za-z0-9_-]`` base64url-ish alphabet) lies
    # ENTIRELY inside the entropy fallback's ``[A-Za-z0-9+/=_-]``
    # alphabet — pre-fix the entropy regex matched the full
    # ``pscale_<tier>_<body>`` span as one generic ``Hochentropischer
    # Token-String`` finding, losing the PlanetScale-specific issuer
    # attribution PLUS the credential-tier disambiguation that anchors
    # the operator's revocation playbook (three distinct revocation
    # panels — OAuth Apps, Service Tokens, Branch Passwords — require
    # tier identification from the prefix).
    #
    # Real-world emission patterns: ``.env`` files (``PLANETSCALE_TOKEN=
    # pscale_tkn_...``, ``DATABASE_URL=mysql://<user>:pscale_pw_...@<host>/
    # <db>``), CI/CD pipeline debug logs (Terraform
    # ``planetscale_database`` resource echoing the token in plan
    # output), notebook outputs hardcoding the PlanetScale Python client
    # constructor, ``pscale`` CLI ``--service-token`` flag in CI YAML
    # files committed to source.
    #
    # Structural anchors:
    #   * ``pscale_`` literal prefix (unambiguous; no other major issuer
    #     uses this prefix).
    #   * ``(?:oauth|tkn|pw)`` strict tier alternation matches the
    #     three documented PlanetScale credential tiers. The strict
    #     alternation prevents false positives on placeholder values
    #     like ``pscale_foo_...`` while preserving per-tier IR triage.
    #   * ``[A-Za-z0-9_-]{43}`` strict 43-char base64url body matches
    #     PlanetScale's documented canonical format. The strict length
    #     rejects accidental fragments while the boundary anchors
    #     prevent extension into longer adjacent identifiers.
    #   * ``(?<![A-Za-z0-9])`` / ``(?![A-Za-z0-9])`` boundary anchors
    #     reject mid-word collisions (``Xpscale_tkn_...`` / ``...G``
    #     tails).
    (
        re.compile(
            r"(?<![A-Za-z0-9])pscale_(?:oauth|tkn|pw)_[A-Za-z0-9_\-]{43}(?![A-Za-z0-9])"
        ),
        "PlanetScale Database Token gefunden",
    ),
    # Heroku Platform API Token (``HRKU-<base64url 36+ body>``).
    # The canonical Heroku Platform API authorization token format issued
    # post-March 2023 in response to the heroku.com OAuth incident
    # (https://status.heroku.com/incidents/2413). Issued via the Heroku
    # CLI (``heroku authorizations:create``) and the Heroku Dashboard at
    # dashboard.heroku.com/account/applications. Used for the Heroku
    # Platform API (``api.heroku.com/apps/...``,
    # ``api.heroku.com/account/...``, ``api.heroku.com/teams/...``)
    # for full app / dyno / config-var / Heroku Postgres / Heroku Redis
    # control-plane access. The body alphabet (``[A-Za-z0-9_-]``) lies
    # ENTIRELY inside the entropy fallback's ``[A-Za-z0-9+/=_-]``
    # alphabet — pre-fix the entropy regex matched the full
    # ``HRKU-<body>`` span as one generic ``Hochentropischer
    # Token-String`` finding, losing the Heroku-specific issuer
    # attribution that anchors the per-account revocation flow.
    #
    # Threat model (HIGH blast radius — full PaaS control plane plus
    # adjacent data-plane access via add-ons): a leaked ``HRKU-`` grants
    # the issuing user / authorization's full Heroku Platform API
    # scope. Read access = enumerate every app the user has access to
    # (across personal account and every Heroku Team they collaborate
    # on), dump every app's config vars (which routinely embed further
    # credentials — ``DATABASE_URL`` for Heroku Postgres with the
    # PostgreSQL connection string including the password,
    # ``REDIS_URL`` for Heroku Redis with the auth token,
    # ``SENDGRID_API_KEY`` / ``STRIPE_SECRET_KEY`` etc. for every
    # third-party add-on the app uses — the canonical "one credential
    # leak cascades to many" amplifier). Write access = arbitrary code
    # execution via ``heroku run`` against any dyno (canonical "shell
    # on the production server" primitive), modify app config vars
    # (overwrite ``DATABASE_URL`` to redirect every app instance to an
    # attacker-controlled DB for credential interception), release
    # new app code via ``heroku releases:rollback`` or new slug uploads
    # (supply-chain compromise of the production app), scale up / down
    # dynos (DoS or cost-amplification attack).
    #
    # Real-world emission patterns: ``.env`` files
    # (``HEROKU_API_KEY=HRKU-...``), ``~/.netrc`` files committed by
    # accident, CI/CD pipeline debug logs (``heroku-cli`` debug output
    # echoing the token in plan output), GitHub Actions secrets dumped
    # to logs by a misconfigured action, ``Procfile`` / ``app.json``
    # examples in README files hardcoding the token. Revocation flow
    # lives at the Heroku Dashboard > Account Settings > Applications
    # > "Revoke" (per-authorization) or via the CLI ``heroku
    # authorizations:revoke <id>`` — distinct per account, distinct
    # from every other PaaS-vendor rotation flow (Render via the
    # dashboard ``Settings > API Keys``, Vercel via the dashboard
    # ``Settings > Tokens``, Fly.io via ``fly tokens revoke``).
    #
    # Structural anchors:
    #   * ``HRKU-`` literal prefix (Heroku's canonical post-2023
    #     prefix; no other major issuer uses this prefix). Case-
    #     sensitive matches the documented uppercase convention.
    #   * ``[A-Za-z0-9_\-]{36,}`` body alphabet covers BOTH the UUID-
    #     shape body (32 hex + 4 dashes = 36 chars) AND the base64url-
    #     shape body (40+ chars). The 36-char floor rejects accidental
    #     fragments while accepting every documented Heroku canonical
    #     token shape.
    #   * ``(?<![A-Za-z0-9])`` / ``(?![A-Za-z0-9])`` boundary anchors
    #     reject mid-word collisions (``XHRKU-...`` / ``...G`` tails).
    (
        re.compile(r"(?<![A-Za-z0-9])HRKU-[A-Za-z0-9_\-]{36,}(?![A-Za-z0-9])"),
        "Heroku Platform API Token gefunden",
    ),
    # Docker Hub Personal Access Token (``dckr_pat_<base64url 27+ body>``).
    # The canonical Docker Hub PAT format used for Docker registry
    # authentication (``docker login`` with the PAT as the password).
    # Issued via the Docker Hub UI at hub.docker.com/settings/security
    # for full user-scoped registry access (push / pull / delete
    # repositories the user owns, list every private repository under
    # the user's namespace). The body alphabet (``[A-Za-z0-9_-]``)
    # lies ENTIRELY inside the entropy fallback's
    # ``[A-Za-z0-9+/=_-]`` alphabet — pre-fix the entropy regex
    # matched the full ``dckr_pat_<body>`` span as one generic
    # ``Hochentropischer Token-String`` finding, losing the
    # Docker-Hub-specific issuer attribution that anchors the per-user
    # revocation flow.
    #
    # Threat model (HIGH blast radius — supply-chain compromise
    # primitive): a leaked ``dckr_pat_`` grants the issuing user's
    # full Docker Hub scope per the token's configured permissions.
    # Read access = pull every private image in the user's namespace
    # (potentially containing baked-in credentials, proprietary source
    # code in container layers, internal-only infrastructure topology
    # encoded in image labels). Write access = push backdoored images
    # to ANY repository under the user's namespace under any tag — the
    # canonical "supply-chain compromise" primitive. Every downstream
    # consumer pulling ``user/image:latest`` (CI/CD pipelines,
    # Kubernetes deployments using ``imagePullPolicy: Always``,
    # ``docker-compose`` setups with no pinned digest) pulls the
    # backdoored image. The blast-radius amplifier is the cascade:
    # Docker Hub is a top-3 public registry and base images frequently
    # get reused across many projects, so a compromised base image
    # with millions of weekly pulls cascades to every downstream
    # consumer.
    #
    # Real-world emission patterns: ``.env`` files
    # (``DOCKER_HUB_TOKEN=dckr_pat_...``), CI/CD pipeline YAML
    # (``docker login -u $USER -p $DOCKER_HUB_TOKEN`` echoed in debug
    # logs when the pipeline runs with ``set -x``),
    # ``~/.docker/config.json`` files committed by mistake (the
    # ``auths`` block embeds the base64-encoded ``user:dckr_pat_<body>``
    # string), GitHub Actions secrets dumped to logs by a misconfigured
    # action, ``docker buildx`` debug output echoing the token in the
    # registry-login phase, notebook outputs running ``docker push``
    # with the token in plain text. Revocation flow lives at the Docker
    # Hub UI > Account Settings > Security > Access Tokens > "Delete"
    # — distinct per user, distinct from every other container-registry
    # vendor's revocation flow (GitHub Container Registry uses GitHub
    # PATs with ``write:packages`` scope; AWS ECR uses IAM credentials;
    # GitLab Container Registry uses GitLab PATs).
    #
    # Structural anchors:
    #   * ``dckr_pat_`` literal prefix (Docker Hub's canonical PAT
    #     prefix; no other major issuer uses this prefix). The trailing
    #     ``_`` after ``pat`` is the documented separator before the
    #     body.
    #   * ``[A-Za-z0-9_\-]{27,}`` body alphabet covers Docker Hub's
    #     documented base64url-ish body shape. The 27-char floor matches
    #     the minimum documented PAT length while accepting future
    #     canonical-length variations.
    #   * ``(?<![A-Za-z0-9])`` / ``(?![A-Za-z0-9])`` boundary anchors
    #     reject mid-word collisions (``Xdckr_pat_...`` / ``...G``
    #     tails). The lookbehind specifically rejects accidentally
    #     prefixed shapes that would otherwise extend the literal
    #     ``dckr_pat_`` identifier into a longer mid-word match.
    (
        re.compile(r"(?<![A-Za-z0-9])dckr_pat_[A-Za-z0-9_\-]{27,}(?![A-Za-z0-9])"),
        "Docker Hub Personal Access Token gefunden",
    ),
]


@dataclass(frozen=True)
class Finding:
    path: Path
    line_number: int
    match: str
    reason: str


def load_ignore_file(base_dir: Path, filename: str = ".secret-scan-ignore") -> list[str]:
    path = base_dir / filename
    if not path.exists():
        return []
    # Security: ``read_capped_text`` enforces a TOCTOU-safe size cap so
    # a planted huge ``.secret-scan-ignore`` cannot exhaust memory and
    # crash the CI gate before secrets are detected on the rest of the
    # repo. ``errors="ignore"`` preserves the legacy lossy-decode
    # contract for non-UTF-8 fragments.
    content = read_capped_text(
        path,
        MAX_IGNORE_FILE_BYTES,
        errors="ignore",
        label="secret-scan-ignore",
        logger=log,
    )
    if content is None:
        return []
    patterns: list[str] = []
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        patterns.append(line)
    return patterns


def _tracked_files(base_dir: Path) -> list[Path]:
    try:
        # Bandit B603/B607: ``git ls-files`` runs on a trusted local path,
        # command list is fully static (no user input).
        completed = subprocess.run(  # nosec B603, B607
            ["git", "ls-files", "-z"],
            cwd=base_dir,
            check=True,
            shell=False,
            capture_output=True,
            timeout=30,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return [path for path in base_dir.rglob("*") if path.is_file()]
    stdout = completed.stdout.decode("utf-8", errors="ignore")
    files: list[Path] = []
    for entry in stdout.split("\0"):
        if not entry:
            continue
        files.append((base_dir / entry).resolve())
    return files


def _is_binary(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            chunk = handle.read(4096)
    except OSError:
        return True
    return b"\0" in chunk


def _looks_like_secret(candidate: str, is_assignment: bool = False) -> bool:
    # Allow shorter secrets for explicit assignments (e.g. password="...")
    min_len = 8 if is_assignment else 24
    if len(candidate) < min_len:
        return False
    categories = 0
    categories += any(c.islower() for c in candidate)
    categories += any(c.isupper() for c in candidate)
    categories += any(c.isdigit() for c in candidate)

    # In strict contexts (assignment to sensitive var), allow symbols/spaces as entropy
    if is_assignment:
        categories += any(not c.isalnum() for c in candidate)

    # In strict contexts (assignments), we allow single-category secrets (e.g. all-lowercase)
    # provided they meet the length and entropy requirements.
    min_categories = 1 if is_assignment else 2
    if categories < min_categories:
        return False
    # Security (long-base64 entropy gap closure): cap the uniqueness
    # requirement at 32 characters. The pre-fix requirement
    # ``max(6, len(candidate) // 4)`` is mathematically unsatisfiable for
    # ANY candidate longer than ~256 chars whose alphabet is the base64
    # ceiling (``[A-Za-z0-9+/=_-]`` — at most 65 unique characters total).
    # Real-world AWS STS Session Tokens (200-700+ char base64 bodies),
    # GCP service-account key fingerprints, long Auth0 / Microsoft Graph
    # OAuth tokens, and any other multi-hundred-char base64 credential
    # were therefore SILENTLY UNDETECTED — neither ``_HIGH_ENTROPY_RE``
    # (entropy fallback) nor ``_SENSITIVE_ASSIGN_RE`` (assignment
    # heuristic) produced a finding because both branches share this
    # uniqueness gate. The 32-char cap preserves the pre-fix accept /
    # reject decision for ALL candidates <= 127 chars (the ``len // 4``
    # branch dominates) while only loosening the gate for longer
    # candidates where the base64 alphabet ceiling makes the original
    # ratio impossible to satisfy. False-positive surface is bounded:
    # ``_HIGH_ENTROPY_RE`` only matches contiguous ``[A-Za-z0-9+/=_-]``
    # spans (natural-language text breaks at punctuation / whitespace,
    # so no long English passage can match), and ``_is_binary`` already
    # skips committed binary files. The remaining surface — base64-
    # encoded data URIs inlined in HTML/Markdown source — is rare in
    # practice and can be allow-listed via ``.secret-scan-ignore`` if
    # it ever matters. The cap value 32 is generous: any realistic
    # high-entropy secret 128+ chars long contains at least 32 distinct
    # characters by simple combinatorial probability.
    if len(set(candidate)) < max(6, min(len(candidate) // 4, 32)):
        return False
    return True


def _mask_secret(value: str) -> str:
    """Mask a secret value for display (e.g. 'AKIA***1234')."""
    length = len(value)
    if length <= 8:
        return "***"
    if length <= 20:
        return f"{value[:2]}***{value[-2:]}"
    return f"{value[:4]}***{value[-4:]}"


def _scan_auth_scheme_credentials(
    content: str,
    covered_ranges: list[tuple[int, int]],
    line_resolver: Callable[[int], int],
) -> list[tuple[int, str, str]]:
    """Scan *content* for HTTP-auth-scheme-prefixed credential leaks.

    Iterates over :data:`_AUTH_SCHEME_DETECTORS` (currently
    ``_BEARER_RE`` + ``_BASIC_AUTH_RE``) and emits one finding per match
    that passes ``_looks_like_secret(candidate, is_assignment=True)`` and
    does not overlap an already-covered range. Mutates
    ``covered_ranges`` in place so subsequent detector loops in
    :func:`_scan_content` correctly suppress the same span.

    Extracted from :func:`_scan_content` to keep the latter at its C901
    complexity baseline while still extending coverage to additional
    auth-scheme literals from the HTTP authentication family (RFC 7235
    §2.1 case-insensitive auth-scheme contract). Future detectors (RFC
    7616 Digest, RFC 4559 SPNEGO, RFC 7486 HOBA) extend
    ``_AUTH_SCHEME_DETECTORS`` without changing this helper or the
    caller.
    """
    findings: list[tuple[int, str, str]] = []
    for regex, reason in _AUTH_SCHEME_DETECTORS:
        for match in regex.finditer(content):
            candidate = match.group(1)
            span_start, span_end = match.span(1)
            if not _looks_like_secret(candidate, is_assignment=True):
                continue
            if any(span_start < ce and span_end > cs for cs, ce in covered_ranges):
                continue
            findings.append((line_resolver(match.start()), candidate, reason))
            covered_ranges.append((span_start, span_end))
    return findings


def _scan_content(content: str) -> list[tuple[int, str, str]]:
    findings: list[tuple[int, str, str]] = []
    covered_ranges: list[tuple[int, int]] = []

    # Pre-calculate line offsets for fast lookup
    # Using simple list of newline positions
    newlines = [i for i, char in enumerate(content) if char == "\n"]

    def get_line_number(index: int) -> int:
        from bisect import bisect_left
        # newlines contains indices of newlines.
        # If index is before first newline, it's line 1 (bisect returns 0)
        # If index is after first newline, it's line 2 (bisect returns 1)
        return bisect_left(newlines, index) + 1

    def is_covered(start: int, end: int) -> bool:
        for c_start, c_end in covered_ranges:
            if start < c_end and end > c_start:
                return True
        return False

    for match in _PEM_RE.finditer(content):
        candidate = match.group(0)
        span_start, span_end = match.span(0)

        if not is_covered(span_start, span_end):
            findings.append((get_line_number(match.start()), candidate, "Private Key (PEM) gefunden"))
            covered_ranges.append((span_start, span_end))

    for regex, reason in _KNOWN_TOKENS:
        for match in regex.finditer(content):
            candidate = match.group(0)
            span_start, span_end = match.span(0)

            if not is_covered(span_start, span_end):
                findings.append((get_line_number(match.start()), candidate, reason))
                covered_ranges.append((span_start, span_end))

    for match in _AWS_ID_RE.finditer(content):
        candidate = match.group(0)
        span_start, span_end = match.span(0)

        if not is_covered(span_start, span_end):
            findings.append((get_line_number(match.start()), candidate, "AWS Access Key ID gefunden"))
            covered_ranges.append((span_start, span_end))

    # Auth-scheme detectors share the same processing shape; the helper
    # :func:`_scan_auth_scheme_credentials` iterates :data:`_AUTH_SCHEME_DETECTORS`
    # and keeps this function at its existing C901 complexity baseline
    # while extending coverage to additional auth-scheme literals from
    # the HTTP authentication family.
    findings.extend(_scan_auth_scheme_credentials(content, covered_ranges, get_line_number))

    for match in _SENSITIVE_ASSIGN_RE.finditer(content):
        candidate = match.group(2).strip()
        # Strip outer quotes if present
        quoted = False
        # Handle triple quotes first (check length >= 6 to avoid index errors)
        if candidate.startswith('"""') and candidate.endswith('"""') and len(candidate) >= 6:
            candidate = candidate[3:-3]
            quoted = True
        elif candidate.startswith("'''") and candidate.endswith("'''") and len(candidate) >= 6:
            candidate = candidate[3:-3]
            quoted = True
        elif (candidate.startswith('"') and candidate.endswith('"')) or (
            candidate.startswith("'") and candidate.endswith("'")
        ):
            candidate = candidate[1:-1]
            quoted = True

        if not quoted:
            # Ignore code-like constructs in unquoted values
            if any(c in candidate for c in "().[]:"):
                continue
            # Ignore common Python keywords to avoid flagging code as secrets
            if candidate.startswith(
                (
                    "return ",
                    "import ",
                    "from ",
                    "class ",
                    "def ",
                    "if ",
                    "else",
                    "elif",
                    "for ",
                    "while ",
                    "try",
                    "except",
                    "with ",
                    "async ",
                    "await ",
                    "raise ",
                )
            ):
                continue
            if candidate in ("None", "True", "False"):
                continue

        # Use the span of the value group (including quotes) for coverage
        span_start, span_end = match.span(2)

        if _looks_like_secret(candidate, is_assignment=True):
            if not is_covered(span_start, span_end):
                findings.append((get_line_number(match.start()), candidate, "Verdächtige Zuweisung eines potentiellen Secrets"))
                covered_ranges.append((span_start, span_end))

    for match in _HIGH_ENTROPY_RE.finditer(content):
        candidate = match.group(0)
        span_start, span_end = match.span(0)

        if candidate.isalpha():
            # Reduce false positives for LongCamelCaseClassNames
            continue

        if _looks_like_secret(candidate):
            if not is_covered(span_start, span_end):
                findings.append((get_line_number(match.start()), candidate, "Hochentropischer Token-String"))

    return findings


def _should_ignore(path: Path, patterns: Sequence[str], base_dir: Path) -> bool:
    """Apply the operator-supplied ``.secret-scan-ignore`` patterns.

    Pre-fix ``relative.match(pattern)`` used ``pathlib.PurePath.match``,
    whose semantics match only against the last path component for
    pattern-without-``/`` cases — so a single line ``*`` in
    ``.secret-scan-ignore`` matched *every* file regardless of depth
    (``PurePath('a/b/c').match('*') is True``), silently disabling the
    scanner across the whole repo; and a line ``.env`` matched every
    nested ``.env`` (e.g. ``src/config/.env``), masking real plants in
    sub-tree configs. Neither aligns with the gitignore intuition the
    one-line comment at the call site implies.

    Post-fix the match uses ``fnmatch.fnmatchcase`` against the FULL forward-
    slash-normalised relative path AND the basename (``fnmatchcase`` is
    case-sensitive on every platform, unlike ``fnmatch`` which case-folds on
    Windows via ``os.path.normcase``), so:

    * A pattern with ``/`` is anchored against the full path
      (``src/leak.py`` matches only that file).
    * A pattern without ``/`` matches the basename (``*.env`` matches
      every ``.env`` at any depth, in line with operator expectation).
    * A bare ``*`` / ``**`` pattern (only asterisks, optionally wrapped in
      whitespace) is explicitly REFUSED: it would match every basename and
      silently disable the entire scanner — the precise ignore-list footgun
      this matcher guards against. Such a pattern is skipped, never honoured.

    The behaviour is closer to ``gitignore`` than the previous
    ``PurePath.match`` and never silently drops the scanner's coverage.
    """
    try:
        relative = path.relative_to(base_dir)
    except ValueError:
        return False
    rel_str = relative.as_posix()
    rel_name = relative.name
    for pattern in patterns:
        # Security: refuse a bare "*"/"**" (only asterisks, possibly wrapped in
        # whitespace). fnmatch("*") matches every basename, so honouring it
        # would silently disable the whole scanner — the ignore-list footgun.
        if not pattern.strip().strip("*"):
            continue
        # Use fnmatchcase, NOT fnmatch: fnmatch routes both the name and the
        # pattern through os.path.normcase, which lowercases on Windows — making
        # the ignore list case-INsensitive there but case-sensitive on Linux,
        # i.e. platform-dependent coverage for a security gate (a Windows-side
        # over-broad match could silently skip a file holding a planted secret).
        # fnmatchcase is case-sensitive on every platform, matching git's
        # default case-sensitive path semantics the docstring describes.
        if "/" in pattern:
            if fnmatch.fnmatchcase(rel_str, pattern):
                return True
        else:
            if fnmatch.fnmatchcase(rel_name, pattern):
                return True
    return False


def scan_repository(
    base_dir: Path,
    *,
    paths: Iterable[Path] | None = None,
    ignore_patterns: Sequence[str] | None = None,
) -> list[Finding]:
    ignore_patterns = tuple(ignore_patterns or ())
    if paths is not None:
        files: list[Path] = []
        for path in paths:
            if path.is_dir():
                files.extend(p for p in path.rglob("*") if p.is_file())
            else:
                files.append(path)
    else:
        files = _tracked_files(base_dir)
    findings: list[Finding] = []
    for file_path in files:
        if not file_path.exists() or not file_path.is_file():
            continue
        if _should_ignore(file_path, ignore_patterns, base_dir):
            continue
        if _is_binary(file_path):
            continue
        # Security: ``read_capped_text`` enforces a TOCTOU-safe size cap
        # so a planted huge tracked file (e.g. an intentionally-corrupt
        # data dump) cannot exhaust memory and crash the scanner before
        # planted secrets in sibling files are flagged.
        # ``errors="ignore"`` preserves the legacy lossy-decode contract
        # for non-UTF-8 fragments that aren't filtered by ``_is_binary``.
        content = read_capped_text(
            file_path,
            MAX_SCAN_FILE_BYTES,
            errors="ignore",
            label="scan target",
            logger=log,
        )
        if content is None:
            continue

        for lineno, snippet, reason in _scan_content(content):
            # Mask the secret value to prevent leakage in logs/CI
            masked = _mask_secret(snippet)
            findings.append(
                Finding(
                    path=file_path,
                    line_number=lineno,
                    match=masked,
                    reason=reason,
                )
            )
    return findings
