"""Sentinel PoC: missing ``_HOBA_RE`` detector — RFC 7486 HTTP
Origin-Bound Authentication (HOBA) credential leaks slip past the
secret scanner with attribution drift OR silent undetection, depending
on the body shape.

This round closes the **named-but-deferred** adjacent-detector
candidate from the 2026-05-16 Token-Scheme detector drift round
(sibling PR ``security(secret-scanner): add Token-Scheme detector per
GitHub REST API``). That round explicitly listed HOBA as one of the
next-round targets::

    **Next-round candidates (named-but-deferred):** ... **HOBA
    detector** (``(?i)HOBA\\s+...``) — RFC 7486 HTTP Origin-Bound Auth;
    rare in practice but a complete auth-scheme enumeration would
    include it. ...

The ``HOBA`` literal is matched case-insensitively per the RFC 7235
§2.1 contract that every HTTP auth-scheme inherits (RFC 7486 §3
references RFC 7235 for the authentication-scheme framework).
``hoba result="..."`` / ``HOBA result="..."`` / ``Hoba result="..."``
are all legitimate canonical HTTP Authorization-header shapes on the
wire.

Threat model
------------

HOBA is the IANA-registered HTTP authentication scheme for
client-asserted public-key authentication WITHOUT TLS client
certificates. Per RFC 7486 §3 the on-the-wire format is::

    Authorization: HOBA result="<KID>.<challenge>.<nonce>.<signature>"

Where:
  * ``KID`` — base64url-encoded SHA-256 hash of the client's public
    key (Key Identifier; 43 chars for the canonical SHA-256 output);
  * ``challenge`` — server-supplied base64url-encoded nonce (8-32
    chars);
  * ``nonce`` — client-supplied base64url-encoded freshness anchor
    (8-32 chars);
  * ``signature`` — base64url-encoded digital signature using the
    client's private key (ECDSA P-256 = 86 base64url chars; RSA-2048
    = 342 base64url chars).

A leaked HOBA ``result`` value gives the attacker:
  * The ``KID`` — reveals which client public key signs HOBA
    requests; combined with a separately-leaked public key file, can
    be cross-referenced to identify the user.
  * The ``signature`` — a one-time-use replay-able auth proof
    bounded by the (challenge, nonce) freshness window; if the
    server accepts replay (a defect, but real in some lazy
    HOBA implementations), the attacker can authenticate as the
    holder of the private key.
  * The ``challenge`` and ``nonce`` — exposes the server-side
    challenge issuance pattern, useful for protocol analysis.

The structural distinction from Bearer / Basic / Negotiate / NTLM /
Token is that **the credential body is a quoted dot-separated 4-tuple
inside a ``result="..."`` parameter** (per RFC 7486 §3) — the only
HTTP auth-scheme in this family with the parameterised quoted-string
body shape rather than the simple ``<scheme> <body>`` form.

Pre-fix the existing scanner failed on HOBA credentials via two
orthogonal failure modes:

1. **Attribution drift (common case)** — 100+-char base64url HOBA
   result strings with mixed character classes DO match
   ``_HIGH_ENTROPY_RE`` (``[A-Za-z0-9+/=_-]{24,}``) for the contiguous
   slices BETWEEN the dots (the dots are OUTSIDE the entropy
   alphabet), but only for individual segments. The HOBA-specific
   attribution is lost AND the credential is split into multiple
   findings — incident-response triage must guess whether the
   leaked dotted structure is a JOSE JWT (revoke at IdP), HOBA
   result (rotate HOBA key pair, reissue KID), or some other
   opaque dotted secret.

2. **Silent undetection (all-letter fields)** — the entropy
   fallback's ``candidate.isalpha()`` short-circuit (added to
   suppress LongCamelCaseClassName false positives) rejects bodies
   composed entirely of ``[A-Za-z]`` characters. HOBA result strings
   whose 4 fields happen to be all-letter (rare in practice but
   possible for hand-crafted test fixtures, CTF challenges, or
   pathological base64url encodings) were SILENTLY UNDETECTED.

Real-world emission patterns
----------------------------

- WebAuthn-pre-cursor HOBA implementations (most legacy HOBA
  deployments predate WebAuthn / FIDO2 and use the original
  RFC 7486 format).
- Academic research papers on HTTP authentication that publish
  example captures.
- IoT device firmware using HOBA for backend auth in lieu of TLS
  client certs (constrained-device contexts where the TLS
  client-cert ladder is too heavyweight).
- Specialised enterprise PKI integrations.
- Browser dev-tools Network tab HAR exports of HOBA-authenticated
  sites.
- Wireshark / tshark capture text exports rendering the
  Authorization header verbatim.
- Python ``requests`` debug logs.
- YAML / Postman / Insomnia request export files embedding the
  Authorization header.

Severity
--------

**LOW-MEDIUM** — HOBA is RARE in production (the canonical "rare
but registered" auth-scheme per the journal entries that explicitly
classified it as deferred-but-completeness-worthy). The leak surface
is bounded: HOBA implementations live in academic / IoT / specialised
PKI contexts, not in mainstream web apps. However, when HOBA IS in
use, a leaked ``result`` value enables replay-based authentication
attacks against lazy implementations and reveals key-binding
information that aids targeted social-engineering attacks. The
silent-undetection branch is higher severity in absolute terms
(detection-from-zero for the all-letter-fields case), but the
practical impact is bounded by HOBA's rare deployment.

Fix
---

Add a HOBA auth-scheme detector mirroring the Bearer/Basic/Negotiate/
NTLM/Token detectors' case-insensitive contract::

    _HOBA_RE = re.compile(
        r'''(?i)HOBA\\s+result\\s*=\\s*["']([A-Za-z0-9_\\-]{8,}(?:\\.[A-Za-z0-9_\\-]{8,}){3})["']'''
    )

Append to ``_AUTH_SCHEME_DETECTORS`` BEFORE the Token-scheme entry so
the more-specific IANA-registered HOBA attribution wins over the
generic Token-scheme catch-all via ``covered_ranges`` arbitration.

The structural disambiguator is the FOUR dot-separated fields inside
a quoted ``result="..."`` parameter — natural-language text never
contains the literal ``HOBA`` followed by this exact shape. The
body alphabet ``[A-Za-z0-9_\\-]`` is the canonical base64url alphabet
per RFC 4648 §5; the quote alphabet ``["']`` accepts both canonical
RFC-double-quote and the de-facto single-quote shape that appears in
YAML / Python / Postman serialisations.

Marker: SENTINEL_HOBA_DRIFT.
"""

# Aufgeteilt am 2026-11-02 (höchstens 700 Zeilen je Datei): Hilfen und Fixtures in
# _support.py, die Tests in test_teil_<n>.py in ihrer alten Reihenfolge.
