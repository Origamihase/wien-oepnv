"""Feed-Wächter: one GitHub issue per broken rule.

Moved verbatim from ``scripts/feed_watch.py``.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from src.feed_watch.evaluate import Result
from src.feed_watch.model import (
    HELD_RUNS_TO_CLOSE,
    LOGGER,
    REOPEN_WINDOW,
    RULES,
    Finding,
    Rule,
    _clean,
    _local,
    _parse_iso,
)

# --- GitHub issues ---------------------------------------------------------------

ISSUE_LABEL = "feed-waechter"
ISSUE_TITLE_PREFIX = "Feed-Wächter: "


class IssueClient:
    """The few GitHub REST calls the watcher needs, through the project's safe HTTP stack."""

    def __init__(self, repository: str, token: str, api_url: str) -> None:
        self.repository = repository
        self.token = token
        self.api_url = api_url.rstrip("/")

    def _request(self, method: str, path: str, payload: Mapping[str, Any] | None = None) -> Any:
        from src.utils.files import _reject_non_finite_constant, _reject_non_finite_float
        from src.utils.http import request_safe, session_with_retries, validate_http_url

        url = f"{self.api_url}/repos/{self.repository}{path}"
        if not validate_http_url(url.split("?", 1)[0]):
            raise ValueError("unsafe GitHub API URL")
        with session_with_retries(
            user_agent="wien-oepnv-feed-watch",
            # POST is not retried: a create GitHub carried out but answered
            # with a 5xx or a timeout would open the issue or comment twice.
            allowed_methods=("GET", "PATCH"),
            raise_on_status=False,
        ) as session:
            session.headers.update(
                {
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {self.token}",
                    "X-GitHub-Api-Version": "2022-11-28",
                }
            )
            kwargs: dict[str, Any] = {"json": dict(payload)} if payload is not None else {}
            response = request_safe(session, url, method=method, timeout=10, raise_for_status=False, **kwargs)
        if response.status_code >= 400:
            raise RuntimeError(f"GitHub {method} {path.split('?', 1)[0]}: HTTP {response.status_code}")
        if not response.content:
            return None
        try:
            return response.json(
                parse_constant=_reject_non_finite_constant,
                parse_float=_reject_non_finite_float,
            )
        except (ValueError, RecursionError) as exc:
            raise RuntimeError(f"GitHub {method}: unreadable answer") from exc

    def issues(self) -> list[dict[str, Any]]:
        data = self._request("GET", f"/issues?state=all&labels={ISSUE_LABEL}&per_page=50&sort=updated")
        return [entry for entry in data if isinstance(entry, dict)] if isinstance(data, list) else []

    def create(self, title: str, body: str) -> int | None:
        data = self._request("POST", "/issues", {"title": title, "body": body, "labels": [ISSUE_LABEL]})
        number = data.get("number") if isinstance(data, dict) else None
        return number if isinstance(number, int) else None

    def comment(self, number: int, body: str) -> None:
        self._request("POST", f"/issues/{number}/comments", {"body": body})

    def set_state(self, number: int, state: str) -> None:
        payload: dict[str, Any] = {"state": state}
        if state == "closed":
            payload["state_reason"] = "completed"
        self._request("PATCH", f"/issues/{number}", payload)


def issue_client_from_env() -> IssueClient | None:
    """A client when ``FEED_WATCH_ISSUES=1`` and a trusted token and repository are set."""
    from src.feed.reporting import _is_trusted_github_api, _is_valid_github_repo_slug
    from src.utils.env import get_bool_env, read_secret

    if not get_bool_env("FEED_WATCH_ISSUES", False):
        return None
    repository = os.getenv("GITHUB_REPOSITORY") or ""
    token = read_secret("GITHUB_TOKEN")
    api_url = os.getenv("GITHUB_API_URL") or "https://api.github.com"
    if not repository or not token:
        LOGGER.warning("Feed-Wächter: keine Issues, GITHUB_TOKEN oder GITHUB_REPOSITORY fehlt")
        return None
    if not _is_valid_github_repo_slug(repository) or not _is_trusted_github_api(api_url):
        LOGGER.warning("Feed-Wächter: keine Issues, Repository oder API-URL nicht vertrauenswürdig")
        return None
    return IssueClient(repository, token, api_url)


