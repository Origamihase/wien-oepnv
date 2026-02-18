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
**Vulnerability:** Static lists of sensitive headers in redirect handling failed to catch custom authentication headers (e.g. `X-Super-Secret-Token`), leading to potential leakage on cross-origin redirects.
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
