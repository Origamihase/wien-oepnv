## 2026-05-06 - Env-Controlled Regex Compiles Without ReDoS Heuristic
**Vulnerability:** `src/providers/vor.py:_compile_regex` read `VOR_BUS_INCLUDE_REGEX` / `VOR_BUS_EXCLUDE_REGEX` from the environment, validated only `re.error` (i.e. *syntax*), and returned the compiled pattern. A pattern with classic catastrophic backtracking (`(a+)+$`, `(.*)*`, `(a?)+`) compiles cleanly but pegs CPU at 100 % during the per-token `match` / `search` loop in `_extract_bus_lines`. Because the regex is reapplied to every bus-line token of every VOR product on every feed build, a single misconfig (operator typo, leaked CI env, compromised secret store) silently turns the build into a DoS against itself. The codebase already had a ReDoS test for the *hardcoded* `LINES_COMPLEX_PREFIX_RE` in `wl_lines.py` but no equivalent guard for the *operator-supplied* patterns in `vor.py`.
**Learning:** Env-controlled regexes are a different threat shape from env-controlled URLs / paths / ints — `validate_http_url`, `validate_path`, and `min(..., DEFAULT)` don't apply, so the existing toolkit doesn't cover them. ReDoS detection in pure Python is hard (no native regex timeout), so the practical defense is two cheap layers before `re.compile`: (a) a length cap on the pattern itself (oversized input risks memory exhaustion during compile), and (b) a static-string heuristic that rejects nested unbounded quantifiers around groups (`[+*?]\s*\)\s*[+*]`, tolerating whitespace). The heuristic is intentionally conservative — alternation-overlap ReDoS like `(a|aa)+` slips through — but it covers the patterns historically responsible for real outages and falls back to vetted defaults whenever it fires.
**Prevention:** Whenever a future env var is plumbed into `re.compile(...)`, mirror the `_compile_regex` pattern: cap the input length, scan with `_REDOS_NESTED_QUANTIFIER_RE`, then `try/except re.error`. Add a sanity test asserting that the project's own *defaults* are not flagged by the heuristic — otherwise the fallback path is unreachable and the next operator override silently bypasses the guard. Grep for `re.compile(.*os.getenv` / `re.compile(.*os.environ` to enumerate any new instances of this pattern.

## 2026-05-06 - Env-Override Disables Hard Contract Cap on VOR Quota
**Vulnerability:** `src/providers/vor.py:MAX_REQUESTS_PER_DAY = _load_int_env("VOR_MAX_REQUESTS_PER_DAY", DEFAULT_MAX_REQUESTS_PER_DAY)` accepted any positive integer. `_load_int_env` only enforces `value > 0`, so `VOR_MAX_REQUESTS_PER_DAY=99999` (intentional misconfig, leaked CI env, or compromised secret store) raised the daily-quota gate above the *contractual* hard cap of 100/day for the VAO Start tier. The constant is read at 8+ sites in `vor.py` and again by `_limit_reached` in `scripts/update_vor_cache.py`; every gate would silently approve requests beyond the cap, risking access-ID suspension by the upstream provider.
**Learning:** Default values are not equivalent to *upper bounds* when the constant encodes a third-party contractual limit. The pattern `MAX_REQUESTS_PER_DAY = _load_int_env("…", DEFAULT)` reads as "default 100, configurable", not "ceiling 100, can only tighten" — but the latter is the security-correct semantics for any quota that has external consequences (rate limits, billing, contract clauses). The same pattern likely applies elsewhere in the codebase (`HTTP_TIMEOUT` for Slowloris, `VOR_MAX_STATIONS_PER_RUN` for fan-out) and should be audited the next time those constants are touched.
**Prevention:** When an integer env var feeds an external-contract or DoS-relevant constant, wrap the `_load_int_env(...)` call in `min(..., DEFAULT)` (or a documented `MAX_*` ceiling) so the env var can only *tighten* the value. Add a security comment at the call site naming the contract clause and the consequence of bypass — future readers must understand the env var is intentionally *not* "set this to whatever you want".

