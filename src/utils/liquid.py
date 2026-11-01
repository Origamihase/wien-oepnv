"""Keep Jekyll's Liquid away from the pages GitHub Pages builds from ``docs/``.

GitHub Pages builds ``docs/`` with Jekyll 3.10 and Liquid 4.0.4. Every
Markdown file there is a page, with or without front matter
(``jekyll-optional-front-matter``, on unless ``_config.yml`` turns it off),
and so is every other file that starts with front matter. Liquid runs over
a page before Markdown does: a ``{{ … }}`` is replaced by its value, mostly
nothing, and a ``{%`` that does not form a valid tag stops the whole build.
From the merge of #2092 on 2026-11-01 (08:43 Vienna time) one bare ``{%``
in ``docs/reference/ki_waechter.md`` failed every Pages build (22 failed,
4 cancelled until 11:13), and the feed at the Pages address, which the TV reads, stayed at 08:30
while the update runs stayed green.

Two rules: no Liquid pair outside a ``raw`` block, and every raw tag of a
Markdown page in a marker line of its own. The tools:

* :func:`liquid_safe` makes generated text inert: a space after every
  ``{`` that opens a pair. Every script that writes a page under ``docs/``
  passes it through here.
* :func:`liquid_findings` tells where Liquid would act on a page. It
  follows Liquid 4.0.4's tokenizer (``lib/liquid.rb``,
  ``block_body.rb``, ``tags/raw.rb``) instead of a regex of its own: the
  review of 2026-11-01 found that ``{%- endraw %}`` does not close a raw
  block there, that ``{{% raw %}`` opens a variable, that text before an
  inner tag of a raw block comes out twice, and that Ruby's ``\\s`` and
  ``\\w`` are ASCII only.
* :func:`raw_marker_findings` checks the layout of the marker lines.
* :func:`rendered_pages` lists the files under ``docs/`` that Jekyll
  renders; ``tests/test_docs_liquid.py`` checks each of them.
* :func:`neutralise` rewrites staged pages that still hold a pair;
  ``scripts/publish_to_main.sh`` calls it before every bot commit, because
  a push with the ``GITHUB_TOKEN`` starts the Pages build but no test run.

Hand-written pages that show such a pair put it, as a whole block, between
two lines ``<!-- {% raw %} -->`` and ``<!-- {% endraw %} -->``, not
indented, with a blank line before and after each (``docs/development.md``,
"Liquid in docs/"). Only that plain form counts: a ``-`` in ``raw`` trims
the text before or after the block, ``{%- endraw`` does not close it (the
build fails, or the block runs on to the next plain ``endraw``), and
anything after ``endraw``, a ``-`` included, is ignored.

Run as a script (``python src/utils/liquid.py --fix PATH...``) it rewrites
each given page that holds a pair and prints one line per rewritten file;
``--null`` reads the paths NUL-separated from stdin and ends each printed
entry with NUL, for any file name. It imports nothing outside the standard
library, so the publish script can call it by path.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

# Ruby's ``\s`` and ``\w`` (Onigmo, no Unicode flag) are ASCII only;
# Python's ``str`` patterns are not, so they are spelled out.
_WS = r"[ \t\n\v\f\r]"
_WORD = r"[A-Za-z0-9_]"

# Liquid::TemplateParser: a tag, a variable (``}`` or ``}}`` ends it), or a
# lone opener.
_TOKEN_RE = re.compile(r"\{%.*?%\}|\{\{.*?\}\}?|\{\{|\{%", re.S)
# Liquid::BlockBody::FullToken.
_FULL_TAG_RE = re.compile(r"\A\{%-?" + _WS + r"*(" + _WORD + r"+)" + _WS + r"*(.*?)-?%\}\Z", re.S)
# Liquid::Raw::FullTokenPossiblyInvalid: what ends a raw block.
_RAW_END_RE = re.compile(r"\A(.*)\{%" + _WS + r"*(" + _WORD + r"+)" + _WS + r"*(.*)?%\}\Z", re.S)
# The only raw tags this project accepts.
_PLAIN_RAW_RE = re.compile(r"\A\{%" + _WS + r"*raw" + _WS + r"*%\}\Z")
# Jekyll::Document::YAML_FRONT_MATTER_REGEXP; Liquid never sees this part.
_FRONT_MATTER_RE = re.compile(r"\A(---" + _WS + r"*\n.*?\n?)^((---|\.\.\.)" + _WS + r"*$\n?)", re.S | re.M)
# Jekyll::Utils.has_yaml_header?, applied to the first line of the file.
_YAML_HEADER_RE = re.compile(rb"\A---[ \t\n\v\f\r]*\r?\n")
_OPENER_RE = re.compile(r"\{(?=[{%])")

# Jekyll's default ``markdown_ext``; ``jekyll-optional-front-matter`` makes
# each of these a page.
MARKDOWN_SUFFIXES = frozenset({".markdown", ".mkdown", ".mkdn", ".mkd", ".md"})
# Liquid templates, Sass partials and data: Jekyll reads them, but they are
# not pages, and the templates use Liquid on purpose.
TEMPLATE_DIRS = frozenset({"_layouts", "_includes", "_sass", "_data"})


@dataclass(frozen=True)
class Finding:
    """One place where Liquid would act on a page."""

    line: int
    token: str
    reason: str


def liquid_safe(text: str) -> str:
    """Return *text* with a space after every ``{`` that opens a Liquid pair.

    ``{{`` becomes ``{ {`` and ``{%`` becomes ``{ %``; a run such as
    ``{{{`` becomes ``{ { {``, so no pair survives (the plain string
    replacement used before 2026-11-01 left ``{ {{`` behind). The space
    shows on the website and on GitHub, which is acceptable for generated
    reports: the text stays readable and Liquid leaves it alone.
    """
    return _OPENER_RE.sub("{ ", text)


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _body_start(text: str) -> int:
    match = _FRONT_MATTER_RE.match(text)
    return match.end() if match else 0


# Reasons that stop the build in any file Liquid reads, templates included.
STOPS_BUILD_UNTERMINATED = "unterminated Liquid tag: stops the Pages build"
STOPS_BUILD_RAW_OPEN = "raw block never closed: stops the Pages build"


def _scan(text: str, start: int) -> tuple[list[Finding], list[tuple[int, int]]]:
    """Findings, and the spans of the raw blocks Liquid leaves as they are."""
    findings: list[Finding] = []
    clean_raw: list[tuple[int, int]] = []
    raw_open: tuple[int, str, int, bool] | None = None  # line, token, offset, clean
    for match in _TOKEN_RE.finditer(text, start):
        token = match.group(0)
        line = _line_of(text, match.start())
        if raw_open is not None:
            end = _RAW_END_RE.match(token)
            if end is None:
                continue
            if end.group(2) != "endraw":
                # Liquid's Raw#parse appends the text before such a tag
                # (group 1) and then the whole token again: that text comes
                # out twice ("{% raw %}{{%a%}{% endraw %}" renders "{{{%a%}").
                if end.group(1):
                    findings.append(Finding(line, token, "text before a tag inside a raw block is repeated by Liquid"))
                    raw_open = (raw_open[0], raw_open[1], raw_open[2], False)
                continue
            opened = raw_open
            raw_open = None
            # Liquid keeps the text before the closing tag (group 1) and
            # ignores whatever follows ``endraw`` (group 3).
            if (end.group(3) or "").strip(" \t\n\v\f\r"):
                line = _line_of(text, match.start() + len(end.group(1)))
                findings.append(Finding(line, token, "raw block closed by a tag other than a plain {% endraw %}"))
            elif opened[3]:
                clean_raw.append((opened[2], match.end()))
            continue
        if token.startswith("{{"):
            findings.append(Finding(line, token, "Liquid output: replaced by its value or a syntax error"))
            continue
        tag = _FULL_TAG_RE.match(token)
        if tag is None:
            findings.append(Finding(line, token, STOPS_BUILD_UNTERMINATED))
        elif tag.group(1) == "raw":
            plain = bool(_PLAIN_RAW_RE.match(token))
            raw_open = (line, token, match.start(), plain)
            if not plain:
                findings.append(Finding(line, token, "raw tag with '-' or markup: trims text or stops the build"))
        else:
            findings.append(Finding(line, token, f"Liquid tag '{tag.group(1)}': acts on the page or stops the build"))
    if raw_open is not None:
        findings.append(Finding(raw_open[0], raw_open[1], STOPS_BUILD_RAW_OPEN))
    return findings, clean_raw


def liquid_findings(text: str, *, front_matter: bool = True) -> list[Finding]:
    """Every place in a page where Liquid would change or reject the text.

    *text* is the whole file. With *front_matter* (Jekyll's behaviour for a
    page) a leading YAML block is skipped, because Jekyll strips it before
    Liquid runs. Line numbers count from the start of the file.
    """
    return _scan(text, _body_start(text) if front_matter else 0)[0]


def liquid_safe_outside_raw(text: str) -> str:
    """*text* with :func:`liquid_safe` applied outside its intact raw blocks.

    A page that wraps an example correctly keeps it byte for byte; only the
    pairs Liquid would act on are broken up. The front matter stays as it
    is. Should the result still hold a finding (it does not in any case
    known), the whole body goes through :func:`liquid_safe`.
    """
    start = _body_start(text)
    _findings, spans = _scan(text, start)
    pieces = [text[:start]]
    cursor = start
    for begin, end in spans:
        pieces.append(liquid_safe(text[cursor:begin]))
        pieces.append(text[begin:end])
        cursor = end
    pieces.append(liquid_safe(text[cursor:]))
    result = "".join(pieces)
    if liquid_findings(result):
        return text[:start] + liquid_safe(text[start:])
    return result


_RAW_MARKER_LINES = frozenset({"<!-- {% raw %} -->", "<!-- {% endraw %} -->"})
_RAW_TAG_RE = re.compile(r"\{%-?" + _WS + r"*(?:end)?raw\b")


def raw_marker_findings(text: str) -> list[Finding]:
    """Raw tags in a Markdown page that do not follow the project's layout.

    A raw tag stands alone in a line ``<!-- {% raw %} -->`` or
    ``<!-- {% endraw %} -->`` with a blank line (or the start or end of the
    file) on both sides. GitHub's own view hides such a comment; without the
    blank lines kramdown, which builds the website, folds a following table
    or paragraph into one paragraph with literal pipes, and the GitHub view
    does not show it (review of 2026-11-01).
    """
    lines = text.split("\n")
    findings: list[Finding] = []
    for index, line in enumerate(lines):
        if not _RAW_TAG_RE.search(line):
            continue
        number = index + 1
        if line.strip() not in _RAW_MARKER_LINES:
            expected = "<!-- {% endraw %} -->" if "endraw" in line else "<!-- {% raw %} -->"
            findings.append(Finding(number, line.strip(), f"raw tag outside a line of its own '{expected}'"))
            continue
        if line != line.strip():
            # Indented by four spaces at the top level, the marker becomes
            # a visible code block; inside a list, wrap the whole list.
            findings.append(Finding(number, line, "raw marker line must not be indented (wrap a whole list instead)"))
            continue
        before = lines[index - 1] if index > 0 else ""
        after = lines[index + 1] if index + 1 < len(lines) else ""
        if before.strip() or after.strip():
            findings.append(Finding(number, line, "raw marker line needs a blank line before and after"))
    return findings


def has_front_matter(path: Path) -> bool:
    """Whether Jekyll treats *path* as a file with front matter."""
    with path.open("rb") as handle:
        return bool(_YAML_HEADER_RE.match(handle.readline()))


def is_rendered(path: Path, docs_root: Path) -> bool:
    """Whether Jekyll runs the file at *path* (under *docs_root*) through Liquid.

    Errs on the side of scanning: a file Jekyll copies as it is may be
    counted (harmless), but no rendered one is missed. Left out are
    ``_config.yml`` and the template and data directories, whose files are
    no pages (``tests/test_docs_liquid.py`` checks the templates for what
    stops the build). Below a ``_posts`` directory, at any depth, Jekyll
    renders a file whose name starts with a date even without front matter,
    whatever its extension; every file there is counted. A post also gets
    an excerpt, a second text run through Liquid that this module does not
    rebuild, so ``docs/`` holds no ``_posts`` (a test forbids it).
    ``.htaccess`` is rendered although its name starts with ``.``.
    """
    rel = path.relative_to(docs_root)
    if rel.parts and (rel.parts[0] in TEMPLATE_DIRS or rel.as_posix() == "_config.yml"):
        return False
    if "_posts" in rel.parts[:-1] or path.suffix.lower() in MARKDOWN_SUFFIXES:
        return True
    return has_front_matter(path)


def _excluded(posix: str, excludes: Sequence[str]) -> bool:
    """Whether an ``exclude`` entry drops the file *posix* (relative path).

    A name starting with ``.htaccess`` stays: Jekyll's default ``include``
    (``.htaccess``) matches every name with that start and wins over
    ``exclude``. An entry ending in ``/`` names a directory and drops only
    what lies below it.
    """
    if posix.rsplit("/", 1)[-1].startswith(".htaccess"):
        return False
    for entry in excludes:
        if entry.endswith("/"):
            if posix.startswith(entry):
                return True
        elif posix == entry or posix.startswith(entry + "/"):
            return True
    return False


def rendered_pages(docs_root: Path, excludes: Iterable[str] = ()) -> list[Path]:
    """Every file under *docs_root* that Jekyll renders, minus ``exclude`` entries."""
    skip = list(excludes)
    pages: list[Path] = []
    for directory, dirs, files in os.walk(docs_root):
        dirs.sort()
        for name in sorted(files):
            path = Path(directory) / name
            if path.is_symlink() or not path.is_file():
                continue
            if _excluded(path.relative_to(docs_root).as_posix(), skip):
                continue
            if is_rendered(path, docs_root):
                pages.append(path)
    return pages


# Far above any page under docs/ (the largest, architecture.md, is about 260 KB).
MAX_PAGE_BYTES = 16 * 1024 * 1024


def read_page(path: Path) -> str:
    """The page text as Jekyll reads it (UTF-8, a leading BOM dropped).

    Reads at most :data:`MAX_PAGE_BYTES` (the bounded read the size-cap rule
    of ``src/`` asks for; ``src.utils.files`` is not imported, so the
    publish script can run this file without the project's dependencies).
    """
    with path.open("rb") as handle:
        data = handle.read(MAX_PAGE_BYTES + 1)
    if len(data) > MAX_PAGE_BYTES:
        raise ValueError(f"{path} exceeds {MAX_PAGE_BYTES} bytes")
    return data.decode("utf-8-sig")


def _rendered_at(path: Path, root: Path) -> bool:
    try:
        return is_rendered(path, root)
    except ValueError:  # not below root
        return False


def _neutralise_one(raw_path: str, root: Path) -> str | None:
    path = Path(raw_path)
    resolved = path.resolve()
    if not resolved.is_file() or not resolved.is_relative_to(root):
        return None
    # A symlink counts as the page it is named as (Jekyll renders
    # ``notiz.md`` pointing to ``notiz.txt`` as Markdown) or as its target.
    # Only the directory is resolved, so the name stays the link's.
    named = path.parent.resolve() / path.name
    if not (_rendered_at(named, root) or _rendered_at(resolved, root)):
        return None
    text = read_page(resolved)
    findings = liquid_findings(text)
    if not findings:
        return None
    resolved.write_text(liquid_safe_outside_raw(text), encoding="utf-8")
    lines = ",".join(str(line) for line in sorted({finding.line for finding in findings}))
    # The entry names the file that was written: for a symlink its target,
    # which is what the publish step must stage again (the link's own blob
    # does not change).
    written = raw_path
    if path.is_symlink():
        written = str(resolved) if path.is_absolute() else os.path.relpath(resolved)
    return f"{written}:{lines}"


def iter_neutralise(
    paths: Iterable[str], docs_root: Path, skipped: list[str] | None = None
) -> Iterable[str]:
    """Rewrite each rendered page among *paths* that holds a Liquid pair.

    Front matter and intact raw blocks stay as they are; the rest goes
    through :func:`liquid_safe`. Yields ``"<path>:<line>,<line>"`` right
    after each file is written (for a symlink, the path of its target), so
    a caller that stops halfway still knows every rewritten file. A path
    that cannot be read (not UTF-8, too large, a symlink loop) is reported
    on stderr, added to *skipped* and passed over; the other paths are
    still checked.
    """
    root = docs_root.resolve()
    for raw_path in paths:
        try:
            entry = _neutralise_one(raw_path, root)
        except Exception as exc:  # one bad path must not stop the rest
            print(f"liquid.py: {raw_path} skipped ({type(exc).__name__}: {exc})", file=sys.stderr)
            if skipped is not None:
                skipped.append(raw_path)
            continue
        if entry is not None:
            yield entry


def neutralise(paths: Sequence[str], docs_root: Path) -> list[str]:
    """:func:`iter_neutralise` as a list."""
    return list(iter_neutralise(paths, docs_root))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--fix", action="store_true", help="rewrite pages that hold a Liquid pair")
    parser.add_argument("--docs-root", default="docs", help="the Pages source directory (default: docs)")
    parser.add_argument(
        "--null", action="store_true", help="read paths NUL-separated from stdin, end each entry with NUL"
    )
    parser.add_argument("paths", nargs="*")
    args = parser.parse_args(argv)
    docs_root = Path(args.docs_root)
    paths: list[str] = list(args.paths)
    if args.null:
        paths += [p for p in sys.stdin.buffer.read().decode("utf-8", "surrogateescape").split("\0") if p]
    if args.fix:
        # Exit 2 when a page was passed over, so the publish step warns that
        # not every staged page was checked.
        end = b"\0" if args.null else b"\n"
        skipped: list[str] = []
        for entry in iter_neutralise(paths, docs_root, skipped):
            sys.stdout.buffer.write(entry.encode("utf-8", "surrogateescape") + end)
            sys.stdout.buffer.flush()
        return 2 if skipped else 0
    found = False
    targets = [Path(p) for p in paths] or rendered_pages(docs_root)
    for path in targets:
        for finding in liquid_findings(read_page(path)):
            found = True
            print(f"{path}:{finding.line}: {finding.reason}: {finding.token[:60]!r}")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