def _run_link() -> str:
    server = os.getenv("GITHUB_SERVER_URL", "https://github.com")
    repository = os.getenv("GITHUB_REPOSITORY", "")
    run_id = os.getenv("GITHUB_RUN_ID", "")
    if repository and run_id.isdigit():
        return f"{server}/{repository}/actions/runs/{run_id}"
    return ""


def _issue_body(rule: Rule, findings: Sequence[Finding], now: datetime) -> str:
    lines = [
        f"**{rule.title}** ({rule.rank}), festgestellt {_local(now):%d.%m.%Y %H:%M} Wiener Zeit.",
        "",
        rule.why,
        "",
        "Im Lauf gefunden:",
        "",
    ]
    lines += [f"- {f.subject}: {f.text}" for f in findings[:10]]
    if len(findings) > 10:
        lines.append(f"- … und {len(findings) - 10} weitere")
    link = _run_link()
    lines += [
        "",
        "Das ist ein Hinweis, kein Fehler des Laufs: Der Feed wurde wie gebaut veröffentlicht, "
        "der Update-Lauf bleibt grün (Regel „Inhaltsfunde nie rot“, docs/reference/feed_watch.md). "
        f"Dieses Issue schließt sich selbst, sobald die Regel {HELD_RUNS_TO_CLOSE} Läufe in Folge hält.",
    ]
    if link:
        lines += ["", f"Lauf: {link}"]
    return "\n".join(lines)


def sync_issues(client: IssueClient, result: Result, now: datetime) -> None:
    """Open, reopen or close one issue per rule. Errors are logged, never raised."""
    by_rule: dict[str, list[Finding]] = {}
    for finding in result.findings:
        by_rule.setdefault(finding.rule_id, []).append(finding)
    rules_state: dict[str, Any] = result.state["rules"]
    to_open = [r for r in by_rule if not rules_state.get(r, {}).get("issue")]
    to_close = [
        r
        for r, entry in rules_state.items()
        if r not in by_rule and entry.get("issue") and int(entry.get("held_runs") or 0) >= HELD_RUNS_TO_CLOSE
    ]
    if not to_open and not to_close:
        return
    try:
        existing = client.issues() if to_open else []
    except Exception as exc:  # network, permission: retried next run
        LOGGER.warning("Feed-Wächter: Issues nicht lesbar: %s", _clean(exc))
        return
    for rule_id in to_open:
        rule = RULES[rule_id]
        title = f"{ISSUE_TITLE_PREFIX}{rule.title}"
        body = _issue_body(rule, by_rule[rule_id], now)
        try:
            number = _open_or_reopen(client, existing, title, body, now)
        except Exception as exc:
            LOGGER.warning("Feed-Wächter: Issue zu %s nicht angelegt: %s", rule_id, _clean(exc))
            continue
        if number is not None:
            rules_state.setdefault(rule_id, {})["issue"] = number
            LOGGER.info("Feed-Wächter: Issue #%s für %s", number, rule_id)
    for rule_id in to_close:
        entry = rules_state[rule_id]
        number = int(entry["issue"])
        try:
            client.comment(
                number,
                f"Die Regel hält wieder seit {HELD_RUNS_TO_CLOSE} Läufen "
                f"(Stand {_local(now):%d.%m.%Y %H:%M} Wiener Zeit). Automatisch geschlossen.",
            )
            client.set_state(number, "closed")
        except Exception as exc:
            LOGGER.warning("Feed-Wächter: Issue #%s nicht geschlossen: %s", number, _clean(exc))
            continue
        rules_state.pop(rule_id, None)
        LOGGER.info("Feed-Wächter: Issue #%s geschlossen (%s hält wieder)", number, rule_id)


def _open_or_reopen(
    client: IssueClient, existing: Sequence[Mapping[str, Any]], title: str, body: str, now: datetime
) -> int | None:
    same = [entry for entry in existing if entry.get("title") == title and "pull_request" not in entry]
    for entry in same:
        if entry.get("state") == "open" and isinstance(entry.get("number"), int):
            return int(entry["number"])
    for entry in same:
        closed = _parse_iso(str(entry.get("closed_at") or "").replace("Z", "+00:00"))
        if closed is not None and now - closed <= REOPEN_WINDOW and isinstance(entry.get("number"), int):
            number = int(entry["number"])
            client.set_state(number, "open")
            client.comment(number, body)
            return number
    return client.create(title, body)