## 2026-05-06 - `except ValueError` Misleadingly Hides Zero-Trust Gap
**Vulnerability:** `src/feed/reporting.py:_submit_github_issue` had `detail = response.json().get("message", response.text)` and `data = response.json(); issue_url = data.get("html_url")`, both wrapped in `except ValueError`. That `except` clause LOOKS defensive — like it covers JSON parsing failures — but it only catches JSON *decode* errors. A successfully-decoded but non-dict body (list/scalar/null from a misbehaving GHE proxy or unexpected upstream change) would propagate `AttributeError: 'list' object has no attribute 'get'` upward, breaking the feed-build flow at the very moment we're trying to *report* a feed-build error.
**Learning:** The previous Zero-Trust journal entries (`places/client.py`, `wl_fetch.py`, `vor.py`) all featured a *missing* `try/except` around `json.loads` — easy to grep for. This one was harder to spot because the `try/except ValueError` is already there; it just doesn't cover the failure mode that matters. The prevention rule ("grep for `json.loads` and `.json()`") only works if you also inspect what's chained after the parse — `.json().get(...)` or `.get(...)` on a `data = response.json()` result, even inside a try/except.
**Prevention:** When auditing JSON parsing sites, the *real* signal is `.get(...)` (or any other dict-only method) used directly on the parse result. Grep `\.json\(\)\.get\|json\.loads\(.*\)\.get` to enumerate them. An `except ValueError` is not equivalent to an `isinstance(payload, dict)` guard — they cover orthogonal failure modes (decode vs shape). Add the isinstance check **inside** the else-branch of the try/except so both are enforced.

## 2026-05-06 - Provider URL Env Vars Weaponise the Public Feed
**Vulnerability:** `WL_RSS_URL` (Wiener Linien) and `OEBB_RSS_URL` (ÖBB) were validated only with `validate_http_url()`, which checks SSRF/DNS-rebinding properties but not host identity. An env override to `https://evil.com` would (a) feed attacker-controlled JSON/XML straight into the cached items, and (b) place the attacker URL into every WL item's `<link>` element (and into the per-item ÖBB `<link>` fallback). The downstream RSS feed is public, so this turns the project into a phishing/redirect amplifier — anyone subscribed to the feed clicks through to the attacker.
**Learning:** "No credentials sent to this URL" doesn't mean an env-controlled URL is safe. When a fetched URL becomes part of the *output*, the host pin is just as important as it is for credential targets. The specific vector that escalated this from "content injection" to "phishing primitive" was reading the env URL straight back into the feed item `<link>`, which I almost missed because I was thinking about the HTTP request side, not the XML output side.
**Prevention:** Trace every env-controlled URL all the way to the *output*, not just the request. If the URL is interpolated into a public artefact (RSS link, sitemap, generated HTML), pin it to the official upstream host with a frozenset allowlist. Both providers used a tiny inline `_validated_X_url(raw)` helper that mirrors `_is_trusted_github_api` in shape, so the same pattern is reusable for any future external endpoint.

## 2026-05-06 - URL Path Injection via Repository Slug
**Vulnerability:** After PR #1258 pinned the GitHub auto-issue reporter to a known host, `FEED_GITHUB_REPOSITORY` (or `GITHUB_REPOSITORY`) was still interpolated raw into `f"{api_url}/repos/{repo}/issues"`. A slug like `owner/repo?injected=1`, `owner/../organizations`, or `owner/repo#frag` rewrote the URL to a *different* GitHub endpoint — still authorised by the token — letting an env-var override redirect the auto-issue post to an arbitrary repo or API surface (issue spam, repo enumeration, mistaken targets).
**Learning:** Pinning the host is necessary but not sufficient when subsequent path components are also env-controlled. Each segment that goes into a request URL needs its own grammar check matching the upstream service's documented format. GitHub's repo slug grammar (1–39 alphanumeric/hyphen owner, no leading hyphen; 1–100 alphanumeric/hyphen/underscore/dot name) is well-defined and trivial to enforce — the gap was that nobody enforced it.
**Prevention:** Every env-controlled value that is interpolated into a request URL must pass a grammar check **before** the URL is built. Use anchored `re.fullmatch` (or `\A...\Z`) so suffix injection (`owner/repo?leak=1`, `owner/repo/extra`) cannot slip through with a partial match. Validate at request time, not just at config time, so the check runs on the value actually used.

## 2026-05-06 - GitHub Token Leak via Env-Controlled API URL
**Vulnerability:** `src/feed/reporting.py` read `FEED_GITHUB_API_URL` (and `GITHUB_API_URL`) directly from the environment and only ran `validate_http_url()` on the constructed `{api_url}/repos/{repo}/issues` endpoint. `validate_http_url()` checks SSRF/DNS-rebinding properties (no localhost, public IP, allowed port, …) but *not* host identity — so any syntactically valid public host (e.g. `https://evil.example.com`, or a typosquat like `api.gihub.com`) would pass and the `Authorization: Bearer ghs_…` header was attached by the next line. Effectively a one-shot token exfiltration primitive whenever an attacker (or a misconfiguration) controlled that env var.
**Learning:** SSRF guards and host-identity guards solve different problems. `validate_http_url()` is designed to keep the request from hitting *internal* infrastructure; it is intentionally not opinionated about which *external* host you talk to. When sending a credential to a third-party API, you need a **separate** allowlist check on the API hostname, applied **before** any token attachment. The default `https://api.github.com` was so well-known it lulled callers into trusting the implicit allowlist, but the env override silently disabled it.
**Prevention:** Any code that attaches a credential to an outbound request must validate the request hostname against a service-specific allowlist (`api.github.com` exact, plus `/api/v3` or `/api/graphql` paths for GitHub Enterprise Server) **before** `session.headers.update(...)` or equivalent. When the token's issuer is known (e.g. `ghs_*` from GitHub Actions), bake the trusted host pattern into the same module that reads the token.

