"""Error lines on the dashboard must follow the language switch.

``docs/assets/site.js`` renders every failure as ``<prefix> <detail>``.
The prefix was always a translation key, but the *detail* was the raw
``Error.message`` — and those messages were hard-coded German sentences
("Feed konnte nicht geparst werden", "Keine CSV-Daten für stoerungen
verfügbar."). An English visitor whose feed failed to load therefore got

    Feed could not be loaded: Feed konnte nicht geparst werden

and ``showError`` stored that same German sentence in
``dataset.errorDetail``, so ``applyTranslationsToDom`` re-rendered it
verbatim on every later language switch: the German half stayed for the
rest of the session.

The fix throws translation *keys* instead of sentences, resolves them
through ``resolveErrorDetail`` at render time, and keeps the key (not the
resolved sentence) in the dataset so the re-render path can translate it
again. These tests pin all three halves of that contract.

Like ``tests/test_dashboard_delay_threshold.py`` and
``tests/test_i18n_coverage_gate.py`` they read the shipped asset from
disk rather than executing it, so no JS runtime is required.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_DOCS = Path(__file__).resolve().parents[1] / "docs"
_SITE_JS = _DOCS / "assets" / "site.js"
_SITE_MIN_JS = _DOCS / "assets" / "site.min.js"

# Keys whose value is an error *detail* (the second half of the line).
_DETAIL_KEY_PREFIX = "err-"


def _site_js() -> str:
    return _SITE_JS.read_text(encoding="utf-8")


def _locate(haystack: str, marker: str, start: int = 0) -> int:
    """``str.index`` with a message that names the missing marker.

    These helpers run at collection time (``parametrize``), where a bare
    ``ValueError`` from ``str.index`` would surface as an opaque import
    error rather than "site.js was restructured".
    """
    at = haystack.find(marker, start)
    if at < 0:
        raise RuntimeError(
            f"docs/assets/site.js no longer contains {marker!r} — the "
            "STATUS_TEXT parser in this module needs updating"
        )
    return at


def _status_text_block(lang: str) -> str:
    """Return the source of one ``STATUS_TEXT`` locale sub-object."""
    js = _site_js()
    start = _locate(js, "const STATUS_TEXT = {")
    end = _locate(js, "\n  };", start)
    block = js[start:end]
    lang_start = _locate(block, f"\n    {lang}: {{")
    lang_end = _locate(block, "\n    },", lang_start)
    return block[lang_start:lang_end]


def _detail_keys(lang: str) -> dict[str, str]:
    """``err-*`` keys of one locale mapped to their literal value.

    Only string literals are collected; the ``en`` locale forwards its
    non-detail keys to ``I18N_EN[...]`` and those are not of interest
    here.
    """
    pattern = re.compile(r'"(' + _DETAIL_KEY_PREFIX + r'[a-z0-9-]+)":\s*"((?:[^"\\]|\\.)*)"')
    return {m.group(1): m.group(2) for m in pattern.finditer(_status_text_block(lang))}


# ----- the thrown values are keys, not sentences ---------------------


def test_feed_parse_failures_are_thrown_as_translation_keys() -> None:
    js = _site_js()
    assert 'throw new Error("err-feed-parse");' in js
    assert 'throw new Error("err-feed-no-channel");' in js


def test_unknown_summary_version_is_thrown_as_a_parameterised_key() -> None:
    # The detail names the version it found, so it carries one argument
    # rather than being a ready-made sentence.
    assert (
        "throw new Error(`err-summary-version${DETAIL_ARG_SEP}${data.schema_version}`);"
        in _site_js()
    ), "the schema guard must throw the key + argument, not a German sentence"


def test_no_thrown_error_message_is_a_german_sentence() -> None:
    """Any literal thrown in site.js must be a key or language-neutral.

    A German sentence here is the regression: it lands unchanged in the
    English UI because there is nothing left to translate it by.
    """
    german = re.compile(
        r"\b(?:konnte|nicht|keine?|verfügbar|werden|wurde|ohne|für|mit|und)\b",
        re.IGNORECASE,
    )
    thrown = re.findall(r"throw new Error\(([`\"'])((?:[^\\]|\\.)*?)\1\)", _site_js())
    assert thrown, "no throw sites found — has site.js been restructured?"
    offenders = [text for _quote, text in thrown if german.search(text)]
    assert offenders == [], f"German literal(s) thrown as an error detail: {offenders}"


# ----- both locales carry every detail key ---------------------------


def test_every_detail_key_exists_in_both_locales() -> None:
    de, en = _detail_keys("de"), _detail_keys("en")
    assert de, "no err-* detail keys found in the de locale"
    assert set(de) == set(en), (
        "err-* detail keys drifted between locales: "
        f"de-only={sorted(set(de) - set(en))} en-only={sorted(set(en) - set(de))}"
    )


@pytest.mark.parametrize("key", sorted(_detail_keys("de")))
def test_english_detail_differs_from_the_german_one(key: str) -> None:
    de, en = _detail_keys("de"), _detail_keys("en")
    assert en[key] != de[key], f"{key} is untranslated — the EN value repeats the German text"


@pytest.mark.parametrize("key", sorted(_detail_keys("en")))
def test_english_detail_has_no_german_orthography(key: str) -> None:
    # Cheap language sniff: umlauts / sharp s never occur in our English
    # copy, so their presence means the German string was copied over.
    value = _detail_keys("en")[key]
    assert not set(value) & set("äöüÄÖÜß"), f"{key} looks like German text: {value!r}"


def test_parameterised_detail_keeps_its_placeholder_in_both_locales() -> None:
    de, en = _detail_keys("de"), _detail_keys("en")
    assert "{arg}" in de["err-summary-version"]
    assert "{arg}" in en["err-summary-version"]


# ----- rendering and re-rendering ------------------------------------


def test_show_error_stores_the_key_not_the_rendered_sentence() -> None:
    """The core of the regression.

    Storing the resolved sentence is what stranded a German detail under
    an English prefix: ``applyTranslationsToDom`` re-renders from the
    dataset and has no way back to the key.
    """
    js = _site_js()
    assert "node.dataset.errorDetail = detailKey;" in js
    assert (
        "node.dataset.errorDetail = detail;" not in js
    ), "showError must store the translation key, not the already-resolved text"


def test_language_switch_rerenders_the_detail_through_the_resolver() -> None:
    js = _site_js()
    assert "resolveErrorDetail(node.dataset.errorDetail)" in js, (
        "the language-switch path must re-resolve the stored key; using the "
        "dataset value verbatim is the bug this suite guards"
    )


def test_resolver_passes_untranslatable_messages_through() -> None:
    """Browser/network messages ("Failed to fetch", "HTTP 503 – …") have
    no key. They must still reach the user instead of vanishing."""
    assert "return statusText(raw) || raw;" in _site_js()


def test_detail_argument_separator_cannot_collide_with_a_real_message() -> None:
    """The separator must be a C0 control character.

    A printable separator (``:`` or ``|``) occurs in real error messages
    and URLs, so an untranslatable message could be mistaken for a
    parameterised key of ours and be silently truncated.
    """
    match = re.search(r'const DETAIL_ARG_SEP = "((?:[^"\\]|\\.)*)";', _site_js())
    assert match is not None, "site.js must declare DETAIL_ARG_SEP"
    literal = match.group(1)
    assert re.fullmatch(r"\\u00[01][0-9a-f]", literal), (
        f"DETAIL_ARG_SEP must be an escaped C0 control character, got {literal!r}"
    )
    # Written as an escape so no raw control byte ends up in the source file.
    assert "\u0001" not in _site_js()


# ----- what the browser actually downloads ---------------------------


def test_shipped_bundle_is_in_step_with_the_source() -> None:
    """``site.min.js`` is what visitors execute — a stale bundle means the
    fix above is invisible in production.

    ``scripts/optimize_site_assets.py --check`` covers this, but only as a
    pre-commit hook; running it here puts the same gate into CI.
    """
    from scripts.optimize_site_assets import (  # noqa: PLC0415
        _minify_css,
        _minify_js,
        _sync_html_versions,
    )

    assert _minify_js(check_only=True), (
        "docs/assets/site.min.js is stale — run "
        "`python scripts/optimize_site_assets.py --skip-images`"
    )
    assert _minify_css(check_only=True), (
        "docs/assets/site.min.css is stale — run "
        "`python scripts/optimize_site_assets.py --skip-images`"
    )
    assert _sync_html_versions(check_only=True), (
        "the ?v= cache-bust tokens in docs/site.html no longer match the bundles"
    )


def test_shipped_bundle_carries_the_english_error_texts() -> None:
    minified = _SITE_MIN_JS.read_text(encoding="utf-8")
    for value in _detail_keys("en").values():
        assert value in minified, f"EN error detail missing from the shipped bundle: {value!r}"
