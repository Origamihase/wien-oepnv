
from src.utils.env import load_env_file
from src.utils.configuration_wizard import _escape_env_value

def test_env_roundtrip_wizard(tmp_path):
    # Verify that values escaped by configuration wizard can be loaded correctly
    original = "line1\nline2\twith\r\ncontrol chars and \"quotes\""
    escaped = _escape_env_value(original)
    content = f"KEY={escaped}"

    env_file = tmp_path / ".env"
    env_file.write_text(content, encoding="utf-8")

    parsed = load_env_file(env_file)
    assert parsed["KEY"] == original

def test_env_multiline_escaped(tmp_path):
    # Test escaped newlines in double quotes
    content = 'KEY="line1\\nline2"'
    env_file = tmp_path / ".env"
    env_file.write_text(content, encoding="utf-8")

    parsed = load_env_file(env_file)
    assert parsed["KEY"] == "line1\nline2"

def test_env_multiline_literal(tmp_path):
    # Test literal newlines in double quotes
    content = 'KEY="line1\nline2"'
    env_file = tmp_path / ".env"
    env_file.write_text(content, encoding="utf-8")

    parsed = load_env_file(env_file)
    assert parsed["KEY"] == "line1\nline2"

def test_env_quotes_escaped(tmp_path):
    # Test escaped quotes
    content = 'KEY="val\\"ue"'
    env_file = tmp_path / ".env"
    env_file.write_text(content, encoding="utf-8")

    parsed = load_env_file(env_file)
    assert parsed["KEY"] == 'val"ue'

def test_env_single_quotes_strict(tmp_path):
    # Test that single quotes do NOT interpret \n
    content = "KEY='line1\\nline2'"
    env_file = tmp_path / ".env"
    env_file.write_text(content, encoding="utf-8")

    parsed = load_env_file(env_file)
    assert parsed["KEY"] == "line1\\nline2"  # Remains literal \n

def test_env_roundtrip_complex(tmp_path):
    # Complex case with backslashes and mixed content
    # Expected: C:\Path\To\File with "quotes"
    # Escaped: "C:\\Path\\To\\File with \"quotes\""
    raw_val = 'C:\\Path\\To\\File with "quotes"'
    escaped_val = raw_val.replace('\\', '\\\\').replace('"', '\\"')
    content = f'KEY="{escaped_val}"'

    env_file = tmp_path / ".env"
    env_file.write_text(content, encoding="utf-8")

    parsed = load_env_file(env_file)
    assert parsed["KEY"] == raw_val

def test_env_inline_comments(tmp_path):
    content = 'KEY=value # comment'
    env_file = tmp_path / ".env"
    env_file.write_text(content, encoding="utf-8")
    parsed = load_env_file(env_file)
    assert parsed["KEY"] == "value"

    content2 = 'KEY="value # not comment"'
    env_file.write_text(content2, encoding="utf-8")
    parsed = load_env_file(env_file)
    assert parsed["KEY"] == "value # not comment"