## 2026-05-06 - Path Containment Drift in Update Scripts
**Vulnerability:** `scripts/update_baustellen_cache.py` read `BAUSTELLEN_FALLBACK_PATH` from the environment, called `Path(...).resolve()` (which **follows symlinks**), and read the file as JSON without any containment check. An attacker controlling that env var could point the script at arbitrary on-disk JSON-shaped files, whose content would then be merged into the feed cache. Symlinks inside `data/` made even an "in-repo" allowlist insufficient if it relied on the original path string.
**Learning:** The codebase already had `_resolve_path()` in `src/providers/vor.py` and `validate_path()` in `src/feed/config.py` for exactly this purpose, but the pattern hadn't been propagated to every script that reads paths from env. `Path.resolve()` is **not** a containment check — it actively *escapes* containment by following symlinks. Any containment check must therefore happen *after* `resolve()`, comparing the resolved path's `relative_to(BASE)` membership.
**Prevention:** When a script reads a file path from an env var or CLI arg, always (1) `resolve()` first to canonicalise, then (2) `relative_to(REPO_ROOT)` (or a more specific allow-root) to enforce containment, and (3) fall back to a known-good default on rejection. Grep for `os.getenv.*PATH\|os.getenv.*FILE` paired with `Path(...).resolve()` to find new instances of this pattern.

## 2026-05-06 - .env Files Inherit Umask Permissions
**Vulnerability:** `scripts/configure_feed.py` wrote the `.env` file via `Path.write_text()`, which respects the process umask. With the typical 0o022 umask the file landed at 0o644 — so `VOR_ACCESS_ID` and any other custom secrets were group/world-readable on shared systems. Worse, re-running the wizard on an existing 0o644 file kept those loose permissions because `write_text` does not change permissions on overwrite.
**Learning:** The codebase already had `atomic_write(..., permissions=0o600)` for caches in `src/utils/files.py`, but the wizard — the canonical entry point that *creates* secrets — was the one place that bypassed it. Files holding secrets must be *created* with restrictive permissions (via `os.open(..., 0o600)` or `atomic_write`), not just chmod'd later, because there is always a race window where another process can read them.
**Prevention:** Any code that writes a file containing credentials must go through `atomic_write` with `permissions=0o600`. Grep for `write_text\|write_bytes` in scripts/ when adding a new credential-handling flow; if the path is `.env`, a credentials file, or a token cache, the call must use atomic_write instead.

## 2025-02-12 - Custom .env Parsing Pitfalls
**Vulnerability:** Incomplete escaping logic in custom `.env` parser allowing secrets with quotes to be corrupted.
**Learning:** Reimplementing standard formats (like shell variable assignment) often misses edge cases like escaped quotes.
**Prevention:** Prefer established libraries (e.g., `python-dotenv`) or rigorous testing of edge cases when implementing low-level parsers.

## 2026-02-02 - Redundant Query Parameter Injection
**Vulnerability:** The VOR provider manually injected `accessId` into query parameters even when `apply_authentication` was configured to use Headers, leading to potential secret leakage in URLs.
**Learning:** Manual overrides in specific API calls can bypass centralized security logic (like `apply_authentication`).
**Prevention:** Rely on centralized authentication handlers (middlewares/hooks) and avoid manual credential injection in individual request functions.

## 2026-02-14 - SSRF TOCTOU via Error Oracle
**Vulnerability:** `fetch_content_safe` checked `raise_for_status()` before verifying the connected IP, allowing attackers to probe internal networks by observing error codes (e.g. 404 vs connection refused) even if the IP was blocked.
**Learning:** Security checks on the response object (like IP verification) must happen *before* any data (including status codes) is processed or returned to the caller.
**Prevention:** Enforce a strict "Verify-Then-Process" order for all network response handling.

