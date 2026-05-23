"""Sentinel PoC: secret-scanner drift Round 10 — three additional GitLab
token issuer prefixes whose canonical format silently bypasses specific
attribution in the post-Round-9 ``_KNOWN_TOKENS`` table.

The 2026-05-10 Round 9 closed New Relic
NRAK / NRRA / NRII and re-stated the prevention rule:

> "Every audit round that adds a new issuer MUST also enumerate THREE
> adjacent sub-landscapes the round did NOT cover."

Round 9 enumerated **observability tier** (closed New Relic), and
named **Datadog**, **PagerDuty**, **Honeycomb** as deferred bucket-(b)
candidates (no canonical prefix). Re-running the issuer-prefix audit
against the **GitLab token family** — currently covered only for
Personal Access Tokens (``glpat-``) and Pipeline Trigger Tokens
(``glptt-``) — surfaced three additional GitLab prefixes whose
canonical formats silently bypass specific attribution.

GitLab's token-prefix taxonomy expanded substantially between the
14.0 / 15.0 / 16.0 releases (the platform-side defence shape
introduced unique prefixes for every issuer family — Personal Access
Token, Pipeline Trigger Token, Runner Authentication Token, Deploy
Token, Cluster Agent Connection Token, CI Build Token, Feed Token,
Incoming Mail Token, SCIM OAuth Access Token). The existing scanner
covers two of the nine issuer families; this round closes the
**CI/CD infrastructure tier** sibling that the Round-9 GitLab
sub-landscape named-but-deferred:

  1. **GitLab Runner Authentication Token** (``glrt-<20+ chars>``) —
     issued via project / group / instance Runner registration in
     GitLab 15.6+ (the post-16.0 default replacing the legacy
     registration-token shape that was unprefixed). Format mirrors
     ``glpat-``: 5-char prefix + 20-char ``[A-Za-z0-9_-]`` body.
     The ``glrt-`` prefix is unambiguous (no other major issuer
     uses it), and the body lies entirely inside the entropy
     fallback's ``[A-Za-z0-9+/=_-]`` alphabet — so the entropy
     regex matches the full ``glrt-<body>`` span as one generic
     ``Hochentropischer Token-String`` finding, losing the
     GitLab-Runner-specific issuer attribution that incident-
     response keys off. A leak grants whoever holds the token the
     ability to **register a rogue GitLab Runner** against the
     issuing project / group / instance scope: the runner
     subsequently drains the CI job queue, and every CI job (with
     whatever build secrets the pipeline exposes — DEPLOYMENT_KEY,
     CONTAINER_REGISTRY_PASSWORD, every ``protected_branches``-
     scoped variable) is delivered to attacker-controlled
     hardware. Blast radius = the entire CI estate's job-execution
     surface — the highest leak surface in the GitLab CI/CD
     stack, structurally identical to the Buildkite Agent Token
     (``bkat_``) covered in Round 7. The revocation flow lives at
     gitlab.com/<scope>/-/runners and is distinct from any other
     vendor's, so issuer-specific attribution accelerates IR
     triage.

  2. **GitLab Deploy Token** (``gldt-<20+ chars>``) — issued via
     project / group settings > Repository > Deploy Tokens in
     GitLab 16.0+ (the post-16.0 default with prefix; pre-16.0
     deploy tokens were unprefixed and fall into the permanent
     bucket-(b) shape). Format mirrors ``glpat-``: 5-char prefix
     + 20-char ``[A-Za-z0-9_-]`` body. The ``gldt-`` prefix is
     unambiguous, and the body lies entirely inside the entropy
     fallback's alphabet — same generic-only attribution gap as
     the ``glrt-`` case. A leak grants the issuing scope's
     **Deploy Token capabilities**: read/write Container
     Registry images, read/write Package Registry artefacts,
     and (for the ``write_repository`` scope) push to protected
     branches. The Container Registry surface is especially
     dangerous: an attacker who can push a tampered image to
     the project's registry persists their compromise across
     every downstream deployment that pulls the image, bypassing
     the source-repository security gate entirely. The
     revocation flow lives at gitlab.com/<project>/-/settings/
     repository#js-deploy-tokens and is distinct from any other
     vendor's.

  3. **GitLab Cluster Agent for Kubernetes Token**
     (``glagent-<50+ chars>``) — issued via project / group
     settings > Operate > Kubernetes clusters > GitLab Agent
     in GitLab 14.0+ for registering a GitLab Agent for
     Kubernetes inside a target cluster. Format diverges from
     the ``glpat-`` family: 8-char prefix + ~50 char body (the
     body is longer because the registered Agent uses the
     token for GraphQL-level mTLS handshake metadata and the
     extra entropy is needed for the agent's identity
     fingerprint). The ``glagent-`` prefix is unambiguous, and
     the body lies entirely inside the entropy fallback's
     alphabet — same generic-only attribution gap as the
     ``glrt-`` / ``gldt-`` cases. A leak grants whoever holds
     the token the ability to **register a rogue GitLab Agent
     for Kubernetes** against the issuing scope: the agent
     subsequently runs ``kubectl`` commands inside the target
     cluster (via the configured impersonation account) and
     reads / mutates every Kubernetes resource the agent's
     RBAC binding permits. Blast radius = the entire connected
     cluster's resource surface — the highest leak surface in
     the GitLab GitOps stack, structurally analogous to the
     Buildkite / GitLab Runner registration tokens but acting
     at the in-cluster orchestrator boundary rather than the
     CI runner boundary. The revocation flow lives at
     gitlab.com/<project>/-/settings/cluster_agents and is
     distinct from every other vendor's.

Each test below pre-fix would have flagged only the generic
high-entropy fallback for the body span after the prefix; post-fix
every token gets the issuer-specific reason that incident-response
playbooks key off.

Closing checklist: Round 10 closes the three named-and-canonical-
prefixed GitLab entries (glrt-, gldt-, glagent-). The next round can
pick up:

* **GitLab developer-tooling continued** — Feed Token (``glft-``,
  20-char body), Incoming Mail Token (``glimt-``, 25-char body),
  CI Build Token (``glcbt-<digits>_<body>``, structured shape).
  Lower blast radius than the CI/CD infrastructure tier closed in
  this round (personal RSS feed access, inbound mail relay, single
  CI job context) but still distinct from the generic high-entropy
  fallback's attribution.
* **GitLab developer-tooling continued** — SCIM OAuth Access Token
  (``glsoat-<20+>``) for SSO-provisioning APIs.
* **CI/CD execution tier sibling** — CircleCI Personal API Tokens
  (``CCIPAT_<32+>``) which Round 7/8 deferred.
"""

