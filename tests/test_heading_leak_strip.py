"""Regression test for Bug 37A (WL HTML heading word leaks into description).

User audit feedback (Round 37): a comprehensive sweep of the current
feed surfaced 8 visible items where the WL HTML ``<h2>Heading</h2>
<p>Wegen …</p>`` structure produced a description starting with a
redundant category word::

    T: 11A: Bauarbeiten bis 05.06.2026
    D: Gleisbauarbeiten Wegen Fortschreiten der
       Gleisbauarbeiten für die Verlängerung der Linie 18 …

    T: 27A/28A/29A: Fronleichnamsumzug
    D: Veranstaltung Wegen Abhaltung eines christlichen Umzuges …

    T: 44A/N43: Dornbacher Straße
    D: Gleisbauarbeiten Wegen Gleisbauarbeiten in der
       Dornbacher Straße …

The body always restates the cause via ``Wegen <reason>``, so the
leading heading word is pure noise that wastes ~15 chars of the
180-char description budget AND repeats information the user
already sees in the title category.

Pre-Round-37 ``_strip_summary_category_prefix`` only handled cases
where the title body's FIRST WORD matched the description's first
word (e.g. ``T: 9/40/41/42: Gleisbauarbeiten`` /
``D: Gleisbauarbeiten Wegen …``). The audit cases all have unrelated
title bodies (``Fronleichnamsumzug``, ``Filmaufnahmen``,
``Dornbacher Straße``, ``Maurer Kirtag 2026``) so the
title-body-match branch did not fire.

Fix
===
A new third branch in ``_strip_summary_category_prefix``: when the
summary's first word is a known WL category (``Gleisbauarbeiten``,
``Bauarbeiten``, ``Kranarbeiten``, ``Veranstaltung``, …) AND the
SECOND word is ``Wegen``, strip the category — independent of the
title shape. Real German prose never opens a sentence with the bare
category word followed by ``Wegen``, so the only producer of that
pattern is the WL HTML heading leak. Lookahead-style safety.

Also extends ``_CATEGORY_PREFIX_WORDS`` with the additional headings
seen in the real WL cache: ``brückenbauarbeiten``,
``brueckenbauarbeiten``, ``brückenarbeiten``, ``brueckenarbeiten``,
``schienenarbeiten``, ``demonstration``, ``filmaufnahmen``,
``falschparker``.
"""

from __future__ import annotations

from src.build_feed import _strip_summary_category_prefix


class TestHeadingLeakStrippedIndependentOfTitle:
    """Branch 3 — strip ``<Category> Wegen <body>`` regardless of title shape."""

    def test_gleisbauarbeiten_with_unrelated_title(self) -> None:
        out = _strip_summary_category_prefix(
            "Gleisbauarbeiten Wegen Fortschreiten der Gleisbauarbeiten "
            "für die Verlängerung der Linie 18.",
            raw_title="11A: Bauarbeiten bis 05.06.2026",
        )
        assert out.startswith("Wegen Fortschreiten")
        assert "Gleisbauarbeiten Wegen" not in out

    def test_veranstaltung_with_fronleichnamsumzug_title(self) -> None:
        out = _strip_summary_category_prefix(
            "Veranstaltung Wegen Abhaltung eines christlichen Umzuges …",
            raw_title="27A/28A/29A: Fronleichnamsumzug",
        )
        assert out.startswith("Wegen Abhaltung")

    def test_veranstaltung_with_filmaufnahmen_title(self) -> None:
        out = _strip_summary_category_prefix(
            "Veranstaltung Wegen der Sperre der Zehetnergasse aufgrund von Filmaufnahmen …",
            raw_title="47A: Filmaufnahmen",
        )
        assert out.startswith("Wegen der Sperre")

    def test_kranarbeiten_with_unrelated_title(self) -> None:
        out = _strip_summary_category_prefix(
            "Kranarbeiten Wegen Kranarbeiten am Karlsplatz wird die Linie 4A umgeleitet.",
            raw_title="4A: Umleitung Karlsplatz",
        )
        assert out.startswith("Wegen Kranarbeiten")

    def test_rohrleitungsarbeiten_with_unrelated_title(self) -> None:
        out = _strip_summary_category_prefix(
            "Rohrleitungsarbeiten Wegen Wartungsarbeiten an einer Rohrleitung.",
            raw_title="49A/50A/50B/N49: Wartung",
        )
        assert out.startswith("Wegen Wartungsarbeiten")

    def test_demonstration_with_unrelated_title(self) -> None:
        out = _strip_summary_category_prefix(
            "Demonstration Wegen einer Demonstration im 1. Bezirk.",
            raw_title="D: Sperre Ring",
        )
        assert out.startswith("Wegen einer Demonstration")


class TestExistingBehaviorPreserved:
    """Round 1–6 patterns (title-body matches description word) must continue to hold."""

    def test_matching_first_word_still_stripped(self) -> None:
        # Title body starts with "Gleisbauarbeiten", desc starts with
        # "Gleisbauarbeiten Wegen ..." → strip via Branch 1.
        out = _strip_summary_category_prefix(
            "Gleisbauarbeiten Wegen Bauarbeiten",
            raw_title="9/40/41/42: Gleisbauarbeiten",
        )
        assert out.startswith("Wegen")

    def test_category_prepended_to_title_equivalent_desc(self) -> None:
        # Title body "Busse halten ...", desc "Bauarbeiten Busse halten ..."
        # → strip via Branch 2.
        out = _strip_summary_category_prefix(
            "Bauarbeiten Busse halten Hauptstraße",
            raw_title="62A: Busse halten Hauptstraße",
        )
        assert out.startswith("Busse halten")