## 2026-02-15 - Unbounded Redirects & Infrastructure TLDs
**Vulnerability:** The HTTP client used the default limit of 30 redirects, which exposes the application to resource exhaustion (DoS) via redirect loops. Additionally, infrastructure TLDs (.arpa, .kubernetes) were not blocked, potentially allowing SSRF against internal cluster services.
**Learning:** Default settings in libraries (like requests) often prioritize usability/compatibility over security.
**Prevention:** Explicitly configure limits (e.g., max_redirects) and maintain a comprehensive blocklist of internal/infrastructure TLDs for SSRF protection.

## 2025-10-26 - Sensitive Headers Leak on Cross-Origin Redirects
**Vulnerability:** Custom sensitive headers (e.g., `X-Goog-Api-Key`, `Private-Token`) were persisted by `requests` when following redirects to different domains, potentially leaking credentials to third-party servers.
**Learning:** `requests` only strips the `Authorization` header automatically on cross-origin redirects. Custom headers are preserved by default.
**Prevention:** Override `requests.Session.rebuild_auth` to explicitly strip a defined list of sensitive headers when the hostname changes during a redirect.

## 2026-03-01 - Partial Log Redaction on Quoted Secrets
**Vulnerability:** The regex used for masking sensitive query parameters (`key=value`) eagerly stopped at the first space, failing to mask the full value if it was a quoted string containing spaces (e.g. `token="secret value"` -> `token=*** value"`).
**Learning:** Simple regex exclusions like `[^&\s]+` are insufficient for formats that support quoting or escaping.
**Prevention:** Explicitly match and consume quoted strings (`"[^"]*"` or `'[^']*'`) *before* falling back to generic token matching in sanitization logic.

## 2026-03-02 - Token Scanning Misses Spaced Secrets
**Vulnerability:** The secret scanner's regex enforced strictly contiguous alphanumeric characters, causing it to miss valid secrets containing spaces (e.g. passphrases) or symbols, even when quoted.
**Learning:** Security tools that assume specific formats for secrets (like Base64) can create blind spots for other valid patterns (like natural language passphrases).
**Prevention:** When scanning for secrets, support broad value capture (e.g. any quoted string) and rely on secondary entropy/complexity checks rather than strict regex pattern matching.

## 2026-03-03 - Strict Category Checks Miss Low-Case Passphrases
**Vulnerability:** The secret scanner required at least two of {Upper, Lower, Digit}, causing it to miss long, high-entropy passphrases that were all-lowercase with spaces or symbols.
**Learning:** Complexity rules (like "must have upper and digit") designed for password policies are often too strict for secret scanning, where "context" (assignment to `PASSWORD`) implies high probability of a secret.
**Prevention:** When scanning high-confidence contexts (assignments), relax complexity checks or treat symbols/spaces as valid entropy categories.

## 2026-03-04 - Incomplete Secret Masking for Cloud Providers
**Vulnerability:** Standard secret masking (e.g., `api_key`, `token`) failed to catch provider-specific naming conventions like Azure's `Ocp-Apim-Subscription-Key` or `x-api-key`, leading to potential leakage in error logs.
**Learning:** Generic blocklists often miss vendor-specific headers or query parameters which are standard in enterprise environments.
**Prevention:** Regularly update secret sanitization lists with vendor-specific patterns (AWS, Azure, GCP) and use broad regex matching (e.g., `.*subscription.*key`) where performance permits.

## 2026-03-05 - Strict TLD Blocking Breaks Reserved Domains
**Vulnerability:** Moving the `_UNSAFE_TLDS` check before DNS resolution caused tests using `.test` and `.example` to fail, as these were correctly flagged as unsafe/internal but were needed for unit testing.
**Learning:** Security controls that enforce "secure by default" (like strict TLD blocking) can conflict with standard testing practices that rely on reserved domains (RFC 2606).
**Prevention:** When hardening validation logic, verify that test fixtures use public/safe domains (e.g. `example.com`) or explicitly mock the validation step if testing unrelated logic.

## 2026-03-08 - Multiline Secrets in .env
**Vulnerability:** The line-based `.env` parser truncated multiline secrets (like private keys), corrupting them and potentially leading to configuration errors or fallback to insecure methods.
**Learning:** Simple line-splitting parsers (`splitlines()`) cannot handle quoted strings that contain newlines, which are common in cryptographic keys.
**Prevention:** Use a state-machine or character-by-character parser that respects quoting rules across line boundaries when parsing configuration files.

