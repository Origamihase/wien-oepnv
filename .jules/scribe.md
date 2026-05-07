# Scribe 🪶 DX & Cartography — Journal

Seventh and final companion log to the project's institutional-memory
journals (`sentinel.md`, `apex.md`, `purist.md`, `surgeon.md`,
`omega.md`, `saboteur.md`). Scribe documents knowledge-surface work:
diagrams, architectural maps, and educational docstrings that turn
the mechanical masterpiece into a human-readable system.

Format mirrors the sister journals: only entries that capture a
unique DX pattern or a new architectural artifact.

---

## 2026-05-07 - Scribe Pass: Architecture Map + Pillar Docstrings

### The architecture as I now understand it

After Sentinel, Apex, Purist, Surgeon, Omega, and Saboteur, the system
has acquired enough sophistication that no single file tells the
whole story. Here is the synthesis:

**Five layered defences against the network:**

1. **Process-level** — `build_feed.main` is the failure unit. A
   corrupt write fails the whole cron, never ships a half-built feed.
2. **Provider-level** (bulkhead) — `_collect_items` runs each
   provider in a per-future try/except inside a
   `ThreadPoolExecutor` with Apex-Phase-1 deadline-eviction. One
   provider's exception cannot drop the others' items.
3. **Call-level** — `request_safe` is a 14-helper security state
   machine that enforces SSRF + DNS-rebinding TOCTOU + Slowloris +
   payload-cap on every outgoing HTTP request.
4. **Transport-level** — `JitterRetry` (urllib3 with ±20% jitter) +
   `PinnedHTTPSAdapter` (per-IP TLS pin keeping SNI on the original
   hostname).
5. **Payload-level** — Zero-Trust top-level type validation +
   `RecursionError` catch (Saboteur's bug fix) + `MAX_PAYLOAD_SIZE`
   (10 MB).

**The data pipeline** (now diagrammed in `docs/architecture.md` §1)
flows: cron → `build_feed.main` → `_collect_items` → split into
cache vs network fetchers → ThreadPoolExecutor (network) → per-provider
`fetch_events` → `request_safe` → upstream → `_merge_result` →
`_dedupe_items` + `deduplicate_fuzzy` → `_make_rss` + `atomic_write`.

**The complexity hierarchy** (post-Surgeon + Omega):

- Top-level orchestrators (`_collect_items`, `request_safe`,
  `fetch_events` per provider) — read like security policies, each
  line is a Sentinel-audited or Apex-tuned step.
- Helper functions (`_categorize_providers`, `_drain_completed_futures`,
  `_send_https_pinned`, `_strip_redirect_secrets`, `_resolve_poor_title`,
  etc.) — each owns one cohesive concern with a docstring naming the
  invariant or attack vector it preserves.
- Primitives (`CircuitBreaker`, `ProviderSpec`, `_ProviderBuckets`)
  — small, strictly-typed, no third-party dependencies.

### Diagrams created

`docs/architecture.md` (new, 5 sections, ~290 lines):

| § | Diagram | Audience |
|---|---|---|
| 1 | Sequence diagram: Transit Data Fetching Pipeline | New contributor tracing a full request flow |
| 2 | Flowchart: `request_safe` security state machine | Reviewer auditing a security-adjacent change |
| 3 | Component diagram: Resilience-layer stack | Operator reasoning about why a provider failed |
| 4 | Flowchart: Provider plugin contract | Author of a new provider |

All four are GitHub-rendered Mermaid blocks, accompanied by prose
that explains the *why* (not just the *what*) of each step.

The README's `## Systemüberblick` section now opens with a callout
pointing readers at the new architecture map — a one-line bridge
that costs nothing but pays back the moment a new contributor lands
in the codebase.

### Educational docstrings added

Three top-level pillars previously had **brief** or **missing**
docstrings. Each now carries a multi-paragraph educational explanation
following Google-style with explicit ``Args``, ``Returns``,
``Raises``, and ``See Also`` sections:

| Function | Before | After |
|---|---|---|
| `src/build_feed.py::_collect_items` | ❌ no docstring | 6-paragraph orchestration narrative + cross-refs to `surgeon.md` and `apex.md` |
| `src/utils/http.py::request_safe` | brief Args/Returns/Raises | full security-pipeline narrative + 14-step ordering + cross-ref to `omega.md` |
| `src/utils/circuit_breaker.py::CircuitBreaker` | concise + tiny example | full state-machine ASCII art + "When to use vs. urllib3 retries" comparison + canonical adoption pattern |

The 14 `request_safe` helpers and the 7 `_collect_items` helpers
already had Sentinel/Surgeon/Omega-quality docstrings naming their
invariants — the Scribe pass focused on the *orchestrators*, which
are the entry points a junior dev opens first.

### Patterns for future Scribe passes

1. **Document the orchestrator, not the helper.** Helpers tend to
   accumulate good docstrings during refactors (Surgeon, Omega) because
   the extraction author had to explain *why* they extracted that
   block. The orchestrator survives those refactors with the brief
   one-liner from years ago. After every major refactor pass, audit
   the orchestrator's docstring against the new internal structure.

2. **Diagrams should explain *order*, not just *components*.** The
   `request_safe` flowchart is valuable not because it lists 14
   helpers (the docstrings already do that) but because it shows the
   *order* in which they fire. Order is the part code reading does
   not surface easily.

3. **A README callout is high-leverage.** A single sentence at the
   top of the system overview — "see `docs/architecture.md` for the
   visual map" — is the difference between a new contributor finding
   the diagrams or never knowing they exist. A diagram nobody opens
   is useless.

4. **Cross-link the journals from the visible surface.** The
   `.jules/*.md` journals have rich audit history but are
   discoverable only by file-tree exploration. Each architecture
   diagram in `docs/architecture.md` links back to the relevant
   journal entries (Sentinel for security gates, Apex for
   performance, etc.), making the institutional memory navigable.

5. **Educational docstrings should name historical context.**
   `request_safe`'s new docstring references "the joint Sentinel +
   Surgeon refactor that extracted these 14 helpers from the original
   280-line monolith." That sentence does double duty: it explains
   the current shape AND warns the next refactorer that there's a
   reason the helpers exist.

### Verification

- `pytest`: 1937 passed, 3 skipped (unchanged — Scribe touches no
  application logic, only documentation/strings).
- `ruff check src/ tests/`: All checks passed!
- `python3 -m mypy --no-pretty src tests` (CI-pinned 1.10.1):
  Success: no issues found in 381 source files.
- All Mermaid blocks fenced as ` ```mermaid ` (GitHub-renderable).
- All docstrings follow PEP-257; sections (`Args:`, `Returns:`,
  `Raises:`, `See Also:`, `Example:`) are Google-style and Sphinx-
  compatible.

### Cross-journal links

- **Sentinel** + **Apex** + **Purist** + **Surgeon** + **Omega** +
  **Saboteur** — the six engineering passes whose work the Scribe
  pass *describes* without modifying. Each has a journal entry
  cross-referenced from `docs/architecture.md`.
- The architecture map is now the canonical answer to "where does X
  fit in the system" — questions previously answered only by reading
  the source.
