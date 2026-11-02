"""Sentinel PoC: missing ``_TOKEN_SCHEME_RE`` detector — the GitHub-style
``Authorization: token <body>`` HTTP authentication scheme (de facto
alias for Bearer popularised by the GitHub REST API and inherited by
Gitea / Forgejo / DigitalOcean / various self-hosted Git hosts) slips
past the secret scanner with attribution drift OR silent undetection,
depending on the body shape.

This round closes the **named-but-deferred** first-priority
adjacent-detector candidate from the 2026-05-16 NTLM detector drift
round (sibling PR ``security(secret-scanner): add NTLM detector per
[MS-NLMP]``). That round explicitly named the GitHub ``Token`` scheme
detector as the first next-round target::

    **Next-round candidates (named-but-deferred):** **GitHub ``Token``
    scheme detector** (``(?i)token\\s+[A-Za-z0-9_\\-]{36,}``) —
    GitHub's older API alias for Bearer (``Authorization: token
    ghp_...``); the embedded GitHub PAT (``ghp_``-prefixed) already
    matches ``_KNOWN_TOKENS`` via the ``ghp_`` regex, so the ``token``
    scheme detector is attribution-only for opaque non-GitHub tokens
    that happen to use the alias.

The ``token`` literal is matched case-insensitively per the RFC 7235
§2.1 contract that every HTTP auth-scheme inherits (even though
``token`` is not a registered IANA HTTP Authentication Scheme — it is a
de facto convention popularised by GitHub's REST API documentation and
inherited by every Git-host fork that mirrors GitHub's HTTP API shape:
Gitea, Forgejo, Codeberg, Gogs, sourcehut's mirror endpoints, plus
DigitalOcean's older API v1 docs and various other tools). ``token
<body>`` / ``TOKEN <body>`` / ``Token <body>`` / ``ToKeN <body>`` are
all legitimate canonical HTTP Authorization-header shapes on the wire.

Threat model
------------

A leaked ``Authorization: token <body>`` (where the body is an
opaque API token — typically a 36-char alphanumeric GitHub PAT body or
a 40-char hex legacy GitHub token from before April 2021, but also any
opaque token from Gitea/Forgejo/DigitalOcean/other ``token``-scheme
issuers) in committed source / log artefacts / CI debug snippets /
hostile-PR fragments fails the existing detection branches in two
distinct ways:

1. **Attribution drift (common case)** — opaque API tokens with mixed
   character classes (the typical 36+ char alphanumeric+hyphen body)
   DO match ``_HIGH_ENTROPY_RE`` (``[A-Za-z0-9+/=_-]{24,}``) generically
   and land as ``Hochentropischer Token-String`` findings. The
   token-scheme-specific attribution is lost — incident-response
   triage must guess whether the leaked entropy span is a GitHub PAT
   (rotate at github.com/settings/tokens, audit
   ``GET /user`` API calls for the token's usage history), a Gitea
   token (rotate at ``/user/settings/applications``, audit Gitea's
   ``OAuth2 Applications`` audit log), a DigitalOcean token (rotate at
   cloud.digitalocean.com/account/api/tokens, audit Spaces / Droplet /
   Kubernetes API calls), or some other opaque secret.

2. **Silent undetection (uniform-character-class body)** — the
   entropy fallback's ``candidate.isalpha()`` short-circuit (added to
   suppress LongCamelCaseClassName false positives) rejects bodies
   composed entirely of ``[A-Za-z]`` characters. Such bodies arise
   from custom token generators that emit alphabetical-only IDs, hash-
   encoded tokens that happen to land in the letter-only sub-alphabet,
   or short test fixtures. Pre-fix every leaked ``token
   <all-letter-body>`` is **silently undetected** entirely.

Note on attribution overlap with ``_KNOWN_TOKENS``: a leaked
``Authorization: token ghp_xxxx...`` carries TWO matchable spans —
the ``ghp_<36>`` GitHub PAT span (matched by ``_KNOWN_TOKENS``) and
the ``token <body>`` HTTP auth-scheme span (matched by this new
detector). The ``_KNOWN_TOKENS`` matcher runs FIRST in
``_scan_content``, so the GitHub-PAT-specific reason wins via the
``covered_ranges`` arbitration — the new token-scheme detector
yields its attribution only for tokens that do NOT match any
``_KNOWN_TOKENS`` prefix (opaque non-prefixed tokens). This mirrors
the cross-detector boundary established by the Bearer / Basic /
Negotiate / NTLM rounds: more-specific issuer attribution always
wins.

Real-world emission patterns
----------------------------

- GitHub REST API examples in legacy curl scripts:
  ``curl -H "Authorization: token ghp_xxx" https://api.github.com/user``
- ``hub`` CLI configuration files (``~/.config/hub``) emitting
  ``oauth_token: <body>`` in YAML — the underlying transport uses the
  ``token`` HTTP scheme.
- Gitea / Forgejo / Codeberg / Gogs API examples mirroring the GitHub
  shape: ``curl -H "Authorization: token <gitea-pat>"``.
- DigitalOcean API v1 docs (legacy):
  ``curl -H "Authorization: token <do-token>" https://api.digitalocean.com``.
- CI/CD workflow files with hard-coded fallback tokens:
  ``- run: curl -H "Authorization: token $TOKEN" ...``.
- Python ``requests`` debug logs (``logging.DEBUG`` on
  ``urllib3.connectionpool``) emitting the full Authorization header.
- Browser dev-tools Network tab exports (HAR files) capturing
  intranet self-hosted Git host API calls.
- Documentation snippets in READMEs and wikis copy-pasting curl
  examples with real (live!) tokens left in.

Severity
--------

**MEDIUM-HIGH** — opaque API token credentials grant authenticated
access to the issuing service. The blast radius depends on the token's
permission scope: GitHub PATs can range from read-only ``public_repo``
to full ``repo + admin:org + admin:gpg_key + admin:public_key + ...``
(every operation the user can perform via the API including private
repo access, secret extraction, branch protection bypass, workflow
modification leading to RCE in CI). Gitea/Forgejo tokens have similar
scope ranges. DigitalOcean tokens grant full account access by
default. Mitigated only by the requirement that the leaked credential
live alongside a ``token`` auth-scheme literal in the same content
blob; in practice this covers every leak through legacy curl examples,
CI workflow templates, HAR exports of API-using intranet apps,
``requests`` debug output, and copy-pasted documentation.

Fix
---

Add a ``token`` auth-scheme detector mirroring the existing
auth-scheme detectors' case-insensitive contract::

    _TOKEN_SCHEME_RE = re.compile(r"(?i)token\\s+([A-Za-z0-9_\\-]{36,})")

Append to ``_AUTH_SCHEME_DETECTORS`` LAST (after Bearer / Basic /
Negotiate / NTLM) so the existing ``_scan_auth_scheme_credentials``
helper processes matches uniformly with the same
``is_assignment=True`` ``_looks_like_secret`` filter that the existing
detectors use. The placement order matters for the cross-detector
boundary: more-specific schemes (Bearer / Basic / Negotiate / NTLM)
must run FIRST so their attribution wins via ``covered_ranges``
arbitration when a token-shaped body sits inside a Bearer / Basic /
etc. header.

The 36+ char body floor is the structural disambiguator against
natural-language false positives — the word ``token`` is common in
English prose and code comments, but is essentially never followed by
36+ contiguous chars from the ``[A-Za-z0-9_\\-]`` alphabet in natural
text (English words break at whitespace and punctuation). The floor
is calibrated to the GitHub PAT body length (``ghp_<36>`` = 40 total
chars, with the 36-char body part captured by the detector when the
PAT prefix is absent — the legacy 40-char hex GitHub token from
before April 2021 also exceeds the 36-char floor). The body alphabet
``[A-Za-z0-9_\\-]`` covers GitHub's PAT alphabet, Gitea/Forgejo's PAT
alphabet (both inherit GitHub's URL-safe alphanumeric+hyphen+underscore
shape per their REST API specs), and DigitalOcean's hex token shape.
The downstream ``_looks_like_secret(candidate, is_assignment=True)``
heuristic (``min_categories=1`` in the auth-scheme path) provides the
second-layer filter for any token-shaped string that does happen to
follow a ``token``-prefixed natural-language passage.

Marker: SENTINEL_TOKEN_SCHEME_DRIFT.
"""

# Aufgeteilt am 2026-11-02 (höchstens 700 Zeilen je Datei): Hilfen und Fixtures in
# _support.py, die Tests in test_teil_<n>.py in ihrer alten Reihenfolge.
