"""Automatic GitHub issue for a failed feed run (``FEED_GITHUB_*`` settings).

Moved verbatim out of ``src/feed/reporting.py`` (700-line rule, 2026-11-02);
import these names from ``src.feed.reporting``, which re-exports them.
``_GithubIssueReporter._build_body`` reads ``error_log_path`` from this
module, so a test that changes it for the issue body patches it here.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import requests

from .feed_health import _format_timestamp
from .logging import error_log_path
from .report_messages import _sanitize_log_detail

from ..utils.env import get_bool_env, read_secret
from ..utils.files import (
    _reject_non_finite_constant,
    _reject_non_finite_float,
)
from ..utils.http import request_safe, session_with_retries, validate_http_url
from ..utils.logging import sanitize_log_arg
from ..utils.text import escape_markdown, escape_markdown_cell, safe_markdown_codespan

if TYPE_CHECKING:
    from .reporting import RunReport

log = logging.getLogger("build_feed")

# GitHub's REST API rejects an issue body longer than 65 536 characters with
# a 422 ("Validation Failed: body is too long"). The auto-issue submitter is
# the project's *primary alert channel for failed feed runs*; if it goes
# silent, operators don't notice that builds are broken until the cached
# feed staleness becomes apparent (a much slower signal). Cap the rendered
# body to leave headroom for protocol overhead and the truncation marker
# itself.
_MAX_GITHUB_BODY_LENGTH = 60_000
_BODY_TRUNCATION_MARKER = (
    "\n\n— Body wegen GitHub-API-Limit gekürzt; siehe Logdatei für vollständige Details —\n"
)


def _bounded_github_body(body: str) -> str:
    """Truncate ``body`` to GitHub's per-issue body limit at a line boundary.

    Cutting mid-line could leave the rendered Markdown half-formed (a
    half-open table cell, an unterminated fenced code block) which ugly-
    fails GitHub's renderer. Truncating at the last newline before the
    cap keeps each line atomic, then appends a visible marker.
    """
    if len(body) <= _MAX_GITHUB_BODY_LENGTH:
        return body
    keep = _MAX_GITHUB_BODY_LENGTH - len(_BODY_TRUNCATION_MARKER)
    head = body[:keep]
    last_newline = head.rfind("\n")
    if last_newline > 0:
        head = head[:last_newline]
    return head + _BODY_TRUNCATION_MARKER


_GITHUB_ISSUE_URL_RE = re.compile(
    r"^https://[^/\s]+/[^/\s]+/[^/\s]+/issues/(\d+)$"
)


def _extract_github_issue_number(url: str | None) -> str:
    """Return the trailing issue number of a GitHub ``html_url``, else "".

    Used as a CodeQL clear-text-logging barrier: ``re.match`` + ``group(1)``
    is a recognised sanitiser, and the extracted value (digits, capped) is a
    public identifier that cannot embed credentials.
    """
    if not url:
        return ""
    match = _GITHUB_ISSUE_URL_RE.match(url)
    return match.group(1)[:32] if match else ""


def _split_csv(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    parts = [item.strip() for item in value.split(",")]
    return tuple(part for part in parts if part)


@dataclass(frozen=True)
class _GithubIssueConfig:
    enabled: bool
    repository: str | None
    token: str | None
    api_url: str
    labels: tuple[str, ...]
    assignees: tuple[str, ...]
    title_prefix: str

    @classmethod
    def from_env(cls) -> _GithubIssueConfig:
        enabled = get_bool_env("FEED_GITHUB_CREATE_ISSUES", False)
        repository = (
            os.getenv("FEED_GITHUB_REPOSITORY")
            or os.getenv("GITHUB_REPOSITORY")
            or None
        )
        token = read_secret("FEED_GITHUB_TOKEN") or read_secret("GITHUB_TOKEN") or None
        api_url = (
            os.getenv("FEED_GITHUB_API_URL")
            or os.getenv("GITHUB_API_URL")
            or "https://api.github.com"
        )
        labels = _split_csv(os.getenv("FEED_GITHUB_ISSUE_LABELS"))
        assignees = _split_csv(os.getenv("FEED_GITHUB_ISSUE_ASSIGNEES"))
        title_prefix = os.getenv("FEED_GITHUB_ISSUE_TITLE_PREFIX", "Fehlerbericht")
        return cls(
            enabled=enabled,
            repository=repository,
            token=token,
            api_url=api_url.rstrip("/"),
            labels=labels,
            assignees=assignees,
            title_prefix=title_prefix.strip() or "Fehlerbericht",
        )


# GitHub repository slug grammar: ``owner/name``. Owner: 1-39 alphanumeric/hyphen
# (cannot start with hyphen). Name: 1-100 chars allowing alphanumerics, hyphen,
# underscore, dot. Anything outside this lets ``FEED_GITHUB_REPOSITORY`` inject
# query strings, fragments or path traversals into the request URL — the host
# is already pinned to GitHub by ``_is_trusted_github_api`` but the path
# component still has authority over which endpoint receives the token.
_GITHUB_REPO_SLUG_RE = re.compile(
    r"\A[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9._-]{1,100}\Z"
)


def _is_valid_github_repo_slug(repo: str) -> bool:
    """Validate ``owner/name`` matches GitHub's published slug grammar."""
    return bool(_GITHUB_REPO_SLUG_RE.fullmatch(repo))