## 2026-03-09 - Sanitization Gaps from Key Variations
**Vulnerability:** Exact string matching for sensitive keys (e.g., `client_id`) allowed variations like `Client-ID` or `client-id` to bypass sanitization in error logs.
**Learning:** Developers often assume canonical forms for keys, but HTTP protocols and frameworks allow case-insensitivity and separator variations.
**Prevention:** Normalize keys (lowercase, remove separators) before checking them against blocklists to ensure consistent redaction regardless of input format.

## 2026-03-10 - Unenforced Timeouts in Helper Functions
**Vulnerability:** The `fetch_content_safe` helper allowed `timeout=None` (disabling total read timeouts) if the caller did not explicitly provide a timeout, bypassing the Slowloris protection.
**Learning:** Optional security parameters in helper functions often default to "insecure" (e.g. `None`) to preserve flexibility, but this shifts the burden of security configuration to every caller.
**Prevention:** Helper functions should enforce secure defaults (e.g., `timeout=DEFAULT_TIMEOUT`) internally if the caller omits the argument, rather than relying on the caller to provide them.

## 2026-10-27 - Secrets in URL Fragments
**Vulnerability:** The error sanitization logic (`_sanitize_url_for_error`) only redacted query parameters and basic auth, but ignored URL fragments (e.g. `#access_token=...`) which are commonly used in OIDC implicit flows.
**Learning:** URL fragments are often treated as "client-side only" but can persist in error logs if the URL object is logged in its entirety. Standard query parsing tools (`parse_qsl`) do not automatically handle fragments.
**Prevention:** Explicitly parse and sanitize URL fragments using query-parameter logic (`parse_qsl`) if they appear to contain key-value pairs, especially for keys like `token` or `key`.

## 2026-10-28 - Broken Secret Roundtrip in .env
**Vulnerability:** The custom `.env` parser ignored standard escape sequences (`\n`, `\r`, `\t`) in double-quoted strings, while the configuration wizard actively escaped them. This caused multiline secrets (like private keys) to be corrupted (flattened to literal `\n`) during the roundtrip.
**Learning:** When implementing custom parsers for standard formats (like `.env`), ensure strict symmetry between the writer (escaping) and the reader (unescaping). Partial implementation leads to data corruption.
**Prevention:** Explicitly support standard escape sequences in custom parsers or verify roundtrip integrity with property-based tests.

## 2026-03-12 - Sensitive Headers Leak on Port Change
**Vulnerability:** The `_safe_rebuild_auth` logic only checked for hostname changes and scheme downgrades, failing to strip sensitive headers when redirecting to a different port on the same host (e.g. `example.com:8443` -> `example.com:9443`).
**Learning:** Security boundaries often include ports, not just hostnames. Different ports can host different services with different trust levels.
**Prevention:** Include port comparison (normalizing default ports) when checking for origin changes in redirect handling logic.

