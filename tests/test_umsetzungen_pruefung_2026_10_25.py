"""Nachprüfung 2026-10-25 of #2061 to #2065: ÖBB train counts and ÖBB's own "an einzelnen Tagen".

* ``_oebb_what_does_not_run_first`` counted only three- to five-digit train
  numbers: the Südstrecke notice of spring 2026 listed 13 trains ("EC 71",
  "EC 79" among them) and read "11 Züge". Every train counts once now, each
  number of a group too.
* ``_oebb_long_date_lists_in_words`` treats ÖBB's own "(jeweils an
  einzelnen Tagen, …)" like a list that went: the period of the same
  sentence goes too (documented in ``docs/architecture.md``).

Mutations checked against this file (each one caught, by the test named):

* only numbers of three to five digits count → ``test_short_train_numbers_count``.
* a carrier in front of a group counts as one more train → ``test_a_group_counts_its_numbers``.
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
        "Wegen Bauarbeiten können 13 Züge nicht fahren: zwischen Wien Hbf und Bruck/Mur "
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
    assert result.startswith("Wegen Bauarbeiten können 3 Züge nicht fahren: zwischen ")


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
