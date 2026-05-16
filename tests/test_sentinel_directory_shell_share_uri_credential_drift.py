r"""Sentinel PoC: silent-undetection drift for **directory / shell /
file-share** connection strings with embedded credentials
(``<scheme>://<user>:<pass>@<host>``).

Pre-fix every LDAP / LDAPS / SSH / SFTP / SMB / CIFS URI with
credentials in the canonical ``<scheme>://<user>:<password>@<host>``
shape — covering Active-Directory bind URIs, remote-shell deployment
URIs, secure file-transfer URIs, and Windows / SMB file-share URIs,
plus the JDBC-prefixed variants used by Java JNDI consumers — was
**SILENTLY UNDETECTED ENTIRELY** by the secret scanner across BOTH
detection branches.

This is the strict-sibling drift of the 2026-05-16 Database Connection
String secret-scanner round (which covered PostgreSQL / MySQL /
MariaDB / MongoDB(+srv) / Redis / AMQP(S) / Kafka / ClickHouse /
Cassandra / ElasticSearch / SMTP(S)) and the 2026-05-16 Log
Sanitisation round (``src/utils/http.py:_URL_AUTH_RE`` and
``src/utils/logging.py``'s "Basic Auth in malformed credentialled
URIs" pattern), both of which were **already extended** to cover the
LDAP / LDAPS / SSH / SFTP / SMB / CIFS adjacent families. The
SCANNER detection codepath was the third sibling left out of step.

Failure modes (mirrors the Database round):

1. **Entropy fallback** (``_HIGH_ENTROPY_RE``): the body alphabet
   ``[A-Za-z0-9+/=_-]`` excludes ``:``, ``/``, ``@``, so the entropy
   matcher splits at every URI delimiter. The resulting fragments
   (``ldap``, ``admin``, ``supersecret``, ``dc01.example.com``,
   ``389``, ``dc=example,dc=com``) are each below the 24-char floor,
   so no entropy finding fires.

2. **Assignment heuristic** (``_SENSITIVE_ASSIGN_RE``): even with a
   sensitive variable name (``LDAP_BIND_URL``, ``SSH_DEPLOY``,
   ``SMB_SHARE_URL``), the unquoted-value branch SKIPS values
   containing ``:`` (port / user-pass separator). Every URI
   contains ``://`` AND ``:`` — the check rejects the URI verbatim.

Combined effect: a committed ``.env`` file with
``LDAP_BIND_URL=ldap://admin:CompanyAdmin2025@dc01.corp.example.com:389/``
ships to production with NO scanner alert at any stage. The log
sanitization path WOULD mask the credential later in operator logs
(closing one sink), but the source-of-truth committed file leaks
the credential verbatim into git history forever.

Threat model (why this drift is higher-severity than Database):
--------------------------------------------------------------

These credentials grant access to **infrastructure-control planes**,
not just data planes:

* **LDAP / LDAPS bind credentials** — Active-Directory service-account
  passwords. A leak grants: enumerate the entire AD forest, read every
  user object (and ``unicodePwd`` history attribute on poorly-locked
  schemas), join attacker-controlled machines to the domain, reset
  privileged accounts, and — if the service account has Replicate-
  Directory-Changes — perform DCSync to extract every krbtgt /
  computer-account hash. Highest-severity credential class in any
  enterprise environment.

* **SSH credentials in URIs** — interactive shell on the target host.
  Common in deployment scripts (Ansible inventory, Capistrano
  ``deploy.rb``, Fabric ``fabfile.py``, GitLab CI ``deploy_keys``),
  GitHub Actions secrets, Dockerfile ``RUN ssh`` patterns. A leak
  grants persistent shell access — the universal post-exploitation
  primitive (install backdoors, exfiltrate filesystem, pivot to
  internal network segments).

* **SFTP credentials** — same as SSH but typically scoped to a chroot
  with file read/write. Common for backup uploads, partner data
  exchange, CI artefact storage. A leak grants exfiltration of every
  file in the chroot (often including database backups containing
  hashed passwords, config files, customer data).

* **SMB / CIFS credentials** — Windows file-share access. Corporate
  file servers routinely store: HR documents (NDAs, payroll, employee
  PII), financial reports, executive correspondence, source code
  backups, and developer machines' roaming profiles (containing
  cached AD credentials). A leak grants read/write to whatever the
  share's ACL permits.

Real-world emission patterns
----------------------------

* ``.env`` files with ``LDAP_BIND_URL=ldap://svc-acct:pw@dc01/``
* Ansible inventory: ``ansible_ssh_url: ssh://deploy:pw@host``
* GitLab CI ``deploy_keys``: ``SFTP_BACKUP_URL=sftp://user:pw@nas/``
* Java JNDI properties: ``java.naming.provider.url=ldap://...``
* Spring application.yml ``spring.ldap.urls`` with embedded credentials
* Mount fstab entries committed to dotfiles: ``smb://user:pw@srv/share``
* PowerShell deployment scripts hardcoding ``\\server\share`` UNC
  paths transcoded as ``smb://`` URIs in cross-platform tooling

Fix
---

Extend ``_KNOWN_TOKENS`` with a sibling regex matching the canonical
``[jdbc:]<scheme>://<user>:<pass>@<host>`` shape for the LDAP / SSH /
file-share family — mirroring the Database round but with distinct
attribution so incident-response triage routes to the correct
revocation flow (AD service-account password rotation vs. SSH key
rotation vs. file-server password reset are three distinct playbooks)::

    (
        re.compile(
            r"(?i)\\b(?:jdbc:)?"
            r"(?:ldap|ldaps|ssh|sftp|smb|cifs)"
            r"://[^@\\s/:]+:[^@\\s/]+@[^\\s/]+"
        ),
        "Directory/Shell/Share Connection String gefunden",
    ),

The schemes mirror exactly the 6 adjacent-family schemes already
present in ``src/utils/http.py:_URL_AUTH_RE`` and
``src/utils/logging.py``'s malformed-URI pattern — sibling-drift
closure aligning the third codepath to the canonical floor.

Marker: SENTINEL_DIRECTORY_SHELL_SHARE_URI_DRIFT.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.utils.secret_scanner import scan_repository


SENTINEL_DIRECTORY_SHELL_SHARE_URI_DRIFT = (
    "Directory/Shell/Share connection string credential silent-"
    "undetection drift: entropy fallback splits at URI delimiters AND "
    "assignment heuristic skips values containing ``:``; both branches "
    "produce zero findings for committed LDAP/SSH/SMB credentials "
    "even though the parallel log-sanitization codepath already "
    "covers the same scheme family."
)

_REASON = "Directory/Shell/Share Connection String gefunden"


# Realistic URIs per scheme (each contains plaintext-recoverable
# password embedded in the canonical URI shape). Passwords are
# distinct per scheme so masking violations would surface the wrong
# credential and be obvious.
_LDAP_URI = "ldap://admin:CompanyAdmin2025@dc01.corp.example.com:389/dc=example,dc=com"
_LDAPS_URI = "ldaps://bind_user:LdapsBindPw!@ldap.corp.local:636/"
_SSH_URI = "ssh://deploy:DeploySshPw2025@bastion.example.com:22"
_SFTP_URI = "sftp://ftpuser:SftpUploadPw!@backup.example.com/data"
_SMB_URI = "smb://shareuser:SmbSharePw2025@fileserver.example.com/share"
_CIFS_URI = "cifs://domain_user:CifsDomainPw!@nas.example.com/backup"
_JDBC_LDAP_URI = "jdbc:ldap://svc_account:JndiLdapPw!@ad.example.com:389/ou=users"


# ---------------------------------------------------------------------------
# (1) Per-scheme attribution PoCs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "uri,label",
    [
        (_LDAP_URI, "LDAP (Active Directory bind)"),
        (_LDAPS_URI, "LDAPS (TLS-wrapped LDAP)"),
        (_SSH_URI, "SSH (deploy shell)"),
        (_SFTP_URI, "SFTP (backup upload)"),
        (_SMB_URI, "SMB (Windows file share)"),
        (_CIFS_URI, "CIFS (legacy SMB)"),
        (_JDBC_LDAP_URI, "JDBC LDAP (Java JNDI)"),
    ],
)
def test_directory_shell_share_uri_with_credentials_detected(
    tmp_path: Path, uri: str, label: str
) -> None:
    """Every canonical LDAP/SSH/SMB/CIFS connection string with
    embedded credentials must be detected as ``Directory/Shell/Share
    Connection String gefunden``. Pre-fix every URI was SILENTLY
    UNDETECTED across both the entropy fallback (delimiter-split
    prevents 24-char floor match) AND the assignment heuristic
    (``:`` in value triggers skip).
    """
    file_path = tmp_path / ".env"
    file_path.write_text(f"CONNECTION_URL={uri}\n", encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])

    reasons = [f.reason for f in findings]
    assert _REASON in reasons, (
        f"{label}: URI did not yield Directory/Shell/Share Connection "
        f"String attribution; got reasons {reasons!r}. "
        f"({SENTINEL_DIRECTORY_SHELL_SHARE_URI_DRIFT})"
    )


# ---------------------------------------------------------------------------
# (2) Case-insensitive scheme matching (RFC 3986 §3.1)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "uri",
    [
        "LDAP://admin:pw@host:389/dc=example,dc=com",
        "Ldap://admin:pw@host:389/",
        "LDAPS://bind:pw@host:636/",
        "SSH://deploy:pw@host:22",
        "Sftp://user:pw@host/",
        "SMB://share:pw@host/share",
        "CIFS://user:pw@host/share",
    ],
)
def test_directory_shell_share_uri_case_insensitive_scheme(
    tmp_path: Path, uri: str
) -> None:
    """The scheme literal must match case-insensitively (URI schemes
    are case-insensitive per RFC 3986 §3.1)."""
    file_path = tmp_path / "config.env"
    file_path.write_text(f"URL={uri}\n", encoding="utf-8")
    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    assert _REASON in reasons, (
        f"Case-insensitive scheme {uri!r} did not match. "
        f"({SENTINEL_DIRECTORY_SHELL_SHARE_URI_DRIFT})"
    )


# ---------------------------------------------------------------------------
# (3) Negative cases: ensure the detector does NOT match benign URIs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        # No credentials in URI (benign)
        "ldap://localhost/dc=example,dc=com",
        "ldap://host.example.com:389/dc=example,dc=com",
        "ssh://host.example.com",
        "sftp://backup.example.com/",
        "smb://fileserver/share",
        "cifs://nas.example.com/share",
        # User-only, no password (unusual but not a password leak)
        "ssh://deploy@bastion.example.com",
        "sftp://user@host",
        "ldap://admin@dc01/",
        "git+ssh://user@github.com/repo.git",
        # Natural-language mentions
        "We use LDAP for directory lookups and SSH for deploys.",
        "Configure your SMB share and CIFS mount.",
        # Different scheme prefix (not in our list)
        "https://admin:pw@api.example.com/v1",  # Covered by URL auth path
        "git://user:pw@github.com/repo.git",  # git scheme not enumerated
        # Empty user or password (technically valid URI but not a
        # meaningful credential leak)
        "ldap://:password@host/",  # empty user
        "ssh://user:@host",  # empty password
    ],
)
def test_no_false_positive_on_benign_directory_shell_share_uris(
    tmp_path: Path, text: str
) -> None:
    """The detector must not flag credential-less URIs, user-only
    URIs, non-enumerated schemes, natural-language prose, or HTTP
    URIs (which have their own URL-credential detection path)."""
    file_path = tmp_path / "config.env"
    file_path.write_text(f"{text}\n", encoding="utf-8")
    findings = scan_repository(tmp_path, paths=[file_path])
    dir_findings = [f for f in findings if f.reason == _REASON]
    assert not dir_findings, (
        f"False-positive Directory/Shell/Share Connection String for "
        f"{text!r}; got findings {dir_findings!r}. "
        f"({SENTINEL_DIRECTORY_SHELL_SHARE_URI_DRIFT})"
    )


# ---------------------------------------------------------------------------
# (4) Bare URI without variable assignment context
# ---------------------------------------------------------------------------


def test_directory_shell_share_uri_in_comment(tmp_path: Path) -> None:
    """An LDAP URI in a README example must still be flagged — copy-
    paste docs are a routine real-world leak source."""
    file_path = tmp_path / "README.md"
    file_path.write_text(
        f"## LDAP Configuration\n\n"
        f"Set your bind URL to {_LDAP_URI}\n",
        encoding="utf-8",
    )
    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    assert _REASON in reasons, (
        f"URI in README/comment context not detected. "
        f"reasons={reasons!r}. "
        f"({SENTINEL_DIRECTORY_SHELL_SHARE_URI_DRIFT})"
    )


# ---------------------------------------------------------------------------
# (5) Multi-scheme config file PoC: realistic deployment ``.env`` with
#     multiple infrastructure credentials must yield separate findings.
# ---------------------------------------------------------------------------


def test_multi_scheme_env_file_yields_separate_findings(
    tmp_path: Path,
) -> None:
    """A real-world ``.env`` file with multiple LDAP/SSH/SMB URIs
    must flag each as a separate Directory/Shell/Share Connection
    String finding. The threat model: a single committed ``.env``
    leaks ALL embedded infrastructure credentials in one shot."""
    file_path = tmp_path / ".env.production"
    file_path.write_text(
        f"LDAP_BIND_URL={_LDAP_URI}\n"
        f"SSH_DEPLOY_URL={_SSH_URI}\n"
        f"SFTP_BACKUP_URL={_SFTP_URI}\n"
        f"SMB_SHARE_URL={_SMB_URI}\n",
        encoding="utf-8",
    )
    findings = scan_repository(tmp_path, paths=[file_path])
    dir_findings = [f for f in findings if f.reason == _REASON]
    assert len(dir_findings) >= 4, (
        f"Multi-scheme ``.env`` should yield at least 4 Directory/"
        f"Shell/Share Connection String findings; got "
        f"{len(dir_findings)}. All findings: {findings!r}. "
        f"({SENTINEL_DIRECTORY_SHELL_SHARE_URI_DRIFT})"
    )


# ---------------------------------------------------------------------------
# (6) Membership invariant
# ---------------------------------------------------------------------------


def test_directory_shell_share_uri_reason_in_known_tokens() -> None:
    """The Directory/Shell/Share Connection String reason must be in
    ``_KNOWN_TOKENS``."""
    from src.utils.secret_scanner import _KNOWN_TOKENS

    reasons = {reason for _regex, reason in _KNOWN_TOKENS}
    assert _REASON in reasons, (
        f"Directory/Shell/Share Connection String reason missing from "
        f"_KNOWN_TOKENS. ({SENTINEL_DIRECTORY_SHELL_SHARE_URI_DRIFT})"
    )


# ---------------------------------------------------------------------------
# (7) Sibling-floor invariant: scheme list must align with the
#     log-sanitization codepaths (src/utils/http.py:_URL_AUTH_RE and
#     src/utils/logging.py's malformed-URI pattern), which both
#     already enumerate this exact 6-scheme adjacent family.
# ---------------------------------------------------------------------------


def test_sibling_floor_alignment_with_log_sanitization() -> None:
    """All 6 schemes in the canonical log-sanitization path
    (ldap/ldaps/ssh/sftp/smb/cifs) MUST be matched by the secret
    scanner's directory/shell/share detector. Drift in either
    direction re-opens the silent-undetection gap."""
    file_path_template = (
        "URL={scheme}://user:password_distinctive_{scheme}@host/path"
    )
    from pathlib import Path as _P
    import tempfile

    schemes = ["ldap", "ldaps", "ssh", "sftp", "smb", "cifs"]
    for scheme in schemes:
        with tempfile.TemporaryDirectory() as td:
            base = _P(td)
            p = base / ".env"
            p.write_text(
                file_path_template.format(scheme=scheme) + "\n",
                encoding="utf-8",
            )
            findings = scan_repository(base, paths=[p])
            reasons = [f.reason for f in findings]
            assert _REASON in reasons, (
                f"Sibling-floor drift: scheme {scheme!r} present in "
                f"log-sanitization but missing from secret scanner. "
                f"reasons={reasons!r}. "
                f"({SENTINEL_DIRECTORY_SHELL_SHARE_URI_DRIFT})"
            )


# ---------------------------------------------------------------------------
# (8) Cross-detector regression: existing detectors continue to fire
# ---------------------------------------------------------------------------


def test_database_uri_detection_unchanged(tmp_path: Path) -> None:
    """Adding the directory/shell/share detector must NOT break the
    Database Connection String detector (sibling round)."""
    db_uri = "postgres://admin:dbsecret123@db.example.com:5432/prod"
    file_path = tmp_path / ".env"
    file_path.write_text(f"DATABASE_URL={db_uri}\n", encoding="utf-8")
    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    assert "Database Connection String gefunden" in reasons, (
        f"Regression: Database detector broke. reasons={reasons!r}. "
        f"({SENTINEL_DIRECTORY_SHELL_SHARE_URI_DRIFT})"
    )


def test_https_url_credentials_routed_to_url_auth_path(
    tmp_path: Path,
) -> None:
    """HTTPS URIs with embedded credentials must NOT match the
    Directory/Shell/Share detector (they have their own URL-auth
    detection path in the log-sanitization codepath; the scanner
    relies on the existing entropy / assignment branches)."""
    https_uri = "https://admin:CompanyHttpsPw2025@api.example.com/v1"
    file_path = tmp_path / "config.env"
    file_path.write_text(f"API_URL={https_uri}\n", encoding="utf-8")
    findings = scan_repository(tmp_path, paths=[file_path])
    dir_findings = [f for f in findings if f.reason == _REASON]
    assert not dir_findings, (
        f"False-positive: HTTPS URI matched Directory/Shell/Share "
        f"detector. findings={dir_findings!r}. "
        f"({SENTINEL_DIRECTORY_SHELL_SHARE_URI_DRIFT})"
    )


# ---------------------------------------------------------------------------
# (9) Masking contract: the raw URI (with embedded password) must
#     NEVER appear unmasked in finding output.
# ---------------------------------------------------------------------------


def test_directory_shell_share_uri_masking_contract(
    tmp_path: Path,
) -> None:
    """The raw URI password must NEVER appear unmasked in the
    finding's reported match — leaving it in CI logs / PR comments
    would defeat the purpose of detection."""
    file_path = tmp_path / ".env"
    file_path.write_text(
        f"LDAP_BIND_URL={_LDAP_URI}\n", encoding="utf-8"
    )
    findings = scan_repository(tmp_path, paths=[file_path])
    for finding in findings:
        if finding.reason == _REASON:
            assert "CompanyAdmin2025" not in finding.match, (
                f"Masking VIOLATED: raw password 'CompanyAdmin2025' "
                f"appears in finding.match={finding.match!r}. "
                f"({SENTINEL_DIRECTORY_SHELL_SHARE_URI_DRIFT})"
            )
