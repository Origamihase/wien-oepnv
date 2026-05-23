"""Sentinel drift coverage for the GitHub Personal-Access / OAuth / App /
Refresh / Fine-Grained token family value-shape log-sanitisation across
``sanitize_log_message`` and the downstream ``_sanitize_exception_msg``
chain.

The 2026-05-17 HashiCorp Vault Token Family Log-Sanitisation Drift Closure
round explicitly named the GitHub token family
(``ghp_`` / ``gho_`` / ``ghu_`` / ``ghs_`` / ``ghr_`` / ``github_pat_``) as
the **first** of the ~70 next-round-candidate scanner detectors whose
detection codepath (``_KNOWN_TOKENS`` in ``src/utils/secret_scanner.py``)
was extended in prior rounds but whose companion log-sanitisation codepath
(``src/utils/logging.py:sanitize_log_message``) was NOT extended in the
same round — the canonical sibling-drift pattern documented across the
2026-05-16 Database / Directory-Shell-Share rounds and the 2026-05-17
Vault rounds. This round closes the GitHub-family arm of that named-but-
deferred backlog.

Pre-fix detection gaps (mirror the 2026-05-17 Vault Token Log-Sanitisation
Drift round's structural analysis):

1. ``sanitize_log_message`` masks credentials only via ``key=value`` /
   URL ``user:pass@`` / header ``Name: Value`` structures. A bare GitHub
   token in plain log text bypasses every existing pattern. Four leak
   surfaces:

   * **Plain application f-string logs** — ``log.error(f"Auth failed
     using {token}: {exc}")``. The bare token shape lands in operator
     log streams verbatim. The existing ``_keys`` alternation includes
     ``[a-z0-9_.\\-]*ghp(?:[-_][a-z0-9_.\\-]*)?`` for KEY names like
     ``ghp=...`` / ``ghp_token=...``, but bare GitHub tokens with no
     surrounding sensitive-key context (e.g. ``Auth failed using
     ghp_<36>``) bypass every key-based mask.

   * **Upstream error responses** — ``log.warning(f"Provider error:
     {response.text}")`` where a misconfigured / compromised upstream
     (GitHub Issue body, webhook payload, GraphQL error message) echoes
     the supplied token back in its error payload.

   * **JSON values without sensitive key names** — ``{"data":
     "ghp_AAA..."}`` or ``{"payload": "ghs_BBB..."}``. The JSON-key
     sensitive-name regex misses keys like ``data`` / ``payload`` /
     ``response_body`` / ``message`` / ``ref`` so the token value
     leaks unredacted into the JSON value span.

   * **URL paths embedding the token** — ``GET /repos/foo/bar?ref=
     ghp_<36>``. The Basic-Auth-in-URL regex requires the credential
     to appear before ``@`` (``user:pass@host``); query-string and
     path-embedded tokens with a NON-sensitive parameter name (``ref``,
     ``commit_sha``, ``q``) slip past entirely.

2. Threat model: mirror the Vault round. A GitHub token leaked into
   the operator log stream (Slack escalation, GitHub Issue body
   submitted by ``submit_auto_issue``, ``docs/feed_health.json`` public
   artefact, SIEM aggregator pipeline) grants the same per-token scope
   as the committed-source leak that the scanner already protects
   against:

   * ``ghp_<36 alphanumeric>`` — Personal Access Token (Classic). Full
     scope per token configuration (``repo``, ``workflow``, ``admin:org``,
     ``delete_repo``, etc.). Leaking grants ability to read every repo
     the user can read, push to every repo the user can write,
     exfiltrate secrets via repo files / GitHub Actions logs, create /
     delete repos, and — with ``admin:org`` scope — administer the
     user's organisations (add new admins, modify team membership,
     audit-log tampering, persistence-via-deploy-keys).

   * ``gho_<36 alphanumeric>`` — OAuth-App Access Token (issued via the
     OAuth web flow). Per-OAuth-app scope tied to the user's consent
     grant; persistent until the user revokes the app at
     https://github.com/settings/applications.

   * ``ghu_<36 alphanumeric>`` — GitHub App User-to-Server Token (App
     acting on behalf of an authenticated user). Per-installation scope
     intersected with the user's repo access. Common in OAuth-flow
     GitHub Apps (Dependabot, Renovate, CodeQL).

   * ``ghs_<36 alphanumeric>`` — **HIGHEST routine-leak severity in the
     GitHub family.** This is the format of ``GITHUB_TOKEN`` auto-
     injected by GitHub Actions into every workflow run — leaking
     grants full ``contents: write`` / ``packages: write`` /
     ``actions: write`` scope on the repo for the duration of the
     workflow run (typically 1-6 hours TTL but actively renewable
     for the workflow's lifetime). Most-common real-world leak
     surface: workflow ``echo`` of ``${{ secrets.GITHUB_TOKEN }}``,
     ``setup-node`` / ``setup-python`` debug logs, ``actions/checkout``
     fork-PR token-leak attack chains.

   * ``ghr_<36 alphanumeric>`` — Refresh Token (issued alongside
     ``gho_`` / ``ghu_`` during token rotation). Mint fresh access
     tokens until the refresh token itself is revoked at
     https://github.com/settings/applications.

   * ``github_pat_<22+ alphanumeric_>`` — **Fine-Grained Personal
     Access Token.** Per-repo or per-org scoped with resource-level
     permissions (Contents, Metadata, Actions, Pull Requests, Issues,
     Workflows, Webhooks, etc.). Modern replacement for ``ghp_``
     classic tokens with finer-grained ACL. Body permits internal
     underscores per GitHub's canonical format (``github_pat_<22>_
     <59>``). A leak grants the per-resource scope configured at token
     creation — typically read/write on a single repo or org, which is
     still high-impact (push branches, modify Actions workflows, set
     repo secrets, trigger deploy workflows).

The log-sanitisation drift closes the operator-log leak sink with the
same family scope as the scanner round.

**Fix:** append two sibling patterns to ``sanitize_log_message``'s
pattern list mirroring the scanner regex structural anchors:

```python
(
    r"(?<![A-Za-z0-9])(ghp|gho|ghu|ghs|ghr)_[0-9a-zA-Z]{36}(?![A-Za-z0-9])",
    r"\1_***",
),
(
    r"(?<![A-Za-z0-9])(github_pat)_[0-9a-zA-Z_]{22,}(?![A-Za-z0-9])",
    r"\1_***",
),
```

Structural anchors mirror the scanner regexes exactly:
* ``(?<![A-Za-z0-9])`` lookbehind prevents mid-word false positives
  (``myghp_xxx``, ``xghs_yyy`` are preserved).
* ``(?![A-Za-z0-9])`` lookahead bounds the body span.
* Strict 36-char alphanumeric body for ``ghp_`` / ``gho_`` / ``ghu_`` /
  ``ghs_`` / ``ghr_`` matches GitHub's canonical token shape (rejects
  accidental fragments).
* 22+ char body with underscores allowed for ``github_pat_`` matches
  the fine-grained format (real tokens are 84+ chars body with one
  internal underscore separating the 22-char prefix-segment from the
  59-char body-segment).
* The mask preserves the issuer-specific prefix (``ghp_***`` /
  ``ghs_***`` etc.) for incident-response triage — each tier has a
  distinct revocation flow.

Idempotence: the masked form (``ghp_***``) does NOT match the regex
because ``*`` is not in the body alphabet ``[0-9a-zA-Z]`` / ``[0-9a-zA-Z_]``
AND the masked body length (3 chars) is below the 36-char / 22-char floor.

Marker: SENTINEL_GITHUB_TOKEN_LOG_SANITIZATION_DRIFT.
"""

