"""Sentinel PoC: AI/ML platform token Round 2 — xAI Grok (``xai-``) +
OpenRouter (``sk-or-v1-``). Both are heavily-used 2024-2025 AI/ML
inference platforms whose tokens were NOT enumerated in PR #1534's
``_KNOWN_TOKENS`` extension (which closed Groq / Replicate / Perplexity).

This round closes the named-but-deferred xAI candidate from PR #1534's
journal entry, AND adds the previously-unenumerated OpenRouter token —
an aggregator that provides OpenAI-compatible API access to 200+ LLMs
via a single API.

Pre-fix inventory (PoC verified):

  * **xAI Grok** (``xai-<32+ alphanumeric>``) — Elon Musk's xAI
    platform. Real tokens leak as generic ``Hochentropischer
    Token-String`` (when the body has ≥2 char categories and meets
    uniqueness floor) or ``Verdächtige Zuweisung eines potentiellen
    Secrets`` (in assignment context). Either way, the xAI-specific
    attribution is lost — incident-response must guess between xAI
    (revoke at console.x.ai/team/<id>/api-keys), Anthropic, OpenAI,
    Groq, Replicate, Perplexity, etc.

  * **OpenRouter** (``sk-or-v1-<32+ alphanumeric>``) — the unified
    OpenAI-compatible API aggregator (https://openrouter.ai) that
    proxies requests to 200+ different LLMs (Claude, GPT, Llama,
    Mixtral, Gemini, etc.). Heavily used by indie developers as a
    single-credential abstraction. The ``sk-or-v1-`` prefix is
    structurally distinct from OpenAI's ``sk-<48 alphanumeric>`` form
    (the embedded hyphens in ``sk-or-v1-`` prevent matching OpenAI's
    strict alphanumeric-body regex), so OpenRouter tokens fall to the
    entropy fallback or assignment heuristic — losing the
    OpenRouter-specific attribution. The high-uniqueness real-world
    shape (64 hex body) usually clears the entropy floor, but
    pathological low-uniqueness variants (e.g., repetitive hex
    sequences in test fixtures) can silently slip through entirely.

Threat model
------------

Both vendors share the AI/ML platform credential threat profile
established in PR #1534:

  * **PRIMARY: Billing-credit drain**. xAI Grok-4 API charges
    USD 5-15 per 1M tokens; OpenRouter passes through underlying-
    provider costs (Claude-3.5-Sonnet at $3/1M input + $15/1M
    output, Grok-4 at $5/1M input + $15/1M output). An attacker
    with a leaked token can drain $1000s in hours via large-context
    completions or fine-tuning runs.

  * **Model-prompt exfiltration**. The attacker queries any of
    OpenRouter's 200+ proxied models via the victim's account,
    exfiltrating proprietary prompts / system instructions.

  * **Account takeover via OpenRouter's BYOK (bring your own
    key)**. OpenRouter allows users to attach their own provider
    keys for fallback / cost optimization. A leaked OpenRouter
    token grants access to ALL the user's attached provider keys
    (visible / reusable via the OpenRouter dashboard). This is
    a CROSS-PLATFORM PIVOT amplifier unique to aggregator
    platforms.

  * **Cross-platform pivoting**. Same as the Round-1 platforms:
    operators frequently embed multiple AI-platform tokens in
    the same config file; OpenRouter's BYOK list compounds the
    risk by enabling pivot to the user's other vendor keys
    without separately leaking them.

Distinct revocation flows:
  * xAI: console.x.ai/team/<id>/api-keys
  * OpenRouter: openrouter.ai/keys

Real-world emission patterns
----------------------------

  * Python notebook outputs hardcoding ``client = openai.OpenAI(
    api_key=os.environ["OPENROUTER_API_KEY"],
    base_url="https://openrouter.ai/api/v1")``.
  * ``.env`` files: ``XAI_API_KEY=xai-...`` /
    ``OPENROUTER_API_KEY=sk-or-v1-...``.
  * Documentation curl examples with real (live!) tokens.
  * CI/CD pipeline debug output.

Fix
---

Add two new entries to ``_KNOWN_TOKENS``:

  * ``xai-<32+ alphanumeric>``
  * ``sk-or-v1-<32+ alphanumeric>``

Each detector uses the canonical ``(?<![A-Za-z0-9])...
(?![A-Za-z0-9])`` boundary anchors per the established style.

Marker: SENTINEL_AI_PLATFORM_TOKENS_ROUND2_DRIFT.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.utils.secret_scanner import scan_repository


SENTINEL_AI_PLATFORM_TOKENS_ROUND2_DRIFT = (
    "AI/ML platform token Round 2 drift: xAI Grok (xai-) + "
    "OpenRouter (sk-or-v1-) missing from _KNOWN_TOKENS"
)


# Realistic token shapes per vendor documentation:
_XAI_TOKEN = "xai-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789AbCdEfGhIjKlMnOpQrStUvWxYz01"
assert _XAI_TOKEN.startswith("xai-")
assert len(_XAI_TOKEN) >= 36
_OPENROUTER_TOKEN = "sk-or-v1-AbCdEf123abc456def789012345AbCdEfGhIjKlMnOpQrStUvWxYz0123"
assert _OPENROUTER_TOKEN.startswith("sk-or-v1-")
assert len(_OPENROUTER_TOKEN) >= 40


# ---------------------------------------------------------------------------
# (1) Per-vendor attribution PoCs: variable-assignment context
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "token,expected_reason,vendor",
    [
        (_XAI_TOKEN, "xAI API Key gefunden", "xAI"),
        (_OPENROUTER_TOKEN, "OpenRouter API Key gefunden", "OpenRouter"),
    ],
)
def test_round2_token_yields_vendor_attribution(
    tmp_path: Path, token: str, expected_reason: str, vendor: str
) -> None:
    """Each Round-2 AI/ML platform token must yield the vendor-specific
    attribution. Pre-fix tokens fell into generic ``Hochentropischer
    Token-String`` or ``Verdächtige Zuweisung`` branches."""
    file_path = tmp_path / "ai_config.py"
    file_path.write_text(
        f'# {vendor} API key\nTOKEN = "{token}"\n', encoding="utf-8"
    )

    findings = scan_repository(tmp_path, paths=[file_path])

    reasons = [f.reason for f in findings]
    assert expected_reason in reasons, (
        f"{vendor} token did not yield vendor attribution; got "
        f"reasons {reasons!r}. ({SENTINEL_AI_PLATFORM_TOKENS_ROUND2_DRIFT})"
    )
    assert token not in [f.match for f in findings]


# ---------------------------------------------------------------------------
# (2) Bare-token PoCs (documentation curl examples, HAR exports)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "token,expected_reason,vendor,api_host",
    [
        (_XAI_TOKEN, "xAI API Key gefunden", "xAI", "api.x.ai"),
        (
            _OPENROUTER_TOKEN,
            "OpenRouter API Key gefunden",
            "OpenRouter",
            "openrouter.ai",
        ),
    ],
)
def test_round2_token_in_curl_example(
    tmp_path: Path,
    token: str,
    expected_reason: str,
    vendor: str,
    api_host: str,
) -> None:
    """A leaked token in a curl example (no assignment context) must
    still yield vendor attribution. Most common real-world leak shape."""
    file_path = tmp_path / "README.md"
    file_path.write_text(
        f"## {vendor} API usage\n\n"
        f"```bash\n"
        f'curl -H "Authorization: Bearer {token}" \\\n'
        f"     https://{api_host}/v1/chat/completions\n"
        f"```\n",
        encoding="utf-8",
    )

    findings = scan_repository(tmp_path, paths=[file_path])

    reasons = [f.reason for f in findings]
    assert expected_reason in reasons, (
        f"{vendor} bare token did not yield vendor attribution; got "
        f"reasons {reasons!r}. ({SENTINEL_AI_PLATFORM_TOKENS_ROUND2_DRIFT})"
    )


# ---------------------------------------------------------------------------
# (3) Negative cases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        # Prefix-only without body
        "xai-short",
        "sk-or-v1-short",
        # Natural-language mentions
        "We integrated xAI's Grok model last week.",
        "OpenRouter supports 200+ models through a unified API.",
        # Code identifiers that contain similar substrings
        "def xai_handler(req): pass",
        "class OpenRouterClient: pass",
        # Pure entropy without the vendor prefix
        "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789AbCdEfGhIjKlMn",
    ],
)
def test_no_false_positives_on_natural_text(
    tmp_path: Path, text: str
) -> None:
    """The new detectors must NOT match natural-language text or code
    identifiers."""
    file_path = tmp_path / "natural.txt"
    file_path.write_text(f"{text}\n", encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])

    vendor_reasons = {"xAI API Key gefunden", "OpenRouter API Key gefunden"}
    matched = [f.reason for f in findings if f.reason in vendor_reasons]
    assert not matched, (
        f"False-positive vendor attribution for text {text!r}; "
        f"got {matched!r}. ({SENTINEL_AI_PLATFORM_TOKENS_ROUND2_DRIFT})"
    )


# ---------------------------------------------------------------------------
# (4) Cross-detector regression guards: existing AI detectors continue
# ---------------------------------------------------------------------------


def test_groq_detection_still_works(tmp_path: Path) -> None:
    """Round-2 additions must NOT break Round-1 Groq detection."""
    groq_token = "gsk_" + "A" * 52
    file_path = tmp_path / "config.py"
    file_path.write_text(f'KEY = "{groq_token}"\n', encoding="utf-8")
    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    assert "Groq API Key gefunden" in reasons, (
        f"Regression: Groq broke. reasons={reasons!r}. "
        f"({SENTINEL_AI_PLATFORM_TOKENS_ROUND2_DRIFT})"
    )


def test_replicate_detection_still_works(tmp_path: Path) -> None:
    """Round-2 additions must NOT break Round-1 Replicate detection."""
    repl_token = "r8_" + "A" * 40
    file_path = tmp_path / "config.py"
    file_path.write_text(f'KEY = "{repl_token}"\n', encoding="utf-8")
    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    assert "Replicate API Token gefunden" in reasons, (
        f"Regression: Replicate broke. reasons={reasons!r}. "
        f"({SENTINEL_AI_PLATFORM_TOKENS_ROUND2_DRIFT})"
    )


def test_openai_detection_still_works(tmp_path: Path) -> None:
    """Round-2 additions must NOT break OpenAI detection — the
    OpenAI ``sk-<48 alphanumeric>`` regex must still fire for OpenAI
    tokens, and NOT be displaced by the new OpenRouter ``sk-or-v1-``
    detector. The two patterns are mutually exclusive at the prefix
    level (``sk-`` followed by alphanumeric vs ``sk-`` followed by
    ``or-v1-``)."""
    openai_token = "sk-" + "B" * 48
    file_path = tmp_path / "openai.py"
    file_path.write_text(f'KEY = "{openai_token}"\n', encoding="utf-8")
    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    assert "OpenAI API Key gefunden" in reasons, (
        f"Regression: OpenAI broke after adding OpenRouter. "
        f"reasons={reasons!r}. ({SENTINEL_AI_PLATFORM_TOKENS_ROUND2_DRIFT})"
    )


def test_openrouter_does_not_steal_openai_attribution(tmp_path: Path) -> None:
    """A pure OpenAI token (``sk-`` + 48 alphanumeric) must NOT be
    falsely attributed to OpenRouter. The two detectors must remain
    mutually exclusive at the prefix level."""
    openai_token = "sk-" + "ABCDEF1234567890" * 3
    file_path = tmp_path / "openai.py"
    file_path.write_text(f'KEY = "{openai_token}"\n', encoding="utf-8")
    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = [f.reason for f in findings]
    # OpenAI attribution must fire, OpenRouter must NOT.
    assert "OpenAI API Key gefunden" in reasons
    assert "OpenRouter API Key gefunden" not in reasons, (
        f"Cross-attribution leak: OpenAI token mis-attributed to "
        f"OpenRouter. reasons={reasons!r}. "
        f"({SENTINEL_AI_PLATFORM_TOKENS_ROUND2_DRIFT})"
    )


# ---------------------------------------------------------------------------
# (5) Multi-vendor PoC: all 8 AI/ML vendors yield distinct attributions
# ---------------------------------------------------------------------------


def test_multi_vendor_full_landscape_yields_all_attributions(
    tmp_path: Path,
) -> None:
    """A realistic AI app config embedding all 8 vendor tokens
    (Round-1 Anthropic/OpenAI/HF/Groq/Replicate/Perplexity + Round-2
    xAI/OpenRouter) must yield ALL 8 distinct attributions."""
    tokens = {
        "ANTHROPIC_KEY": "sk-ant-api03-" + "A" * 95 + "B",
        "OPENAI_KEY": "sk-" + "B" * 48,
        "HF_TOKEN": "hf_" + "C" * 36,
        "GROQ_KEY": "gsk_" + "D" * 52,
        "REPLICATE_TOKEN": "r8_" + "E" * 40,
        "PERPLEXITY_KEY": "pplx-" + "F" * 48,
        "XAI_KEY": _XAI_TOKEN,
        "OPENROUTER_KEY": _OPENROUTER_TOKEN,
    }
    body = "\n".join(f'{k} = "{v}"' for k, v in tokens.items())
    file_path = tmp_path / "ai_landscape.py"
    file_path.write_text(body + "\n", encoding="utf-8")

    findings = scan_repository(tmp_path, paths=[file_path])
    reasons = set(f.reason for f in findings)

    expected = {
        "Anthropic API Key gefunden",
        "OpenAI API Key gefunden",
        "Hugging Face Access Token gefunden",
        "Groq API Key gefunden",
        "Replicate API Token gefunden",
        "Perplexity API Key gefunden",
        "xAI API Key gefunden",
        "OpenRouter API Key gefunden",
    }
    missing = expected - reasons
    assert not missing, (
        f"Multi-vendor full landscape missing: {missing!r}. "
        f"Got: {reasons!r}. ({SENTINEL_AI_PLATFORM_TOKENS_ROUND2_DRIFT})"
    )


# ---------------------------------------------------------------------------
# (6) Membership invariant
# ---------------------------------------------------------------------------


def test_round2_reasons_in_known_tokens() -> None:
    """The two new reasons must appear in ``_KNOWN_TOKENS``."""
    from src.utils.secret_scanner import _KNOWN_TOKENS

    reasons = {reason for _regex, reason in _KNOWN_TOKENS}
    required = {"xAI API Key gefunden", "OpenRouter API Key gefunden"}
    missing = required - reasons
    assert not missing, (
        f"Required reasons missing from _KNOWN_TOKENS: {missing!r}. "
        f"({SENTINEL_AI_PLATFORM_TOKENS_ROUND2_DRIFT})"
    )


# ---------------------------------------------------------------------------
# (7) Masking contract
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "token,vendor",
    [(_XAI_TOKEN, "xAI"), (_OPENROUTER_TOKEN, "OpenRouter")],
)
def test_round2_masking_contract(
    tmp_path: Path, token: str, vendor: str
) -> None:
    """Raw token body must never appear in finding output."""
    file_path = tmp_path / "config.py"
    file_path.write_text(f'TOKEN = "{token}"\n', encoding="utf-8")
    findings = scan_repository(tmp_path, paths=[file_path])
    for finding in findings:
        assert token not in finding.match, (
            f"{vendor} masking VIOLATED: finding.match={finding.match!r}. "
            f"({SENTINEL_AI_PLATFORM_TOKENS_ROUND2_DRIFT})"
        )
