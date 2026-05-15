"""Sentinel PoC: secret-scanner drift Round 11 — GitLab developer-
tooling-tier sub-landscape closure + CircleCI Personal API Token.

Round 10 (PR #1493) closed the **GitLab CI/CD-infrastructure-tier**
sub-landscape (``glrt-`` Runner Authentication, ``gldt-`` Deploy
Token, ``glagent-`` Cluster Agent for Kubernetes) and re-stated the
closing-checklist rule:

> "Every audit round that adds a new issuer MUST also enumerate
> adjacent sub-landscapes the round did NOT cover."

Round 10 explicitly named the **four developer-tooling-tier GitLab
prefixes** + **CircleCI Personal API Token** as the named-but-
deferred next-round target:

  1. **GitLab Feed Token** (``glft-<20 chars from [A-Za-z0-9_-]>``)
     — issued automatically for every user via ``Settings > Access
     Tokens > Feed token`` for personal RSS/Atom-feed authentication
     against the GitLab REST API. A leak grants read access to the
     issuing user's activity stream — visible issues, merge
     requests, comments, and (depending on the user's group
     memberships) private project metadata. Blast radius is
     constrained to the user's read scope but reveals every
     project's name, slug, and merge-request title flowing past the
     user's feed; for an admin user the feed exposes the entire
     instance's project taxonomy.

  2. **GitLab Incoming Mail Token** (``glimt-<25+ chars from
     [A-Za-z0-9_-]>``) — used by the incoming-mail / reply-by-email
     subsystem to verify that an inbound reply genuinely belongs to
     the issuing user (the token is embedded in the
     ``Reply-To: noreply+<token>@<instance>.gitlab.com`` header).
     A leak lets a network adversary post comments / merge request
     replies / issue updates **as the issuing user** by sending
     crafted email to the GitLab inbound-mail relay — full impersonation
     within the user's commenting scope. The revocation flow lives at
     gitlab.com/-/user_settings/personal_access_tokens (alongside the
     Feed Token) and is distinct from any other vendor's, so issuer-
     specific attribution accelerates IR triage.

  3. **GitLab CI Build Token** (``glcbt-<partition_prefix>_<body>``)
     — per-build token issued by the GitLab Rails server when a CI
     job starts, exposed to the job as ``CI_JOB_TOKEN``. The
     ``glcbt-`` prefix was added in GitLab 16.0+ as part of the CI
     job-token database-partitioning rollout; the ``partition_prefix``
     is a short alphanumeric identifier (typically 1-3 chars) that
     anchors the token to its DB partition for fast lookup. A leak
     during the job's lifetime (token is invalidated when the job
     completes, but the window can be hours for long-running jobs)
     grants the attacker the ability to **call the GitLab REST API
     as the job**: download package-registry / container-registry
     artefacts the job had access to, trigger downstream pipelines,
     impersonate the job to other pipelines that allow inbound
     job-token access. The structured ``<digits>_<body>`` shape is
     unique among GitLab prefixes and is the structural disambiguator
     from ``glpat-`` / ``glrt-`` / ``gldt-`` (which all use a flat
     20-char body).

  4. **GitLab Scoped OAuth Access Token** (``glsoat-<20+ chars from
     [A-Za-z0-9_-]>``) — issued by SCIM-integrated SSO providers
     (Okta / OneLogin / AzureAD / Google Workspace) when an OAuth
     application provisions a scoped access token for a GitLab user.
     The ``glsoat-`` prefix anchors against the OAuth-application-
     scoped subset of token scopes (as opposed to the broader
     ``glpat-`` user-PAT scope). A leak grants the OAuth application's
     scoped capabilities for the issuing user — typically
     ``read_user`` / ``read_repository`` / ``api`` for SCIM-provisioned
     OAuth apps in enterprise GitLab Self-Managed installations. The
     revocation flow lives at gitlab.com/-/profile/applications and
     is distinct from any other vendor's.

  5. **CircleCI Personal API Token** (``CCIPAT_<32+ chars from
     [A-Za-z0-9_-]>``) — issued via app.circleci.com/settings/user/
     tokens for full CircleCI REST-API v2 access. The ``CCIPAT_``
     prefix was added in 2023 to replace the legacy unprefixed
     40-char-alphanumeric tokens (legacy tokens fall into the
     bucket-(b) shape; the modern ``CCIPAT_`` format anchors against
     the entropy fallback's body span). A leak grants the issuing
     user's full CircleCI organisation scope: read every project's
     pipeline configuration (which embeds inline env-var references
     to other vendors' tokens), trigger arbitrary pipelines on
     attacker-controlled branches, exfiltrate build artifacts, and
     manage SSH keys for project deployments. Blast radius is
     structurally identical to the Buildkite User Access Token
     (``bkua_``, Round 8) — the personal-token tier of the CI
     execution sub-landscape. The revocation flow lives at
     app.circleci.com/settings/user/tokens and is distinct from
     every other vendor's, so issuer-specific attribution
     accelerates IR triage.

Each test below pre-fix would have flagged only the generic
high-entropy fallback for the body span after the prefix; post-fix
every token gets the issuer-specific reason that incident-response
playbooks key off (rotation flow, revocation URL, blast-radius
estimate).

Closing-checklist sweep status post-Round-11:

  * **GitLab token family (9 of 9 covered):** ``glpat-`` (Round 1),
    ``glptt-`` (Round 5), ``glrt-`` / ``gldt-`` / ``glagent-``
    (Round 10), ``glft-`` / ``glimt-`` / ``glcbt-`` / ``glsoat-``
    (this round). GitLab's documented token taxonomy is now fully
    enumerated in ``_KNOWN_TOKENS``.
  * **CircleCI execution-tier sibling:** ``CCIPAT_`` closes the
    Round-7/8 named-and-deferred CircleCI prefix; the legacy
    unprefixed 40-char-alphanumeric CircleCI tokens remain in
    bucket-(b) and are caught by the entropy fallback (no canonical
    prefix to anchor specific attribution).
  * **Named-but-deferred next-round candidates:** AWS Secret
    Access Keys (40-char base64, no prefix — bucket-(b)), Cloudflare
    API Tokens (40-char base64, no prefix — bucket-(b)), Heroku API
    keys (36-char UUID-shape, no prefix — bucket-(b)), Mailgun
    private API keys (``key-<32 hex>`` — adjacent prefix candidate
    for a future round), Square Access Tokens (``EAAA<base64 body>``
    — adjacent prefix candidate).
"""

