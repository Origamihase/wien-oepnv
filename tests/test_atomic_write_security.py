import unittest
from unittest.mock import MagicMock, patch, mock_open
from pathlib import Path
import pytest
from src.utils.files import atomic_write

class TestAtomicWriteSecurity(unittest.TestCase):
    def setUp(self):
        self.tmp_path = Path("/tmp/test_dir")
        self.target = self.tmp_path / "target.txt"
        # We will mock uuid to return a fixed value
        self.fixed_uuid_hex = "00000000000000000000000000000000"
        self.expected_tmp_path = self.tmp_path / f"target.txt.{self.fixed_uuid_hex}.tmp"

    @patch("src.utils.files.uuid.uuid4")
    @patch("builtins.open", new_callable=mock_open)
    @patch("src.utils.files.os.chmod")
    @patch("src.utils.files.os.replace")
    @patch("src.utils.files.os.fsync")
    @patch("src.utils.files.os.link")
    @patch("src.utils.files.os.unlink")
    def test_overwrite_false_uses_link(self, mock_unlink, mock_link, mock_fsync, mock_replace, mock_chmod, mock_file, mock_uuid):
        # Setup uuid mock
        mock_uuid_obj = MagicMock()
        mock_uuid_obj.hex = self.fixed_uuid_hex
        mock_uuid.return_value = mock_uuid_obj

        # Setup file mock
        mock_file.return_value.fileno.return_value = 123

        # We need to mock os.path.exists and Path.exists because atomic_write checks it first
        with patch("src.utils.files.Path.exists", return_value=False):
            with patch("src.utils.files.Path.mkdir"):
                # Also mock os.path.exists for cleanup check
                with patch("src.utils.files.os.path.exists", return_value=True):
                    with atomic_write(self.target, overwrite=False) as f:
                        pass

        # Verify open was called with correct path
        mock_file.assert_called_once_with(self.expected_tmp_path, 'w', encoding='utf-8', newline=None)

        # Verify link was called instead of replace
        args, _ = mock_link.call_args
        # args[0] is src (tmp), args[1] is dst (target)
        # Note: implementation uses tmp_path (Path object) or str(tmp_path) depending on os.link implementation handling
        # src.utils.files uses os.link(tmp_path, target). Both are Path objects usually.
        # Check equality with Path objects
        assert args[0] == self.expected_tmp_path
        assert args[1] == self.target

        # Verify cleanup
        mock_unlink.assert_called_with(self.expected_tmp_path)
        mock_replace.assert_not_called()

    @patch("src.utils.files.uuid.uuid4")
    @patch("builtins.open", new_callable=mock_open)
    @patch("src.utils.files.os.chmod")
    @patch("src.utils.files.os.replace")
    @patch("src.utils.files.os.fsync")
    @patch("src.utils.files.os.link")
    @patch("src.utils.files.os.unlink")
    def test_overwrite_true_uses_replace(self, mock_unlink, mock_link, mock_fsync, mock_replace, mock_chmod, mock_file, mock_uuid):
        mock_uuid_obj = MagicMock()
        mock_uuid_obj.hex = self.fixed_uuid_hex
        mock_uuid.return_value = mock_uuid_obj
        mock_file.return_value.fileno.return_value = 123

        with patch("src.utils.files.Path.exists", return_value=False):
            with patch("src.utils.files.Path.mkdir"):
                with atomic_write(self.target, overwrite=True) as f:
                    pass

        # Verify replace was called
        args, _ = mock_replace.call_args
        assert args[0] == self.expected_tmp_path
        assert args[1] == self.target

        mock_link.assert_not_called()

    @patch("src.utils.files.uuid.uuid4")
    @patch("builtins.open", new_callable=mock_open)
    @patch("src.utils.files.os.chmod")
    @patch("src.utils.files.os.replace")
    @patch("src.utils.files.os.fsync")
    @patch("src.utils.files.os.link")
    @patch("src.utils.files.os.unlink")
    def test_overwrite_false_race_condition(self, mock_unlink, mock_link, mock_fsync, mock_replace, mock_chmod, mock_file, mock_uuid):
        mock_uuid_obj = MagicMock()
        mock_uuid_obj.hex = self.fixed_uuid_hex
        mock_uuid.return_value = mock_uuid_obj
        mock_file.return_value.fileno.return_value = 123

        # Simulate FileExistsError from os.link (race condition hit)
        mock_link.side_effect = FileExistsError("File exists")

        with patch("src.utils.files.Path.exists", return_value=False):
            with patch("src.utils.files.Path.mkdir"):
                with patch("src.utils.files.os.path.exists", return_value=True):
                    with pytest.raises(FileExistsError):
                        with atomic_write(self.target, overwrite=False) as f:
                            pass

        # Verify attempt to link
        mock_link.assert_called_once()

        # Verify cleanup - unlink should be called
        mock_unlink.assert_called_with(self.expected_tmp_path)
