"""Sentinel PoC: missing detectors for the modern AI/ML inference-
platform credential family — Groq (``gsk_``), Replicate (``r8_``), and
Perplexity (``pplx-``). Three popular indie-developer-facing AI APIs
that grew rapidly in 2024-2025 but were not enumerated in the
``_KNOWN_TOKENS`` table at the time of the previous Hugging Face
(``hf_``) and Anthropic (``sk-ant-``) coverage rounds.

Pre-fix every leaked Groq / Replicate / Perplexity API token in
committed source / log artefacts / CI debug snippets fell into one
of two generic detection branches:

  * **Attribution drift via the entropy fallback** (bare tokens
    outside variable-assignment context) — the body matches
    ``_HIGH_ENTROPY_RE`` (``[A-Za-z0-9+/=_-]{24,}``) and lands as
    ``Hochentropischer Token-String``. The vendor-specific
    attribution is lost — incident-response triage must guess
    whether the leaked entropy span is a Groq token (rotate at
    console.groq.com/keys; audit completion-API usage logs for
    chargeback fraud / model-prompt exfiltration), a Replicate
    token (rotate at replicate.com/account/api-tokens; audit
    deployed-model API usage; check for billing-credit drain
    via attacker-triggered inference jobs), a Perplexity token
    (rotate at perplexity.ai/settings/api; audit Sonar-API
    completion logs), or some other opaque secret. Each has a
    DISTINCT vendor control plane.

  * **Attribution drift via the assignment heuristic** (tokens
    in ``KEY = "value"`` contexts) — when the variable name
    matches the sensitive-keyword family (``api_key``, ``token``,
    etc.), the assignment heuristic produces a generic
    ``Verdächtige Zuweisung eines potentiellen Secrets`` finding
    that loses the same vendor-specific attribution.

The threat model for AI/ML platform credential leaks
----------------------------------------------------

A leaked AI inference-platform API token enables:

  * **Billing-credit drain (PRIMARY ATTACK VECTOR)** — the
    attacker triggers expensive inference jobs (large-context
    chat completions, image/video generation, fine-tuning runs)
    that bill against the victim's account. Modern LLM inference
    can cost USD 0.10-1.00 per completion at scale; a leaked
    token can drain $1000s in hours before detection. Replicate
    specifically charges per GPU-second for hosted models —
    long-running inference jobs are extremely cost-amplifying.

  * **Model-prompt exfiltration** — the attacker queries the
    victim's deployed models via the leaked token, exfiltrating
    proprietary prompts, system instructions, or fine-tuned
    model weights (where the API exposes weight download).

  * **Account takeover via account-management API surfaces** —
    some platforms allow API-key-authenticated access to billing,
    team membership, or webhook configuration. A leaked token
    can register a webhook to attacker-controlled URLs, then
    redirect all completion responses (including those containing
    the victim's prompt data) to the attacker.

  * **Cross-platform pivoting** — operators frequently embed
    multiple AI-platform tokens in the same config file (Anthropic
    for chat, Replicate for image gen, Groq for fast inference).
    A leak of any ONE token often implies a leak of the file,
    pivoting to the others.

Real-world emission patterns
----------------------------

  * Python notebook outputs (``.ipynb``) hardcoding API keys in
    cell output cells (``client = Groq(api_key="gsk_...")``).
  * ``.env`` files committed accidentally (e.g.,
    ``REPLICATE_API_TOKEN=r8_xxx``).
  * Documentation README snippets showing curl examples with
    real (live!) tokens (``curl -H "Authorization: Bearer
    pplx-..."``).
  * CI/CD pipeline debug output (verbose curl in shell script,
    with the token in the Authorization header).
  * Python ``requests`` library debug logs (urllib3 DEBUG
    logging) emitting the Authorization header verbatim during
    test runs.
  * Browser dev-tools Network tab HAR exports of intranet AI
    dashboards or proxy tools (when the operator exports HAR
    for debugging and uploads it to a public sharing service).

Fix
---

Add three new entries to ``_KNOWN_TOKENS`` for the three vendor
prefixes:

  * ``gsk_<32+ alphanumeric>`` — Groq API key (canonical 52-char
    body; 32+ floor allows future format variations).
  * ``r8_<40 alphanumeric>`` — Replicate API token (strict 40-
    char body matches the documented format).
  * ``pplx-<32+ alphanumeric>`` — Perplexity API key (canonical
    48+ body; 32+ floor allows future variations).

Each detector uses the canonical ``(?<![A-Za-z0-9])...
(?![A-Za-z0-9])`` boundary anchors per the established style.

Marker: SENTINEL_AI_PLATFORM_TOKENS_DRIFT.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.utils.secret_scanner import scan_repository


SENTINEL_AI_PLATFORM_TOKENS_DRIFT = (
    "AI/ML platform token family drift: Groq (gsk_) / Replicate (r8_) "
    "/ Perplexity (pplx-) missing from _KNOWN_TOKENS"
)


# Realistic token shapes per vendor documentation:
_GROQ_TOKEN = "gsk_AbCdEfGhIjKlMnOpQrStUvWxYz0123456789AbCdEfGhIjKlMn"  # gsk_ + 48 alnum
assert _GROQ_TOKEN.startswith("gsk_")
assert len(_GROQ_TOKEN) >= 36
_REPLICATE_TOKEN = "r8_AbCdEfGhIjKlMnOpQrStUvWxYz01234567890123"  # r8_ + 40 alnum
assert _REPLICATE_TOKEN.startswith("r8_")
assert len(_REPLICATE_TOKEN) == 43  # 3 prefix + 40 body
_PERPLEXITY_TOKEN = "pplx-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789AbCdEfGhIjKlMn"  # pplx- + 50 alnum
assert _PERPLEXITY_TOKEN.startswith("pplx-")
assert len(_PERPLEXITY_TOKEN) >= 36


# ---------------------------------------------------------------------------
# (1) Per-vendor attribution PoCs: each vendor's canonical token shape
#     must yield its vendor-specific reason, not generic
#     "Hochentropischer Token-String" or "Verdächtige Zuweisung".
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "token,expected_reason,vendor",
    [
        (_GROQ_TOKEN, "Groq API Key gefunden", "Groq"),
        (_REPLICATE_TOKEN, "Replicate API Token gefunden", "Replicate"),
        (_PERPLEXITY_TOKEN, "Perplexity API Key gefunden", "Perplexity"),
    ],
)
def test_ai_platform_token_yields_vendor_attribution(
    tmp_path: Path, token: str, expected_reason: str, vendor: str
) -> None:
    """Each AI/ML platform token must yield the vendor-specific
    ``<vendor> API ... gefunden`` reason. Pre-fix these tokens fell
    into the generic ``Hochentropischer Token-String`` or
    ``Verdächtige Zuweisung eines potentiellen Secrets`` branches,
    losing the vendor-specific attribution that incident-response
    keys off (each platform has its own control plane and revocation
    flow).
    """
    file_path = tmp_path / "leaked_config.py"
    file_path.write_text(f"# bare {vendor} token in source\nTOKEN = '{token}'\n", encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])

    reasons = [f.reason for f in findings]
    assert expected_reason in reasons, (
        f"{vendor} token did not yield vendor attribution; got "
        f"reasons {reasons!r}. ({SENTINEL_AI_PLATFORM_TOKENS_DRIFT})"
    )
    # Raw token must not appear in findings (redaction contract).
    assert token not in [f.match for f in findings]


# ---------------------------------------------------------------------------
# (2) Bare-token (no assignment context) attribution: the canonical
#     leak shape from documentation curl examples or HAR exports.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "token,expected_reason,vendor",
    [
        (_GROQ_TOKEN, "Groq API Key gefunden", "Groq"),
        (_REPLICATE_TOKEN, "Replicate API Token gefunden", "Replicate"),
        (_PERPLEXITY_TOKEN, "Perplexity API Key gefunden", "Perplexity"),
    ],
)
def test_ai_platform_token_in_curl_example(
    tmp_path: Path, token: str, expected_reason: str, vendor: str
) -> None:
    """A leaked token in a documentation curl example (no variable
    assignment context) must still yield vendor attribution. This
    is the most common real-world leak shape — README snippets, CI
    debug output, requests library debug logs."""
    file_path = tmp_path / "README.md"
    file_path.write_text(
        f"## {vendor} API usage\n\n"
        f"```bash\n"
        f'curl -H "Authorization: Bearer {token}" \\\n'
        f"     https://api.{vendor.lower()}.com/v1/chat/completions\n"
        f"```\n",
        encoding="utf-8",
    )

    findings = scan_repository(tmp_path, paths=[file_path])

    reasons = [f.reason for f in findings]
    assert expected_reason in reasons, (
        f"{vendor} bare token did not yield vendor attribution; got "
        f"reasons {reasons!r}. ({SENTINEL_AI_PLATFORM_TOKENS_DRIFT})"
    )


# ---------------------------------------------------------------------------
# (3) Negative cases: ensure the new detectors do NOT over-match
#     similar-but-distinct prefixes or natural-language text.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        # Prefix-only without body — too short for the body floor.
        "gsk_short",
        "r8_short",
        "pplx-short",
        # Natural-language mentions
        "The Groq inference platform offers very fast token generation.",
        "Replicate is a popular hosted model API.",
        "Use Perplexity for AI-powered search.",
        # Code identifiers that happen to contain similar prefixes
        "def gsk_handler(req): pass",
        "class R8Handler: pass",
        # Replicate's r8_ prefix is short — guard against `r8` as a
        # word followed by 40 chars of unrelated content (rare but
        # possible). The lookbehind ``(?<![A-Za-z0-9])`` should
        # prevent matching mid-word.
        "myr8_dataAbCdEfGhIjKlMnOpQrStUvWxYz0123456789",  # 'myr8_' embedded
        # Pure entropy strings without the vendor prefix
        "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789AbCdEfGh",
    ],
)
def test_no_false_positives_on_natural_text(
    tmp_path: Path, text: str
) -> None:
    """The new detectors must NOT match natural-language text or
    code identifiers that happen to mention the vendor name without
    a canonical token-shape suffix.
    """
    file_path = tmp_path / "natural.txt"
    file_path.write_text(f"{text}\n", encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])

    vendor_reasons = {
        "Groq API Key gefunden",
        "Replicate API Token gefunden",
        "Perplexity API Key gefunden",
    }
    matched_vendors = [f.reason for f in findings if f.reason in vendor_reasons]
    assert not matched_vendors, (
        f"False-positive vendor attribution for natural text "
        f"{text!r}; got reasons {matched_vendors!r}. "
        f"({SENTINEL_AI_PLATFORM_TOKENS_DRIFT})"
    )


# ---------------------------------------------------------------------------
# (4) Cross-detector ordering: the new vendor detectors must NOT
#     cannibalise existing detectors. Verify regression: Anthropic /
#     OpenAI / Hugging Face continue to detect.
# ---------------------------------------------------------------------------


def test_anthropic_detection_still_works(tmp_path: Path) -> None:
    """Adding AI platform detectors must NOT break Anthropic
    detection. Regression guard."""
    anthropic_token = "sk-ant-api03-" + "A" * 95 + "B"
    file_path = tmp_path / "claude_client.py"
    file_path.write_text(f'API_KEY = "{anthropic_token}"\n', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    assert "Anthropic API Key gefunden" in reasons, (
        f"Regression: Anthropic detection broke. Got reasons "
        f"{reasons!r}. ({SENTINEL_AI_PLATFORM_TOKENS_DRIFT})"
    )


def test_openai_detection_still_works(tmp_path: Path) -> None:
    """Adding AI platform detectors must NOT break OpenAI
    detection."""
    openai_token = "sk-" + "A" * 48
    file_path = tmp_path / "openai_client.py"
    file_path.write_text(f'API_KEY = "{openai_token}"\n', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    assert "OpenAI API Key gefunden" in reasons, (
        f"Regression: OpenAI detection broke. Got reasons "
        f"{reasons!r}. ({SENTINEL_AI_PLATFORM_TOKENS_DRIFT})"
    )


def test_hugging_face_detection_still_works(tmp_path: Path) -> None:
    """Adding AI platform detectors must NOT break Hugging Face
    detection."""
    hf_token = "hf_" + "A" * 36
    file_path = tmp_path / "hf_client.py"
    file_path.write_text(f'token = "{hf_token}"\n', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    assert "Hugging Face Access Token gefunden" in reasons, (
        f"Regression: Hugging Face detection broke. Got reasons "
        f"{reasons!r}. ({SENTINEL_AI_PLATFORM_TOKENS_DRIFT})"
    )


# ---------------------------------------------------------------------------
# (5) Multi-vendor PoC: a single config file with all four AI
#     platform tokens (existing Anthropic/OpenAI/HF + new Groq/
#     Replicate/Perplexity) must yield all four attributions.
# ---------------------------------------------------------------------------


def test_multi_vendor_config_yields_all_attributions(tmp_path: Path) -> None:
    """A realistic AI app config file embedding multiple vendor
    tokens must yield ALL vendor-specific attributions, not just
    a subset. The cross-platform pivoting threat model: a leak of
    the file leaks all embedded tokens, each requiring its own
    revocation flow."""
    anthropic = "sk-ant-api03-" + "A" * 95 + "B"
    openai = "sk-" + "B" * 48
    hf = "hf_" + "C" * 36
    file_path = tmp_path / "multi_ai_config.py"
    file_path.write_text(
        f'ANTHROPIC_KEY = "{anthropic}"\n'
        f'OPENAI_KEY = "{openai}"\n'
        f'HF_TOKEN = "{hf}"\n'
        f'GROQ_KEY = "{_GROQ_TOKEN}"\n'
        f'REPLICATE_TOKEN = "{_REPLICATE_TOKEN}"\n'
        f'PERPLEXITY_KEY = "{_PERPLEXITY_TOKEN}"\n',
        encoding="utf-8",
    )

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = set(f.reason for f in findings)

    expected = {
        "Anthropic API Key gefunden",
        "OpenAI API Key gefunden",
        "Hugging Face Access Token gefunden",
        "Groq API Key gefunden",
        "Replicate API Token gefunden",
        "Perplexity API Key gefunden",
    }
    missing = expected - reasons
    assert not missing, (
        f"Multi-vendor config missing attributions for: {missing!r}. "
        f"Got reasons: {reasons!r}. "
        f"({SENTINEL_AI_PLATFORM_TOKENS_DRIFT})"
    )


# ---------------------------------------------------------------------------
# (6) Membership invariant: the three new entries appear in
#     ``_KNOWN_TOKENS`` and their reasons are unique strings.
# ---------------------------------------------------------------------------


def test_new_ai_platform_reasons_in_known_tokens() -> None:
    """The three new vendor-specific reasons must be in the
    canonical ``_KNOWN_TOKENS`` table. This invariant pins the
    membership against future regression."""
    from src.utils.secret_scanner import _KNOWN_TOKENS

    reasons = {reason for _regex, reason in _KNOWN_TOKENS}
    required = {
        "Groq API Key gefunden",
        "Replicate API Token gefunden",
        "Perplexity API Key gefunden",
    }
    missing = required - reasons
    assert not missing, (
        f"Required reasons missing from _KNOWN_TOKENS: {missing!r}. "
        f"({SENTINEL_AI_PLATFORM_TOKENS_DRIFT})"
    )


# ---------------------------------------------------------------------------
# (7) Masking-contract test: the raw token body must NEVER appear
#     unmasked in finding output.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "token,vendor",
    [
        (_GROQ_TOKEN, "Groq"),
        (_REPLICATE_TOKEN, "Replicate"),
        (_PERPLEXITY_TOKEN, "Perplexity"),
    ],
)
def test_ai_platform_masking_contract(
    tmp_path: Path, token: str, vendor: str
) -> None:
    """Each vendor's token must be masked before surfacing in
    findings — the raw credential body must never reach CI logs /
    GitHub PR comments / pre-commit hook output."""
    file_path = tmp_path / "config.py"
    file_path.write_text(f'TOKEN = "{token}"\n', encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])

    for finding in findings:
        assert token not in finding.match, (
            f"{vendor} masking VIOLATED: raw token in "
            f"finding.match={finding.match!r}. "
            f"({SENTINEL_AI_PLATFORM_TOKENS_DRIFT})"
        )
