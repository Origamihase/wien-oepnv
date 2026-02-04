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