from __future__ import annotations

import pytest

from src.utils.http import _sanitize_exception_msg
from src.utils.logging import sanitize_log_arg, sanitize_log_message
from src.utils.secret_scanner import _KNOWN_TOKENS

SENTINEL_GITHUB_TOKEN_LOG_SANITIZATION_DRIFT = (
    "SENTINEL_GITHUB_TOKEN_LOG_SANITIZATION_DRIFT: "
    "sanitize_log_message had NO value-shape mask for the GitHub token "
    "family (ghp_/gho_/ghu_/ghs_/ghr_/github_pat_) that the scanner's "
    "_KNOWN_TOKENS already detects in committed source files. Bare "
    "GitHub tokens in plain log text, JSON values with non-sensitive "
    "keys, URL paths / query strings, and exception messages slipped "
    "past all key/header/URL-credential masking patterns and leaked "
    "verbatim into operator log streams and the public docs/feed_health.json "
    "artefact."
)

# Real GitHub PAT bodies are EXACTLY 36 chars alphanumeric. Each prefix gets
# a distinctive body so a per-case failure is unambiguous in the test report.
_GHP_TOKEN = "ghp_" + "Aa1" * 12  # 36 chars body, mixed alphabet
_GHO_TOKEN = "gho_" + "Bb2" * 12
_GHU_TOKEN = "ghu_" + "Cc3" * 12
_GHS_TOKEN = "ghs_" + "Dd4" * 12
_GHR_TOKEN = "ghr_" + "Ee5" * 12