## 2026-03-15 - Information Leakage in JSON Logs
**Vulnerability:** Log sanitization relied on regex patterns expecting whitespace (`\s`) but escaped newlines (`\n`) to literal `\\n` *before* matching. This caused multiline JSON logs (e.g., `{"password":\n"secret"}`) to bypass redaction because `\s` does not match `\`.
**Learning:** Order of operations matters in sanitization. Escaping control characters for log injection prevention must happen *after* sensitive data redaction, otherwise it corrupts the patterns used for detection.
**Prevention:** Always perform semantic analysis/redaction on the raw input first, then apply transport/storage safety encoding (like escaping) as the final step.

## 2026-03-20 - Secrets Leaked in Exception Tracebacks
**Vulnerability:** The logging formatter sanitized the main log message but appended the raw exception traceback, which could contain secrets in the exception message (e.g., `ValueError("Invalid token: secret_token")`).
**Learning:** Standard Python `logging` formatting separates the message from the traceback. Sanitizing only `record.msg` or `record.getMessage()` is insufficient if the exception info is also logged.
**Prevention:** Override `formatException` in custom formatters to explicitly sanitize the string representation of the traceback before appending it to the log entry.

## 2025-02-17 - [Log Sanitization: Whitespace Blindness]
**Vulnerability:** Log sanitization regexes for `key=value` assignments were too strict, failing to redact sensitive data when spaces were present around the operator (e.g., `password = secret`).
**Learning:** Developers often add spaces for readability in debug logs or configuration dumps. Standard query parameter parsers don't produce spaces, but free-text logging does.
**Prevention:** When writing regexes for log sanitization, always account for optional whitespace around separators (`\s*=\s*`) to cover human-formatted strings.

## 2026-03-21 - OAuth/SAML Token Leakage in Logs
**Vulnerability:** Log sanitization rules missed critical OAuth/SAML parameters (`client_assertion`, `SAMLRequest`, `nonce`, `state`), allowing them to be logged in plain text during authentication flows.
**Learning:** General-purpose secret scanners often focus on generic terms (like `password` or `token`) but miss protocol-specific sensitive fields.
**Prevention:** Explicitly include protocol-specific sensitive parameters (e.g., from OAuth 2.0, OIDC, SAML specs) in log redaction configurations.

## 2026-03-25 - Information Leakage in Short Secret Masking
**Vulnerability:** The secret masking logic revealed 4 characters at both the start and end of any secret longer than 8 characters, exposing nearly 50% of short secrets (e.g. 16-char API keys).
**Learning:** One-size-fits-all redaction rules (like "show first/last 4") leak disproportionately more information for shorter secrets.
**Prevention:** Implement tiered redaction logic that scales the visible portion based on the total length of the secret (e.g. only show 2 chars for secrets < 20 chars).

## 2026-10-29 - Dynamic Sensitive Header Stripping
**Vulnerability:** Static lists of sensitive headers in redirect handling failed to catch custom authentication headers (e.g. `X-Super-Secret-Token`), leading to potential leakage in cross-origin redirects.
**Learning:** Security allowlists/blocklists are brittle against custom naming conventions.
**Prevention:** Implement dynamic header inspection using partial keyword matching (e.g. "token", "secret", "auth") to automatically detect and strip sensitive headers during redirects, ensuring defense-in-depth.

## 2024-05-22 - Session ID and Cookie Leakage in Logs
**Vulnerability:** `session_id` and `cookie` query parameters and key-value pairs were not redacted in logs because they didn't match existing sensitive key patterns (specifically missing from `_SENSITIVE_QUERY_KEYS` and logging regex).
**Learning:** Regex-based redaction is fragile if keys are not explicitly listed or covered by broad patterns. Normalization helps but `session_id` vs `session` was a gap.
**Prevention:** Maintain a comprehensive list of sensitive keys and test with common variations (snake_case, camelCase). Use broad matching where possible but verify false positives.

## 2026-10-30 - Fallback Log Sanitization Gap
**Vulnerability:** The fallback log sanitization in `src/utils/env.py` (used during import errors) lacked patterns for OAuth/SAML secrets (`nonce`, `state`, `client_assertion`) that were present in the primary `src/utils/logging.py`, creating a window of exposure if dependencies failed.
**Learning:** Fallback or redundant security implementations often drift from the primary source of truth, creating inconsistent security postures.
**Prevention:** Automatically verify that fallback/redundant security logic matches the primary implementation (e.g., via unit tests that compare regex patterns or outputs).

## 2026-02-21 - [Secret Scanner Enhancement]
**Vulnerability:** Generic high-entropy detection lacked specificity for common high-value secrets like Google API Keys and Telegram Bot Tokens.
**Learning:** Specific regex patterns improve triage and remediation speed by identifying the exact type of secret exposed.
**Prevention:** Added specific regexes to `_KNOWN_TOKENS` in `src/utils/secret_scanner.py`.

## 2025-04-24 - Zero Trust Upstream Payload Validation
**Vulnerability:** Upstream provider API integrations (`src/providers/vor.py` and `src/providers/wl_fetch.py`) parsed JSON directly via `json.loads` without validating the returned data type. A compromised or misconfigured API returning unexpected JSON structures (like a list instead of a dict) could cause runtime crashes or inject malformed data into downstream parsing logic that assumes dictionary methods (like `.get()`).
**Learning:** Even "trusted" official external APIs must be treated as untrusted boundaries in a Zero Trust architecture. Just because data parses successfully as JSON doesn't mean it conforms to the expected shape or type for the application state.
**Prevention:** Always follow up `json.loads` with explicit type and schema validation (e.g., `if not isinstance(data, dict): return safe_fallback`) before passing the deserialized payload to application logic, ensuring the application fails securely and drops malformed data at the network boundary.

## 2026-04-29 - Security Theater: Cryptography vs. Determinism
**Vulnerability:** Using `secrets.SystemRandom` instead of `random.Random` when a predictable, seeded state is required.
**Learning:** Applying cryptographic security libraries where a predictable, seeded state is required constitutes "Security Theater" and actively breaks the intended application logic. It highlights the conceptual difference between true cryptographic randomness and functional determinism.
**Prevention:** Distinguish between true cryptographic needs and deterministic randomness, using inline comments (`# noqa: S311 # nosec B311`) to suppress security linter warnings where pseudo-randomness is intentionally required.

