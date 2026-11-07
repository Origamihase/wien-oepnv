"""Markdown-sink safety helpers: normalisation, escaping and code spans.

Part of :mod:`src.utils.text`, which re-exports the public names.
"""

import html
import re


# Defence-in-depth control-character allow-list for Markdown sinks. The
# CSV writer in :mod:`src.utils.stats` already strips C0/DEL bytes at
# the persistence boundary, but Markdown rendering is the LAST gate
# before the value reaches a human renderer (GitHub, IDE, static-site
# builder); historical rows, future writer-siblings, and the Unicode
# line / paragraph / BiDi separators (which the CSV regex does not
# cover) all need to be normalised here. The character class mirrors
# the canonical Trojan-Source / line-terminator union pinned in
# ``src/utils/logging.py`` minus the readable whitespace bytes that
# the immediately-following whitespace-collapse step replaces with a
# single space (TAB \x09, LF \x0a, CR \x0d):
#   * \x00-\x08 + \x0b-\x0c + \x0e-\x1f — C0 controls except TAB/LF/CR.
#   * \x7f-\x9f — DEL + C1 controls (incl. U+0085 NEXT LINE which
#     several Markdown / SIEM splitters honour as a record terminator).
#   * U+061C — Arabic Letter Mark (post-Unicode-6.3 BiDi control).
#   * U+200B-U+200F — ZWSP / ZWNJ / ZWJ + LRM / RLM (CVE-2021-42574
#     Trojan-Source first half + zero-width clutter).
#   * U+2028-U+202E — LINE SEP / PARA SEP + LRE/RLE/PDF/LRO/RLO BiDi
#     formatting controls.
#   * U+2066-U+2069 — LRI / RLI / FSI / PDI BiDi isolates
#     (CVE-2021-42574 second half).
#   * U+FEFF — Byte Order Mark / ZWNBSP.
# 2026-05-11 "Tag-Character / Variation-Selector Drift": widened in
# lockstep with the canonical _INVISIBLE_DANGEROUS_RE union to cover
# the Unicode Tag block (U+E0000..U+E007F), the BMP Variation
# Selectors (U+FE00..U+FE0F), and the supplementary Variation
# Selectors (U+E0100..U+E01EF). A planted upstream payload carrying
# tag-character or variation-selector bytes in a station name, error
# message, or stats field flows through every Markdown sink
# (docs/feed_health.md, docs/statistik.md, GitHub Issue body) and
# reaches the rendered Markdown verbatim - a Trojan-Source /
# steganography / prompt-injection smuggling primitive on every
# operator-facing report.
# 2026-05-14 "Zero-Width Format Drift": widened in lockstep with the
# canonical _INVISIBLE_DANGEROUS_RE union to cover U+180E (MONGOLIAN
# VOWEL SEPARATOR) and U+2060..U+2064 (WORD JOINER, FUNCTION
# APPLICATION, INVISIBLE TIMES, INVISIBLE SEPARATOR, INVISIBLE PLUS).
# Each is a Cf (Format) zero-width Unicode primitive in the same
# family as ZWSP/ZWNJ/ZWJ; the U+2060..U+2064 band is the canonical
# "invisible Unicode steganography" alphabet (combinations encode
# arbitrary bytes that survive copy-paste from a Markdown sink into
# an LLM context window). The expanded U+2060..U+2069 range folds
# in the existing BiDi-isolate band (U+2066..U+2069) plus reserved
# U+2065; the unassigned slot has no defined meaning so the additive
# strip is safe.
# 2026-05-14 "Cf-Format Drift": widened in lockstep with the canonical
# _INVISIBLE_DANGEROUS_RE union to cover the remaining 13 Unicode
# Cf-class bands (51 code points): U+00AD SOFT HYPHEN, U+0600..U+0605
# Arabic prefix marks, U+06DD ARABIC END OF AYAH, U+070F SYRIAC
# ABBREVIATION MARK, U+0890..U+0891, U+08E2, U+206A..U+206F deprecated
# BiDi controls (folds the existing U+2060..U+2069 band into
# U+2060..U+206F), U+FFF9..U+FFFB INTERLINEAR ANNOTATION,
# U+110BD/U+110CD KAITHI, U+13430..U+1343F EGYPTIAN HIEROGLYPH,
# U+1BCA0..U+1BCA3 SHORTHAND FORMAT, and U+1D173..U+1D17A MUSICAL
# SYMBOL formatting. Pre-fix Cf bytes survived into every Markdown
# sink (data/feed_health.md, docs/statistik.md, GitHub Issue body) -
# SOFT HYPHEN renders zero-width unconditionally so a planted
# "Wien Hbf<U+00AD>evil" payload reaches the operator-facing report
# visually identical to "Wien Hbfevil" but byte-distinct downstream.
_MARKDOWN_NORMALISE_UNSAFE_RE = re.compile(
    r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f"
    r"\u00ad\u0600-\u0605\u061c\u06dd\u070f\u0890\u0891\u08e2\u180e"
    r"\u200b-\u200f\u2028-\u202e\u2060-\u206f"
    r"\ufe00-\ufe0f\ufeff\ufff9-\ufffb"
    r"\U000110bd\U000110cd"
    r"\U00013430-\U0001343F"
    r"\U0001bca0-\U0001bca3"
    r"\U0001d173-\U0001d17a"
    r"\U000e0000-\U000e007f\U000e0100-\U000e01ef]"
)
_MARKDOWN_WHITESPACE_RE = re.compile(r"\s+")