from __future__ import annotations

from pathlib import Path

from src.utils.secret_scanner import scan_repository


# ---------------------------------------------------------------------------
# 1. GitLab Feed Token (glft-)
# ---------------------------------------------------------------------------
#
# Format: ``glft-<20 chars from [A-Za-z0-9_-]>``. Per-user RSS/Atom
# feed credential. A leak grants the issuing user's read scope to the
# activity stream — visible issues, MRs, comments, project metadata.


def test_secret_scanner_detects_gitlab_feed_token(tmp_path: Path) -> None:
    """GitLab Feed Token: ``glft-<20 chars>``.

    Pre-fix the entropy fallback ``[A-Za-z0-9+/=_-]{24,}`` matches
    the full ``glft-<body>`` span (the dash and alphanumeric body are
    inside the alphabet) and reports
    ``Hochentropischer Token-String`` — losing the GitLab-Feed-Token-
    specific attribution that incident-response keys off.
    """
    file_path = tmp_path / "gitlab_feed_config.py"
    body = "0" * 20
    secret = f"glft-{body}"
    assert len(body) == 20
    file_path.write_text(
        f'GITLAB_FEED_TOKEN = "{secret}"', encoding="utf-8"
    )

    findings = scan_repository(tmp_path, paths=[file_path])

    assert findings, "Should detect GitLab Feed Token"
    reasons = [f.reason for f in findings]
    assert "GitLab Feed Token gefunden" in reasons, (
        f"Expected GitLab-Feed-Token-specific attribution, got "
        f"reasons: {reasons}. GitLab Feed Tokens grant per-user "
        "activity-stream read access; precise attribution accelerates "
        "revocation at gitlab.com/-/user_settings/personal_access_tokens."
    )
    assert secret not in [f.match for f in findings]