# GitHub Fine-Grained PAT canonical shape: ``github_pat_<22>_<59>`` — 82
# char body with one internal underscore separating the prefix-segment from
# the body-segment.
_GITHUB_PAT_TOKEN = (
    "github_pat_"
    + "F" * 22
    + "_"
    + "f0G1H2I3" * 7 + "abc"  # 59 chars body-segment
)


# ---------------------------------------------------------------------------
# (1) Plain log line — bare GitHub token in application f-string logs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "token,prefix",
    [
        (_GHP_TOKEN, "ghp"),
        (_GHO_TOKEN, "gho"),
        (_GHU_TOKEN, "ghu"),
        (_GHS_TOKEN, "ghs"),
        (_GHR_TOKEN, "ghr"),
    ],
)
def test_github_token_in_plain_log_line_is_masked(token: str, prefix: str) -> None:
    """Bare GitHub token in plain log text MUST be masked by
    ``sanitize_log_message`` — pre-fix this leaked verbatim through
    the operator-log sink."""
    log_line = f"GitHub API returned 401: invalid credential {token}"
    result = sanitize_log_message(log_line)
    assert token not in result, (
        f"GitHub {prefix.upper()}_ token leaked through sanitize_log_message: "
        f"{SENTINEL_GITHUB_TOKEN_LOG_SANITIZATION_DRIFT}"
    )
    assert f"{prefix}_***" in result, (
        f"GitHub {prefix.upper()}_ token mask MUST preserve the "
        f"'{prefix}_' prefix for incident-response triage"
    )


def test_github_fine_grained_pat_in_plain_log_line_is_masked() -> None:
    """Bare GitHub Fine-Grained PAT (``github_pat_<22>_<59>``) in plain
    log text MUST be masked — distinct body alphabet (permits internal
    underscores) and distinct length floor (22+) from the classic
    ``ghp_/gho_/...`` family."""
    log_line = f"GitHub fine-grained auth failed using {_GITHUB_PAT_TOKEN}"
    result = sanitize_log_message(log_line)
    assert _GITHUB_PAT_TOKEN not in result
    assert "github_pat_***" in result


# ---------------------------------------------------------------------------
# (2) JSON value with non-sensitive key — leak vector via response_body etc.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "token,prefix",
    [
        (_GHP_TOKEN, "ghp"),
        (_GHO_TOKEN, "gho"),
        (_GHU_TOKEN, "ghu"),
        (_GHS_TOKEN, "ghs"),
        (_GHR_TOKEN, "ghr"),
    ],
)
@pytest.mark.parametrize("key_name", ["data", "payload", "response_body", "message"])
def test_github_token_in_json_value_is_masked(
    token: str, prefix: str, key_name: str
) -> None:
    """GitHub token in JSON value with a NON-sensitive key name MUST
    be masked — pre-fix the JSON-key sensitive-name regex missed keys
    like ``data`` / ``payload`` / ``response_body`` and the token
    value leaked verbatim."""
    log_line = f'{{"{key_name}": "{token}"}}'
    result = sanitize_log_message(log_line)
    assert token not in result, (
        f"GitHub {prefix.upper()}_ token in JSON value with non-sensitive "
        f"key '{key_name}' leaked through sanitize_log_message"
    )
    assert f"{prefix}_***" in result


def test_github_fine_grained_pat_in_json_value_is_masked() -> None:
    log_line = f'{{"response_body": "{_GITHUB_PAT_TOKEN}"}}'
    result = sanitize_log_message(log_line)
    assert _GITHUB_PAT_TOKEN not in result
    assert "github_pat_***" in result


# ---------------------------------------------------------------------------
# (3) URL path / query string with non-sensitive param — non-`user:pass@` form
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "token,prefix",
    [
        (_GHP_TOKEN, "ghp"),
        (_GHO_TOKEN, "gho"),
        (_GHU_TOKEN, "ghu"),
        (_GHS_TOKEN, "ghs"),
        (_GHR_TOKEN, "ghr"),
    ],
)
def test_github_token_in_url_query_with_non_sensitive_param_is_masked(
    token: str, prefix: str
) -> None:
    """GitHub token in URL query string with a NON-sensitive parameter
    name (``ref`` / ``commit_sha`` / ``q``) MUST be masked — pre-fix
    the URL credential regex required the credential to appear before
    ``@``; query-string and path-embedded tokens slipped past."""
    log_line = f"GET /repos/foo/bar?ref={token} HTTP/1.1 404"
    result = sanitize_log_message(log_line)
    assert token not in result
    assert f"{prefix}_***" in result


