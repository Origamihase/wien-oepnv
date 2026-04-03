import hashlib
from src.utils.files import get_file_hash

def test_get_file_hash_uses_chunking(tmp_path):
    test_file = tmp_path / "test.txt"
    test_content = b"test content " * 1000
    test_file.write_bytes(test_content)

    expected_hash = hashlib.sha256(test_content).hexdigest()
    actual_hash = get_file_hash(test_file, chunk_size=1024)

    assert actual_hash == expected_hash