def normalise_markdown_text(text: str, *, max_len: int = 200) -> str:
    """Normalise *text* before interpolating it into a Markdown sink.

    Strips control bytes / BiDi marks / Unicode line separators (which
    would otherwise either split a Markdown table row, fool a SIEM
    splitter consuming the rendered file, or invert displayed text via
    CVE-2021-42574-style "Trojan Source" payloads), collapses every
    whitespace run (including embedded TAB / LF / CR) to a single
    space, and caps length at *max_len*. The output is plain text,
    free of layout-breaking whitespace and invisible control bytes —
    pair with :func:`escape_markdown` (or :func:`escape_markdown_cell`
    for table cells) to apply context-specific escaping.
    """
    if not text:
        return ""
    cleaned = _MARKDOWN_NORMALISE_UNSAFE_RE.sub("", text)
    cleaned = _MARKDOWN_WHITESPACE_RE.sub(" ", cleaned).strip()
    if len(cleaned) > max_len:
        cleaned = cleaned[:max_len].rstrip()
    return cleaned


def escape_markdown(text: str) -> str:
    """Escape HTML and Markdown characters to prevent injection/XSS."""
    # Order matters: backslash-escape the Markdown metacharacters FIRST, then
    # HTML-escape LAST (see the trailing ``html.escape`` call). Doing
    # ``html.escape`` first was a bug — it rewrites an apostrophe to the numeric
    # entity ``&#x27;``, and the ``#`` step of the per-char loop then escaped the
    # ``#`` *inside* that entity (``&\\#x27;``), which CommonMark / GFM render as
    # the literal text ``&#x27;`` instead of an apostrophe, silently corrupting
    # every apostrophe in operator-facing reports (``KeyError: 'station'``,
    # station names like ``O'Brien``). Escaping the metacharacters first keeps
    # the loop's ``#`` away from the numeric entity emitted later, with every
    # injection defence below still intact.
    #
    # Security (backslash-precedence-bypass round): escape ``\\``
    # itself FIRST, before the per-char loop below. CommonMark 2.4
    # consumes ``\\\\`` left-to-right as a single literal ``\\``, so
    # without this step an attacker can plant a literal backslash
    # in front of a Markdown metacharacter (``\\*EMPHASIS\\*``)
    # and have the per-char loop append a SECOND backslash —
    # producing ``\\\\*EMPHASIS\\\\*`` which CommonMark parses as
    # ``\\\\`` -> literal ``\\``, ``*`` -> UNESCAPED emphasis
    # delimiter -> ``<em>EMPHASIS</em>``. The bypass re-opens every
    # prior canonical-escape round (#1471/1472/1476/1477) on every
    # metacharacter at once. Doubling the backslash here means each
    # per-char-loop ``\\<meta>`` becomes ``\\\\\\<meta>`` in the
    # output, which CommonMark parses as ``\\\\`` -> ``\\`` then
    # ``\\<meta>`` -> literal ``<meta>``. Legitimate text containing
    # ``\\`` (Windows paths ``C:\\Users\\foo``, regex patterns,
    # error messages from third-party libraries) is visually
    # preserved on the rendered page — each ``\\\\`` renders as a
    # single literal ``\\``.
    text = text.replace("\\", "\\\\")
    # Escape Markdown characters that could create links or formatting.
    # We backslash-escape: [ ] ( ) * _ ` @ # ~
    #
    # ``<`` and ``>`` are intentionally NOT backslash-escaped here: the trailing
    # ``html.escape`` turns them into ``&lt;`` / ``&gt;`` (rendered as literal
    # ``<`` / ``>``, unable to form an HTML tag, autolink, or blockquote marker).
    # Backslash-escaping them would instead yield ``\\&lt;``, which renders as
    # the literal text ``&lt;``.
    #
    # Security: ``#`` was missing pre-Sentinel ``escape_markdown`` ATX-
    # heading-injection round. Callers in :mod:`src.feed.reporting`
    # interpolate the result directly after a list-item marker
    # (``f"- {escape_markdown(warning)}"`` /
    # ``f"- {escape_markdown(error)}"`` at lines 637/647/1100/1107).
    # A hostile warning / error string starting with ``# evil`` then
    # produces ``- # evil`` in the rendered Markdown, which CommonMark
    # and GFM parse as ``<ul><li><h1>evil</h1></li></ul>`` —
    # a fresh ATX heading inside a bullet list. The same payload at
    # ``## evil`` … ``###### evil`` reaches every heading level
    # (h1..h6). Sinks: the public ``docs/feed-health.md`` artefact
    # (committed by ``update-cycle.yml`` and rendered on GitHub
    # Pages) and the auto-submitted GitHub Issue body
    # (``submit_auto_issue`` — visible to every repo watcher).
    # ``clean_message`` collapses whitespace before the value reaches
    # here, but ``#`` is a printable ASCII character that survives
    # every prior round of sanitisation. The backslash-escaped
    # ``\\#`` renders as literal ``#`` in CommonMark / GFM so
    # legitimate text ("issue #123", "C# code") is visually
    # unchanged on the rendered page. Mirrors the canonical
    # backslash-escape shape pinned for ``[]()*_```@<>``.
    #
    # Security (GFM strikethrough injection round): ``~`` was the
    # last inline-formatting metacharacter the canonical escape set
    # left uncovered. GitHub Flavored Markdown (GFM) parses the
    # bigram ``~~text~~`` as strikethrough (``<del>text</del>``);
    # the extension is enabled on every renderer in this codebase's
    # data path — github.com rendering of ``docs/feed-health.md``
    # / ``docs/stations_validation_report.md``, GitHub Pages
    # serving the same files via the default kramdown_GFM input
    # mode, and the auto-submitted GitHub Issue body rendered by
    # GitHub's own GFM renderer (``submit_auto_issue``). A hostile
    # warning / error message ``"~~RESOLVED~~ still broken"`` lands
    # ``<del>RESOLVED</del>`` inline in the operator-facing report
    # — pure visual misinformation (no JS, no phishing), but the
    # operator triaging off the rendered page cannot distinguish
    # struck-through text from text that was never struck through.
    # The backslash-escaped ``\\~`` renders as literal ``~`` in
    # CommonMark / GFM (``~`` is ASCII punctuation per CommonMark
    # 2.4, so backslash escapes apply), so legitimate text
    # ("~/foo" path abbreviations, "~5 minutes" approximation
    # symbols) is visually unchanged on the rendered page.
    for char in "[]()*_`@#~":
        text = text.replace(char, f"\\{char}")
    # HTML-escape LAST: ``& < > " '`` -> entities so the result is safe both as
    # Markdown and as inline HTML (GFM interprets raw HTML). Running this after
    # the loop keeps the numeric entity ``&#x27;`` it emits for an apostrophe
    # intact — the loop's ``#`` step can no longer reach inside it.
    text = html.escape(text)
    return text


def escape_markdown_cell(text: str) -> str:
    """Escape pipe characters and HTML to prevent injection and table breakage."""
    escaped = escape_markdown(text)
    # Use HTML entity for pipe to be safe in tables
    return escaped.replace("|", r"\|")


def safe_markdown_codespan(text: str, *, max_len: int = 200) -> str:
    """Return *text* normalised for inclusion inside a `` `…` `` code span.

    CommonMark code spans render their interior verbatim — backslash
    escapes are NOT active. The only character that can break out is a
    literal backtick, which closes the span. Replace backticks with the
    apostrophe ``'`` (the project-wide convention — see
    :func:`src.feed.reporting._sanitize_code_span`) and apply the same
    control-byte / whitespace / length normalisation as
    :func:`normalise_markdown_text` so embedded newlines cannot break
    out of the surrounding fenced code block by smuggling a closing
    ``\\n```\\n`` into the label.
    """
    return normalise_markdown_text(text, max_len=max_len).replace("`", "'")