def test_github_token_in_url_path_segment_is_masked() -> None:
    """GitHub token embedded in URL path segment (NOT ``user:pass@``
    form) MUST be masked — covers the path-embedded leak surface."""
    log_line = f"GET /api/internal/audit/{_GHS_TOKEN}/details 200"
    result = sanitize_log_message(log_line)
    assert _GHS_TOKEN not in result
    assert "ghs_***" in result


# ---------------------------------------------------------------------------
# (4) End-to-end via _sanitize_exception_msg — exception text from provider
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "token,prefix",
    [
        (_GHP_TOKEN, "ghp"),
        (_GHO_TOKEN, "gho"),
        (_GHU_TOKEN, "ghu"),
        (_GHS_TOKEN, "ghs"),
        (_GHR_TOKEN, "ghr"),
    ],
)
def test_github_token_through_sanitize_exception_msg(token: str, prefix: str) -> None:
    """Exception messages routed through ``_sanitize_exception_msg``
    (the canonical exception-text sanitisation path in
    ``src/utils/http.py``) MUST mask GitHub tokens. The function
    extracts HTTP URLs via a pre-regex and falls back to
    ``sanitize_log_message`` for the remainder; fixing the latter
    closes the exception-text leak sink."""
    exc_msg = f"HTTPError: 401 Unauthorized — credential {token} is revoked"
    result = _sanitize_exception_msg(exc_msg)
    assert token not in result
    assert f"{prefix}_***" in result


def test_github_fine_grained_pat_through_sanitize_exception_msg() -> None:
    exc_msg = f"HTTPError: 403 Forbidden — token {_GITHUB_PAT_TOKEN} lacks scope"
    result = _sanitize_exception_msg(exc_msg)
    assert _GITHUB_PAT_TOKEN not in result
    assert "github_pat_***" in result


# ---------------------------------------------------------------------------
# (5) sanitize_log_arg passthrough — string and non-string args
# ---------------------------------------------------------------------------


def test_sanitize_log_arg_masks_github_token_in_string() -> None:
    """``sanitize_log_arg`` is the canonical wrapper for logging args.
    GitHub tokens in string args MUST be masked."""
    arg = f"audit: {_GHP_TOKEN}"
    result = sanitize_log_arg(arg)
    assert _GHP_TOKEN not in result
    assert "ghp_***" in result


def test_sanitize_log_arg_masks_github_token_in_object_repr() -> None:
    """Non-string args are routed through ``str()`` before sanitisation.
    A custom object whose ``__str__`` contains a GitHub token MUST have
    the token masked (either by the existing ``token=`` query-param mask
    or by the new value-shape mask — both produce a leak-free output)."""

    class _Wrapper:
        def __str__(self) -> str:
            return f"Wrapper(audit={_GHS_TOKEN})"

    result = sanitize_log_arg(_Wrapper())
    assert _GHS_TOKEN not in result, (
        "GitHub GHS_ (GITHUB_TOKEN) leaked through sanitize_log_arg"
    )
    assert "ghs_***" in result


# ---------------------------------------------------------------------------
# (6) Negative cases — false positives must remain absent
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "benign",
    [
        # Short fragments — body below the 36-char floor
        "ghp_abc and gho_xyz",
        "ghu_short",
        "ghs_xxx",
        # Mid-identifier collisions (lookbehind prevents these)
        "xghp_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        "Yghs_BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB",
        "1ghr_CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC",
        # Fine-grained pat: short fragments below 22-char floor
        "github_pat_short",
        "github_pat_x",
    ],
)
def test_benign_github_prefix_is_not_masked(benign: str) -> None:
    """Negative case: short prefixes / mid-identifier collisions MUST
    NOT be masked. The ``(?<![A-Za-z0-9])`` lookbehind plus the body
    floor are the structural disambiguators."""
    result = sanitize_log_message(benign)
    assert result == benign, (
        f"False-positive GitHub token mask on benign input: {benign!r} → {result!r}"
    )


