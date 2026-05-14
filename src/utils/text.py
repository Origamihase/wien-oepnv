#!/usr/bin/env python3
"""Text utilities."""

import html
import re
from html.parser import HTMLParser
from re import Match


_WS_RE = re.compile(r"[ \t\r\f\v]+")

# Common German prepositions that should not be followed by a bullet.
PREPOSITIONS: tuple[str, ...] = (
    # Alphabetical order for easier maintenance; keep umlaut/ASCII pairs
    # together for readability.
    "ab",
    "am",
    "an",
    "auf",
    "bei",
    "bis",
    "durch",
    "gegen",
    "in",
    "nach",
    "ueber",
    "über",
    "vom",
    "zum",
    "zur",
)

_PREP_BULLET_RE = re.compile(
    rf"\b({'|'.join(re.escape(p) for p in PREPOSITIONS)})\s*•\s*",
    re.IGNORECASE,
)

# Precompiled regexes for html_to_text cleanup
_NEWLINE_CLEANUP_RE = re.compile(r"[ \t\r\f\v]*\n[ \t\r\f\v]*")
_COLON_BULLET_RE = re.compile(r":\s*•\s*")
_COLON_NEWLINE_RE = re.compile(r":\s*\n")
# Insert a space between a digit and a following letter so concatenated
# unit-style tokens like ``12Uhr`` render as ``12 Uhr``. A single trailing
# uppercase letter, however, is a Wiener-Linien line-code suffix (``11A``,
# ``27A``, ``5B``) and must stay glued — splitting produced visibly wrong
# descriptions like ``Linie 11 A: …``.
_DIGIT_ALPHA_RE = re.compile(
    r"(\d)([A-Za-zÄÖÜäöüß][a-zäöüß]+|[a-zäöüß])"
)
_MULTI_BULLET_RE = re.compile(r"(?:\s*•\s*){2,}")
_LEADING_BULLET_RE = re.compile(r"^\s*•\s*")
_TRAILING_BULLET_RE = re.compile(r"\s*•\s*$")
_MULTI_SPACE_RE = re.compile(r"\s{2,}")

# Void elements (self-closing) shouldn't be added to closing stack
# Source: https://html.spec.whatwg.org/multipage/syntax.html#void-elements
VOID_ELEMENTS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}

class HTMLTruncator(HTMLParser):
    """Truncates HTML content while preserving tags and structure."""

    def __init__(self, limit: int, ellipsis: str = "...") -> None:
        super().__init__(convert_charrefs=False)
        self.limit = limit
        self.ellipsis = ellipsis
        self.current_length = 0
        self.output: list[str] = []
        self.tags_stack: list[str] = []
        self.done = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.done:
            return

        # Reconstruct the tag
        attr_str = ""
        for attr, value in attrs:
            if value is None:
                attr_str += f" {attr}"
            else:
                # Basic escaping for attribute values
                val_escaped = html.escape(value, quote=True)
                attr_str += f' {attr}="{val_escaped}"'

        self.output.append(f"<{tag}{attr_str}>")

        if tag.lower() not in VOID_ELEMENTS:
            self.tags_stack.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if self.done:
            return

        self.output.append(f"</{tag}>")
        # Try to match with the most recent open tag
        if self.tags_stack:
            if self.tags_stack[-1] == tag:
                self.tags_stack.pop()
            else:
                # Tag mismatch (malformed HTML?), try to find it up the stack
                if tag in self.tags_stack:
                    while self.tags_stack and self.tags_stack[-1] != tag:
                        self.tags_stack.pop()
                    self.tags_stack.pop() # Pop the matching tag

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.done:
            return
        # Self-closing tag like <br />
        attr_str = ""
        for attr, value in attrs:
            if value is None:
                attr_str += f" {attr}"
            else:
                val_escaped = html.escape(value, quote=True)
                attr_str += f' {attr}="{val_escaped}"'
        self.output.append(f"<{tag}{attr_str} />")

    def handle_data(self, data: str) -> None:
        if self.done:
            return

        remaining = self.limit - self.current_length
        if len(data) > remaining:
            candidate = data[:remaining]
            last_space = candidate.rfind(" ")
            if last_space != -1:
                cut_index = last_space
            else:
                cut_index = remaining

            self.output.append(data[:cut_index])
            self.output.append(self.ellipsis)
            self.current_length += cut_index
            self.done = True
        else:
            self.output.append(data)
            self.current_length += len(data)

    def handle_entityref(self, name: str) -> None:
        if self.done:
            return
        # Count entity as 1 character for display length
        entity = f"&{name};"
        if self.limit - self.current_length >= 1:
            self.output.append(entity)
            self.current_length += 1
        else:
            self.output.append(self.ellipsis)
            self.done = True

    def handle_charref(self, name: str) -> None:
        if self.done:
            return
        entity = f"&#{name};"
        if self.limit - self.current_length >= 1:
            self.output.append(entity)
            self.current_length += 1
        else:
            self.output.append(self.ellipsis)
            self.done = True

    def close_open_tags(self) -> None:
        # Close remaining tags in reverse order
        while self.tags_stack:
            tag = self.tags_stack.pop()
            self.output.append(f"</{tag}>")


