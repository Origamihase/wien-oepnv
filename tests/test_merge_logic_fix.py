import unittest
from src.feed.merge import _has_significant_overlap

class TestMergeLogic(unittest.TestCase):
    def test_single_token_overlap_false_positive(self):
        # "Zug" matches, but "fällt aus" vs "verspätet" are different contexts (though arguably similar event).
        # Better example: "U1: Störung" vs "U1: Bauarbeiten" -> "Störung" and "Bauarbeiten" are disjoint.
        # But "Störung in Station X" vs "Aufzug in Station X defekt".
        # Token overlap: "in", "Station", "X". 3 tokens overlap. This is significant.

        # Bad case: "Zug A" vs "Zug B".
        # Tokens: {"zug", "a"}, {"zug", "b"}.
        # Intersection: {"zug"}. Union: {"zug", "a", "b"}.
        # Jaccard: 1/3 = 0.33. Should be False (< 0.4).
        # Current logic: True (not disjoint).
        name1 = "Zug A"
        name2 = "Zug B"
        self.assertFalse(_has_significant_overlap(name1, name2),
                         f"Should not merge '{name1}' and '{name2}' just because of 'Zug'")

    def test_significant_overlap_true(self):
        # "Störung Wien Mitte" vs "Störung in Wien Mitte"
        # T1: {störung, wien, mitte}
        # T2: {störung, in, wien, mitte}
        # Intersection: {störung, wien, mitte} (3)
        # Union: {störung, in, wien, mitte} (4)
        # Jaccard: 3/4 = 0.75 >= 0.4 -> True.
        name1 = "Störung Wien Mitte"
        name2 = "Störung in Wien Mitte"
        self.assertTrue(_has_significant_overlap(name1, name2))

if __name__ == "__main__":
    unittest.main()
