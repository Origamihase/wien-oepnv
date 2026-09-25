"""Regression test for Bug 32A (trailing WL directional marker '>').

User feedback: a WL Hinweis surfaced with a dangling ``>`` in the
description::

    T: "2: Veranstaltung Betrieb ab Schwedenplatz"
    D: "Betrieb ab Schwedenplatz > [Am 16.05.2026]"

The ``>`` is WL's ASCII "service onwards" arrow. With a destination
after it (``Betrieb ab Schwedenplatz > Praterstern``) it carries
meaning. Standalone at the end of the summary it reads like a
broken HTML tag glyph or a truncation artifact next to the
``[Am 16.05.2026]`` timeframe bracket.

The fix strips trailing ``>``/``<`` (with optional surrounding
whitespace) from the summary in ``_format_item_content``. Marker
characters mid-text are preserved — only the trailing form (which
has no semantic referent) is removed.

The strip happens BEFORE the title-body duplicate check (Bug 27A)
so a summary like ``Betrieb ab Schwedenplatz >`` can match against
a title body of ``Betrieb ab Schwedenplatz`` and be dropped
entirely when redundant.

Cache items affected (current snapshot): WL Störung #30
(``Ring, Volkstheater >``), #32 (``Schwedenplatz >``), #42
(``Thaliastraße >`` — additionally triggers Round 27 dedup after
the strip).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

from src import build_feed
from src.feed_types import FeedItem


def _format(raw_title: str, raw_desc: str, *, split_reason: bool = True) -> tuple[str, str]:
    item = cast(
        FeedItem,
        {
            "title": raw_title,
            "description": raw_desc,
            "source": "Wiener Linien",
            "category": "Störung",
            "guid": "test",
            "link": "",
        },
    )
    now = datetime(2026, 5, 16, 12, 0, tzinfo=UTC)
    formatted = build_feed._format_item_content(
        item, ident="t", starts_at=now, ends_at=None, split_reason=split_reason
    )
    return formatted.title_out, formatted.desc_text_truncated


class TestTrailingDirectionalMarkerStripped:
    def test_schwedenplatz_marker_stripped(self) -> None:
        # User's exact reproduction.
        title = "2: Veranstaltung Betrieb ab Schwedenplatz"
        desc = "Veranstaltung\nBetrieb ab Schwedenplatz >"
        _, out = _format(title, desc)
        # The dangling ">" must NOT appear in the rendered description.
        assert ">" not in out.replace("[", "")
        # And the timeframe is still present.
        assert "[Am" in out or "[Seit" in out

    def test_volkstheater_marker_strip_enables_dedup_across_the_category_word(
        self,
    ) -> None:
        """Same proof as Thaliastraße above, one branch over.

        What changed here: this test used to assert ``"Volkstheater" in
        out``, i.e. that the marker went and the text stayed. It stayed
        only because the duplicate check compared a summary with its
        category word stripped against a title body that still carried
        one, so ``Veranstaltung`` on both sides made the two look
        different. They are not — the reader saw ``Betrieb ab Ring,
        Volkstheater`` in the headline and again underneath it. The check
        now strips on both sides and the restatement is dropped, which is
        exactly what this file's docstring says the strip exists to
        enable.

        The assertion below still proves the marker strip: without it the
        summary would read ``Betrieb ab Ring, Volkstheater >``, would not
        match the title body, and would survive.

        The difference to Thaliastraße is the branch, and it is why both
        tests earn their place: there the title does NOT name the reason,
        so it is kept as ``Grund: Gleisbauarbeiten.``; here the title
        opens with ``Veranstaltung`` itself, so nothing is left to save.
        """
        title = "2: Veranstaltung Betrieb ab Ring, Volkstheater"
        desc = "Veranstaltung\nBetrieb ab Ring, Volkstheater >"
        # The colliding-title path, where the dedupe decides.
        _, out = _format(title, desc, split_reason=False)
        assert "Volkstheater" not in out
        assert out.strip().startswith("["), out
        # Published since 2026-09-25: the consequence moves under the short
        # title, once, and without the marker.
        short, out = _format(title, desc)
        assert short == "2: Veranstaltung"
        assert out.startswith("Betrieb ab Ring, Volkstheater [")
        assert ">" not in out

    def test_thaliastrasse_marker_strip_enables_dedup(self) -> None:
        # Stripping the trailing ">" makes the summary match the title body
        # verbatim, so the Round 27 duplicate check fires and the restatement
        # is dropped. That is what this test is about, and the first
        # assertion below is what proves it: without the strip the summary
        # would still read "Betrieb ab Thaliastraße >" and survive.
        #
        # What the strip leaves behind changed afterwards. "Gleisbauarbeiten"
        # is the REASON of this WL display ticker, not a leaked HTML heading,
        # and dropping it too left the item as a headline over a bare
        # timeframe — never saying why. It is now kept as "Grund: …" (see
        # ``_reason_only_summary``); the old ``startswith("[")`` assertion
        # pinned that empty leftover, not the marker strip this file tests.
        title = "46: Betrieb ab Thaliastraße"
        desc = "Gleisbauarbeiten\nBetrieb ab Thaliastraße >"
        _, out = _format(title, desc)
        # The title body must NOT be restated in the description.
        assert "Betrieb ab Thaliastraße" not in out
        assert ">" not in out, "the dangling marker must be gone either way"
        assert out.startswith("Grund: Gleisbauarbeiten."), out


class TestGluedMarkerReconcilesTitleAndBody:
    """The arrow glued to the next token — where title and body disagree.

    ``_tidy_title_wl`` ends on ``re.sub(r"[<>«»‹›]+", "", t)``, so the
    title never carries the glyph. The description did. Live in the
    cache on 2026-09-19::

        T: 1: Bhf. Hütteldorf ÖBB-Ersatzbus für 80
        D: Bhf. Hütteldorf ÖBB-Ersatzbus für <80

    One item said two different things about the same replacement bus.
    The fix is in the *comparison*, not in the text: the duplicate check
    ignores ``<``/``>`` so the restatement is recognised and dropped.

    The first attempt stripped the glyph from the summary itself and
    broke five injection tests — ``&lt;b&gt;x&lt;/b&gt;`` decodes to the
    literal text ``<b>x</b>``, which the pipeline carries as plain text
    on purpose. ``TestLiteralAngleBracketTextIsNeverRewritten`` below
    pins that lesson.
    """

    def test_the_restatement_is_recognised_despite_the_glyph(self) -> None:
        title = "1: Bhf. Hütteldorf ÖBB-Ersatzbus für 80"
        desc = "Bhf. Hütteldorf\nÖBB-Ersatzbus für <80"
        _, out = _format(title, desc)
        assert "<80" not in out
        # The headline already says it: only the timeframe remains.
        assert out.strip().startswith("[")

    def test_a_body_that_says_more_keeps_its_text_verbatim(self) -> None:
        """No match, no rewrite — the glyph is upstream's, not ours to edit.

        Deliberately narrower than "strip every stray marker": the only
        thing established here is that title and body were the same
        sentence. Where they are not, the published text stays exactly
        as upstream wrote it.
        """
        _, out = _format("1: Ersatzbus für 80 ab Hütteldorf", "Ersatzbus für <80 ab Penzing")
        assert "für <80 ab Penzing" in out

    def test_a_body_that_only_differs_in_words_is_still_no_duplicate(self) -> None:
        _, out = _format("1: Ersatzbus für 80", "Ersatzbus für <90")
        assert "für <90" in out

    def test_the_comparison_stays_case_insensitive(self) -> None:
        """The first comparison casefolds; the marker-blind one must too.

        WL writes the same words with different capitalisation in title
        and ticker text often enough that a case-sensitive second attempt
        would quietly stop recognising the restatement.
        """
        _, out = _format("1: Ersatzbus für 80", "ersatzbus für <80")
        assert "ersatzbus" not in out.casefold()
        assert out.strip().startswith("[")


class TestLiteralAngleBracketTextIsNeverRewritten:
    """Regression guard for the five injection tests this change broke once.

    ``html_to_text`` decodes an entity-escaped ``&lt;b&gt;`` into the
    literal characters ``<b>``, which stay in the body as TEXT and are
    escaped again at the ``_emit_item`` sink. Any rule that removes
    ``<`` before a word destroys that text — and with it the evidence
    that the sink, not the body, is what makes an injected tag inert.
    """

    def test_literal_tag_text_survives_the_summary_stage(self) -> None:
        _, out = _format("31: Hinweis", "&lt;b&gt;x&lt;/b&gt; upstream")
        assert "<b>x</b> upstream" in out

    def test_a_script_payload_keeps_its_angle_brackets(self) -> None:
        """Only the brackets are this file's business.

        ``alert(1)`` comes out as ``alert (1)`` — ``repair_glued_words``
        puts a space before an opening bracket glued to a word. That is
        pre-existing and unrelated; asserting the payload verbatim would
        pin someone else's rule by accident.
        """
        _, out = _format("31: Hinweis", "&lt;script&gt;alert(1)&lt;/script&gt; Text")
        assert "<script>" in out
        assert "</script>" in out


class TestMidTextDirectionalMarkerPreserved:
    def test_arrow_with_destination_kept(self) -> None:
        # ``A > B`` is a legitimate WL directional clause — preserve.
        title = "U6: Verspätung"
        desc = "Betrieb ab Schwedenplatz > Praterstern wegen Bauarbeiten."
        _, out = _format(title, desc)
        assert "Schwedenplatz > Praterstern" in out

    def test_arrow_in_middle_unchanged(self) -> None:
        # An arrow inside a sentence stays put — only trailing forms
        # are noise.
        title = "U6: Test"
        desc = "Linie U6 > Floridsdorf umgeleitet."
        _, out = _format(title, desc)
        assert "U6 > Floridsdorf" in out


class TestNormalTitlesUntouched:
    def test_no_marker_no_change(self) -> None:
        title = "U6: Verspätung wegen Schadhaftem Fahrzeug"
        desc = "Linie U6: Unregelmäßige Intervalle in beiden Richtungen."
        _, out = _format(title, desc)
        assert "Unregelmäßige Intervalle" in out
        assert "<" not in out and ">" not in out

    def test_multiple_trailing_markers_all_stripped(self) -> None:
        # Defence: ``>>>`` and ``> > >`` patterns also collapse.
        title = "2: Test Betrieb ab Foo"
        desc = "Test\nBetrieb ab Foo >>>"
        _, out = _format(title, desc)
        assert ">" not in out.replace("[", "").replace("]", "")

    def test_trailing_less_than_marker_stripped(self) -> None:
        # WL also occasionally uses ``<`` for opposite direction.
        title = "2: Test Betrieb ab Foo"
        desc = "Test\nBetrieb ab Foo <"
        _, out = _format(title, desc)
        assert "Foo <" not in out
