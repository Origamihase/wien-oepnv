from src.providers.oebb import _clean_title_keep_places


def test_wien_und_arrow_and_clean() -> None:
    t = "Verkehrsmeldung: Wien Floridsdorf Bahnhof und Wien Meidling Hbf"
    assert _clean_title_keep_places(t) == "Wien Floridsdorf ↔ Wien Meidling"


def test_clean_title_canonicalizes_endpoints() -> None:
    t = "Verkehrsmeldung: Wien Franz Josefs Bahnhof - St Poelten Hbf"
    assert _clean_title_keep_places(t) == "Wien Franz-Josefs-Bahnhof ↔ St. Pölten Hauptbahnhof"


def test_clean_title_expands_wien_hbf_abbreviation() -> None:
    t = "Störung: Wien Hbf (U) <-> Wien Meidling Bahnhof"
    assert _clean_title_keep_places(t) == "Wien Hauptbahnhof ↔ Wien Meidling"


def test_clean_title_removes_redundant_suffix() -> None:
    # Issue: Station name is duplicated in the feed title if it's already part of the message text.
    t = "Bahnsteig 2/3 in Sigmundsherberg nicht barrierefrei: Sigmundsherberg"
    assert _clean_title_keep_places(t) == "Bahnsteig 2/3 in Sigmundsherberg nicht barrierefrei"


def test_clean_title_removes_redundant_suffix_behind_a_category_label() -> None:
    # Live defect: ÖBB prefixes the title with a category label, so the
    # redundant restatement sits behind the SECOND colon. The first-colon
    # anchor compared "Bauarbeiten" against the rest, found no redundancy
    # and published the station twice — observed verbatim in
    # ``cache/oebb_c40d21/events.json`` as
    # ``S 4: Bauarbeiten: kein Halt in Lind-Rosegg Föderlach: Lind-Rosegg Föderlach``.
    t = "Bauarbeiten: kein Halt in Lind-Rosegg Föderlach: Lind-Rosegg Föderlach"
    assert _clean_title_keep_places(t) == "kein Halt in Lind-Rosegg Föderlach"


def test_clean_title_keeps_a_line_prefix_while_dropping_the_duplicate() -> None:
    # The same item as it reached the feed, line prefix included.
    t = "S 4: Bauarbeiten: kein Halt in Lind-Rosegg Föderlach: Lind-Rosegg Föderlach"
    assert _clean_title_keep_places(t) == "S 4: kein Halt in Lind-Rosegg Föderlach"


def test_clean_title_keeps_a_non_redundant_tail_after_a_label() -> None:
    # Guard against over-eager stripping: the tail is only dropped when one
    # side actually restates the other.
    t = "Bauarbeiten: kein Halt in Tullnerfeld"
    assert _clean_title_keep_places(t) == "kein Halt in Tullnerfeld"


def test_clean_title_does_not_cut_inside_a_clock_time() -> None:
    # The prefix loop strips a leading "Kategorie: " label. It matched the
    # colon of a CLOCK TIME too, because ``_is_category`` only inspects the
    # words: "Sperre 17" counts as a category, so "Sperre 17:30 Uhr Wien Hbf"
    # lost its hour and rendered as "30 Uhr Wien".
    out = _clean_title_keep_places("Sperre 17:30 Uhr Wien Hbf")
    assert "17:30 Uhr" in out
    assert not out.startswith("30 Uhr")


def test_clean_title_still_strips_a_real_category_label() -> None:
    # The clock guard must not disable the ordinary label strip.
    assert (
        _clean_title_keep_places("Störung: Wien Meidling ↔ Wien Liesing")
        == "Wien Meidling ↔ Wien Liesing"
    )
