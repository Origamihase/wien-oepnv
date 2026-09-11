"""Regressions for the feed display defects catalogued in the 2026-09 audits.

Each test pins one visible defect that reached the published feeds
(``docs/feed.xml`` / ``docs/feed.en.xml``) and is documented under
``docs/archive/audits/``:

* ``audit-title-bauarbeiten26-2026-09-09.md`` — ``17A: Bauarbeiten26``
* ``audit-title-line-deduplication-2026-09-07.md`` — ``3A: 3A Netzänderung …``
* ``audit-2026-09-08.md`` — run-together words in Baustellen descriptions
* ``audit-2026-09-05.md`` — bare ``X4X`` sentinel leak, ``4. Gate`` vs ``4. Tor``
* ``audit-2026-09-06.md`` — ``Uhr`` → "clock"/"watch", truncation that cuts
  off the reason it announces
* ``audit-2026-09-09.md`` — ``Bereich`` → "range"
* ``audit-2026-09-10.md`` — ``Einstieg`` kept German in the EN feed
* ``audit-2026-09-11.md`` — ``&`` double-escaped in ``<description>``,
  ``Minuten`` instead of ``min`` in the Stammstrecke feed event
"""

from src.build_feed import (
    _RESIDUAL_PLACEHOLDER_RE,
    _apply_domain_glossary,
    _escape_description_markup,
    _mask_entities,
    _normalise_for_translation,
    _truncate_summary_180,
    _unmask_entities,
)
from src.feed.stammstrecke import _build_event
from src.providers.wl_lines import _ensure_line_prefix, _extract_prefix_lines
from src.providers.wl_text import _tidy_title_wl
from src.utils.text import repair_glued_words


# --------------------------------------------------------------------------
# 1. Stranded 2-digit year glued onto the preceding word
# --------------------------------------------------------------------------


def test_two_digit_year_is_stripped_whole() -> None:
    # The live cache item that produced ``17A: Bauarbeiten26``.
    assert _tidy_title_wl("Bauarbeiten ab 14.09.26") == "Bauarbeiten"


def test_four_digit_year_still_stripped_whole() -> None:
    assert _tidy_title_wl("Bauarbeiten ab 14.09.2026") == "Bauarbeiten"


def test_date_without_year_still_stripped() -> None:
    assert _tidy_title_wl("Gleisbauarbeiten ab 14.09.") == "Gleisbauarbeiten"


def test_malformed_year_leaves_title_untouched_rather_than_half_stripped() -> None:
    # Neither alternative fits, so nothing is removed — a half-strip is the
    # exact failure this fix exists to prevent.
    assert _tidy_title_wl("Bauarbeiten ab 14.09.266") == "ab 14.09.266"


def test_date_at_string_start_is_stripped() -> None:
    assert _tidy_title_wl("ab 03.10.26 Umleitung") == "Umleitung"


def test_unrelated_date_is_preserved() -> None:
    assert (
        _tidy_title_wl("Veranstaltung am 12.09.2026")
        == "Veranstaltung am 12.09.2026"
    )


# --------------------------------------------------------------------------
# 2. Line identifier repeated at the start of the title body
# --------------------------------------------------------------------------


def test_duplicate_line_token_removed_from_body() -> None:
    body, lines = _extract_prefix_lines("3A: 3A Netzänderung Betrieb ab Riemergasse")
    assert lines == ["3A"]
    assert body == "Netzänderung Betrieb ab Riemergasse"
    assert (
        _ensure_line_prefix("3A: 3A Netzänderung Betrieb ab Riemergasse", ["3A"])
        == "3A: Netzänderung Betrieb ab Riemergasse"
    )


def test_duplicate_token_removed_when_only_one_of_several_lines_repeats() -> None:
    body, lines = _extract_prefix_lines("11A, 11B: 11A Gleisschaden")
    assert lines == ["11A", "11B"]
    assert body == "Gleisschaden"


def test_duplicate_token_removed_across_dash_separator() -> None:
    assert _extract_prefix_lines("U1: U1 - Gleisarbeiten") == ("Gleisarbeiten", ["U1"])


def test_word_boundary_protects_longer_number() -> None:
    # ``1`` must not eat the ``1`` of ``10er``.
    assert _extract_prefix_lines("1: 10er Garnitur im Einsatz") == (
        "10er Garnitur im Einsatz",
        ["1"],
    )


def test_ordinal_after_line_code_is_not_a_duplicate() -> None:
    assert _extract_prefix_lines("10: 10. Bezirk gesperrt") == (
        "10. Bezirk gesperrt",
        ["10"],
    )


