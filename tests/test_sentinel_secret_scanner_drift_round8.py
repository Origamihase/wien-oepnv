"""Sentinel PoC: secret-scanner drift Round 8 — three additional high-impact
issuer prefixes whose canonical format silently bypasses specific attribution
in the post-Round-7 ``_KNOWN_TOKENS`` table.

The 2026-05-10 Round 7 closed Doppler /
Buildkite Agent Token / Netlify and re-stated the prevention rule:

> "Every audit round that adds a new issuer MUST also enumerate THREE
> adjacent sub-landscapes the round did NOT cover."

Round 7 enumerated **secrets management continued** (closed Doppler),
**CI/CD execution tier** (closed Buildkite Agent), and **CI/CD hosting
tier** (closed Netlify), and named four next-round candidates:

* **CI/CD platforms hosting tier continued** — Render
  (``rnd_<base64>``) — UNAMBIGUOUS prefix, deferred from Round 6.
* **CI/CD platforms execution tier continued** — Buildkite User
  Access Token (``bkua_<base64>``) — UNAMBIGUOUS prefix, sibling of
  the Round-7 Buildkite Agent Token.
* **PaaS / edge runtime** — Fly.io (``FlyV1 <macaroon>``) —
  multi-segment dot/slash separated, needs a new pattern shape.
* **Secrets management continued** — Infisical
  (``<32 hex>:<32 hex>``-shape, no prefix — bucket-(b)) — deferred
  for a later round (no canonical prefix).

Closing the three named-and-canonical-prefixed entries (Render,
Buildkite User Access Token, Fly.io) re-establishes the issuer-
attribution coverage the Round 7 closing checklist guaranteed. Each
token's canonical format silently bypasses specific attribution in
``_KNOWN_TOKENS``:

  1. **Render Personal Access Token** (``rnd_<40+ alphanumeric body>``)
     — issued via dashboard.render.com/u/settings#api-keys for full
     Render REST-API access (the modern Render-platform token format).
     Total length 44+ chars (4-char prefix + 40+ char body). The
     ``rnd_`` prefix is unambiguous (no other major issuer uses
     it), and the strict alphanumeric body lies entirely inside the
     entropy fallback's alphabet — so the entropy regex matches the
     full ``rnd_<body>`` span as one generic finding, losing the
     Render-specific attribution that incident-response keys off. A
     leak grants the issuing user's full Render API scope: read /
     write every owned service's deploys, environment variables,
     persistent disks, custom domains, build hooks and webhook
     configuration; a malicious deploy can replace the live
     application (web service, static site, cron job, background
     worker) with arbitrary code, bypassing every downstream gate.
     Render is the canonical hosting-platform sibling of Netlify
     (Round 7) and Vercel (deferred — bucket-(b) no-prefix).

  2. **Buildkite User Access Token** (``bkua_<40+ alphanumeric body>``)
     — issued via buildkite.com/user/api-access-tokens for
     user-scoped REST-API access (issue queries, build retries,
     pipeline manipulation, agent management). Distinct from the
     Round-7 Buildkite **Agent** Token (``bkat_``): agent tokens
     register CI workers, user tokens act on behalf of a human user.
     The ``bkua_`` prefix is unambiguous (no other major issuer
     uses it), and the strict alphanumeric body lies entirely
     inside the entropy fallback's alphabet — same generic-only
     attribution gap as ``bkat_``. A leak grants the issuing user's
     full Buildkite API scope across every accessible organisation:
     read pipeline definitions (which often embed secrets in env
     references), retry historical builds with attacker-controlled
     env overrides, manage agents, and exfiltrate access logs. The
     revocation flow lives at buildkite.com/user/api-access-tokens
     and is distinct from agent-token revocation, so issuer-specific
     attribution accelerates IR triage.

  3. **Fly.io API Token** (``FlyV1 fm[12]_<base64 body>`` or
     ``FlyV1 fo1_<base64 body>``) — issued via the ``fly auth token``
     CLI or fly.io/dashboard for full Fly.io platform API access
     (deploy apps, read secrets, manipulate Wireguard peers, manage
     organisations). The canonical leak surface is the
     Authorization-header form ``FlyV1 <token>``: the ``FlyV1 ``
     scheme prefix (with literal space) anchors against fly.io
     specifically. Modern macaroon tokens use ``fm2_`` (current
     default) or ``fm1_`` (legacy macaroon), and the oldest opaque
     tokens use ``fo1_``. Total length 200+ chars (the macaroon
     body encodes embedded JSON capability descriptions plus
     organisation / app scope). The literal space in ``FlyV1 ``
     and the body alphabet ``[A-Za-z0-9_=\\-]`` (base64url + ``=``
     padding) place the prefix outside the entropy fallback's
     contiguous-match span — pre-fix the entropy regex matches
     only the body span after the underscore, losing both the
     ``FlyV1 fm2_`` prefix AND the Fly.io-specific issuer
     attribution. A leak grants the issuing principal's full Fly.io
     organisation scope: deploy arbitrary container images
     (including ones that exfiltrate every secret in the org's
     apps), modify networking (Wireguard peers, IP allocations,
     Anycast routes), and rotate billing credentials. Fly.io is the
     canonical PaaS / edge-runtime sibling not previously covered.

Each test below pre-fix would have flagged only the generic high-entropy
fallback (or, for Fly.io, only the body span after ``fm2_``); post-fix
every token gets the issuer-specific reason that incident-response
playbooks key off.
"""
from __future__ import annotations