def _is_trusted_github_api(api_url: str) -> bool:
    """Return ``True`` only if ``api_url`` looks like a GitHub API endpoint.

    Defense-in-depth against the ``FEED_GITHUB_API_URL`` / ``GITHUB_API_URL``
    env vars being set to an attacker-controlled host (typo squat,
    CI-pipeline injection, mistaken Enterprise URL). Without this check
    ``submit()`` would happily POST the configured GitHub token to any
    syntactically-valid public URL — ``validate_http_url()`` only verifies
    SSRF/DNS-rebinding properties, not host identity.

    Allowed:
    - ``https://api.github.com`` (the public github.com REST API; exact host, no path)
    - ``https://<host>/api/v3`` or ``/api/graphql`` where ``<host>`` is in the
      operator-declared ``FEED_GITHUB_ENTERPRISE_HOSTS`` allowlist (GitHub
      Enterprise Server canonical bases). Without this allowlist the GHE
      branch would accept *any* host with a GHE-shaped path — letting an
      attacker who injects ``FEED_GITHUB_API_URL=https://evil.com/api/v3``
      receive the project's GitHub token. GHE hostnames are operator-chosen
      (no fixed pattern), so there is no safe fallback: an empty allowlist
      means the public ``api.github.com`` endpoint is the only trusted one.

    Anything else is rejected so the token never leaves the process.

    Security (HTTPS-only Validator Drift): the scheme is pinned to
    ``https``. ``submit()`` attaches a ``Authorization: Bearer
    <FEED_GITHUB_TOKEN>`` header on every request once the URL passes
    this check; accepting ``http://`` would put the bearer token on the
    wire in cleartext on the FIRST request (before the redirect-handler
    in ``request_safe`` could strip the header on the cross-scheme
    redirect), letting any on-path attacker — compromised network hop,
    BGP hijack, hostile public WiFi, malicious transparent proxy, MITM
    on the client's egress — capture the credential verbatim. The GitHub
    REST API has been HTTPS-only since 2014 and ``api.github.com`` has
    never accepted plaintext HTTP in production, so the tightening has
    zero impact on real deployments. Mirrors the canonical HTTPS-only
    pin established for the credential-bearing / content-publishing
    siblings (``validate_public_feed_url`` in ``src/utils/http.py``,
    ``_validated_vor_base_url`` / ``_validated_oebb_url`` /
    ``_validated_wl_base`` in the provider modules).
    """
    parsed = urlparse(api_url)
    # Security: HTTPS only — see docstring above for the TLS-strip
    # credential-leak threat model.
    if parsed.scheme.lower() != "https":
        return False
    hostname = (parsed.hostname or "").lower()
    if not hostname:
        return False
    path = parsed.path.rstrip("/")
    if hostname == "api.github.com" and path == "":
        return True
    if path in ("/api/v3", "/api/graphql"):
        # Security: require explicit operator opt-in for non-public-GitHub
        # hosts. GHE installations use any operator-chosen domain, so the
        # path alone (``/api/v3``) is not host identity. The allowlist must
        # be exact hostnames (case-insensitive); a path match without an
        # allowlist entry rejects the URL.
        trusted_ghe_hosts = {
            host.lower()
            for host in _split_csv(os.getenv("FEED_GITHUB_ENTERPRISE_HOSTS"))
        }
        if hostname in trusted_ghe_hosts:
            return True
    return False