## 2026-05-05 - AI Provider Tokens Missed by Scanner
**Vulnerability:** `_KNOWN_TOKENS` in `secret_scanner.py` lacked patterns for Anthropic (`sk-ant-…`) and OpenAI (`sk-proj-…`, `sk-svcacct-…`, legacy `sk-<48 alnum>`) keys, even though this project itself runs on Claude. A leaked key would have been caught only by the generic high-entropy fallback (which is silenced by `is_covered` if any specific token already matches the same span) and would not be reported with a precise reason.
**Learning:** Secret scanners must include patterns for the AI/cloud services the project itself depends on — those credentials are exactly the ones most likely to end up in this codebase. The legacy OpenAI `sk-<48>` pattern is benign next to `sk-ant-` / `sk-proj-` because the latter contain a hyphen after `sk-`, which is excluded from `[A-Za-z0-9]{48}`.
**Prevention:** When introducing a new external API integration, also extend `_KNOWN_TOKENS` with the issuer's documented key prefix and length. Order strict patterns before looser ones so `is_covered` correctly attributes findings.

## 2026-05-05 - GitHub Non-PAT Tokens Missed by Scanner
**Vulnerability:** `_KNOWN_TOKENS` only matched `ghp_` (Personal Access Token) and `github_pat_` (fine-grained PAT). The four other GitHub token prefixes — `gho_` (OAuth App), `ghu_` (App user-to-server), `ghs_` (App server-to-server, identical to the `GITHUB_TOKEN` GitHub Actions auto-injects) and `ghr_` (refresh) — would only be caught by the generic high-entropy fallback, which suppresses precise attribution and is easier to silence with a per-line ignore.
**Learning:** Token-prefix lists drift behind GitHub's actual token taxonomy. The `ghs_` gap is especially dangerous because every Actions workflow run produces one of those tokens; a leak in a log artefact or committed snapshot grants repo-scoped write access for the workflow lifetime.
**Prevention:** When adding a token-prefix entry, scan the issuer's full prefix list (GitHub: `gh[opsur]_<36 alnum>`) and add all related variants in one pass. Keep each variant as its own pattern with a distinct reason so the finding identifies which token type leaked.

## 2026-05-05 - SendGrid Keys Defeat the Entropy Fallback
**Vulnerability:** SendGrid API keys have the structural format `SG.<22 chars>.<43 chars>`. The dots between segments are outside the high-entropy character class (`[A-Za-z0-9+/=_-]`), so the generic fallback regex cannot match the full token — it would only flag the trailing 43-character segment in unassigned contexts (e.g. `connect("SG.…")`), and it would do so as a generic "high-entropy" string with no SendGrid attribution. The `SG.` prefix and the 22-char identifier silently disappeared from the report.
**Learning:** Multi-segment tokens that use a non-alphanumeric separator (`.`, `:`, `|`) bypass character-class-based entropy detectors entirely. The fallback only sees one of the segments, which is shorter than the real secret and missing the issuer-identifying prefix — making triage and revocation significantly slower.
**Prevention:** When adding a token to `_KNOWN_TOKENS`, check whether its canonical format contains separators outside `[A-Za-z0-9+/=_-]` (especially `.` for JWT-shaped tokens, Discord bot tokens, SendGrid). If so, the entropy fallback cannot replace a specific pattern — add the full multi-segment regex so the whole token is captured and attributed.

## 2026-05-06 - Stripe `sk_test_` and Slack `xox[ar]-` Missed by Scanner
**Vulnerability:** `_KNOWN_TOKENS` in `secret_scanner.py` covered Stripe live keys (`sk_live_`) and Slack bot/user tokens (`xoxb-`, `xoxp-`) but stopped there. Stripe `sk_test_` keys, Slack OAuth-app access tokens (`xoxa-`) and Slack refresh tokens (`xoxr-`) were left to the generic high-entropy fallback — which suppresses precise attribution and is easier to silence per-line. The Slack refresh-token gap was the worst of the three: `xoxr-` mints fresh `xoxb-`/`xoxp-` until revoked, so a leaked refresh token is effectively a long-lived workspace credential.
**Learning:** This is the same drift pattern already recorded for GitHub (`gh[opsur]_`) and the AI-provider keys — if any one variant of an issuer's token taxonomy is in `_KNOWN_TOKENS`, the *missing* variants stand out in the diff and almost always belong there too. The mitigation isn't to add a new rule; it's to grep the existing `_KNOWN_TOKENS` list against each issuer's official prefix list whenever a new entry lands. Stripe's `sk_test_` was especially easy to miss because it's "less catastrophic" — but a leaked test key still grants test-dashboard access *and* signals that a live key probably exists nearby.
**Prevention:** Treat `_KNOWN_TOKENS` as an issuer-keyed table, not a list. Whenever a new issuer is added or an existing entry is edited, walk the issuer's full documented prefix taxonomy (Stripe: `sk_live_`, `sk_test_`, `rk_live_`, `rk_test_`, `whsec_`; Slack: `xoxb-`, `xoxp-`, `xoxa-`, `xoxr-`, `xoxe-`, `xoxs-`) and add every variant in the same pass with a distinct reason. Each variant gets its own test in `tests/test_secret_scanner_*` so future drift is caught by CI rather than during incident response.

