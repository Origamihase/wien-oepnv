from unittest.mock import MagicMock, patch
import src.build_feed as bf

def test_build_feed_mutation():
    items = [{"title": "original", "guid": "123"}]

    with patch.object(bf, "_invoke_collect_items", return_value=items), \
         patch.object(bf, "_drop_old_items", return_value=(items, set())), \
         patch.object(bf, "_summarize_duplicates", return_value=[]), \
         patch.object(bf, "_dedupe_items", return_value=items), \
         patch.object(bf, "deduplicate_fuzzy", return_value=items), \
         patch.object(bf, "_make_rss", return_value=("", set())), \
         patch.object(bf, "_load_state", return_value={}), \
         patch.object(bf, "_save_state"), \
         patch.object(bf, "atomic_write", MagicMock()):

        # Make a hook to capture pre_dedupe_items before it goes to dedupe functions
        original_summarize = bf._summarize_duplicates
        captured_pre_dedupe = []
        def mock_summarize(items_arg):
            captured_pre_dedupe.extend(items_arg)
            return original_summarize(items_arg)

        with patch.object(bf, "_summarize_duplicates", side_effect=mock_summarize):
            # To avoid hitting actual paths
            with patch("src.build_feed.validate_path", MagicMock()), \
                 patch("src.build_feed.write_feed_health_report", MagicMock()), \
                 patch("src.build_feed.write_feed_health_json", MagicMock()):
                bf.main()

        # Now verify deepcopy
        assert id(captured_pre_dedupe[0]) != id(items[0])
        assert captured_pre_dedupe[0]["title"] == items[0]["title"]
        assert captured_pre_dedupe[0]["guid"] == items[0]["guid"]