def test_short_github_token_body_is_not_masked() -> None:
    """Body shorter than 36 chars is below the structural floor and
    MUST NOT be masked (rejects accidental fragments that the strict
    36-char body floor disambiguates against)."""
    short = "ghp_" + "a" * 35  # 35 chars body, below floor
    result = sanitize_log_message(short)
    assert result == short


def test_short_github_fine_grained_pat_body_is_not_masked() -> None:
    short = "github_pat_" + "a" * 21  # 21 chars body, below 22-char floor
    result = sanitize_log_message(short)
    assert result == short


# ---------------------------------------------------------------------------
# (7) Idempotence — mask MUST be stable across repeated applications
# ---------------------------------------------------------------------------


def test_github_token_mask_is_idempotent() -> None:
    """Running ``sanitize_log_message`` twice on the same input MUST
    produce the same output. The mask token ``ghp_***`` MUST NOT be
    re-matched as a GitHub PAT (``***`` is not in the body alphabet
    AND is below the 36-char floor)."""
    log_line = f"Failed: {_GHP_TOKEN}"
    first = sanitize_log_message(log_line)
    second = sanitize_log_message(first)
    assert first == second
    assert _GHP_TOKEN not in first


def test_github_fine_grained_pat_mask_is_idempotent() -> None:
    log_line = f"Failed: {_GITHUB_PAT_TOKEN}"
    first = sanitize_log_message(log_line)
    second = sanitize_log_message(first)
    assert first == second
    assert _GITHUB_PAT_TOKEN not in first


# ---------------------------------------------------------------------------
# (8) Sibling-alignment invariant — log mask covers every scanner prefix
# ---------------------------------------------------------------------------


def test_scanner_and_log_sanitiser_share_github_token_family() -> None:
    """**Sibling-alignment invariant.** Every GitHub token prefix that
    appears in ``_KNOWN_TOKENS`` MUST have a matching mask in
    ``sanitize_log_message``. Any future GitHub-family prefix addition
    to the scanner without a companion log-mask addition fails this
    test on the first pytest run after the new scanner entry is
    committed — surfacing the next drift family programmatically."""
    expected_short_prefixes = {"ghp", "gho", "ghu", "ghs", "ghr"}
    expected_long_prefixes = {"github_pat"}

    scanner_short_prefixes: set[str] = set()
    scanner_long_prefixes: set[str] = set()
    for regex, _attribution in _KNOWN_TOKENS:
        pattern_text = regex.pattern
        for prefix in expected_short_prefixes:
            # The short prefixes are anchored with the literal '_' AND
            # the body alphabet ``[0-9a-zA-Z]`` (no underscores in body)
            # so we look for ``<prefix>_[`` to distinguish from the
            # longer ``github_pat_`` shape.
            if f"{prefix}_[0-9a-zA-Z]" in pattern_text:
                scanner_short_prefixes.add(prefix)
        for prefix in expected_long_prefixes:
            if f"{prefix}_[0-9a-zA-Z_]" in pattern_text:
                scanner_long_prefixes.add(prefix)
    assert scanner_short_prefixes == expected_short_prefixes, (
        f"Scanner GitHub short-prefix family drift: expected "
        f"{expected_short_prefixes}, found {scanner_short_prefixes}. "
        f"If a new GitHub token prefix was added to _KNOWN_TOKENS, "
        f"add it to sanitize_log_message AND update this test."
    )
    assert scanner_long_prefixes == expected_long_prefixes, (
        f"Scanner GitHub long-prefix family drift: expected "
        f"{expected_long_prefixes}, found {scanner_long_prefixes}"
    )

    # Confirm sanitize_log_message masks every short prefix.
    body = "Z" * 36
    for prefix in expected_short_prefixes:
        log_line = f"diagnostic: {prefix}_{body}"
        result = sanitize_log_message(log_line)
        assert f"{prefix}_{body}" not in result, (
            f"GitHub token prefix '{prefix}_' missing from "
            f"sanitize_log_message mask family — log-sanitisation drift "
            f"vs. scanner _KNOWN_TOKENS"
        )
        assert f"{prefix}_***" in result, (
            f"GitHub token prefix '{prefix}_' mask MUST preserve issuer "
            f"attribution as '{prefix}_***' for incident-response triage"
        )

    # Confirm sanitize_log_message masks the github_pat_ prefix.
    long_body = "Y" * 22 + "_" + "y" * 59
    log_line = f"diagnostic: github_pat_{long_body}"
    result = sanitize_log_message(log_line)
    assert f"github_pat_{long_body}" not in result
    assert "github_pat_***" in result
