
import os
from pathlib import Path
from src.utils.env import load_env_file

def test_multiline_env_parsing(tmp_path):
    env_content = """
SINGLE_LINE="value"
MULTI_LINE="first line
second line"
KEY_WITH_ESCAPES="line one\\nline two"
"""
    env_file = tmp_path / ".env"
    env_file.write_text(env_content, encoding="utf-8")

    parsed = load_env_file(env_file)

    assert parsed.get("SINGLE_LINE") == "value"
    assert parsed.get("MULTI_LINE") == "first line\nsecond line"
    # Escapes are handled by python string literal in test, but in file they are literal chars
    # Wait, in file: KEY="line one\nline two" -> backslash and n.
    # The parser handles escaped quotes and backslashes if followed by special char.
    # Standard shell/env: \n inside quotes is usually literal \n (newline char) OR backslash n depending on implementation.
    # My implementation:
    # if char == '\\': if quote_char == '"': unescape...

    # If file content is literal \ and n.
    # My parser: \ followed by n. n is "n" -> unescape to newline.
    # So result is "line one\nline two" (actual newline).

    assert parsed.get("KEY_WITH_ESCAPES") == "line one\nline two"