from __future__ import annotations

from pathlib import Path

from src.utils.secret_scanner import scan_repository


# ---------------------------------------------------------------------------
# 1. GitLab Runner Authentication Token (glrt-)
# ---------------------------------------------------------------------------
#
# Format: ``glrt-<20 chars from [A-Za-z0-9_-]>``. Issued via project /
# group / instance Runner registration. Same blast radius as
# Buildkite Agent Token (``bkat_``, Round 7) — register a rogue
# runner, drain the CI job queue, exfiltrate every protected-branch
# CI secret.


def test_secret_scanner_detects_gitlab_runner_authentication_token(
    tmp_path: Path,
) -> None:
    """GitLab Runner Authentication Token: ``glrt-<20 chars>``.

    Pre-fix the entropy fallback ``[A-Za-z0-9+/=_-]{24,}`` matches
    the full ``glrt-<body>`` span (the dash and alphanumeric body
    are inside the alphabet) and reports
    ``Hochentropischer Token-String`` — losing the GitLab-Runner-
    specific attribution that incident-response keys off.
    """
    file_path = tmp_path / "gitlab_runner_config.py"
    # Synthetic glrt- shape: 5-char prefix + 20-char body. All-zeros
    # body keeps the fixture structurally invalid as a real GitLab
    # Runner token (real tokens carry non-trivial entropy and a
    # service-side fingerprint) while still matching the regex
    # under test. Mirrors the New-Relic Round-9 fixture rationale:
    # safely committable past push-time secret-scanning gates that
    # look for realistic key shapes.
    body = "0" * 20
    secret = f"glrt-{body}"
    assert len(body) == 20
    file_path.write_text(
        f'GITLAB_RUNNER_TOKEN = "{secret}"', encoding="utf-8"
    )

    findings = scan_repository(tmp_path, paths=[file_path])

    assert findings, "Should detect GitLab Runner Authentication Token"
    reasons = [f.reason for f in findings]
    assert "GitLab Runner Authentication Token gefunden" in reasons, (
        f"Expected GitLab-Runner-specific attribution, got reasons: "
        f"{reasons}. GitLab Runner tokens grant rogue-runner "
        "registration capability — blast radius is the entire CI "
        "estate's job-execution surface; precise attribution "
        "accelerates revocation at gitlab.com/<scope>/-/runners."
    )
    # Ensure raw secret never appears in findings (redaction).
    assert secret not in [f.match for f in findings]