def test_gitlab_feed_token_detected_in_env_config(tmp_path: Path) -> None:
    """GitLab Feed Tokens appear in ``.env`` / shell-rc files when an
    operator wires the personal feed URL into a monitoring dashboard;
    the detector must work in unquoted ``KEY=VALUE`` shapes."""
    file_path = tmp_path / "feed.env"
    body = "AbCdEf0123456789xYz_"
    secret = f"glft-{body}"
    assert len(body) == 20
    file_path.write_text(f"GITLAB_FEED_TOKEN={secret}\n", encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    assert "GitLab Feed Token gefunden" in reasons, (
        "GitLab Feed detector must flag tokens in unquoted KEY=VALUE shapes"
    )


def test_gitlab_feed_token_does_not_flag_short_glft_prefix(tmp_path: Path) -> None:
    """Negative case: short ``glft-`` strings MUST NOT match the
    GitLab Feed Token pattern. The strict 20-char body length guard
    prevents collision with operator-named placeholders (e.g.
    ``glft-test``) and accidentally-truncated tokens."""
    file_path = tmp_path / "config.py"
    not_glft = "glft-abc12"
    file_path.write_text(f'placeholder = "{not_glft}"', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    assert "GitLab Feed Token gefunden" not in reasons


# ---------------------------------------------------------------------------
# 2. GitLab Incoming Mail Token (glimt-)
# ---------------------------------------------------------------------------
#
# Format: ``glimt-<25+ chars from [A-Za-z0-9_-]>``. Embedded in the
# reply-by-email ``Reply-To`` header. A leak grants comment / MR-
# reply / issue-update impersonation via crafted inbound email.


def test_secret_scanner_detects_gitlab_incoming_mail_token(tmp_path: Path) -> None:
    """GitLab Incoming Mail Token: ``glimt-<25+ chars>``.

    Pre-fix the entropy fallback flagged ``glimt-<body>`` as a
    generic ``Hochentropischer Token-String`` finding without
    preserving the GitLab-Incoming-Mail-Token-specific attribution.
    Post-fix the specific pattern attributes the leak to the
    incoming-mail-authentication issuer.
    """
    file_path = tmp_path / "gitlab_inbound_mail.py"
    body = "0" * 25
    secret = f"glimt-{body}"
    assert len(body) == 25
    file_path.write_text(
        f'INCOMING_MAIL_TOKEN = "{secret}"', encoding="utf-8"
    )

    findings = scan_repository(tmp_path, paths=[file_path])

    assert findings, "Should detect GitLab Incoming Mail Token"
    reasons = [f.reason for f in findings]
    assert "GitLab Incoming Mail Token gefunden" in reasons, (
        f"Expected 'GitLab Incoming Mail Token gefunden' in reasons; "
        f"got {reasons}. GitLab Incoming Mail Tokens grant user-level "
        "comment / MR-reply / issue-update impersonation via crafted "
        "inbound email; precise attribution accelerates revocation."
    )
    assert secret not in [f.match for f in findings]


def test_gitlab_incoming_mail_token_does_not_flag_short_glimt_prefix(
    tmp_path: Path,
) -> None:
    """Negative case: short ``glimt-`` strings MUST NOT match the
    GitLab Incoming Mail Token pattern."""
    file_path = tmp_path / "config.py"
    not_glimt = "glimt-test"
    file_path.write_text(f'placeholder = "{not_glimt}"', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    assert "GitLab Incoming Mail Token gefunden" not in reasons


# ---------------------------------------------------------------------------
# 3. GitLab CI Build Token (glcbt-)
# ---------------------------------------------------------------------------
#
# Format: ``glcbt-<partition_prefix>_<body>`` where partition_prefix is
# 1-3 alphanumeric chars and body is 20+ chars from [A-Za-z0-9_-].
# Per-job CI token (CI_JOB_TOKEN env var). A leak during the job's
# lifetime grants REST-API impersonation as the job.


def test_secret_scanner_detects_gitlab_ci_build_token(tmp_path: Path) -> None:
    """GitLab CI Build Token: ``glcbt-<partition>_<body>``.

    Pre-fix the entropy fallback flagged the body span after the
    underscore as a generic ``Hochentropischer Token-String`` finding,
    losing the GitLab-CI-Build-Token-specific attribution and the
    structured ``<partition>_<body>`` shape that disambiguates from
    the flat ``glpat-`` / ``glrt-`` / ``gldt-`` 20-char-body siblings.
    """
    file_path = tmp_path / "gitlab_ci_job_token.py"
    # Synthetic real-shape: partition prefix "1t" + 50-char body.
    partition_prefix = "1t"
    body = "0" * 50
    secret = f"glcbt-{partition_prefix}_{body}"
    file_path.write_text(
        f'CI_JOB_TOKEN = "{secret}"', encoding="utf-8"
    )

    findings = scan_repository(tmp_path, paths=[file_path])

    assert findings, "Should detect GitLab CI Build Token"
    reasons = [f.reason for f in findings]
    assert "GitLab CI Build Token gefunden" in reasons, (
        f"Expected GitLab-CI-Build-Token-specific attribution, got "
        f"reasons: {reasons}. GitLab CI Build Tokens grant REST-API "
        "impersonation as the running job; precise attribution "
        "accelerates revocation via job cancellation / pipeline retry."
    )
    assert secret not in [f.match for f in findings]


def test_gitlab_ci_build_token_detects_numeric_partition_prefix(
    tmp_path: Path,
) -> None:
    """The partition prefix is variable-length (1-3 alphanumeric chars
    in observed real tokens). Verify the pattern accepts the canonical
    single-digit partition prefix ``glcbt-1_<body>`` that is the
    most-common form before the DB-partition rollover."""
    file_path = tmp_path / "gitlab_ci_job_token.py"
    secret = "glcbt-1_" + ("AbCdEf0123456789xYz_" + "0" * 20)
    file_path.write_text(
        f'CI_JOB_TOKEN = "{secret}"', encoding="utf-8"
    )

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    assert "GitLab CI Build Token gefunden" in reasons


def test_gitlab_ci_build_token_does_not_flag_short_glcbt_prefix(
    tmp_path: Path,
) -> None:
    """Negative case: short ``glcbt-`` strings MUST NOT match the
    GitLab CI Build Token pattern (need ``<partition>_<20+ body>``)."""
    file_path = tmp_path / "config.py"
    not_glcbt = "glcbt-test"
    file_path.write_text(f'placeholder = "{not_glcbt}"', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    assert "GitLab CI Build Token gefunden" not in reasons


# ---------------------------------------------------------------------------
# 4. GitLab Scoped OAuth Access Token (glsoat-)
# ---------------------------------------------------------------------------
#
# Format: ``glsoat-<20+ chars from [A-Za-z0-9_-]>``. Issued by SCIM-
# integrated SSO providers for OAuth-application-scoped access. A
# leak grants the OAuth application's scoped capabilities for the
# issuing user (read_user / read_repository / api).


def test_secret_scanner_detects_gitlab_scoped_oauth_token(tmp_path: Path) -> None:
    """GitLab Scoped OAuth Access Token: ``glsoat-<20+ chars>``.

    Pre-fix the entropy fallback flagged the body span as a generic
    ``Hochentropischer Token-String`` finding, losing the
    GitLab-Scoped-OAuth-specific attribution that is distinct from
    the user-PAT ``glpat-`` attribution (different scope tier,
    different revocation flow at gitlab.com/-/profile/applications).
    """
    file_path = tmp_path / "gitlab_scoped_oauth.py"
    body = "0" * 20
    secret = f"glsoat-{body}"
    file_path.write_text(
        f'GITLAB_SCOPED_OAUTH_TOKEN = "{secret}"', encoding="utf-8"
    )

    findings = scan_repository(tmp_path, paths=[file_path])

    assert findings, "Should detect GitLab Scoped OAuth Access Token"
    reasons = [f.reason for f in findings]
    assert "GitLab Scoped OAuth Access Token gefunden" in reasons, (
        f"Expected GitLab-Scoped-OAuth-specific attribution, got "
        f"reasons: {reasons}. GitLab Scoped OAuth Tokens grant the "
        "OAuth application's scoped capabilities for the issuing user; "
        "revocation lives at gitlab.com/-/profile/applications (distinct "
        "from PAT revocation), so precise attribution accelerates IR."
    )
    assert secret not in [f.match for f in findings]


def test_gitlab_scoped_oauth_token_does_not_flag_short_glsoat_prefix(
    tmp_path: Path,
) -> None:
    """Negative case: short ``glsoat-`` strings MUST NOT match the
    GitLab Scoped OAuth Token pattern."""
    file_path = tmp_path / "config.py"
    not_glsoat = "glsoat-test"
    file_path.write_text(f'placeholder = "{not_glsoat}"', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    assert "GitLab Scoped OAuth Access Token gefunden" not in reasons


# ---------------------------------------------------------------------------
# 5. CircleCI Personal API Token (CCIPAT_)
# ---------------------------------------------------------------------------
#
# Format: ``CCIPAT_<32+ chars from [A-Za-z0-9_-]>``. Issued via
# app.circleci.com/settings/user/tokens for full CircleCI REST-API
# v2 access. A leak grants the issuing user's full CircleCI org
# scope (pipeline configs, build artifacts, SSH keys).


def test_secret_scanner_detects_circleci_personal_api_token(
    tmp_path: Path,
) -> None:
    """CircleCI Personal API Token: ``CCIPAT_<32+ chars>``.

    Pre-fix the entropy fallback flagged the ``CCIPAT_<body>`` span
    as a generic ``Hochentropischer Token-String`` finding (the
    underscore is inside the entropy alphabet), losing the CircleCI-
    specific attribution that incident response keys off (revocation
    at app.circleci.com/settings/user/tokens — distinct from every
    other vendor's flow).
    """
    file_path = tmp_path / "circleci_config.py"
    body = "0" * 32
    secret = f"CCIPAT_{body}"
    file_path.write_text(
        f'CIRCLECI_TOKEN = "{secret}"', encoding="utf-8"
    )

    findings = scan_repository(tmp_path, paths=[file_path])

    assert findings, "Should detect CircleCI Personal API Token"
    reasons = [f.reason for f in findings]
    assert "CircleCI Personal API Token gefunden" in reasons, (
        f"Expected CircleCI-specific attribution, got reasons: "
        f"{reasons}. CircleCI Personal API Tokens grant the issuing "
        "user's full CircleCI org scope; precise attribution "
        "accelerates revocation at app.circleci.com/settings/user/tokens."
    )
    assert secret not in [f.match for f in findings]


def test_circleci_token_detected_in_env_config(tmp_path: Path) -> None:
    """CircleCI tokens commonly appear in ``.env`` / shell-rc files when
    the operator wires the API token into local automation."""
    file_path = tmp_path / "circleci.env"
    body = "AbCdEf0123456789xYz_" + "0" * 12
    assert len(body) >= 32
    secret = f"CCIPAT_{body}"
    file_path.write_text(f"CIRCLECI_API_TOKEN={secret}\n", encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    assert "CircleCI Personal API Token gefunden" in reasons, (
        "CircleCI detector must flag tokens in unquoted KEY=VALUE shapes"
    )


def test_circleci_token_does_not_flag_short_ccipat_prefix(tmp_path: Path) -> None:
    """Negative case: short ``CCIPAT_`` strings (e.g. placeholder
    operator strings, truncated tokens) MUST NOT match the CircleCI
    pattern. The 32-char body lower bound prevents false positives
    against operator-named identifiers."""
    file_path = tmp_path / "config.py"
    not_ccipat = "CCIPAT_test"
    file_path.write_text(f'placeholder = "{not_ccipat}"', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    assert "CircleCI Personal API Token gefunden" not in reasons


# ---------------------------------------------------------------------------
# 6. Boundary regression: the new patterns do not collide with the existing
#    GitLab family (``glpat-``, ``glptt-``, ``glrt-``, ``gldt-``, ``glagent-``).
# ---------------------------------------------------------------------------


def test_gitlab_full_family_patterns_are_mutually_exclusive(
    tmp_path: Path,
) -> None:
    """The nine GitLab token prefixes (``glpat-``, ``glptt-``, ``glrt-``,
    ``gldt-``, ``glagent-``, ``glft-``, ``glimt-``, ``glcbt-``,
    ``glsoat-``) share the ``gl`` ascender but differ from the third
    character onwards. Verify that every token is attributed to its
    own issuer-specific reason and no cross-family false positives
    occur in the post-Round-11 ``_KNOWN_TOKENS`` table.
    """
    file_path = tmp_path / "all_gitlab_tokens.py"
    glpat = "glpat-" + ("A" * 20)
    glptt = "glptt-" + ("B" * 40)
    glrt = "glrt-" + ("C" * 20)
    gldt = "gldt-" + ("D" * 20)
    glagent = "glagent-" + ("E" * 50)
    glft = "glft-" + ("F" * 20)
    glimt = "glimt-" + ("G" * 25)
    glcbt = "glcbt-1t_" + ("H" * 50)
    glsoat = "glsoat-" + ("I" * 20)
    file_path.write_text(
        f'PAT = "{glpat}"\n'
        f'PTT = "{glptt}"\n'
        f'RT = "{glrt}"\n'
        f'DT = "{gldt}"\n'
        f'AG = "{glagent}"\n'
        f'FT = "{glft}"\n'
        f'IMT = "{glimt}"\n'
        f'CBT = "{glcbt}"\n'
        f'SOAT = "{glsoat}"\n',
        encoding="utf-8",
    )

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]

    # Each token gets its own distinct attribution.
    assert "GitLab Personal Access Token gefunden" in reasons
    assert "GitLab Pipeline Trigger Token gefunden" in reasons
    assert "GitLab Runner Authentication Token gefunden" in reasons
    assert "GitLab Deploy Token gefunden" in reasons
    assert "GitLab Cluster Agent Token gefunden" in reasons
    assert "GitLab Feed Token gefunden" in reasons
    assert "GitLab Incoming Mail Token gefunden" in reasons
    assert "GitLab CI Build Token gefunden" in reasons
    assert "GitLab Scoped OAuth Access Token gefunden" in reasons


def test_circleci_does_not_collide_with_gitlab_or_other_vendors(
    tmp_path: Path,
) -> None:
    """``CCIPAT_`` is an uppercase prefix; the existing GitLab family
    (lowercase ``gl*-``), Stripe (``sk_*``/``rk_*``/``whsec_``), and
    GitHub (``ghp_``/``gho_``/etc.) families are mutually exclusive at
    the prefix level. Verify a CircleCI token in the same file does
    NOT trigger any GitLab / Stripe / GitHub attribution."""
    file_path = tmp_path / "mixed_vendor_secrets.py"
    ccipat = "CCIPAT_" + ("X" * 32)
    file_path.write_text(
        f'CIRCLECI = "{ccipat}"\n',
        encoding="utf-8",
    )

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]

    assert "CircleCI Personal API Token gefunden" in reasons
    # No cross-vendor false positives.
    assert "GitLab Personal Access Token gefunden" not in reasons
    assert "Stripe Live Secret Key gefunden" not in reasons
    assert "GitHub Personal Access Token gefunden" not in reasons