def truncate_html(text: str, limit: int, ellipsis: str = "...") -> str:
    """
    Truncate HTML text to a specified character limit (of content), preserving tags.
    Ensures all opened tags are closed.
    """
    if not text:
        return ""

    # SECURITY: Mitigate DoS/memory exhaustion from massively oversized inputs
    MAX_SAFE_LENGTH = 1_000_000
    if len(text) > MAX_SAFE_LENGTH:
        text = text[:MAX_SAFE_LENGTH]

    # Quick check if it's even needed
    # Note: This is a loose check because tags add length but don't count towards content limit.
    # But if raw length is <= limit, we certainly don't need to truncate content.
    if len(text) <= limit:
        return text

    parser = HTMLTruncator(limit, ellipsis)
    parser.feed(text)
    parser.close_open_tags()

    return "".join(parser.output)


def normalize_bullets(text: str) -> str:
    """Remove bullets that directly follow known prepositions."""

    def _repl(match: Match[str]) -> str:
        prefix = match.group(1)
        tail = match.group(0)[len(prefix):]
        if "\n" in tail:
            return prefix + "\n"
        return prefix + " "

    return _PREP_BULLET_RE.sub(_repl, text)


class _HTMLToTextParser(HTMLParser):
    """Lightweight HTML-to-text parser that inserts newlines and bullets."""

    _BLOCK_TAGS = {
        "p",
        "div",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "table",
        "tr",
        "td",
        "th",
    }
    _IGNORE_TAGS = {"script", "style"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._ignore_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:  # noqa: D401, ANN001
        tag = tag.lower()
        if tag in self._IGNORE_TAGS:
            self._ignore_depth += 1
            return
        if self._ignore_depth > 0:
            return

        if tag == "br":
            self.parts.append("\n")
        elif tag == "li":
            self.parts.append("\n• ")
        elif tag in self._BLOCK_TAGS:
            self.parts.append("\n")
        # ul/ol are structural containers; no separator on start

    def handle_endtag(self, tag: str) -> None:  # noqa: D401, ANN001
        tag = tag.lower()
        if tag in self._IGNORE_TAGS:
            if self._ignore_depth > 0:
                self._ignore_depth -= 1
            return
        if self._ignore_depth > 0:
            return

        if tag == "li":
            self.parts.append("\n")
        elif tag in self._BLOCK_TAGS or tag in {"ul", "ol"}:
            self.parts.append("\n")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:  # noqa: D401, ANN001
        tag = tag.lower()
        if self._ignore_depth > 0:
            return

        if tag == "br":
            self.parts.append("\n")
        elif tag in self._BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:  # noqa: D401
        if self._ignore_depth > 0:
            return
        self.parts.append(data)


def html_to_text(s: str, *, collapse_newlines: bool = False) -> str:
    """Convert HTML fragments to plain text.

    ``collapse_newlines`` can be set to ``True`` to restore the legacy behaviour
    where line breaks were replaced by the ``" • "`` separator.
    """
    if not s:
        return ""

    # Implement HTML-aware truncation before parsing to text
    truncated_s = truncate_html(s, limit=5000, ellipsis="... [TRUNCATED]")

    parser = _HTMLToTextParser()
    parser.feed(truncated_s)
    parser.close()

    txt = "".join(parser.parts)

    # Note: html.unescape is skipped because HTMLParser(convert_charrefs=True)
    # already decodes entities. Calling unescape again is redundant.
    txt = txt.replace("\xa0", " ")

    newline_replacement = " • " if collapse_newlines else "\n"
    txt = _NEWLINE_CLEANUP_RE.sub(newline_replacement, txt)
    if collapse_newlines:
        txt = _COLON_BULLET_RE.sub(": ", txt)
    else:
        txt = _COLON_NEWLINE_RE.sub(":\n", txt)
    txt = _DIGIT_ALPHA_RE.sub(r"\1 \2", txt)
    txt = _WS_RE.sub(" ", txt)
    # Collapse any repeated bullet separators before removing those after prepositions
    txt = _MULTI_BULLET_RE.sub(" • ", txt)
    txt = normalize_bullets(txt)

    strip_border_bullets = collapse_newlines or " • " in txt
    if strip_border_bullets:
        txt = _LEADING_BULLET_RE.sub("", txt)
        txt = _TRAILING_BULLET_RE.sub("", txt)

    if collapse_newlines:
        txt = _WS_RE.sub(" ", txt)
        txt = _MULTI_SPACE_RE.sub(" ", txt).strip()
    else:
        # Optimization: _NEWLINE_CLEANUP_RE and _MULTI_NEWLINE_RE are
        # effectively handled by split/strip/join below.
        lines = [line.strip() for line in txt.split("\n")]
        txt = "\n".join(line for line in lines if line)

    return txt


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
_MARKDOWN_NORMALISE_UNSAFE_RE = re.compile(
    r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f"
    r"\u061c\u200b-\u200f\u2028-\u202e\u2066-\u2069"
    r"\ufe00-\ufe0f\ufeff\U000e0000-\U000e007f\U000e0100-\U000e01ef]"
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
    text = html.escape(text)
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
    # We backslash-escape: [ ] ( ) * _ ` @ < > # ~
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
    for char in "[]()*_`@<>#~":
        text = text.replace(char, f"\\{char}")
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