from pathlib import Path

from src.utils.secret_scanner import scan_repository


# ---------------------------------------------------------------------------
# Render Personal Access Token
# ---------------------------------------------------------------------------
#
# Format: ``rnd_<40+ alphanumeric body>``. Issued via
# dashboard.render.com/u/settings#api-keys for full Render REST-API
# access. A leak grants the issuing user's full Render scope:
# read/write every owned service's deploys, environment variables,
# persistent disks, custom domains, build hooks and webhook
# configuration. Malicious deploys can replace the live application
# with arbitrary code, bypassing every downstream gate.
# The revocation flow lives at dashboard.render.com/u/settings#api-keys.


def test_secret_scanner_detects_render_pat(tmp_path: Path) -> None:
    """Render PAT: ``rnd_<40+ alphanumeric body>``.

    Pre-fix: the entropy fallback ``[A-Za-z0-9+/=_-]{24,}`` matches
    the full ``rnd_<body>`` span (the underscore is in the alphabet)
    and reports ``Hochentropischer Token-String`` — losing the
    Render-specific attribution that incident-response keys off.
    """
    file_path = tmp_path / "render_deploy.py"
    # Realistic synthetic Render PAT: 4-char prefix + 40-char
    # alphanumeric body matching the documented modern format.
    body = "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789AbCd"  # 40 alphanumeric
    assert len(body) == 40
    secret = f"rnd_{body}"
    file_path.write_text(
        f'RENDER_API_KEY = "{secret}"', encoding="utf-8"
    )

    findings = scan_repository(tmp_path, paths=[file_path])

    assert findings, "Should detect Render Personal Access Token"
    reasons = [f.reason for f in findings]
    assert "Render API Key gefunden" in reasons, (
        f"Expected Render-specific attribution, got reasons: "
        f"{reasons}. Render PATs grant full deploy access, letting "
        "attackers replace the live service with arbitrary code. "
        "Precise attribution accelerates revocation at "
        "dashboard.render.com/u/settings#api-keys."
    )
    # Ensure raw secret never appears in findings (redaction).
    assert secret not in [f.match for f in findings]


