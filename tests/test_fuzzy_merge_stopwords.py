from src.feed.merge import _has_significant_overlap, deduplicate_fuzzy

def test_has_significant_overlap_stopwords() -> None:
    # Only stopwords in both, they SHOULD merge because there's nothing else to distinguish them.
    # The requirement is to avoid false-positive merge on single generic tokens when they refer to different things.
    # i.e., "Störung" shouldn't merge with "Störung am Schottentor".
    assert _has_significant_overlap("Störung Info", "Info Störung")

    # "Störung" and "Störung am Schottentor" -> meaningful intersection is empty, union has meaningful word.
    # Should not merge.
    assert not _has_significant_overlap("Störung", "Störung am Schottentor")

    # Exact same stopwords only
    assert _has_significant_overlap("Störung", "Störung")

    # Meaningful overlap -> True
    # intersection: {'schottentor'}, union: {'schottentor', 'störung', 'info', 'ausfall'}
    # length: 1 / 4 = 0.25 (not >= 0.4) so this returns False. Let's make it >= 0.4
    assert _has_significant_overlap("Schottentor Störung", "Schottentor")

    # Stopwords overlap, but no meaningful overlap, meaning intersection exists but is only stopwords
    # and there are other words -> False
    assert not _has_significant_overlap("Kettenbrückengasse Störung", "Stephansplatz Störung")

    # Completely distinct
    assert not _has_significant_overlap("A", "B")


def test_no_merge_on_generic_filler_words() -> None:
    """German fillers ("im", "Bereich", "am") must not bridge two titles
    that otherwise reference different stations."""
    # Different stations should never merge.
    assert not _has_significant_overlap(
        "Störung im Bereich Praterstern", "Störung im Bereich Karlsplatz"
    )
    assert not _has_significant_overlap(
        "Aufzug defekt am Längenfeldgasse", "Aufzug defekt am Karlsplatz"
    )
    assert not _has_significant_overlap(
        "Sperre wegen Bauarbeiten", "Sperre wegen Polizeieinsatz"
    )
    # Same station with related verbs: should still recognise as overlap.
    assert _has_significant_overlap(
        "Störung Karlsplatz", "Karlsplatz gesperrt"
    )


def test_dedupe_fuzzy_keeps_distinct_station_events() -> None:
    """Regression: two U1 incidents at different stations must stay separate."""
    items = [
        {
            "guid": "a",
            "_identity": "a",
            "source": "Wiener Linien",
            "title": "U1: Störung im Bereich Praterstern",
            "description": "Signalstörung im Bereich Praterstern.",
        },
        {
            "guid": "b",
            "_identity": "b",
            "source": "Wiener Linien",
            "title": "U1: Störung im Bereich Karlsplatz",
            "description": "Signalstörung im Bereich Karlsplatz.",
        },
    ]
    result = deduplicate_fuzzy(items)
    assert len(result) == 2
    titles = sorted(item["title"] for item in result)
    assert titles == [
        "U1: Störung im Bereich Karlsplatz",
        "U1: Störung im Bereich Praterstern",
    ]


def test_dedupe_fuzzy_still_merges_same_topic() -> None:
    """The fuzzy merge must still combine ÖBB+VOR for the same incident."""
    items = [
        {
            "guid": "oebb-foo",
            "_identity": "oebb|foo",
            "source": "ÖBB",
            "title": "U6: Signalstörung Spittelau",
            "description": "Signalstörung im Bereich Spittelau",
        },
        {
            "guid": "vor-bar",
            "_identity": "vor|bar",
            "source": "VOR/VAO",
            "title": "U6: Signalstörung Spittelau",
            "description": "Signalstörung U6 Spittelau, Auswirkung bis Längenfeldgasse",
        },
    ]
    result = deduplicate_fuzzy(items)
    assert len(result) == 1
    # VOR record wins as master; ÖBB description appended only when substantive.
    assert result[0].get("source") == "VOR/VAO"