def test_gitlab_runner_token_detected_in_env_config(tmp_path: Path) -> None:
    """GitLab Runner tokens appear in ``.env`` / shell-rc files when
    the runner registration is wired through CI bootstrap scripts;
    the detector must work in unquoted ``KEY=VALUE`` shapes."""
    file_path = tmp_path / "runner.env"
    body = "AbCdEf0123456789xYz_"
    secret = f"glrt-{body}"
    assert len(body) == 20
    file_path.write_text(f"CI_SERVER_TOKEN={secret}\n", encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    assert "GitLab Runner Authentication Token gefunden" in reasons, (
        "GitLab Runner detector must flag tokens in unquoted KEY=VALUE shapes"
    )


def test_gitlab_runner_token_does_not_flag_short_glrt_prefix(
    tmp_path: Path,
) -> None:
    """Negative case: short ``glrt-`` strings (e.g. operator-named
    placeholders, accidentally-truncated tokens) MUST NOT match the
    GitLab Runner pattern. The strict 20-char body length guard
    prevents collision with shorter identifiers."""
    file_path = tmp_path / "config.py"
    # 5-char body — far too short to be a real GitLab Runner token.
    not_glrt = "glrt-abc12"
    file_path.write_text(f'placeholder = "{not_glrt}"', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    assert "GitLab Runner Authentication Token gefunden" not in reasons


# ---------------------------------------------------------------------------
# 2. GitLab Deploy Token (gldt-)
# ---------------------------------------------------------------------------
#
# Format: ``gldt-<20 chars from [A-Za-z0-9_-]>``. Issued via project /
# group Repository settings > Deploy Tokens in GitLab 16.0+. A leak
# grants Container Registry / Package Registry read/write access
# (and write_repository for repo-write deploy tokens), so a tampered
# image pushed via a leaked deploy token persists across every
# downstream pull that follows.


def test_secret_scanner_detects_gitlab_deploy_token(tmp_path: Path) -> None:
    """GitLab Deploy Token: ``gldt-<20 chars>``.

    Pre-fix the entropy fallback flagged ``gldt-<body>`` as a
    generic ``Hochentropischer Token-String`` finding without
    preserving the GitLab-Deploy-Token-specific issuer attribution
    that incident-response keys off. Post-fix the specific pattern
    attributes the leak to a GitLab Deploy Token.
    """
    file_path = tmp_path / "gitlab_deploy_config.py"
    body = "0" * 20
    secret = f"gldt-{body}"
    assert len(body) == 20
    file_path.write_text(
        f'GITLAB_DEPLOY_TOKEN = "{secret}"', encoding="utf-8"
    )

    findings = scan_repository(tmp_path, paths=[file_path])

    assert findings, "Should detect GitLab Deploy Token"
    reasons = [f.reason for f in findings]
    assert "GitLab Deploy Token gefunden" in reasons, (
        f"Expected 'GitLab Deploy Token gefunden' in reasons; got "
        f"{reasons}. GitLab Deploy Tokens grant Container Registry / "
        "Package Registry access; precise attribution accelerates "
        "revocation at gitlab.com/<project>/-/settings/repository."
    )
    assert secret not in [f.match for f in findings]


def test_gitlab_deploy_token_does_not_flag_short_gldt_prefix(
    tmp_path: Path,
) -> None:
    """Negative case: short ``gldt-`` strings MUST NOT match the
    GitLab Deploy Token pattern."""
    file_path = tmp_path / "config.py"
    not_gldt = "gldt-abc12"
    file_path.write_text(f'placeholder = "{not_gldt}"', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    assert "GitLab Deploy Token gefunden" not in reasons


# ---------------------------------------------------------------------------
# 3. GitLab Cluster Agent for Kubernetes Token (glagent-)
# ---------------------------------------------------------------------------
#
# Format: ``glagent-<50+ chars from [A-Za-z0-9_-]>``. Issued via
# project / group Operate > Kubernetes clusters > GitLab Agent in
# GitLab 14.0+. The 50-char body lower bound is longer than the
# ``glrt-`` / ``gldt-`` 20-char shapes because the Agent uses the
# token for GraphQL-level mTLS handshake metadata and the extra
# entropy is needed for the agent's identity fingerprint. A leak
# grants whoever holds the token the ability to register a rogue
# Agent for Kubernetes against the issuing scope; the agent
# subsequently runs ``kubectl`` commands inside the target cluster
# under the configured impersonation account.


def test_secret_scanner_detects_gitlab_cluster_agent_token(
    tmp_path: Path,
) -> None:
    """GitLab Cluster Agent for Kubernetes Token:
    ``glagent-<50+ chars>``.

    Pre-fix the entropy fallback matched the full ``glagent-<body>``
    span as a generic ``Hochentropischer Token-String`` finding,
    losing the GitLab-Agent-for-Kubernetes-specific attribution.
    Post-fix the specific pattern attributes the leak to the Agent
    family.
    """
    file_path = tmp_path / "gitlab_agent_config.py"
    body = "0" * 50
    secret = f"glagent-{body}"
    assert len(body) == 50
    file_path.write_text(
        f'GITLAB_AGENT_TOKEN = "{secret}"', encoding="utf-8"
    )

    findings = scan_repository(tmp_path, paths=[file_path])

    assert findings, "Should detect GitLab Cluster Agent Token"
    reasons = [f.reason for f in findings]
    assert "GitLab Cluster Agent Token gefunden" in reasons, (
        f"Expected GitLab-Agent-for-Kubernetes-specific attribution, "
        f"got reasons: {reasons}. GitLab Agent tokens grant rogue-"
        "agent registration capability — blast radius is the entire "
        "connected cluster's resource surface; precise attribution "
        "accelerates revocation at gitlab.com/<project>/-/settings/"
        "cluster_agents."
    )
    assert secret not in [f.match for f in findings]


def test_gitlab_cluster_agent_token_does_not_flag_short_prefix(
    tmp_path: Path,
) -> None:
    """Negative case: short ``glagent-`` strings (e.g. variable
    names) MUST NOT match the GitLab Cluster Agent pattern. The
    50-char body lower bound prevents collision with operator-
    named identifiers (e.g. ``glagent-test`` as a Python variable
    name)."""
    file_path = tmp_path / "config.py"
    # 4-char body — far too short to be a real Agent token.
    not_glagent = "glagent-test"
    file_path.write_text(f'placeholder = "{not_glagent}"', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    assert "GitLab Cluster Agent Token gefunden" not in reasons


# ---------------------------------------------------------------------------
# 4. Boundary regression: the new patterns do not collide with the existing
#    ``glpat-`` / ``glptt-`` siblings.
# ---------------------------------------------------------------------------


def test_gitlab_token_family_patterns_are_mutually_exclusive(
    tmp_path: Path,
) -> None:
    """The five GitLab token prefixes (``glpat-``, ``glptt-``,
    ``glrt-``, ``gldt-``, ``glagent-``) share the ``gl`` ascender
    but differ at the third character onwards. Verify that a
    ``glpat-`` token is NOT attributed to ``glrt-`` / ``gldt-`` /
    ``glagent-`` (and vice versa).
    """
    file_path = tmp_path / "all_gitlab_tokens.py"
    glpat = "glpat-" + ("A" * 20)
    glrt = "glrt-" + ("B" * 20)
    gldt = "gldt-" + ("C" * 20)
    glagent = "glagent-" + ("D" * 50)
    file_path.write_text(
        f'PAT = "{glpat}"\nRT = "{glrt}"\nDT = "{gldt}"\nAG = "{glagent}"\n',
        encoding="utf-8",
    )

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]

    # Each token gets its own distinct attribution.
    assert "GitLab Personal Access Token gefunden" in reasons
    assert "GitLab Runner Authentication Token gefunden" in reasons
    assert "GitLab Deploy Token gefunden" in reasons
    assert "GitLab Cluster Agent Token gefunden" in reasons