def test_duration_after_line_code_is_not_a_duplicate() -> None:
    assert _extract_prefix_lines("5: 5 Minuten Verspätung") == (
        "5 Minuten Verspätung",
        ["5"],
    )


def test_count_noun_after_line_code_is_not_a_duplicate() -> None:
    assert _extract_prefix_lines("44: 44 Fahrten entfallen") == (
        "44 Fahrten entfallen",
        ["44"],
    )


def test_strip_never_empties_the_body() -> None:
    assert _extract_prefix_lines("3A: 3A") == ("3A", ["3A"])


def test_body_without_repetition_is_untouched() -> None:
    assert _extract_prefix_lines("44: Veranstaltung Züge halten") == (
        "Veranstaltung Züge halten",
        ["44"],
    )


# --------------------------------------------------------------------------
# 3. Run-together words in upstream prose
# --------------------------------------------------------------------------


def test_sentence_glue_after_period_is_repaired() -> None:
    assert (
        repair_glued_words("kann aufrecht gehalten werden.Nähere Informationen")
        == "kann aufrecht gehalten werden. Nähere Informationen"
    )


def test_word_glue_is_repaired() -> None:
    assert (
        repair_glued_words("Hadikgasse.DerFußgängerverkehr")
        == "Hadikgasse. Der Fußgängerverkehr"
    )
    assert (
        repair_glued_words("SchönbrunnerSchloßbrücke") == "Schönbrunner Schloßbrücke"
    )


def test_bracket_glue_is_repaired() -> None:
    assert (
        repair_glued_words("in Fahrtrichtung 14. Bezirk(Hadikgasse) gesperrt")
        == "in Fahrtrichtung 14. Bezirk (Hadikgasse) gesperrt"
    )


def test_ordinals_dates_and_abbreviations_survive() -> None:
    for text in (
        "Ab 15. September 2026, etwa 00:30 Uhr, bis Ende November 2026.",
        "Beginn: 14.09.2026 00:00 Uhr Maßnahme: Gleisbau Bezirk: 15",
        "Gerasdorf b. Wien und Karlsplatz U. Grund: Rettungseinsatz.",
        "Wiener Linien GmbH & Co KG",
        "https://www.data.gv.at/katalog",
    ):
        assert repair_glued_words(text) == text


def test_binnen_i_gender_form_is_not_split() -> None:
    text = "Die MitarbeiterInnen informieren SchülerInnen."
    assert repair_glued_words(text) == text


def test_repair_is_idempotent() -> None:
    once = repair_glued_words("werden.NähereInformationen")
    assert repair_glued_words(once) == once


# --------------------------------------------------------------------------
# 4. Truncation that announces information it then cuts off
# --------------------------------------------------------------------------


_LABEL_TAIL_SUMMARY = (
    "Unregelmäßige Intervalle in beiden Richtungen bei den Linien elf und "
    "einundsiebzig im gesamten Streckenabschnitt zwischen Kaiserebersdorf "
    "und Zentralfriedhof. Grund: Rettungseinsatz."
)


def test_dangling_label_is_dropped_at_the_truncation_point() -> None:
    out = _truncate_summary_180(_LABEL_TAIL_SUMMARY)
    # Pre-fix the feed published "… Zentralfriedhof. Grund …" — a label
    # announcing a reason that the cut had already swallowed.
    assert "Grund" not in out
    assert out.endswith("Zentralfriedhof. …")


def test_label_with_its_value_intact_is_kept() -> None:
    short = "Unregelmäßige Intervalle. Grund: Rettungseinsatz."
    assert _truncate_summary_180(short) == short


# --------------------------------------------------------------------------
# 5. Mangled translation sentinels
# --------------------------------------------------------------------------


def test_bare_index_sentinel_is_detected() -> None:
    leaked = "S 60: Wien Meidling ↔ Wien Hauptbahnhof ↔X4X Wien Stadlau"
    assert _RESIDUAL_PLACEHOLDER_RE.search(leaked)


def test_prefixed_sentinel_still_detected() -> None:
    assert _RESIDUAL_PLACEHOLDER_RE.search("XGLOdeadbeefX2X leaked")


def test_clean_english_text_is_not_flagged() -> None:
    for text in (
        "Track works between Xaverplatz and Sievering",
        "Wien Hbf ↔ Wien Stadlau",
        "Exit 4X and X4 crossing",
    ):
        assert not _RESIDUAL_PLACEHOLDER_RE.search(text)


