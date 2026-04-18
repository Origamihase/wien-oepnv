from src.feed.merge import _has_significant_overlap

def test_has_significant_overlap_stopwords():
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
