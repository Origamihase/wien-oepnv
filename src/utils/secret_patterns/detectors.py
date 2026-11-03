"""Generic and HTTP auth-scheme detector regexes of the secret scanner.

Moved verbatim from ``src/utils/secret_scanner.py``, which re-exports every name.
The generic detectors the scanner reads itself (``_HIGH_ENTROPY_RE``,
``_SENSITIVE_ASSIGN_RE``, ``_AWS_ID_RE``, ``_AUTH_SCHEME_DETECTORS``, ``_PEM_RE``)
are defined there, next to the code that reads them.
"""

from __future__ import annotations

import re

# ``__all__`` lists every name this module defines. Some private names are
# read only by other modules and tests; CodeQL (py/unused-global-variable)
# counts such a read through an import only when the name is listed here.
__all__ = [
    "_BEARER_RE", "_BASIC_AUTH_RE", "_NEGOTIATE_RE", "_NTLM_RE", "_TOKEN_SCHEME_RE", "_HOBA_RE",
]


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
