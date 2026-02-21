import unittest
from unittest.mock import patch
from src.providers.vor import load_request_count

class TestVorError(unittest.TestCase):
    def test_load_request_count_permission_error(self):
        # Mock Path.read_text to raise PermissionError
        # Note: We need to patch pathlib.Path.read_text where it's defined/used
        with patch("pathlib.Path.read_text", side_effect=PermissionError("Mock Permission Denied")):
            date, count = load_request_count()
            self.assertIsNone(date)
            self.assertEqual(count, 0)

if __name__ == "__main__":
    unittest.main()