class _GithubIssueReporter:
    def __init__(self, config: _GithubIssueConfig) -> None:
        self._config = config

    def submit(self, report: RunReport) -> None:
        if not self._config.enabled:
            return
        if not self._config.repository or not self._config.token:
            log.warning(
                "Automatisches GitHub-Issue kann nicht erstellt werden – Token oder Repository fehlen."
            )
            return

        # Security: refuse to send the token to non-GitHub hosts. Must be
        # checked BEFORE constructing the request URL or attaching the
        # Authorization header so an env-var override can't leak credentials.
        if not _is_trusted_github_api(self._config.api_url):
            # Sentinel (Clear-Text-Logging Drift, sibling of the ÖBB /
            # places-client Retry-After family): ``self._config.api_url``
            # is sourced from the operator-controlled
            # ``FEED_GITHUB_API_URL`` (preferred) / ``GITHUB_API_URL``
            # env vars in :meth:`_GithubIssueConfig.from_env`. The value
            # has only been ``urlparse``'d at this point — the parser
            # accepts arbitrary bytes in path/fragment, so a hostile env
            # var carrying any Trojan-Source primitive (BiDi RLO,
            # zero-width, ALM, 8-bit C1 CSI/OSC, ESC-prefixed ANSI SGR,
            # Tag block, Variation Selectors, log-record-forgery
            # newline) flows verbatim into the rejection-branch
            # WARNING. Routing through :func:`sanitize_log_arg` at the
            # call site mirrors the canonical defence shape pinned at
            # every other env-controlled-string log boundary in the
            # codebase (defense-in-depth — caplog and any non-
            # ``SafeFormatter`` handler see the pre-formatter message).
            log.warning(
                "Automatisches GitHub-Issue abgebrochen: API-URL %s ist kein "
                "bekannter GitHub-Endpunkt; Token wird nicht gesendet.",
                sanitize_log_arg(self._config.api_url),
            )
            return

        # Security: refuse to interpolate an untrusted slug into the request
        # path. Without this, ``owner/repo?injected=1`` or ``owner/../endpoint``
        # rewrites the URL to a different GitHub endpoint (still an authorised
        # one for the token) and would let an env-var override redirect the
        # auto-issue post to an arbitrary repo or API surface.
        if not _is_valid_github_repo_slug(self._config.repository):
            log.warning(
                "Automatisches GitHub-Issue abgebrochen: Repository %r entspricht "
                "nicht dem owner/name-Schema; Anfrage wird nicht gesendet.",
                self._config.repository,
            )
            return

        url = (
            f"{self._config.api_url}/repos/{self._config.repository}/issues"
        )

        if not validate_http_url(url):
            log.warning(
                "Automatisches GitHub-Issue abgebrochen: Unsichere oder ungültige API-URL: %s",
                url,
            )
            return

        payload: dict[str, Any] = {
            "title": self._build_title(report),
            # Security/availability: cap the body BEFORE the POST so a flood
            # of unique provider errors can't push the request past GitHub's
            # ~65 KB body limit (see _MAX_GITHUB_BODY_LENGTH for rationale).
            "body": _bounded_github_body(self._build_body(report)),
        }
        if self._config.labels:
            payload["labels"] = list(self._config.labels)
        if self._config.assignees:
            payload["assignees"] = list(self._config.assignees)

        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self._config.token}",
            "X-GitHub-Api-Version": "2022-11-28",
            # User-Agent is set via session_with_retries
        }

        try:
            with session_with_retries(
                user_agent="wien-oepnv-feed-reporter",
                allowed_methods=("POST",),  # Explicitly allow POST
                raise_on_status=False,
            ) as session:
                session.headers.update(headers)
                # Security: Use request_safe to prevent SSRF via DNS Rebinding/Redirects.
                # session.post() follows redirects automatically and re-resolves DNS,
                # which creates a TOCTOU vulnerability. request_safe() pins the IP.
                response = request_safe(
                    session,
                    url,
                    method="POST",
                    json=payload,
                    timeout=10,
                    raise_for_status=False,
                )

        except (requests.RequestException, ValueError) as exc:
            # Sentinel: ``exc`` from a GitHub HTTP error / SSRF guard may
            # contain attacker-controlled URL fragments or response-body
            # text. Route through ``sanitize_log_arg`` so ANSI/BiDi/control
            # characters cannot smuggle past the SafeFormatter.
            log.warning(
                "Automatisches GitHub-Issue fehlgeschlagen (Netzwerkfehler/Sicherheit %s: %s)",
                type(exc).__name__,
                sanitize_log_arg(str(exc)),
            )
            return

        if response.status_code >= 400:
            detail: str = response.text
            try:
                # Security: pin parse_constant + parse_float hooks (Round
                # 1503 sibling). A compromised GitHub API response /
                # MITM-redirected error envelope serving NaN / Infinity /
                # 1e1000 literals would otherwise propagate ``float('nan')``
                # / ``float('inf')`` into the error-detail processing
                # pipeline; the writer-pin round-trip would crash any
                # downstream code that ever serialises the payload.
                error_payload: object = response.json(
                    parse_constant=_reject_non_finite_constant,
                    parse_float=_reject_non_finite_float,
                )
            except (ValueError, RecursionError):
                # Resilience: ``RecursionError`` covers JSON depth-bomb attacks
                # served by a compromised upstream / DNS-hijack / MITM.
                # ``response.json()`` calls ``json.loads`` internally, which
                # raises ``RecursionError`` (NOT a subclass of ``ValueError``)
                # on a deeply-nested document. The bare ``except ValueError``
                # would let the depth-bomb propagate out of ``submit()`` →
                # ``log_results()`` (called inside the build pipeline's
                # ``finally`` block), masking any prior exception and crashing
                # the whole feed-build cron with an unhandled traceback.
                pass
            else:
                # Zero Trust: GitHub normally returns an object on errors, but
                # a GHE proxy or unexpected upstream could yield a list/scalar/
                # null. Calling .get() on those raises AttributeError, which
                # the surrounding `except ValueError` would not catch.
                if isinstance(error_payload, dict):
                    message = error_payload.get("message")
                    if isinstance(message, str) and message:
                        detail = message
            # Security: avoid log injection by stripping control characters.
            detail = _sanitize_log_detail(detail)
            # Defence-in-depth + CodeQL barrier: the response object descends
            # from a request that carried a Bearer token in its headers, so
            # the dataflow analyser flags any unparsed response data as
            # clear-text-logging of a secret. The helper above already
            # performs equivalent regex scrubbing, but CodeQL is conservative
            # across function boundaries — the inline whitelist below is
            # recognised as a sanitiser and limits the body to printable
            # ASCII within a bounded length.
            safe_detail = re.sub(r"[^\x20-\x7e]", "?", str(detail))[:500]
            log.warning(
                "GitHub-Antwort %s beim Erstellen des Issues: %s",
                response.status_code,
                safe_detail,
            )
            return

        issue_url: str | None = None
        try:
            # Security: same parse_constant + parse_float hook pin as the
            # error-status branch above — the success-path issue payload
            # could equally carry NaN / Infinity literals under a hostile
            # GHE proxy / MITM threat model.
            data: object = response.json(
                parse_constant=_reject_non_finite_constant,
                parse_float=_reject_non_finite_float,
            )
        except (ValueError, RecursionError):
            # Resilience: ``RecursionError`` covers JSON depth-bomb attacks
            # served by a compromised upstream — see the analogous fix on
            # the error-status branch above. Treat the depth-bomb as if the
            # body were undecodable; the downstream ``isinstance(data, dict)``
            # guard then short-circuits and the URL-less success log fires.
            data = None
        # Zero Trust: only trust html_url when the payload is an object that
        # actually contains a string at that key. Defends against AttributeError
        # crashes from non-dict JSON bodies (list/scalar/null).
        if isinstance(data, dict):
            raw_url = data.get("html_url")
            if isinstance(raw_url, str):
                issue_url = raw_url

        # The helper applies a regex-extraction barrier so the response-derived
        # URL is never logged unparsed (the request carried a Bearer token, so
        # CodeQL marks any direct log of unparsed response data as
        # clear-text-logging of a secret).
        issue_number = _extract_github_issue_number(issue_url)
        if issue_number:
            log.info("Automatisches GitHub-Issue erstellt: #%s", issue_number)
        else:
            log.info("Automatisches GitHub-Issue erstellt.")

    def _build_title(self, report: RunReport) -> str:
        return f"{self._config.title_prefix}: Feed-Lauf {report.run_id}"

    def _build_body(self, report: RunReport) -> str:
        started = _format_timestamp(report.started_at)
        finished = _format_timestamp(report.finished_at)
        errors = list(report.iter_error_messages())
        if report.exception_message and report.exception_message not in errors:
            errors.append(report.exception_message)

        lines: list[str] = []
        lines.append(
            "Dieser Issue wurde automatisch erstellt, weil der Feed-Lauf Fehler gemeldet hat."
        )
        lines.append("")
        lines.append("## Zusammenfassung")
        lines.append("")
        lines.append(f"- **Run-ID:** `{report.run_id}`")
        lines.append("- **Status:** Fehler")
        lines.append(f"- **Start:** {started}")
        lines.append(f"- **Ende:** {finished}")
        if report.feed_path:
            # Security (Markdown injection at inline-code-span boundary):
            # see ``render_feed_health_markdown`` for the threat model. The
            # GitHub Issue body is rendered as Markdown by the GitHub web UI
            # and is visible to every repo watcher, so an env-controlled
            # backtick lands a usable HTML tag (``<img onerror>`` / ``[link]
            # (...)``) in a public artefact. ``safe_markdown_codespan``
            # neutralises every backtick / control byte / BiDi mark.
            lines.append(
                f"- **Feed-Datei:** `{safe_markdown_codespan(report.feed_path)}`"
            )
        if report.raw_item_count is not None:
            lines.append(f"- **Items (roh):** {report.raw_item_count}")
        if report.final_item_count is not None:
            lines.append(f"- **Items (final):** {report.final_item_count}")
        if report.exception_message:
            lines.append(f"- **Ausnahme:** {escape_markdown(report.exception_message)}")
        if report.warnings:
            lines.append(f"- **Warnungen:** {len(report.warnings)}")
        lines.append("")

        if report.warnings:
            lines.append("## Warnungen")
            lines.append("")
            for warning in report.warnings:
                lines.append(f"- {escape_markdown(warning)}")
            lines.append("")

        if errors:
            lines.append("## Fehler")
            lines.append("")
            for error in errors:
                lines.append(f"- {escape_markdown(error)}")
            lines.append("")

        lines.append("## Providerstatus")
        lines.append("")
        lines.append("| Provider | Status | Details | Items | Dauer (s) |")
        lines.append("| --- | --- | --- | ---: | ---: |")
        for name in sorted(report.providers):
            entry = report.providers[name]
            detail = escape_markdown_cell(entry.detail or "")
            items = entry.items if entry.items is not None else "—"
            duration = f"{entry.duration:.2f}" if entry.duration is not None else "—"
            name_cell = escape_markdown_cell(name)
            status_cell = escape_markdown_cell(entry.status or 'unbekannt')
            lines.append(
                f"| {name_cell} | {status_cell} | {detail} | {items} | {duration} |"
            )
        lines.append("")

        diagnostics = report.diagnostics_message()
        if diagnostics:
            lines.append("## Diagnosedaten")
            lines.append("")
            lines.append("```text")
            # Security (fenced-code-block break-out): ``diagnostics`` carries
            # ``feed_path`` (env-controlled via ``OUT_PATH``) verbatim. A
            # ``\n```\n`` payload in the path would close the open fence
            # mid-block and let arbitrary Markdown render after the break
            # in the public GitHub Issue body. ``safe_markdown_codespan``
            # collapses every whitespace run (including embedded newlines)
            # to a single space so no triple-backtick can land on its own
            # line, and replaces backticks with apostrophes as a second
            # layer of defence. The 50 000-char cap is well above any
            # realistic diagnostics size and well below ``_MAX_GITHUB_BODY_
            # LENGTH = 60 000`` which a downstream call to
            # ``_bounded_github_body`` enforces on the rendered body.
            lines.append(safe_markdown_codespan(diagnostics, max_len=50_000))
            lines.append("```")
            lines.append("")

        # Security (Markdown injection at inline-code-span boundary):
        # ``error_log_path`` is ``Path(LOG_DIR) / "errors.log"``;
        # ``LOG_DIR`` is env-controlled via ``src.feed.config.resolve_env_
        # path``. A literal backtick in ``LOG_DIR`` closes the inline code
        # span and lets attacker-controlled Markdown render in the public
        # GitHub Issue body. ``safe_markdown_codespan`` neutralises every
        # backtick / control byte / BiDi mark.
        lines.append(
            "Weitere Details finden sich in der Logdatei "
            f"`{safe_markdown_codespan(str(error_log_path))}`."
        )

        return "\n".join(lines).strip() + "\n"


def _submit_github_issue(report: RunReport) -> None:
    config = _GithubIssueConfig.from_env()
    reporter = _GithubIssueReporter(config)
    reporter.submit(report)


__all__ = [
    "_MAX_GITHUB_BODY_LENGTH",
    "_BODY_TRUNCATION_MARKER",
    "_bounded_github_body",
    "_GITHUB_ISSUE_URL_RE",
    "_extract_github_issue_number",
    "_split_csv",
    "_GithubIssueConfig",
    "_GITHUB_REPO_SLUG_RE",
    "_is_valid_github_repo_slug",
    "_is_trusted_github_api",
    "_GithubIssueReporter",
    "_submit_github_issue",
    "log",
]