def test_secret_scanner_detects_render_token_in_env_config(tmp_path: Path) -> None:
    """Render tokens commonly appear in ``.env`` / shell-rc files; the
    detector must work regardless of surrounding context (quoted /
    unquoted, KEY=VALUE shapes)."""
    file_path = tmp_path / "production.env"
    body = "ZyXwVuTsRqPoNmLkJiHgFeDcBaZyXwVuTsRqPoNm"  # 40 alphanumeric
    secret = f"rnd_{body}"
    file_path.write_text(f"RENDER_API_KEY={secret}\n", encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    assert "Render API Key gefunden" in reasons, (
        "Render detector must flag tokens in unquoted KEY=VALUE shapes"
    )


def test_secret_scanner_does_not_flag_short_rnd_prefix(tmp_path: Path) -> None:
    """Negative case: short ``rnd_`` strings (e.g. accidental fragments
    or operator-named placeholders like ``rnd_seed``) MUST NOT match
    the Render pattern. The strict 40+ char body length guard prevents
    collision with operator-named identifiers."""
    file_path = tmp_path / "config.py"
    # 16-char body — too short to be a real Render PAT.
    not_render = "rnd_abcdef0123456789"
    file_path.write_text(f'placeholder = "{not_render}"', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    assert "Render API Key gefunden" not in reasons


# ---------------------------------------------------------------------------
# Buildkite User Access Token
# ---------------------------------------------------------------------------
#
# Format: ``bkua_<40+ alphanumeric body>``. Issued via
# buildkite.com/user/api-access-tokens for user-scoped REST-API access
# (issue queries, build retries, pipeline manipulation, agent
# management). Distinct from the Round-7 Buildkite Agent Token
# (``bkat_``): agent tokens register CI workers, user tokens act on
# behalf of a human user. A leak grants the issuing user's full
# Buildkite API scope across every accessible organisation.


def test_secret_scanner_detects_buildkite_user_access_token(tmp_path: Path) -> None:
    """Buildkite User Access Token: ``bkua_<40+ alphanumeric body>``.

    Pre-fix: the entropy fallback ``[A-Za-z0-9+/=_-]{24,}`` matches
    the full ``bkua_<body>`` span (the underscore is in the alphabet)
    and reports ``Hochentropischer Token-String`` — losing the
    Buildkite-user-specific attribution that incident-response keys
    off.
    """
    file_path = tmp_path / "buildkite_user_token.py"
    # Realistic synthetic Buildkite user access token: 5-char prefix +
    # 40-char alphanumeric body. Real tokens range 40-50 chars.
    body = "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789AbCd"  # 40 alphanumeric
    assert len(body) == 40
    secret = f"bkua_{body}"
    file_path.write_text(
        f'BUILDKITE_API_TOKEN = "{secret}"', encoding="utf-8"
    )

    findings = scan_repository(tmp_path, paths=[file_path])

    assert findings, "Should detect Buildkite User Access Token"
    reasons = [f.reason for f in findings]
    assert "Buildkite User Access Token gefunden" in reasons, (
        f"Expected Buildkite-user-specific attribution, got reasons: "
        f"{reasons}. Buildkite user access tokens grant attackers the "
        "ability to read pipeline definitions, retry historical builds "
        "with attacker-controlled env overrides, manage agents, and "
        "exfiltrate access logs. Precise attribution accelerates "
        "revocation at buildkite.com/user/api-access-tokens."
    )
    assert secret not in [f.match for f in findings]


def test_secret_scanner_does_not_flag_short_bkua_prefix(tmp_path: Path) -> None:
    """Negative case: short ``bkua_`` strings MUST NOT match the
    Buildkite user-access pattern. The strict 40+ char body length
    guard prevents collision with operator-named identifiers."""
    file_path = tmp_path / "config.py"
    # 16-char body — too short to be a real Buildkite user access token.
    not_buildkite = "bkua_abcdef0123456789"
    file_path.write_text(f'placeholder = "{not_buildkite}"', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    assert "Buildkite User Access Token gefunden" not in reasons


def test_buildkite_user_does_not_misattribute_as_agent(tmp_path: Path) -> None:
    """Mutual-exclusion regression: ``bkua_`` (user access token) MUST
    NOT be flagged as ``bkat_`` (agent token). The Round-7 agent token
    detector and this Round-8 user token detector key on different
    fourth-character disambiguators (``a`` for agent, ``u`` for user),
    so their patterns are mutually exclusive at the prefix level. A
    leaked user access token has a different revocation URL
    (buildkite.com/user/api-access-tokens vs
    buildkite.com/organizations/<org>/agents) and IR triage MUST get
    the right one."""
    file_path = tmp_path / "config.py"
    body = "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789AbCd"  # 40 alphanumeric
    secret = f"bkua_{body}"
    file_path.write_text(f'TOKEN = "{secret}"', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    assert "Buildkite User Access Token gefunden" in reasons
    assert "Buildkite Agent Token gefunden" not in reasons


# ---------------------------------------------------------------------------
# Fly.io API Token (FlyV1 macaroon)
# ---------------------------------------------------------------------------
#
# Format: ``FlyV1 fm[12]_<base64 body>`` or ``FlyV1 fo1_<base64 body>``.
# Issued via ``fly auth token`` for full Fly.io platform API access
# (deploy apps, read secrets, manipulate Wireguard peers, manage
# organisations). A leak grants the issuing principal's full Fly.io
# organisation scope: deploy arbitrary container images, modify
# networking, and rotate billing credentials.
# The revocation flow lives at fly.io/dashboard/<org>/tokens.


def test_secret_scanner_detects_flyio_macaroon_fm2(tmp_path: Path) -> None:
    """Fly.io macaroon (modern fm2_): ``FlyV1 fm2_<base64 body>``.

    Pre-fix: the literal space in ``FlyV1 `` and the underscore after
    ``fm2`` place the prefix outside the entropy fallback's
    contiguous-match span — only the body span after ``fm2_``
    matches as one finding, losing the Fly.io-specific issuer
    attribution that incident-response keys off.
    """
    file_path = tmp_path / "flyio_deploy.py"
    # Realistic synthetic Fly.io macaroon. Real tokens are 200+ chars
    # encoding multiple capability descriptions; this synthetic uses
    # 80 chars in the body which exceeds the detector's 50-char floor
    # while staying small enough to be readable.
    body = (
        "lJPECAAAAAAA"          # macaroon header
        "AbCdEfGhIjKlMnOpQrSt"  # 20 chars
        "UvWxYz01234567890123"  # 20 chars
        "_-AbCdEfGhIjKlMnOpQr"  # 20 chars
        "StUvWx"                # 6 chars (78 total) + 2 = 80
    ) + "_-"
    assert len(body) >= 50
    secret = f"FlyV1 fm2_{body}"
    file_path.write_text(
        f'FLY_API_TOKEN = "{secret}"', encoding="utf-8"
    )

    findings = scan_repository(tmp_path, paths=[file_path])

    assert findings, "Should detect Fly.io API Token"
    reasons = [f.reason for f in findings]
    assert "Fly.io API Token gefunden" in reasons, (
        f"Expected Fly.io-specific attribution, got reasons: "
        f"{reasons}. Fly.io tokens grant full organisation scope, "
        "letting attackers deploy arbitrary container images that "
        "exfiltrate every secret. Precise attribution accelerates "
        "revocation at fly.io/dashboard/<org>/tokens."
    )


def test_secret_scanner_detects_flyio_macaroon_fm1(tmp_path: Path) -> None:
    """Fly.io macaroon (legacy fm1_): ``FlyV1 fm1_<base64 body>``.

    The legacy fm1_ macaroon format is still accepted by the Fly.io
    API; tokens issued before the fm2_ rollout remain valid until
    explicitly revoked. The detector MUST cover both formats so a
    leaked legacy token gets the same issuer attribution.
    """
    file_path = tmp_path / "legacy_flyio.py"
    body = "ZyXwVuTsRqPoNmLkJiHgFeDcBaZyXwVuTsRqPoNmLkJiHgFeDcBa"  # 52 chars
    secret = f"FlyV1 fm1_{body}"
    file_path.write_text(f'TOKEN = "{secret}"', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    assert "Fly.io API Token gefunden" in reasons


def test_secret_scanner_detects_flyio_opaque_fo1(tmp_path: Path) -> None:
    """Fly.io opaque (oldest fo1_): ``FlyV1 fo1_<base64 body>``.

    The oldest fo1_ opaque-token format pre-dates the macaroon
    migration; tokens issued before the fm1_ macaroon rollout remain
    valid until explicitly revoked. The detector MUST cover this
    format too so a leaked legacy opaque token gets the same issuer
    attribution.
    """
    file_path = tmp_path / "ancient_flyio.py"
    body = "MnOpQrStUvWxYzAbCdEfGhIjKl0123456789MnOpQrStUvWxYz"  # 50 chars
    secret = f"FlyV1 fo1_{body}"
    file_path.write_text(f'TOKEN = "{secret}"', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    assert "Fly.io API Token gefunden" in reasons


def test_secret_scanner_does_not_flag_short_flyv1_prefix(tmp_path: Path) -> None:
    """Negative case: short ``FlyV1 fm2_`` strings MUST NOT match the
    Fly.io pattern. The strict 50+ char body length guard prevents
    collision with placeholders or accidental fragments."""
    file_path = tmp_path / "config.py"
    # 16-char body — far below canonical Fly.io macaroon length
    # (real tokens are 200+ chars).
    not_flyio = "FlyV1 fm2_abcdef0123456789"
    file_path.write_text(f'placeholder = "{not_flyio}"', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    assert "Fly.io API Token gefunden" not in reasons


def test_secret_scanner_does_not_flag_unrelated_flyv1_prefix(tmp_path: Path) -> None:
    """Negative case: bare ``FlyV1`` strings without the ``fm[12]_`` /
    ``fo1_`` body-prefix MUST NOT match. ``FlyV1`` could plausibly
    appear in unrelated documentation or version strings, so the
    pattern strictly requires the canonical token-body-prefix."""
    file_path = tmp_path / "config.py"
    not_flyio = "FlyV1 documentation_string_that_is_long_enough_to_exceed_50_chars_for_safety"
    file_path.write_text(f'doc = "{not_flyio}"', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    assert "Fly.io API Token gefunden" not in reasons


# ---------------------------------------------------------------------------
# Static-check: each new pattern must remain in _KNOWN_TOKENS
# ---------------------------------------------------------------------------


def test_known_tokens_round8_taxonomy() -> None:
    """Audit invariant: each Round-8 token class must remain in
    ``_KNOWN_TOKENS``.

    A future PR that drops one of these patterns silently re-opens the
    issuer-attribution gap that this round closes. This test pins the
    canonical set so any such regression fails at PR-review time.
    """
    repo_root = Path(__file__).resolve().parents[1]
    source = (repo_root / "src" / "utils" / "secret_scanner.py").read_text(
        encoding="utf-8"
    )

    expected_reasons = [
        # 2026-05-10 / Round 8 additions (this PR):
        "Render API Key gefunden",
        "Buildkite User Access Token gefunden",
        "Fly.io API Token gefunden",
    ]
    for reason in expected_reasons:
        assert reason in source, (
            f"src/utils/secret_scanner.py must register the {reason!r} "
            f"detector in _KNOWN_TOKENS. See "
            f"tests/test_sentinel_secret_scanner_drift_round8.py for the "
            f"per-issuer PoC and the rationale for each pattern."
        )