# --------------------------------------------------------------------------
# 6. Translation-only surface normalisation
# --------------------------------------------------------------------------


def test_clock_suffix_dropped_before_translation() -> None:
    assert (
        _normalise_for_translation("Wr. Neustadt Hbf bis 15:13 Uhr")
        == "Wr. Neustadt Hbf bis 15:13"
    )
    assert _normalise_for_translation("um 9 Uhr") == "um 9"


def test_clock_suffix_normalisation_leaves_other_uses_alone() -> None:
    for text in ("Die Uhr am Turm", "Uhrzeit beachten", "Verspätung 2026 Uhr"):
        assert _normalise_for_translation(text) == text


def test_gate_designation_is_masked_as_one_entity() -> None:
    masked, mapping = _mask_entities("Zentralfriedhof, 4. Tor")
    assert "Tor" not in masked
    assert _unmask_entities(masked, mapping) == "Zentralfriedhof, 4. Tor"


def test_ordinal_without_gate_noun_is_not_treated_as_a_gate() -> None:
    masked, mapping = _mask_entities("Der 3. Bezirk ist gesperrt")
    assert _unmask_entities(masked, mapping) == "Der 3. Bezirk ist gesperrt"


def test_boarding_and_area_terms_resolve_to_english() -> None:
    processed, mapping = _apply_domain_glossary(
        "Einstieg bei Vorgartenstraße im Bereich Hernalser Hauptstraße"
    )
    assert "Einstieg" not in processed
    assert "Bereich" not in processed
    assert sorted(mapping.values()) == ["boarding", "in the area of"]


def test_street_festival_resolves_to_english() -> None:
    processed, mapping = _apply_domain_glossary("Währinger Straßenfest 2026")
    assert "Straßenfest" not in processed
    assert "street festival" in mapping.values()


def test_oebb_station_abbreviation_resolves_to_english() -> None:
    processed, mapping = _apply_domain_glossary(
        "Halt in Bahnhst. Gerasdorf entfällt", source="ÖBB"
    )
    assert "Bahnhst" not in processed
    assert "station" in mapping.values()


# --------------------------------------------------------------------------
# 7. Double-escaped ampersands in the published <description>
# --------------------------------------------------------------------------


def test_ampersand_is_not_escaped_at_the_description_sink() -> None:
    # ElementTree applies the single XML escape on serialise; escaping ``&``
    # here too published "GmbH &amp;amp; Co KG"
    # (``docs/archive/audits/audit-2026-09-11.md`` §2).
    assert (
        _escape_description_markup("Wiener Linien GmbH & Co KG")
        == "Wiener Linien GmbH & Co KG"
    )


def test_angle_brackets_are_still_escaped_at_the_description_sink() -> None:
    # The injection defence is unchanged: a tag can only be formed by a RAW
    # ``<``, and that never survives this sink. (The end-to-end proof lives in
    # tests/test_description_html_injection.py, which drives _emit_item.)
    assert (
        _escape_description_markup("<img src=x onerror=alert(1)>")
        == "&lt;img src=x onerror=alert(1)&gt;"
    )
    assert _escape_description_markup("Ersatzbus für <80") == "Ersatzbus für &lt;80"


def test_already_encoded_entity_stays_inert_without_double_escaping() -> None:
    # An entity in the source text is inert in the HTML context by itself —
    # it can never become a tag — so it needs no second layer of escaping.
    assert _escape_description_markup("&lt;script&gt;") == "&lt;script&gt;"


# --------------------------------------------------------------------------
# 8. "min" instead of "Minuten" in the Stammstrecke feed event
# --------------------------------------------------------------------------


def test_stammstrecke_event_uses_the_min_abbreviation() -> None:
    from datetime import UTC, datetime

    from src.feed.stammstrecke import DIRECTIONS

    now = datetime(2026, 9, 12, 8, 0, tzinfo=UTC)
    event = _build_event(
        direction=DIRECTIONS[0],
        avg_delay_minutes=1.5,
        now=now,
        episode_start=now,
    )
    description = event["description"]
    assert "1.5 min" in description
    assert "Minuten" not in description


# --------------------------------------------------------------------------
# 9. Vienna districts in the EN feed
# --------------------------------------------------------------------------


def test_district_resolves_deterministically() -> None:
    processed, mapping = _apply_domain_glossary(
        "Die Umleitung in Fahrtrichtung 21. Bezirk erfolgt über die Angyalföldstraße"
    )
    assert "Bezirk" not in processed
    assert "district" in mapping.values()
