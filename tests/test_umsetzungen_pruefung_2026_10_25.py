"""Nachprüfung 2026-10-25 of #2061 to #2065: ÖBB train counts and ÖBB's own "an einzelnen Tagen".

* ``_oebb_what_does_not_run_first`` counted only three- to five-digit train
  numbers: the Südstrecke notice of spring 2026 listed 13 trains ("EC 71",
  "EC 79" among them) and read "11 Züge". Every train counts once now, each
  number of a group too.
* The operator's decision of the same evening ("Nenne die Linien, nicht die
  Anzahl an Linien"): the count keeps the lines of the list ("11 Züge der
  Linien S1, S2 und S3"), in English too.
* ``_oebb_long_date_lists_in_words`` treats ÖBB's own "(jeweils an
  einzelnen Tagen, …)" like a list that went: the period of the same
  sentence goes too (documented in ``docs/architecture.md``).

Mutations checked against this file (each one caught, by the test named):

* only numbers of three to five digits count → ``test_short_train_numbers_count``.
* a carrier in front of a group counts as one more train → ``test_a_group_counts_its_numbers``.
* the count drops the lines → ``test_the_count_names_its_lines``.
* the English drops the lines → ``test_the_count_names_its_lines``.
* ÖBB's own phrase no longer lets the period go → ``test_oebbs_own_phrase_lets_the_period_go``.
"""

from __future__ import annotations

from src import build_feed as bf


def test_short_train_numbers_count() -> None:
    sentence = (
        "Wegen Bauarbeiten können zwischen Wien Hbf und Bruck/Mur Bahnhof / Graz Hbf von "
        "14.03.2026 (22:00 Uhr) bis 29.05.2026 (05:00 Uhr) (an einigen Tagen) die Züge EC "
        "206, EC 207, EC 71, EC 79, EC 533, RJ 751, RJX 637, RJX 639, IC 535, IC 850, IC "
        "1136, IC 1138 und D 1135 nicht fahren."
    )
    assert bf._oebb_what_does_not_run_first(sentence) == (
        "Wegen Bauarbeiten können 13 Züge (EC, RJ, RJX, IC und D) nicht fahren: zwischen Wien Hbf und Bruck/Mur "
        "Bahnhof / Graz Hbf von 14.03.2026 (22:00 Uhr) bis 29.05.2026 (05:00 Uhr) (an "
        "einigen Tagen)."
    )


def test_a_group_counts_its_numbers() -> None:
    sentence = (
        "Wegen Bauarbeiten können zwischen Gänserndorf Bahnhof und Bernhardsthal Bahnhof "
        "und zwischen Wien Floridsdorf Bahnhof (U) und Wien Süßenbrunn Bahnhst von "
        "09.03.2026 bis 03.07.2026 die Züge REX 1 (2334, 2338 und 2342) nicht fahren."
    )
    result = bf._oebb_what_does_not_run_first(sentence)
    assert result.startswith("Wegen Bauarbeiten können 3 Züge der Linie REX 1 nicht fahren: zwischen ")


def test_oebbs_own_phrase_lets_the_period_go() -> None:
    sentence = (
        "Wegen Bauarbeiten können von 13.04.2026 bis 28.05.2026 (jeweils an einzelnen "
        "Tagen, 23:20 Uhr - 04:20 Uhr) zwischen Flughafen Wien Bahnhof und Wien "
        "Mitte-Landstraße Bahnhof (U) einige Nahverkehrszüge nicht fahren."
    )
    assert bf._oebb_long_date_lists_in_words(sentence) == (
        "Wegen Bauarbeiten können (jeweils an einzelnen Tagen, 23:20 Uhr - 04:20 Uhr) "
        "zwischen Flughafen Wien Bahnhof und Wien Mitte-Landstraße Bahnhof (U) einige "
        "Nahverkehrszüge nicht fahren."
    )


def test_the_count_names_its_lines() -> None:
    sentence = (
        "Wegen Bauarbeiten können zwischen Wien Liesing Bahnhof und Wien Meidling Bahnhof "
        "(U) am 02.08.2026 die Züge S1 (28632), S1 (28684), S1 (28656), S1 (28664), S1 "
        "(28672), S2 (28053), S3 (28073), S2 (28093), S3 (28113), S2 (28133) und S2 "
        "(28153) nicht fahren."
    )
    german = bf._oebb_what_does_not_run_first(sentence)
    assert german == (
        "Wegen Bauarbeiten können 11 Züge der Linien S1, S2 und S3 nicht fahren: zwischen "
        "Wien Liesing Bahnhof und Wien Meidling Bahnhof (U) am 02.08.2026."
    )
    parts = bf._oebb_cannot_run_parts(german)
    assert parts is not None
    assert parts.head == "11 trains on lines S1, S2 and S3 cannot run"
    assert parts.rest == (
        " between Wien Liesing station and Wien Meidling station (U) on 02.08.2026."
    )