## 2026-05-06 - Zero-Trust Gap in VOR Station-API Loop Affects Loop Continuity
**Vulnerability:** `scripts/update_vor_stations.py:fetch_vor_stops_from_api` parsed `response.json()` for each station ID and immediately called `payload.get("StopLocation")` without an `isinstance(payload, Mapping)` guard. Decode failures (`ValueError`) were already routed to the fallback path, but a successfully-decoded list / scalar / null body would raise `AttributeError` from `.get()`. Because the call happens **inside** a `for station_id in ids:` loop, the exception propagates *out of the loop entirely* — every subsequent station is silently skipped, and the same-batch fallbacks for those stations never run.
**Learning:** Per-iteration Zero-Trust failures are worse than per-call ones: a raised `AttributeError` in a flat loop terminates the whole batch, and the per-station fallback handler that exists *for exactly this scenario* is bypassed because it is unreachable after the raise. The fix is not just "add the isinstance check" but specifically "route shape failures through the same fallback branch as decode failures and HTTP errors", so that loop continuity matches the existing failure-handling contract.
**Prevention:** When a Zero-Trust shape guard is added inside a loop, mirror the structure of the nearest existing failure branch (`except ValueError:` here) verbatim — same log call, same fallback lookup, same `continue`. Test the new branch with parametrised non-object payloads (`[]`, `None`, `42`, `"a string"`) and assert the loop **continues** to subsequent iterations, not just that the current one is skipped.

## 2026-05-06 - Zero-Trust Validation Missed in Baustellen Fallback Loader
**Vulnerability:** `scripts/update_baustellen_cache.py:_load_fallback` returned `cast(dict[str, Any], json.loads(raw))` with no runtime `isinstance` guard. The remote-fetch path (`_load_json_from_content`) already enforced the shape, but the fallback path — used precisely when the network is unreachable — did not. A list / scalar / null body in the on-disk fallback (whether tampered or simply mis-edited) would propagate to `_iter_features`, where `payload.get("type")` would crash with `AttributeError`. The cache update then exits non-zero on the very failure path it exists to recover.
**Learning:** The `cast(...Dict, json.loads(...))` red flag is the same one already journaled for `src/places/client.py`, but the audit had not been re-run against `scripts/`. Fallback / offline paths are easy to forget because they're rarely exercised in normal CI runs — and they're exactly where Zero-Trust matters most, since the network guard (`_load_json_from_content`) doesn't cover them. Whenever a remote loader gets a shape check, its fallback twin needs the same one.
**Prevention:** When grepping for `cast(.*Dict.*json` (the explicit anti-pattern) or `json\.loads(` followed by a `cast`, treat fallback / on-disk variants of any HTTP-loader as in-scope — they share the same parsing call but bypass the network helper. Reuse the network helper's shape guard verbatim, or factor a shared `_require_json_object(raw)` helper so the two loaders cannot drift again.

## 2026-05-05 - Zero-Trust Validation Missed in Places Client
**Vulnerability:** The April-2025 "Zero Trust Upstream Payload Validation" fix added `isinstance(payload, dict)` checks in `src/providers/vor.py` and `src/providers/wl_fetch.py` after `json.loads`, but `src/places/client.py:_post` still returned `cast(Dict[str, object], payload)` with no runtime validation. `cast()` only lies to the type checker — at runtime, a list/null/scalar JSON body from the Google Places API would propagate to `_iter_tile`, where `response.get("places", [])` would crash with `AttributeError` (lists don't have `.get()`).
**Learning:** A repo-wide "Zero Trust" pass needs to enumerate **every** call site of `json.loads` *and* `response.json()`, not just provider-named files. The places client lives outside `src/providers/` so it was overlooked. A `cast(Dict, …)` adjacent to a `json()` call is a strong signal the validation was forgotten — `cast` performs no runtime check.
**Prevention:** When fixing a class of issues across the repo, grep for **both** `json\.loads` and `\.json()` (and any `cast(.*Dict` pattern adjacent to JSON parsing). Treat every external HTTP boundary identically, regardless of which directory it sits in.