class TestNoFalsePositives:
    """The Wegen branch must not trip on real German prose."""

    def test_leading_wegen_not_stripped(self) -> None:
        # Already starts with Wegen — no category to strip.
        text = "Wegen Schaden kommt es zu Verspätungen."
        out = _strip_summary_category_prefix(text, raw_title="40: Sperre")
        assert out == text

    def test_subject_not_in_category_set_not_stripped(self) -> None:
        # "Heute" / "Information" / "Hinweis" are not WL category headings.
        for text in [
            "Heute Wegen Bauarbeiten kein Betrieb",
            "Information Wegen Fahrgastinformation",
            "Hinweis Wegen technischer Probleme",
        ]:
            out = _strip_summary_category_prefix(text, raw_title="U6: foo")
            assert out == text, f"Falsely stripped: {text!r}"

    def test_normal_sentence_with_subject_not_stripped(self) -> None:
        text = "Die Linie 40 wird wegen Bauarbeiten umgeleitet."
        out = _strip_summary_category_prefix(text, raw_title="40: Umleitung")
        assert out == text

    def test_second_word_not_wegen_not_stripped(self) -> None:
        # Category word present but next word is body content, not ``Wegen``.
        # The original Branch 1/2 logic governs.
        text = "Bauarbeiten Renngasse: weitere Information"
        out = _strip_summary_category_prefix(
            text, raw_title="U6: Verspätung"  # unrelated title
        )
        # Branch 3 declines (no Wegen), Branches 1/2 decline (title
        # body's first word is "Verspätung", doesn't match
        # "Bauarbeiten" or "Renngasse:"). Text passes through.
        assert out == text


class TestEdgeCases:
    def test_empty_summary(self) -> None:
        assert _strip_summary_category_prefix("", raw_title="U6: foo") == ""

    def test_single_word_summary(self) -> None:
        # No second word to check — Branch 3 declines.
        out = _strip_summary_category_prefix("Bauarbeiten", raw_title="U6: foo")
        assert out == "Bauarbeiten"

    def test_summary_with_only_whitespace(self) -> None:
        assert _strip_summary_category_prefix("   ", raw_title="U6: foo") == "   "


class TestComplexHeadingWords:
    """Verify all words in the extended _CATEGORY_PREFIX_WORDS set work."""

    def test_brueckenbauarbeiten_stripped(self) -> None:
        out = _strip_summary_category_prefix(
            "Brückenbauarbeiten Wegen Reparatur einer Brücke.",
            raw_title="U6: Sperre",
        )
        assert out.startswith("Wegen Reparatur")

    def test_schienenarbeiten_stripped(self) -> None:
        out = _strip_summary_category_prefix(
            "Schienenarbeiten Wegen Schienenstoß-Reparatur.",
            raw_title="40: Umleitung",
        )
        assert out.startswith("Wegen Schienenstoß")

    def test_straßenbauarbeiten_stripped(self) -> None:
        out = _strip_summary_category_prefix(
            "Straßenbauarbeiten Wegen Asphaltierung.",
            raw_title="60A: Sperre",
        )
        assert out.startswith("Wegen Asphaltierung")

    def test_falschparker_stripped(self) -> None:
        out = _strip_summary_category_prefix(
            "Falschparker Wegen eines Falschparkers wird die Linie umgeleitet.",
            raw_title="40: Umleitung",
        )
        assert out.startswith("Wegen eines Falschparkers")


class TestEndToEnd:
    """Pipeline check on user-reported real cache items."""

    def test_user_audit_11A_bauarbeiten(self) -> None:
        # Real cache item.
        title = "11A: Bauarbeiten bis 05.06.2026"
        desc = (
            "Gleisbauarbeiten Wegen Fortschreiten der Gleisbauarbeiten "
            "für die Verlängerung der Linie 18 kommt es zu einer "
            "Anpassung der Umleitung."
        )
        out = _strip_summary_category_prefix(desc, raw_title=title)
        # Leading heading word gone; body intact.
        assert not out.startswith("Gleisbauarbeiten")
        assert out.startswith("Wegen Fortschreiten")
        assert "Verlängerung der Linie 18" in out

    def test_user_audit_56A_60A_kirtag(self) -> None:
        # Most complex case: title with stacked sub-prefix +
        # description with heading leak.
        title = "56A/60A/N60: 56A, 60A, N60, Rufbus N61: Maurer Kirtag 2026"
        desc = (
            "Veranstaltung Wegen Abhaltung des Maurer Kirtages am "
            "Maurer Hauptplatz kommt es zu Umleitungen bei den dort "
            "verkehrenden Buslinien."
        )
        out = _strip_summary_category_prefix(desc, raw_title=title)
        assert not out.startswith("Veranstaltung")
        assert out.startswith("Wegen Abhaltung")
